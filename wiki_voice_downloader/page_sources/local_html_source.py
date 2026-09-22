"""Explicit bounded regular-file input. No URL/DNS fetch or auxiliary loading."""
from __future__ import annotations
import codecs
import hashlib
import os
from pathlib import Path
import re
import stat
from .base import PageDocument
from ..errors import SnapshotError
from ..models import utc_now


def unquote_path(value: str) -> str:
    value = str(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def validate_encoding(name: str | None) -> str:
    try:
        codec = codecs.lookup('utf-8-sig' if name is None else name)
        if not getattr(codec, '_is_text_encoding', False):
            raise LookupError
        b''.decode(codec.name)
    except (LookupError, TypeError):
        raise SnapshotError('unknown/non-text --html-encoding; save as UTF-8 or select a valid text codec') from None
    return codec.name


class LocalHtmlPageSource:
    def __init__(self, path, config, policy, encoding=None):
        value = unquote_path(path)
        if value.startswith(('\\\\', '//')):
            raise SnapshotError('network/device namespace paths are not local regular-file inputs')
        self.path, self.config, self.policy = Path(value), config, policy
        self.encoding = validate_encoding(encoding)

    def load(self, context):
        # Pure syntax/allowlist validation; no socket/DNS operation.
        effective = self.policy.check(context.request_url, 'page')
        path = self.path
        if path.suffix.lower() not in {'.html', '.htm', '.txt'}:
            raise SnapshotError('only .html/.htm/.txt containing original HTML are supported (not MHTML/PDF/images/ZIP)')
        limit = int(self.config.max_page_size_mb * 1024 * 1024)
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise SnapshotError('input must be an explicitly selected regular file, not a directory/device/pipe')
            # O_NONBLOCK prevents a POSIX regular-file-to-FIFO race from hanging.
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise SnapshotError('input is not a regular file')
                chunks, size = [], 0
                while True:
                    data = stream.read(min(65536, max(1, limit + 1 - size)))
                    if not data:
                        break
                    size += len(data)
                    if size > limit:
                        raise SnapshotError('file exceeds max_page_size_mb; reading stopped at the bound')
                    chunks.append(data)
            raw = b''.join(chunks)
        except OSError:
            raise SnapshotError('cannot read the specified regular file; check path and permissions') from None
        if raw.startswith((b'%PDF-', b'PK\x03\x04', b'\x89PNG', b'\xff\xd8\xff', b'GIF8', b'RIFF')):
            raise SnapshotError('unsupported binary representation; renaming it to .html does not convert it')
        try:
            html = raw.decode(self.encoding).removeprefix('\ufeff')
        except (UnicodeError, LookupError):
            raise SnapshotError('strict decoding failed; re-save as UTF-8 or supply the correct --html-encoding') from None
        beginning = html[:8192].lower()
        if ('mime-version:' in beginning or 'content-type: multipart/' in beginning or
            beginning.lstrip().startswith('from: <saved by')):
            raise SnapshotError('MHTML is unsupported; save/copy original body HTML instead')
        if '\x00' in html or not re.search(r'<(?:html|div|article|section|table|body|head|h[1-6]|!doctype)\b', html, re.I):
            raise SnapshotError('no supported HTML context; plain dialogue, screenshots and lone Play links are not valid inputs')
        return PageDocument(html, 'local_html', context.input_url, effective,
                            context.fragment_hint, None, False, hashlib.sha256(raw).hexdigest(),
                            utc_now(), path.name)
