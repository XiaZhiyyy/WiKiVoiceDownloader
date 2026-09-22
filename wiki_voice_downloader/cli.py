"""Chinese interactive CLI; parser and network code never call input()."""
from __future__ import annotations
import argparse
from datetime import datetime
import logging
from pathlib import Path
import sys
import uuid
import webbrowser
from . import __version__, __revision__
from .config import application_dir, load_config, output_paths
from .downloader import Downloader
from .errors import AppError, ParseError, SelectionError, StorageError
from .http_client import HTTPClient
from .parsers.registry import all_parsers, get_parser
from .selection import parse_selection, selected_candidates
from .service import DownloadService
from .storage import Store, find_directory, safe_name
from .urls import request_url, redact_message, redact_url
from .profile_loader import load_profiles
from .models import PageRequestContext
from .manual_import import obtain_page
from .section_selection import select_section
from .text_format import render_text
from .views import view_key
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

class SafeFormatter(logging.Formatter):
    def format(self, record):
        return redact_message(super().format(record))

def make_logger(directory: Path, level: str) -> tuple[logging.Handler, Path]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6] + "_run.log"
        path = directory / name
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"日志目录不可写（{type(exc).__name__}）；请修改 log_dir。") from None
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger("wiki_voice_downloader")
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    return handler, path

def rename_log(handler, path: Path, name: str, level: str):
    """Switch to a character-named log only after closing the old Windows handle."""
    logger = logging.getLogger("wiki_voice_downloader")
    logger.removeHandler(handler)
    handler.close()
    target = path.with_name(path.stem.removesuffix("_run") + "_" + safe_name(name) + ".log")
    try:
        path.rename(target)
    except OSError:
        target = path
    replacement = logging.FileHandler(target, encoding="utf-8")
    replacement.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(replacement)
    return replacement, target

def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="WikiVoiceDownloader",
        description="顺序下载 Biligame 碧蓝航线或 Koumakan Quotes 单个角色页配音；每次重新选择分组。",
    )
    parser.add_argument("url", nargs="?", help="角色详情页 URL；不填写则交互询问")
    parser.add_argument("--config", metavar="JSON", help="配置文件；相对路径基于应用目录")
    parser.add_argument("--name", help="覆盖显示名称；不会重命名已知页面的历史目录")
    parser.add_argument("--parser", help="手动选择已实现解析器")
    parser.add_argument('--server', choices=['jp','cn','en'],
                        help='选择 Wiki 服务器文本分区；不代表已识别音频实际语言')
    parser.add_argument('--html-file', metavar='PATH',
                        help='Read local .html/.htm/.txt instead of requesting the page; normal mode downloads audio')
    parser.add_argument('--html-encoding', metavar='ENCODING',
                        help='Text encoding for --html-file only; default strict UTF-8/BOM')
    parser.add_argument("--dry-run", action="store_true", help="只读分析，不分配编号、不创建日志或输出")
    parser.add_argument("--version", action="version", version=__version__ + " " + __revision__)
    return parser

def _choose_name(info, explicit: str | None, ask, emit) -> str:
    if explicit is not None:
        if not explicit.strip():
            raise AppError("--name 不能为空。")
        return explicit.strip()
    values = info.name_candidates
    if info.name_reliable and len(values) == 1:
        return values[0]
    if values:
        emit("名称候选：" + " / ".join(values))
        emit("页面标题、角色名称和 URL 别名可能不同。")
    default = info.character_name
    while True:
        value = ask(f"请确认角色名称{f'（回车使用 {default}）' if default else ''}：").strip()
        if value or default:
            return value or default
        emit("名称不能为空，请重新输入。")

def _choose_parser(url: str, parser_id: str | None, ask, emit, profiles=None):
    try:
        return get_parser(url, parser_id, profiles)
    except ParseError:
        emit("无法自动识别。当前实际实现的解析器：")
        parsers = all_parsers(profiles)
        for index, parser in enumerate(parsers, 1):
            emit(f"[{index}] {parser.display_name}")
        emit("[0] 取消")
        while True:
            answer = ask("请选择解析器：").strip()
            if answer == "0":
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(parsers):
                return parsers[int(answer) - 1]
            emit("选项无效。")

def _show_summary(summary, directory: Path, log_path: Path | None, emit):
    if summary.interrupted:
        emit("\n用户中断；下次请选择“继续 / 补全下载”。")
    elif summary.stopped:
        emit("\n本轮因站点访问限制或服务器等待要求停止。")
    elif summary.failed:
        emit("\n本轮全部请求失败。" if summary.failed == summary.total else "\n本轮部分失败。")
    else:
        emit("\n本轮全部成功。")
    emit(f"本次新下载成功数：{summary.downloaded}")
    emit(f"已有有效文件数：{summary.existing}")
    emit(f"本次下载 / 覆盖失败数：{summary.failed}")
    emit(f"未执行或中断数：{summary.unexecuted}")
    emit(f'其中受阻数（已计入失败数）：{summary.blocked}')
    if summary.stop_reason:
        emit('stop_reason=' + summary.stop_reason)
    emit(f"重复引用排除数：{summary.duplicates}")
    emit(f"页面无音频文字跳过数：{summary.text_only}")
    emit(f"本次选中无台词但已保存数：{summary.missing_text_saved}")
    emit(f"角色累计有效音频数：{summary.cumulative}")
    emit(f'实际音频 HTTP 请求数（含重试 / 重定向）：{summary.audio_requests}')
    emit(f'新增引用：{summary.references_added}；补入语言变体：{summary.languages_added}')
    emit(f'TXT 当前文本视图：{summary.active_view}')
    for filename, reason in summary.failures:
        emit(f"  {filename}：{reason}")
    emit(f"保存位置：{directory}")
    if log_path:
        emit(f"日志位置：{log_path}")

def main(argv=None, *, input_fn=None, output_fn=None, client_factory=HTTPClient,
         app_dir: Path | None = None, interactive: bool | None = None,
         browser_open=None) -> int:
    interactive = (input_fn is not None or bool(getattr(sys.stdin, 'isatty', lambda: False)())) if interactive is None else interactive
    raw_ask, emit = input_fn or input, output_fn or print
    def ask(prompt):
        if not interactive:
            raise EOFError
        return raw_ask(prompt)
    browser_open = browser_open or (lambda url: webbrowser.open(url, new=2))
    args = argument_parser().parse_args(argv)
    handler, log_path = None, None
    app = app_dir or application_dir()
    try:
        if args.html_encoding is not None and args.html_file is None:
            raise AppError('--html-encoding requires --html-file')
        if args.html_file is not None and not args.url and not interactive:
            raise AppError('missing_source_url: --html-file requires the original source page URL')
        config, base = load_config(args.config, app)
        profiles = load_profiles(config.site_profile_dir, app)
        download_root, logs_root = output_paths(config, base)
        if not args.dry_run:
            handler, log_path = make_logger(logs_root, config.log_level)
        emit(f"Wiki Voice Downloader v{__version__} {__revision__}")
        log.info("启动 version=%s python=%s", __version__, sys.version.split()[0])
        raw_url = args.url or ask("请输入角色 Wiki 页面 URL：\n> ").strip()
        if not raw_url:
            raise AppError("页面网址不能为空。")
        url = request_url(raw_url)
        if url.lower().split("?", 1)[0].endswith((".mp3", ".wav", ".ogg", ".flac")):
            raise AppError("你输入了音频地址；请改为角色详情页 URL。")
        context = PageRequestContext(raw_url, url, fragment_hint=urlsplit(raw_url).fragment,
                                     explicit_server=args.server)
        parser = _choose_parser(url, args.parser, ask, emit, profiles)
        if parser is None:
            emit("已取消。")
            return 0
        # Manual selection never bypasses supported source hosts.
        if not parser.supports(url):
            raise ParseError("unsupported_site：所选解析器不支持这个网址；请使用对应站点的角色详情页 / Quotes 页。", "unsupported")
        log.info("解析器=%s 页面=%s", parser.parser_id, redact_url(url))
        with client_factory(config) as client:
            client.policy = parser.policy
            page = obtain_page(
                context=context, parser=parser, config=config, client=client,
                html_file=args.html_file, html_encoding=args.html_encoding,
                dry_run=args.dry_run, interactive=interactive,
                ask=ask, emit=emit, browser_open=browser_open)
            if page is None:
                emit('已取消；未执行下载，已有数据未修改。')
                return 0
            acquisition = page.acquisition
            emit('Page source: ' + acquisition['acquisition_kind']
                 + '; coverage=' + acquisition['coverage_status'])
            emit('HTML SHA-256: ' + acquisition['content_sha256'])
            if acquisition['acquisition_kind'] == 'local_html':
                emit('本地 HTML：页面获取零请求；音频是否可访问尚未证明。')
            log.info('acquisition_kind=%s snapshot_sha256=%s coverage=%s identity=%s',
                     acquisition['acquisition_kind'], acquisition['content_sha256'],
                     acquisition['coverage_status'], acquisition['identity_status'])
            emit(f'站点规则：{parser.profile.site_id} schema=1 sha256={parser.profile.fingerprint}')
            log.info('profile=%s schema=1 fingerprint=%s', parser.profile.site_id, parser.profile.fingerprint)
            select_section(page, parser, context, ask, emit)
            log.info('selected_section=%s text_language=%s', page.selected_section, page.text_language)
            name = _choose_name(page.info, args.name, ask, emit)
            if handler:
                handler, log_path = rename_log(handler, log_path, name, config.log_level)
            for warning in page.warnings:
                log.warning("解析诊断：%s", warning)
            if page.warnings:
                emit(f"解析诊断 {len(page.warnings)} 项：")
                for warning in page.warnings[:6]:
                    emit("  " + warning)
                if len(page.warnings) > 6:
                    emit("  其余诊断详见日志。")
            directory_override = None
            while True:
                try:
                    directory, data = find_directory(
                        download_root, page.info, name, parser.parser_id, directory_override)
                    break
                except StorageError as exc:
                    # A different directory is the only interactive resolution;
                    # never fix corrupt metadata behind the user's back.
                    emit(str(exc))
                    directory_override = ask("请输入另一个目录名称，或输入 0 取消：").strip()
                    if directory_override == "0":
                        emit("已取消；原目录未修改。")
                        return 0
                    if not directory_override:
                        emit("目录名称不能为空。")
            effective_name = data["character_name"] if data else name
            emit(f"角色：{effective_name}")
            emit(f"保存位置：{directory}")
            if data and name != effective_name:
                emit("已按可核实页面身份复用历史目录，不重新编号。")
            emit("\n检测到语音分组：")
            for index, group in enumerate(page.groups, 1):
                emit(f"[{index}] {group.name}  {group.unique_count} 条唯一音频")
            emit("[A] 当前分区全部")
            if args.dry_run and not interactive:
                selection = list(range(len(page.groups)))
                emit('Non-interactive dry-run: previewing all groups in the selected server only.')
            else:
                while True:
                    try:
                        selection = parse_selection(ask("请选择，例如 1,3 或 A：\n> "), len(page.groups))
                        break
                    except SelectionError as exc:
                        emit(str(exc))
            candidates, duplicates, raw_count = selected_candidates(page.groups, selection)
            if not candidates:
                raise ParseError('no_audio: selection has no safe audio; no data was modified', 'no_audio')
            emit('\n配对预览（候选序号，不是已分配的音频 ID）：')
            for index, candidate in enumerate(candidates[:5], 1):
                texts = ([t for t in candidate.texts if t.language == page.text_language]
                         if page.text_language else candidate.texts)
                body = render_text(texts) if candidate.text_status not in {'ambiguous','ambiguous_pairing'} else '[\u65e0\u53f0\u8bcd]'
                source = redact_url(candidate.source_url)
                emit(f'  候选 {index}： {candidate.group_name} | {candidate.category} | {body[:160]} | {source}')
            missing = sum(not any(t.language == page.text_language and t.text.strip() for t in c.texts)
                          for c in candidates) if page.text_language else sum(not c.texts for c in candidates)
            ambiguous = sum(c.text_status in {'ambiguous','ambiguous_pairing'} for c in candidates)
            emit(f'缺少目标语言正文：{missing}；配对歧义：{ambiguous}')
            if data and data['schema_version'] == 1:
                emit('migration_required：确认执行后将 schema 1 升至 2，并先备份 metadata / TXT。')
            old_view = data.get('active_export_view', 'legacy/*') if data else None
            new_view = view_key(page.selected_section, page.text_language)
            if data and old_view != new_view:
                emit(f'TXT 文本视图切换：{old_view} -> {new_view}；确认后，有有效目标媒体时才备份并切换 TXT。')
            overwrite = False
            if data and not (args.dry_run and not interactive):
                emit("\n[1] 继续 / 补全下载（默认）")
                emit("[2] 重新下载并覆盖本次选中的语音")
                emit("[3] 取消")
                while True:
                    answer = ask("请选择：").strip() or "1"
                    if answer in {"1", "2", "3"}:
                        break
                    emit("请输入 1、2 或 3。")
                if answer == "3":
                    log.info("用户取消；没有执行恢复或编号规划。")
                    emit("已取消；原有音频、索引和 TXT 均未修改。")
                    return 0
                overwrite = answer == "2"
            valid_count = 0
            if data:
                preview = Store(directory, data)
                keys = {candidate.source_url for candidate in candidates}
                valid_count = sum(preview.valid(item) for item in data["items"]
                                  if item["source_key"] in keys)
            request_count = len(candidates) if overwrite else len(candidates) - valid_count
            emit(f"\n选中候选 {raw_count}；URL 去重后 {len(candidates)}；已有有效 {valid_count}；预计请求 {request_count}。")
            log.info("选择=%s candidates=%s unique=%s duplicates=%s valid=%s expected=%s overwrite=%s",
                     [page.groups[i].name for i in selection], raw_count, len(candidates),
                     duplicates, valid_count, request_count, overwrite)
            if args.dry_run:
                emit("只读分析结束；未分配编号，未修改任何输出。")
                return 0
            if overwrite:
                emit("仅覆盖本次选中的语音，保留编号和未选择内容；下载失败时保留旧有效版本。")
                if ask("确认执行覆盖？输入 YES，其他输入取消：").strip().upper() != "YES":
                    emit("已取消；原有音频、索引和 TXT 均未修改。")
                    return 0
            service = DownloadService(Downloader(client))
            summary = service.run(
                page, candidates, directory, effective_name, parser.parser_id,
                overwrite=overwrite, duplicates=duplicates,
                progress=lambda index, total, filename, result:
                    emit(f"[任务 {index}/{total}] {filename}  {result}"),
            )
            _show_summary(summary, directory, log_path, emit)
            log.info("汇总 success=%s existing=%s failed=%s unexecuted=%s cumulative=%s exit=%s",
                     summary.downloaded, summary.existing, summary.failed,
                     summary.unexecuted, summary.cumulative, summary.exit_code)
            return summary.exit_code
    except EOFError:
        emit("输入已结束：缺少必要交互选项；请在终端或通过 start.bat 运行。")
        log.warning("EOF：必要输入缺失。")
        return 1
    except KeyboardInterrupt:
        emit("\n用户中断。已有数据保留，下次请选择“继续 / 补全下载”。")
        log.warning("用户 Ctrl+C 中断。")
        return 130
    except (AppError, OSError) as exc:
        message = str(exc) if isinstance(exc, AppError) else f"本地操作失败（{type(exc).__name__}）；检查权限、磁盘和文件占用。"
        message = redact_message(message)
        emit("错误：" + message)
        log.error("%s", message)
        if log_path:
            emit(f"日志位置：{log_path}")
        return 1
    finally:
        if handler:
            logging.getLogger("wiki_voice_downloader").removeHandler(handler)
            handler.close()
