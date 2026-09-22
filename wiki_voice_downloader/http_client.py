"""Single-threaded HTTP with explicit attempts and checked redirect targets."""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import hashlib
import time
from typing import Callable, TypeVar
import requests
from .config import Config
from .errors import AppError, NetworkError
from .urls import check_resource_url, request_url, redact_url
from .access_detection import PROBE_LIMIT, classify_access, require_access, is_text_representation

T = TypeVar("T")
log = logging.getLogger(__name__)
TEMPORARY = {408, 425, 429, 500, 502, 503, 504}
RESTRICTED = {401, 403, 407, 451}

def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        try:
            return float(int(value))
        except (ValueError, OverflowError):
            return float("inf")
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class ProbedResponse:
    """One iterator, prefix replayed exactly once. Never a second GET/HEAD."""
    def __init__(self, response, iterator, chunks):
        self.response, self.iterator, self.chunks = response, iterator, chunks
        self.status_code, self.headers, self.url = response.status_code, response.headers, response.url
        self._used = False

    def iter_content(self, chunk_size=65536):
        if self._used:
            raise RuntimeError('Response body has already been consumed')
        self._used = True
        for chunk in self.chunks:
            yield chunk
        yield from self.iterator

    def close(self):
        self.response.close()


def probe_response(response, kind, config):
    # A header decision must work even on redirect and streaming-error responses.
    initial = classify_access(headers=response.headers, resource_kind=kind)
    if initial.stop_current_job:
        from dataclasses import replace
        initial = replace(initial, http_status=response.status_code)
    require_access(initial)
    limit = min(PROBE_LIMIT, int((config.max_page_size_mb if kind == 'page'
                                 else config.max_file_size_mb) * 1024 * 1024))
    iterator = iter(response.iter_content(chunk_size=max(1, min(8192, limit))))
    chunks, prefix, started = [], bytearray(), time.monotonic()
    # Each socket read still has the configured read timeout. The additional
    # elapsed bound is checked between chunks; it cannot preempt a socket read.
    for chunk in iterator:
        if time.monotonic() - started > config.read_timeout_seconds:
            raise NetworkError('access_probe_timeout: bounded response probe expired', retryable=True)
        if not chunk:
            continue
        chunks.append(chunk)
        prefix.extend(chunk[:max(0, limit-len(prefix))])
        stripped = bytes(prefix).lstrip(b'\xef\xbb\xbf \r\n\t')
        if stripped and not is_text_representation(bytes(prefix)):
            # Normal binary: replay immediately, preserving file streaming and
            # interruption behavior. Never scan inside binary for gate keywords.
            break
        if len(prefix) >= limit:
            break
    require_access(classify_access(bytes(prefix), headers=response.headers,
                                  status=response.status_code, resource_kind=kind))
    return ProbedResponse(response, iterator, chunks)

class HTTPClient:
    def __init__(self, config: Config, *, session=None, sleeper: Callable[[float], None] = time.sleep, policy=None):
        config.validate()
        self.config = config
        self.policy = policy
        self.audio_request_count = 0
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({
            "User-Agent": "WikiVoiceDownloader/1.0.0 (sequential public Wiki audio indexer)",
            "Accept-Encoding": "identity",
        })
        self.session.max_redirects = 5
        self.session.proxies = ({"http": config.proxy, "https": config.proxy}
                                if config.proxy else {})
        # Requests' standard adapter has max_retries=0. Do not add another retry layer.
        self.sleeper = sleeper
        self.last_attempts = 0

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def check_url(self, url: str, kind: str):
        return self.policy.check(url, kind) if self.policy else check_resource_url(url, kind)

    def _get(self, url: str, kind: str, referer: str | None):
        current = self.check_url(url, kind)
        headers = {"Accept-Encoding": "identity"}
        if referer:
            headers["Referer"] = self.check_url(referer, "page")
        for hop in range(6):
            if kind == "audio":
                self.audio_request_count += 1
            response = self.session.get(
                current, stream=True, allow_redirects=False,
                timeout=(self.config.connect_timeout_seconds,
                         self.config.read_timeout_seconds),
                headers=headers,
            )
            try:
                response = probe_response(response, kind, self.config)
            except BaseException:
                response.close()
                raise
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location")
            response.close()
            if not location or hop == 5:
                raise NetworkError("重定向缺少地址或超过 5 次。")
            try:
                target = self.check_url(request_url(location, current), kind)
            except AppError:
                raise NetworkError("重定向目标不在允许的来源范围；已停止。",
                                   stop_site=True) from None
            if current.startswith("https:") and target.startswith("http:"):
                raise NetworkError("拒绝从 HTTPS 重定向到不安全 HTTP。",
                                   stop_site=True)
            current = target
        raise AssertionError("Unreachable redirect state")

    def perform(self, url: str, kind: str, consumer: Callable[[object, int], T],
                *, referer: str | None = None) -> T:
        last_error = None
        for attempt in range(1, self.config.max_attempts + 1):
            self.last_attempts = attempt
            wait = None
            status = None
            log.info("请求 attempt=%s/%s kind=%s url=%s", attempt,
                     self.config.max_attempts, kind, redact_url(url))
            try:
                response = self._get(url, kind, referer)
                try:
                    status = response.status_code
                    log.info("响应 status=%s url=%s", status, redact_url(response.url))
                    if status in RESTRICTED:
                        raise NetworkError(f"HTTP {status}：站点拒绝访问；不尝试绕过。",
                                           stop_site=True, status_code=status,
                                           reason="authentication_required" if status in {401, 407} else "access_restricted")
                    if status != 200:
                        if status in TEMPORARY:
                            wait = retry_after_seconds(response.headers.get("Retry-After"))
                            if wait is not None and wait > self.config.max_retry_after_seconds:
                                raise NetworkError(
                                    "服务器 Retry-After 超过配置允许的等待上限；已停止本轮任务，未提前重试。",
                                    stop_site=True, status_code=status, reason="retry_after_exceeded")
                            raise NetworkError(f"HTTP {status}：临时服务器错误。",
                                               retryable=True, status_code=status)
                        raise NetworkError(f"HTTP {status}：该请求未成功。",
                                           status_code=status)
                    return consumer(response, attempt)
                finally:
                    response.close()
            except requests.exceptions.SSLError:
                last_error = NetworkError("TLS 证书校验失败；请检查系统时间与网络证书，未关闭 TLS 校验。")
            except requests.exceptions.ProxyError:
                last_error = NetworkError("代理连接失败，请检查配置（代理凭据不写入日志）。",
                                          retryable=True)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ContentDecodingError) as exc:
                # Never include raw requests exception strings; they can expose
                # proxy passwords or query tokens, including through traceback.
                last_error = NetworkError(f"网络传输中断（{type(exc).__name__}）。",
                                          retryable=True)
            except requests.exceptions.RequestException as exc:
                last_error = NetworkError(f"HTTP 请求失败（{type(exc).__name__}）。")
            except NetworkError as exc:
                last_error = exc
            last_error.attempts = attempt
            if hasattr(last_error, 'decision'):
                decision = last_error.decision
                log.warning('access_kind=%s provider=%s evidence=%s actual_attempts=%s stop_current_job=%s',
                            decision.kind, decision.provider, decision.evidence_codes, attempt,
                            decision.stop_current_job)
            if last_error.status_code == 429 and attempt == self.config.max_attempts:
                last_error.stop_site = True
                last_error.reason = "rate_limited"
            log.warning("尝试失败 attempt=%s status=%s reason=%s",
                        attempt, last_error.status_code, last_error)
            if last_error.stop_site or not last_error.retryable or attempt == self.config.max_attempts:
                raise last_error from None
            if wait is None:
                wait = min(self.config.retry_backoff_max_seconds,
                           self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
            if wait > 0:
                log.info("仅错误重试等待 seconds=%s", wait)
                self.sleeper(wait)
        raise last_error or NetworkError("请求失败。")

    def fetch_page(self, url: str) -> tuple[str, str]:
        def consume(response, attempt):
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type and content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise NetworkError("响应不是 HTML 页面；请输入角色详情页而非媒体地址。")
            chunks, size = [], 0
            limit = int(self.config.max_page_size_mb * 1024 * 1024)
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise NetworkError("角色页超过 max_page_size_mb 安全上限。")
                chunks.append(chunk)
            data = b"".join(chunks)
            if not data:
                raise NetworkError("角色页响应为空。", retryable=True)
            # BWIKI is UTF-8; a decoding failure must not silently corrupt names.
            try:
                html = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise NetworkError("角色页不是有效 UTF-8，拒绝以乱码解析。") from None
            require_access(classify_access(html, headers=response.headers,
                                          status=response.status_code, resource_kind='page'))
            self.last_page_sha256 = hashlib.sha256(data).hexdigest()
            return html, response.url
        return self.perform(url, "page", consume)
