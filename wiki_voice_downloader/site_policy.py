"""Exact-host resource policy shared by every HTTP hop and adapter."""
from __future__ import annotations
from urllib.parse import urlsplit, unquote
import re
from .errors import AppError
from .profile_loader import SiteProfile, builtin_profile

class SitePolicy:
    def __init__(self, profile: SiteProfile):
        self.profile=profile

    def check(self, url: str, kind: str) -> str:
        from .urls import request_url
        normalized=request_url(url)
        parts=urlsplit(normalized)
        if parts.port not in {None,80,443}:
            raise AppError('unsupported_site: non-standard resource port')
        path=unquote(parts.path)
        # Inspect multiple decoding layers conservatively. Never normalize away traversal.
        inspected=path
        for _ in range(3):
            if (any(ord(c)<32 or ord(c)==127 for c in inspected) or '\\' in inspected
                or any(piece in {'.','..'} for piece in inspected.split('/'))):
                raise AppError('unsupported_site: unsafe resource path')
            inspected=unquote(inspected)
        data=self.profile.data
        if kind=='page':
            page=data['page']
            if parts.hostname not in page['hosts'] or not re.fullmatch(page['path_pattern'],path):
                raise AppError('unsupported_site: page is outside this site profile')
            if data['strategy']=='biligame_blhx':
                from .urls import is_blhx_page
                if not is_blhx_page(normalized): raise AppError('unsupported_site: not a BLHX character page')
            elif data['strategy']=='koumakan_quotes':
                title=path.removeprefix('/wiki/').removesuffix('/').removesuffix('/Quotes')
                if any(title.startswith(prefix) for prefix in ('Special:','File:','Category:','Template:')):
                    raise AppError('unsupported_site: not a character Quotes page')
                # No query-title form has been observed or implemented.
                if parts.query: raise AppError('unsupported_site: query forms are not supported for Quotes pages')
        elif kind=='audio':
            if not any(parts.hostname==s['host'] and path.startswith(s['path_prefix']) for s in data['audio_sources']):
                raise AppError('unsupported_site: audio source is outside the declared host/path scope')
        else:
            raise AppError('unsupported_site: undeclared request kind')
        return normalized

    def supports(self,url: str) -> bool:
        try: self.check(url,'page'); return True
        except (AppError,ValueError): return False


def default_policy() -> SitePolicy:
    return SitePolicy(builtin_profile('biligame_blhx'))
