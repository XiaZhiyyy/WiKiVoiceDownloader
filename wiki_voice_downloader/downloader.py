from __future__ import annotations
import os
import requests
from pathlib import Path
from .errors import InvalidMedia, StorageError, UnsupportedMedia
from .http_client import HTTPClient
from .models import DownloadResult
from .validation import inspect_error_prefix
from .media.registry import validate_media

class Downloader:
    def __init__(self, client: HTTPClient):
        self.client = client

    def download(self, source_url: str, part_path: Path, referer: str) -> DownloadResult:
        def consume(response, attempt):
            if part_path.is_symlink() or (part_path.exists() and not part_path.is_file()):
                raise StorageError("临时路径不是普通文件；拒绝覆盖。")
            encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
            transfer = response.headers.get("Transfer-Encoding", "").lower().strip()
            expected = None
            if encoding in {"", "identity"} and not transfer:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        expected = int(content_length)
                        if expected < 0:
                            raise ValueError
                    except ValueError:
                        raise InvalidMedia("服务器 Content-Length 无效。") from None
            limit = int(self.client.config.max_file_size_mb * 1024 * 1024)
            if expected is not None and expected > limit:
                raise InvalidMedia("响应超过 max_file_size_mb 安全上限。")
            total, prefix = 0, bytearray()
            try:
                with part_path.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > limit:
                            raise InvalidMedia("音频超过 max_file_size_mb 安全上限。")
                        if len(prefix) < 4096:
                            prefix.extend(chunk[:4096 - len(prefix)])
                            if len(prefix) >= 4096:
                                inspect_error_prefix(bytes(prefix))
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                media = validate_media(part_path, response.headers.get("Content-Type", ""), expected)
                if self.client.policy and media.format not in self.client.policy.profile.data['media_formats']:
                    raise UnsupportedMedia('unsupported_media_format: inspected format is disabled by this site profile')
            except requests.exceptions.RequestException:
                # Requests transport exceptions also inherit OSError: preserve
                # them for the HTTP retry layer, not the local disk-error path.
                raise
            except OSError as exc:
                raise StorageError(f"写入/校验音频失败（{type(exc).__name__}）；请检查磁盘空间、权限或文件占用。") from None
            return DownloadResult(media.size_bytes, media.sha256, response.url,
                                  attempt, media.frame_count, media.format, media.codec,
                                  media.extension, media.validation_level, media.page_count)
        return self.client.perform(source_url, "audio", consume, referer=referer)
