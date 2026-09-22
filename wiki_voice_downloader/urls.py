from __future__ import annotations
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, urldefrag, unquote
from .errors import AppError

PAGE_HOSTS = frozenset({"wiki.biligame.com"})
AUDIO_HOSTS = frozenset({"patchwiki.biligame.com", "wiki.biligame.com"})

def request_url(value: str, base: str = "") -> str:
    if not isinstance(value, str) or not value.strip():
        raise AppError("网址为空。")
    value = value.strip()
    if any(ord(c) < 32 for c in value) or "\\" in value:
        raise AppError("网址含非法控制字符或反斜杠。")
    url = urldefrag(urljoin(base, value))[0]
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise AppError("网址的主机或端口无效。") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise AppError("只支持 HTTP/HTTPS 网址。")
    if parts.username is not None or parts.password is not None:
        raise AppError("来源网址不允许嵌入账号密码。")
    # Lowercase only scheme and host, never path or query.
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{port}" if port is not None else "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))

def is_blhx_page(url: str) -> bool:
    try:
        p = urlsplit(request_url(url))
        path = unquote(p.path)
        title = path.removeprefix("/blhx/")
        return (p.hostname in PAGE_HOSTS and p.port in {None, 80, 443}
                and path.startswith("/blhx/") and bool(title)
                and not path.lower().endswith((".mp3", ".wav", ".ogg", ".flac"))
                and not title.startswith(("Special:", "特殊:", "文件:", "File:")))
    except (AppError, ValueError):
        return False

def check_resource_url(url: str, kind: str, policy=None) -> str:
    from .site_policy import default_policy
    return (policy or default_policy()).check(url, kind)

def page_url_identity(url: str) -> str:
    p = urlsplit(request_url(url))
    # Unicode and percent-encoded page aliases are equivalent identities.
    # This is ONLY for page identity; audio URLs keep the query unchanged.
    return f"{p.hostname}{unquote(p.path)}"

def redact_url(url: str) -> str:
    try:
        p = urlsplit(url)
        host = p.hostname or "<invalid>"
        if p.port:
            host += f":{p.port}"
        query = "&".join(f"{part.split('=', 1)[0]}=<redacted>"
                         for part in p.query.split("&")) if p.query else ""
        return urlunsplit((p.scheme, host, p.path, query, ""))
    except (ValueError, TypeError):
        return "<invalid-url>"

def redact_message(message: str) -> str:
    return re.sub(r"https?://[^\s<>\"']+", lambda m: redact_url(m[0]), message)
