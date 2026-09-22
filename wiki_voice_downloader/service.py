from __future__ import annotations
import logging
from pathlib import Path
from .downloader import Downloader
from .errors import NetworkError, StorageError
from .models import ParsedPage, VoiceCandidate, RunSummary
from .storage import DirectoryLock, Store, validate_directory_path, NOOP

log = logging.getLogger(__name__)

class DownloadService:
    def __init__(self, downloader: Downloader, *, checkpoint=NOOP):
        self.downloader = downloader
        self.checkpoint = checkpoint

    def run(self, page: ParsedPage, candidates: list[VoiceCandidate], directory: Path,
            character_name: str, parser_id: str, *, overwrite: bool = False,
            duplicates: int = 0, progress=None) -> RunSummary:
        summary = RunSummary(total=len(candidates), duplicates=duplicates,
                             text_only=page.skipped_text_only)
        progress = progress or (lambda index, total, filename, result: None)
        def display_filename(item):
            return item['filename'] or f"{item['id']:03d} (format pending)"
        if not candidates:
            from .errors import ParseError
            raise ParseError('no_audio: no selected safe audio candidates; no data was modified', 'no_audio')
        validate_directory_path(directory)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"输出目录不可写（{type(exc).__name__}）；请修改 download_dir。") from None
        with DirectoryLock(directory):
            store = Store.open_confirmed(directory, page.info, character_name,
                                         parser_id, self.checkpoint)
            store.data['last_input_url'] = page.info.source_page_url
            if page.profile_id: store.data['site_id'] = page.profile_id
            if page.request_context:
                from dataclasses import asdict
                store.data['last_request_context'] = asdict(page.request_context)
            if page.acquisition:
                import copy
                store.data['last_acquisition'] = copy.deepcopy(page.acquisition)
            # Do not rebuild the old TXT before a possible switch: it may contain
            # user edits that must be backed up exactly when activation is safe.
            store.recover(rebuild=False)
            if page.profile_fingerprint:
                store.data['last_profile'] = {'site_id': page.profile_id, 'sha256': page.profile_fingerprint}
            tasks = store.plan(candidates, overwrite=overwrite,
                               section_id=page.selected_section, text_language=page.text_language)
            summary.references_added = store.references_added
            summary.languages_added = store.languages_added
            summary.active_view = store.data['active_export_view']
            client = getattr(self.downloader, 'client', None)
            requests_before = getattr(client, 'audio_request_count', 0)
            try:
                for index, (item, candidate) in enumerate(zip(tasks, candidates), 1):
                    if not overwrite and store.valid(item):
                        summary.existing += 1
                        progress(index, len(tasks), display_filename(item), "已有有效文件")
                        continue
                    store.start_attempt(item)
                    result = None
                    try:
                        result = self.downloader.download(
                            item["source_url"], store.path(item, part=True),
                            page.info.resolved_page_url,
                        )
                        store.commit(item, candidate, result, overwrite=overwrite)
                    except NetworkError as exc:
                        if result is not None and not exc.attempts: exc.attempts = result.attempts
                        if hasattr(exc, 'decision'):
                            item['last_access_decision'] = exc.decision.record()
                        else:
                            item.pop('last_access_decision', None)
                        retained = store.mark_failure(item, str(exc), exc.attempts)
                        message = str(exc) + ("；旧有效文件已保留" if retained else "")
                        summary.failed += 1
                        summary.failures.append((display_filename(item), message))
                        progress(index, len(tasks), display_filename(item), "失败：" + message)
                        log.warning("下载失败 id=%s attempts=%s old_retained=%s reason=%s",
                                    item["id"], exc.attempts, retained, exc)
                        if exc.stop_site:
                            summary.stopped = True
                            summary.blocked += 1  # Also counted in failed; disjoint totals remain unchanged.
                            summary.stop_reason = exc.reason
                            summary.access_decision = getattr(exc, 'decision', None)
                            if summary.access_decision:
                                summary.access_decision = summary.access_decision.record()
                            break
                        continue
                    summary.downloaded += 1
                    log.info("成功 id=%s bytes=%s attempts=%s", item["id"],
                             result.size_bytes, result.attempts)
                    progress(index, len(tasks), display_filename(item), "成功")
            except KeyboardInterrupt:
                # Do not write stale in-memory state after an interrupted commit.
                # The journal is authoritative and next launch will recover it.
                summary.interrupted = True
                log.warning("用户中断；保留编号和可恢复提交状态。")
            summary.unexecuted = summary.total - summary.downloaded - summary.existing - summary.failed
            # Read only here. A commit interrupted after os.replace must not
            # overwrite its on-disk journal with an old in-memory snapshot.
            summary.cumulative = sum(store.valid(item) for item in store.data["items"])
            selected_keys = {c.source_url for c in candidates}
            from .views import choose_body
            summary.active_view = store.data['active_export_view']
            view = store.data['export_views'][store.data['active_export_view']]
            summary.missing_text_saved = sum(
                store.valid(item) and str(item['id']) in view['members'] and
                choose_body(item, view['members'][str(item['id'])], view['text_language']) == '[\u65e0\u53f0\u8bcd]'
                for item in store.data['items'] if item['source_key'] in selected_keys
            )
            summary.audio_requests = getattr(client, 'audio_request_count', requests_before) - requests_before
            if not summary.interrupted:
                store.data['last_run'] = {
                    'downloaded': summary.downloaded, 'reused': summary.existing,
                    'failed_including_blocked': summary.failed, 'blocked': summary.blocked,
                    'unexecuted': summary.unexecuted, 'stop_reason': summary.stop_reason,
                    'active_export_view': summary.active_view,
                    'actual_audio_http_requests': summary.audio_requests,
                }
                store.save()
            summary.audio_requests = getattr(client, 'audio_request_count', requests_before) - requests_before
            return summary
