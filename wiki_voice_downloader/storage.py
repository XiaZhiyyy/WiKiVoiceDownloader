"""Persistent identity, number allocation, safe paths and journaled commits.

metadata.json is authoritative. The parent item preserves the old completed
state until pending_commit.new has been safely installed and acknowledged.
"""
from __future__ import annotations
import copy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Callable
from .errors import AppError, StorageError
from .models import PageInfo, VoiceCandidate, DownloadResult, utc_now
from .text_format import serialize_record, render_text, normalize_line
from .urls import request_url
from .validation import file_matches

log = logging.getLogger(__name__)
SCHEMA_VERSION = 2
NOOP = lambda name: None

def safe_name(value: str) -> str:
    value = unicodedata.normalize("NFC", normalize_line(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    # Conservative 80 UTF-16 code unit component budget.
    out, units = [], 0
    for char in value:
        count = 2 if ord(char) > 0xFFFF else 1
        if units + count > 80:
            break
        out.append(char)
        units += count
    value = "".join(out).rstrip(" .") or "Character"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])",
                    value.split(".", 1)[0], flags=re.I):
        value = "_" + value
    return value

def _linked(path: Path) -> bool:
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        return True
    try:
        import stat
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
    except FileNotFoundError:
        return False

def ensure_plain(path: Path) -> None:
    if _linked(path):
        raise StorageError(f"拒绝操作符号链接或目录联接：{path.name}")

def validate_directory_path(directory: Path) -> None:
    ensure_plain(directory)
    for ancestor in directory.absolute().parents:
        ensure_plain(ancestor)
    # Conservative Windows MAX_PATH compatible limit, including longest
    # normal filename and temporary journal suffix.
    if len(str(directory.absolute()).encode("utf-16-le")) // 2 + 48 > 240:
        raise StorageError("输出路径过长；请在配置中使用更短的 download_dir。")
    if directory.exists() and not directory.is_dir():
        raise StorageError("角色输出位置不是目录。")
    for name in ("audio", "metadata.json", ".wvd.lock"):
        ensure_plain(directory / name)

def fsync_directory(directory: Path) -> None:
    if os.name != "nt" and hasattr(os, "O_DIRECTORY"):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def atomic_write(path: Path, data: bytes, checkpoint: Callable[[str], None] = NOOP,
                 event: str = "before_atomic_replace") -> None:
    ensure_plain(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint(event)
        os.replace(temporary, path)
        temporary = None
        fsync_directory(path.parent)
    except OSError as exc:
        raise StorageError(f"无法原子保存 {path.name}（{type(exc).__name__}）；检查磁盘、权限和文件占用。") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result

def _time_ok(value, nullable: bool = True) -> bool:
    if value is None:
        return nullable
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False

def _validate_record(record: dict, *, pending_allowed: bool = True, schema: int = 1) -> None:
    required = {
        "id", "filename", "source_url", "source_key", "resolved_audio_url",
        "group_id", "group_name", "category", "texts", "text_status", "status",
        "size_bytes", "sha256", "first_seen_at", "downloaded_at",
        "last_attempt_at", "last_error",
    }
    if not isinstance(record, dict) or required - record.keys():
        raise ValueError("missing item fields")
    number = record["id"]
    if type(number) is not int or number < 1 or number > 10**12:
        raise ValueError("invalid id")
    filename = record['filename']
    if schema == 1:
        if filename != f"{number:03d}.mp3":
            raise ValueError("filename disagrees with stable id")
    else:
        if filename not in {None, f"{number:03d}.mp3", f"{number:03d}.ogg"}:
            raise ValueError("filename disagrees with stable id/format")
        if record.get('format_hint') not in {None, 'mp3', 'ogg'}:
            raise ValueError('unsupported format hint')
        if record.get('media_format') not in {None, 'mp3', 'ogg'}:
            raise ValueError('unsupported stored media format')
        if record['status'] == 'completed' and (
            record.get('media_format') not in {'mp3','ogg'}
            or filename != f"{number:03d}." + record['media_format']
        ):
            raise ValueError('completed media format/filename mismatch')
        from .views import validate_reference
        if not isinstance(record.get('references'), dict) or not record['references']:
            raise ValueError('missing provenance references')
        for rid, ref in record['references'].items():
            validate_reference(ref, rid, record['source_url'])
        if record.get('media_format') == 'ogg' and record.get('frame_count') is not None:
            raise ValueError('Ogg must not carry a fabricated MP3 frame count')
    if record["source_url"] != request_url(record["source_url"]):
        raise ValueError("non-normalized source URL")
    if record["source_key"] != record["source_url"]:
        raise ValueError("source key mismatch")
    for key in ("group_id", "group_name", "category"):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError("invalid grouping")
    if record["resolved_audio_url"] is not None:
        request_url(record["resolved_audio_url"])
    if record["status"] not in {"pending", "completed", "failed"}:
        raise ValueError("unknown status")
    if record["text_status"] not in ({"present", "missing", "ambiguous"} if schema == 1 else {"present", "missing", "ambiguous", "missing_target_language", "ambiguous_pairing"}):
        raise ValueError("unknown text status")
    if not isinstance(record["texts"], list):
        raise ValueError("invalid texts")
    for text in record["texts"]:
        if (not isinstance(text, dict) or set(text) != {"language", "text"}
            or not isinstance(text["text"], str)
            or not (text["language"] is None or isinstance(text["language"], str))):
            raise ValueError("invalid semantic text")
    if record["text_status"] == "present" and not record["texts"]:
        raise ValueError("present text must not be empty")
    if record["status"] == "completed":
        if type(record["size_bytes"]) is not int or record["size_bytes"] <= 0:
            raise ValueError("invalid completed size")
        if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            raise ValueError("invalid completed digest")
        if not _time_ok(record["downloaded_at"], False):
            raise ValueError("invalid completion time")
    if not _time_ok(record["first_seen_at"], False) or not _time_ok(record["last_attempt_at"]):
        raise ValueError("invalid timestamps")
    if record["last_error"] is not None and not isinstance(record["last_error"], str):
        raise ValueError("invalid error")
    if "pending_commit" in record:
        if not pending_allowed:
            raise ValueError("nested pending commit")
        pending = record["pending_commit"]
        if not isinstance(pending, dict) or not {"new", "staged_at"} <= pending.keys() or pending.keys() - ({"new", "staged_at", "part_filename"} if schema == 2 else {"new", "staged_at"}):
            raise ValueError("invalid journal")
        _validate_record(pending["new"], pending_allowed=False, schema=schema)
        new = pending["new"]
        if (new["status"] != "completed" or not _time_ok(pending["staged_at"], False)
            or any(new[key] != record[key] for key in ("id", "source_key", "source_url"))
            or ((schema == 1 or record["downloaded_at"] is not None) and new["filename"] != record["filename"])):
            raise ValueError("journal identity mismatch")
        if 'part_filename' in pending and pending['part_filename'] not in {
            f'{number:03d}.mp3.part', f'{number:03d}.ogg.part', f'{number:03d}.media.part'
        }:
            raise ValueError('unsafe pending temporary path')

def validate_metadata(data: dict) -> None:
    try:
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data.get("schema_version") not in {1, 2}):
            raise ValueError("unsupported schema_version")
        required = {"parser_id", "character_name", "directory_name", "source_page_url",
                    "resolved_page_url", "page_identity", "created_at", "updated_at",
                    "next_id", "items", "txt_filename", "identity_aliases"}
        if required - data.keys():
            raise ValueError("missing top-level fields")
        for key in ("parser_id", "character_name", "directory_name", "page_identity"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError("invalid top-level text")
        if data["directory_name"] != safe_name(data["directory_name"]):
            raise ValueError("unsafe directory name")
        if data["txt_filename"] != safe_name(data["character_name"]) + ".txt":
            raise ValueError("unsafe TXT filename")
        request_url(data["source_page_url"])
        request_url(data["resolved_page_url"])
        if not isinstance(data["identity_aliases"], list) or not all(
            isinstance(v, str) for v in data["identity_aliases"]
        ):
            raise ValueError("invalid aliases")
        if not _time_ok(data["created_at"], False) or not _time_ok(data["updated_at"], False):
            raise ValueError("invalid metadata timestamps")
        if not isinstance(data["items"], list):
            raise ValueError("invalid items")
        numbers, keys = set(), set()
        for item in data["items"]:
            _validate_record(item, schema=data["schema_version"])
            if item["id"] in numbers or item["source_key"] in keys:
                raise ValueError("duplicate stable identity")
            numbers.add(item["id"])
            keys.add(item["source_key"])
        if data['schema_version'] == 2:
            from .views import validate_views
            if not isinstance(data.get('site_id'), str) or not data['site_id']:
                raise ValueError('missing site namespace')
            validate_views(data)
        if type(data["next_id"]) is not int or data["next_id"] <= max(numbers, default=0):
            raise ValueError("next_id is not greater than allocated ids")
    except (KeyError, TypeError, ValueError, AppError) as exc:
        raise StorageError(f"metadata.json 损坏或版本不支持（{exc}）；已保留现场，禁止重新初始化覆盖。") from None

def read_metadata(directory: Path) -> dict | None:
    validate_directory_path(directory)
    path = directory / "metadata.json"
    if not path.exists():
        if directory.exists() and any(directory.iterdir()):
            # A previous process may have only created the persistent lock.
            # No data is guessed; this special empty initialization is safe.
            names = {p.name for p in directory.iterdir()}
            if names != {".wvd.lock"}:
                raise StorageError("非空目录没有可靠 metadata.json；请换目录或恢复有效索引。")
        return None
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise StorageError("metadata.json 超过 32 MiB 安全上限。")
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON constant")))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StorageError(f"metadata.json 无法读取（{type(exc).__name__}）；已保留现场。") from None
    validate_metadata(data)
    if data["directory_name"] != directory.name:
        raise StorageError("目录已被重命名，和 metadata.directory_name 不一致；请恢复原目录名。")
    return data

def same_page(data: dict, page: PageInfo, parser_id: str) -> bool:
    if data["parser_id"] != parser_id:
        return False
    old, new = data["page_identity"], page.page_identity
    if ":pageid:" in old and ":pageid:" in new and old != new:
        return False
    left = {old, *data.get("identity_aliases", [])}
    right = {new, *page.identity_aliases}
    return bool(left.intersection(right))

def find_directory(root: Path, page: PageInfo, name: str, parser_id: str,
                   directory_name: str | None = None) -> tuple[Path, dict | None]:
    """Read-only resolution, including aliases and Windows case-insensitive collisions."""
    matches = []
    if root.exists():
        if not root.is_dir():
            raise StorageError("download_dir 不是目录。")
        for child in root.iterdir():
            if not child.is_dir() or _linked(child):
                continue
            if (child / "metadata.json").exists():
                try:
                    data = read_metadata(child)
                except StorageError:
                    continue  # Unrelated damaged directory is not silently repaired.
                if data and same_page(data, page, parser_id):
                    matches.append((child, data))
    if len(matches) > 1:
        raise StorageError("发现多个索引声称同一页面身份；请手动保留明确的一份后再运行。")
    if matches:
        return matches[0]
    component = safe_name(directory_name or name)
    target = root / component
    if root.exists():
        for child in root.iterdir():
            if unicodedata.normalize("NFC", child.name).casefold() == component.casefold():
                target = child
                break
    validate_directory_path(target)
    data = read_metadata(target)
    if data and not same_page(data, page, parser_id):
        raise StorageError("同名目录属于不同角色页面；请指定不同的目录名称。")
    return target, data

class DirectoryLock:
    """OS advisory lock, automatically released after crashes.

    The one-byte lock file is retained to avoid unlink/recreate inode races.
    No stale-PID guessing and no permanent lockout after abnormal termination.
    """
    def __init__(self, directory: Path):
        self.path = directory / ".wvd.lock"
        self.stream = None

    def __enter__(self):
        ensure_plain(self.path)
        try:
            self.stream = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                self.stream.write(b"\0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self.stream:
                self.stream.close()
            self.stream = None
            raise StorageError("该角色目录正在被其他进程使用，或锁文件不可写；请关闭另一个实例后重试。") from None
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None

class Store:
    def __init__(self, directory: Path, data: dict, checkpoint: Callable[[str], None] = NOOP):
        self.directory = directory
        self.audio = directory / "audio"
        self.data = data
        self.checkpoint = checkpoint
        self._validation_cache = set()

    @classmethod
    def open_confirmed(cls, directory: Path, page: PageInfo, name: str, parser_id: str,
                       checkpoint: Callable[[str], None] = NOOP):
        """Must only be called after confirmation and while holding DirectoryLock."""
        data = read_metadata(directory)
        if data is None:
            now = utc_now()
            data = {
                "schema_version": 1, "parser_id": parser_id,
                "character_name": name, "directory_name": directory.name,
                "source_page_url": page.source_page_url,
                "resolved_page_url": page.resolved_page_url,
                "canonical_url": page.canonical_url, "page_identity": page.page_identity,
                "identity_aliases": list(dict.fromkeys([page.page_identity] + page.identity_aliases)),
                "txt_filename": safe_name(name) + ".txt",
                "created_at": now, "updated_at": now, "next_id": 1, "items": [],
            }
            from .migrations import upgrade_data
            data = upgrade_data(data)
        elif not same_page(data, page, parser_id):
            raise StorageError("目录身份冲突；另一个进程可能已创建不同页面的索引。")
        from .migrations import migrate_confirmed
        data = migrate_confirmed(directory, data, checkpoint)
        data["identity_aliases"] = list(dict.fromkeys(
            [data["page_identity"], page.page_identity]
            + data.get("identity_aliases", []) + page.identity_aliases))
        if ":pageid:" in page.page_identity:
            data["page_identity"] = page.page_identity
        data["resolved_page_url"] = page.resolved_page_url
        data["effective_page_url"] = page.effective_page_url or page.resolved_page_url
        data["resolved_url_observed"] = page.resolved_url_observed
        store = cls(directory, data, checkpoint)
        # Persist identity before creating audio/, making initialization recoverable.
        store.save()
        try:
            ensure_plain(store.audio)
            store.audio.mkdir(exist_ok=True)
        except OSError as exc:
            raise StorageError(f"无法创建 audio 目录（{type(exc).__name__}）。") from None
        return store

    def managed_path(self, filename: str) -> Path:
        if not isinstance(filename, str) or not re.fullmatch(r"[0-9]{3,}\.(?:mp3|ogg|media)(?:\.part)?", filename):
            raise StorageError('storage_conflict: unsafe managed audio filename')
        path = self.audio / filename
        ensure_plain(self.audio)
        ensure_plain(path)
        if path.parent.resolve() != self.audio.resolve():
            raise StorageError('storage_conflict: audio path escaped its directory')
        return path

    def path(self, item: dict, *, part: bool = False) -> Path:
        number = item['id']
        filename = item['filename']
        if filename not in {None, f'{number:03d}.mp3', f'{number:03d}.ogg'}:
            raise StorageError('storage_conflict: filename/stable id mismatch')
        if filename is None:
            if not part:
                raise StorageError('storage_conflict: no committed filename yet')
            filename = f'{number:03d}.media'
        return self.managed_path(filename + ('.part' if part else ''))

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        validate_metadata(self.data)
        raw = (json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        atomic_write(self.directory / "metadata.json", raw, self.checkpoint, "before_metadata_replace")

    def valid(self, item: dict) -> bool:
        if item['status'] != 'completed':
            return False
        path = self.path(item)
        try:
            st = path.stat()
            if not path.is_file(): return False
            key = (str(path), st.st_size, st.st_mtime_ns, st.st_ctime_ns,
                   st.st_ino, item.get('sha256'), item.get('media_format'), item['filename'])
        except OSError:
            return False
        if key in self._validation_cache:
            return True
        if file_matches(path, item):
            self._validation_cache.add(key)
            return True
        return False

    def rebuild_txt(self) -> int:
        from .views import choose_body
        lines = []
        view = self.data['export_views'][self.data['active_export_view']]
        for item in sorted(self.data['items'], key=lambda v: v['id']):
            member = view['members'].get(str(item['id']))
            if self.valid(item) and member:
                ref = item['references'][member['preferred_reference']]
                lines.append(serialize_record([
                    item['filename'], ref['group_name'], ref['category'],
                    choose_body(item, member, view['text_language']),
                ]))
        if not lines and self.data['active_export_view'] != 'legacy/*' and not (self.directory / self.data['txt_filename']).exists():
            return 0
        text = '\n'.join(lines) + ('\n' if lines else '')
        atomic_write(self.directory / self.data['txt_filename'], text.encode('utf-8'),
                     self.checkpoint, 'before_txt_replace')
        return len(lines)

    def recover(self, *, rebuild: bool = True) -> None:
        changed = False
        for item in self.data["items"]:
            pending = item.get("pending_commit")
            if pending:
                new = pending["new"]
                target = self.path(new)
                part = (self.managed_path(pending["part_filename"]) if "part_filename" in pending
                        else self.path(item, part=True))
                if file_matches(target, new):
                    item.clear()
                    item.update(copy.deepcopy(new))
                    log.info("恢复已替换的正式音频 id=%s", item["id"])
                elif file_matches(part, new):
                    if target.exists() and (item['downloaded_at'] is None or target.name != item['filename']):
                        raise StorageError('storage_conflict: unexpected target appeared during staged recovery')
                    try:
                        os.replace(part, target)
                        fsync_directory(self.audio)
                    except OSError as exc:
                        raise StorageError(f"待提交音频无法恢复替换（{type(exc).__name__}）。") from None
                    item.clear()
                    item.update(copy.deepcopy(new))
                    log.info("恢复已验证 .part id=%s", item["id"])
                else:
                    item.pop("pending_commit")
                    item["last_error"] = "待提交内容丢失或校验不符；旧有效版本保留，否则等待重下。"
                    log.warning("待提交校验失败 id=%s", item["id"])
                changed = True
            if item["status"] == "completed" and not self.valid(item):
                item["status"] = "failed"
                item["last_error"] = "本地正式音频缺失或校验不符；仅本次选中时重下。"
                log.warning("本地文件需修复 id=%s", item["id"])
                changed = True
        if changed:
            self.save()
        managed = {item['filename'] for item in self.data['items'] if item['filename']}
        managed |= {self.path(item, part=True).name for item in self.data['items']}
        for path in self.audio.iterdir():
            if path.name not in managed:
                log.warning("发现未经索引管理的音频目录内容，保留不动：%s", path.name)
        if rebuild:
            self.refresh_export()

    def _activate_available_view(self, *, save=True):
        target = self.data.get('requested_export_view', self.data['active_export_view'])
        previous = self.data['active_export_view']
        view = self.data['export_views'][target]
        available = any(str(item['id']) in view['members'] and self.valid(item)
                        for item in self.data['items'])
        if not available:
            return target == previous
        if target != previous:
            from .migrations import backup_files
            self.checkpoint('before_view_activation')
            txt = self.directory / self.data['txt_filename']
            if txt.exists():
                backup = backup_files(self.directory, [self.data['txt_filename']],
                                      'view_'+safe_name(previous), self.checkpoint)
                self.data.setdefault('export_backups', []).append({
                    'previous_view': previous, 'directory': str(backup.relative_to(self.directory)),
                    'at': utc_now()})
                log.info('export backup old=%s new=%s path=%s', previous, target, backup.name)
            self.data['active_export_view'] = target
            if save:
                self.save()  # Journal the selected view before replacing derived TXT.
            self.checkpoint('after_view_activation')
        return True

    def refresh_export(self):
        if self._activate_available_view():
            return self.rebuild_txt()
        # A new target with no usable media must not rewrite even an edited old TXT.
        return 0

    def prepare_view_backup(self, section_id, text_language):
        from .views import view_key
        from .migrations import backup_files
        new_key = view_key(section_id, text_language)
        old_key = self.data['active_export_view']
        if old_key != new_key and self.data['items'] and (self.directory/self.data['txt_filename']).exists():
            backup = backup_files(self.directory, [self.data['txt_filename']],
                                  'view_'+safe_name(old_key), self.checkpoint)
            self._view_backup = (old_key, new_key, backup)

    def plan(self, candidates: list[VoiceCandidate], *, overwrite: bool = False,
             section_id: str = 'legacy', text_language: str | None = None) -> list[dict]:
        from .views import view_key, candidate_references, merge_references
        from .migrations import backup_files
        from urllib.parse import urlsplit
        tasks = []
        planned = copy.deepcopy(self.data)
        new_key = view_key(section_id, text_language)
        previous = planned['active_export_view']
        # Reserving IDs/members does not activate an empty view. Activation is
        # delayed until a selected target media file is verified or reused.
        view = planned['export_views'].setdefault(new_key, {
            'section_id': section_id, 'text_language': text_language, 'members': {}})
        planned['requested_export_view'] = new_key
        planned_map = {item['source_key']: item for item in planned['items']}
        self.references_added = self.languages_added = 0
        for candidate in candidates:
            if candidate.section_id != section_id:
                raise StorageError('storage_conflict: mixed server candidates are not permitted')
            key = candidate.source_url
            item = planned_map.get(key)
            new_item = item is None
            if new_item:
                number = planned['next_id']
                suffix = urlsplit(key).path.rsplit('.', 1)[-1].lower()
                hint = candidate.format_hint or (suffix if suffix in {'mp3','ogg'} else None)
                filename = f'{number:03d}.{hint}' if hint else None
                # Numeric ID collision is checked across ALL supported suffixes.
                for occupied in self.audio.iterdir():
                    match = re.fullmatch(r'([0-9]+)\.(?:mp3|ogg|media)(?:\.part)?', occupied.name)
                    if match and int(match[1]) == number:
                        ensure_plain(occupied)
                        raise StorageError(f'storage_conflict: {occupied.name} is not managed; refusing overwrite')
                item = {
                    'id': number, 'filename': filename, 'source_url': key, 'source_key': key,
                    'resolved_audio_url': None, 'group_id': candidate.group_id,
                    'group_name': candidate.group_name, 'category': candidate.category,
                    'texts': [asdict(t) for t in candidate.texts], 'text_status': candidate.text_status,
                    'status': 'pending', 'size_bytes': None, 'sha256': None,
                    'first_seen_at': utc_now(), 'downloaded_at': None, 'last_attempt_at': None,
                    'last_error': None, 'last_attempt_count': 0, 'diagnostics': list(candidate.diagnostics),
                    'media_format': None, 'format_hint': hint, 'codec': None,
                    'validation_level': None, 'references': {},
                }
                planned['items'].append(item); planned['next_id'] += 1; planned_map[key] = item
            if not overwrite or new_item:
                added, languages = merge_references(item, candidate, source_page_url=planned.get('last_input_url', planned['source_page_url']))
            else:
                # Add previously unseen references, but don't update any old
                # reference version until its replacement audio commits.
                fresh = copy.deepcopy(candidate)
                fresh.references = [r for r in candidate_references(candidate) if r['reference_id'] not in item['references']]
                if fresh.references:
                    added, languages = merge_references(item, fresh, source_page_url=planned.get('last_input_url', planned['source_page_url']))
                else: added = languages = 0
            self.references_added += added; self.languages_added += languages
            ids = [r['reference_id'] for r in candidate_references(candidate)]
            member = view['members'].setdefault(str(item['id']), {
                'preferred_reference': ids[0], 'references': []})
            member['references'] = list(dict.fromkeys(member['references'] + ids))
            target_texts = {t['text'] for rid in member['references'] for t in item['references'][rid]['texts']
                            if t['language'] == text_language and t['text'].strip()}
            if text_language and len(target_texts) > 1:
                msg = 'conflicting_duplicate_text: retained earliest selected reference in '+new_key
                if msg not in item['diagnostics']: item['diagnostics'].append(msg)
                log.warning('%s id=%s', msg, item['id'])
            if section_id == 'legacy':
                ref = item['references']['legacy']
                for field in ('texts','text_status','category'):
                    item[field] = copy.deepcopy(ref[field])
            tasks.append(item)
        self.data = planned
        self._activate_available_view(save=False)
        self.save()  # Reserve ALL IDs/members before HTTP; active view may remain old.
        self.checkpoint('after_plan')
        self.refresh_export()
        return tasks

    def start_attempt(self, item: dict) -> None:
        item["last_attempt_at"] = utc_now()
        self.save()

    def mark_failure(self, item: dict, error: str, attempts: int = 0) -> bool:
        old_retained = self.valid(item)
        if not old_retained:
            item["status"] = "failed"
        item["last_error"] = error
        item["last_attempt_count"] = attempts
        self.save()
        self.refresh_export()
        return old_retained

    def commit(self, item: dict, candidate: VoiceCandidate, result: DownloadResult,
               *, overwrite: bool = False) -> None:
        from .views import merge_references
        from .errors import InvalidMedia
        new = copy.deepcopy(item)
        new.pop('pending_commit', None)
        new.pop('last_access_decision', None)
        if result.format not in {'mp3','ogg'} or result.extension != result.format:
            raise InvalidMedia('unsupported_media_format: inconsistent inspection result')
        target_filename = f"{item['id']:03d}.{result.extension}"
        if item['downloaded_at'] is not None and item['filename'] != target_filename:
            raise InvalidMedia('source_format_changed: retaining the old stable filename and valid audio')
        part = self.path(item, part=True)
        new['filename'] = target_filename
        target = self.path(new)
        if target.exists() and (item['filename'] != target_filename or item['downloaded_at'] is None):
            raise StorageError('storage_conflict: unowned file occupies detected-format destination')
        if item.get('format_hint') and item['format_hint'] != result.format:
            new['diagnostics'].append('format_hint_mismatch: using inspected '+result.format)
        if overwrite:
            merge_references(new, candidate, overwrite=True, source_page_url=self.data.get('last_input_url', self.data['source_page_url']))
        if candidate.section_id == 'legacy':
            ref = new['references']['legacy']
            for key in ('texts','text_status','category'):
                new[key] = copy.deepcopy(ref[key])
        new.update(
            status='completed', size_bytes=result.size_bytes, sha256=result.sha256,
            resolved_audio_url=result.resolved_audio_url, downloaded_at=utc_now(),
            last_error=None, last_attempt_count=result.attempts, frame_count=result.frame_count,
            media_format=result.format, codec=result.codec, validation_level=result.validation_level,
            page_count=result.page_count,
        )
        if not file_matches(part, new):
            raise StorageError("提交前 .part 校验发生变化；拒绝替换旧文件。")
        item["pending_commit"] = {"new": new, "staged_at": utc_now(), "part_filename": part.name}
        self.save()
        self.checkpoint("after_stage")
        try:
            os.replace(part, target)
            fsync_directory(self.audio)
        except OSError as exc:
            raise StorageError(f"正式音频替换失败（{type(exc).__name__}）；待提交状态已保存，可继续恢复。") from None
        self.checkpoint("after_audio_replace")
        item.clear()
        item.update(new)
        self.save()
        self.checkpoint("after_metadata_complete")
        self.refresh_export()
