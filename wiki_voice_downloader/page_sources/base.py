from __future__ import annotations
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit
from bs4 import BeautifulSoup
from ..access_detection import classify_access, require_access
from ..errors import AppError, SnapshotError, ParseError
from ..models import PageRequestContext
from ..urls import request_url

@dataclass
class PageDocument:
    html: str
    acquisition_kind: str
    source_page_url: str
    effective_page_url: str
    input_fragment: str
    resolved_url: str | None
    resolved_url_observed: bool
    content_sha256: str
    loaded_at: str
    snapshot_basename: str | None = None
    coverage_status: str = 'unknown'
    diagnostics: list[str] = field(default_factory=list)
    identity_status: str = 'user_supplied_unverified'

    def provenance(self):
        # No HTML or absolute file path, and no inferred HTTP 200/authorization.
        return {key: getattr(self, key) for key in (
            'acquisition_kind', 'source_page_url', 'effective_page_url',
            'input_fragment', 'resolved_url', 'resolved_url_observed',
            'content_sha256', 'loaded_at', 'snapshot_basename',
            'coverage_status', 'diagnostics', 'identity_status')}


def _snapshot_identity(soup, document, parser):
    """Untrusted declarations are consistency checks, never authentication."""
    def identity(url):
        normalized = request_url(url, document.effective_page_url)
        parts = urlsplit(normalized)
        path = unquote(parts.path).rstrip('/').replace('_', ' ')
        return parts.hostname, path, parts.query
    expected = identity(document.effective_page_url)
    declarations = [str(n['href']) for n in soup.select('link[rel~="canonical"][href]')]
    if parser.profile.data['strategy'] == 'koumakan_quotes':
        # General character navigation is present in the supplied browser DOM.
        for link in soup.select('.shiptabber a[href]'):
            if link.get_text(' ', strip=True).casefold() == 'general':
                value = request_url(str(link['href']), document.effective_page_url)
                parts = urlsplit(value)
                value = parts._replace(path=parts.path.rstrip('/')+'/Quotes', fragment='').geturl()
                declarations.append(value)
        heading = soup.select_one('#firstHeading') or soup.find('h1')
        if heading:
            name = heading.get_text(' ', strip=True).strip()
            if name.endswith('/Quotes'):
                declarations.append('/wiki/' + quote(name, safe='/'))
    checked = False
    for value in declarations:
        try:
            actual = identity(value)
            if not parser.supports(request_url(value, document.effective_page_url)):
                raise ValueError('outside profile')
        except (AppError, ValueError):
            raise SnapshotError('declared canonical/navigation points outside the selected source',
                                'snapshot_identity_mismatch') from None
        if actual != expected:
            raise SnapshotError('canonical/General navigation belongs to another page; check URL and file',
                                'snapshot_identity_mismatch')
        checked = True
    document.identity_status = 'declaration_matches_untrusted' if checked else 'user_supplied_unverified'
    if not checked:
        document.diagnostics.append('snapshot_identity_unverified: using the supplied page URL; no independent identity marker')


def parse_document(document: PageDocument, parser, context: PageRequestContext):
    """Shared pure parsing path, before any directory/ID/view mutation."""
    soup = BeautifulSoup(document.html, 'html.parser')
    selector = parser.profile.data['selectors'].get('quote_table') or parser.profile.data['selectors'].get('voice_table')
    has_content = bool(soup.select(selector)) if selector else False
    require_access(classify_access(document.html, resource_kind='page', has_content=has_content))
    if document.acquisition_kind == 'local_html':
        _snapshot_identity(soup, document, parser)
    if soup.find('base'):
        document.diagnostics.append('ignored_base_href: relative media use the checked source URL, not HTML base')
    if soup.select('iframe, meta[http-equiv="refresh"]'):
        document.diagnostics.append('ignored_auxiliary_resources: scripts, frames and refresh are not executed or fetched')
    try:
        page = parser.parse(document.html, document.effective_page_url)
    except ParseError as exc:
        if exc.reason in {'incomplete', 'structure_changed', 'unsupported_layout'}:
            raise ParseError('unexpected_page: supported voice context is absent; input may be a shell or changed layout',
                             'unexpected_page') from None
        raise
    if page.sections:
        # Count observed roots, not a claim about all online groups. A full
        # document can still be missing lazy-loaded content.
        document.coverage_status = 'selected_panel' if len(page.sections) == 1 else 'multiple_panels'
    elif soup.html is not None:
        document.coverage_status = 'whole_document_unverified'
    else:
        document.coverage_status = 'content_fragment'
    document.diagnostics.append('input_coverage_only: only groups present in this input are available; online completeness unverified')
    page.info.source_page_url = document.source_page_url
    # Compatibility field is the effective base, NOT evidence of an HTTP hop.
    page.info.resolved_page_url = document.effective_page_url
    page.info.effective_page_url = document.effective_page_url
    page.info.resolved_url_observed = document.resolved_url_observed
    page.acquisition = document.provenance()
    page.warnings = list(dict.fromkeys(document.diagnostics + page.warnings))
    context.resolved_url = document.resolved_url
    page.request_context = context
    return page
