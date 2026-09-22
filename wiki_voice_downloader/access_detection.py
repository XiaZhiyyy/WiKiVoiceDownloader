"""Bounded, evidence-based access classification, never challenge solving.

Only an observed cf-mitigated response header identifies Cloudflare. HTML-only
rules are conservative heuristics with an unknown provider. Do not persist input
HTML, headers, challenge URLs, or form values in a decision.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from bs4 import BeautifulSoup

PROBE_LIMIT = 64 * 1024
TEMPORARY = {408, 425, 429, 500, 502, 503, 504}

@dataclass(frozen=True)
class AccessDecision:
    kind: str = 'normal'
    resource_kind: str = 'page'
    http_status: int | None = None
    provider: str = 'unknown'
    evidence_codes: tuple[str, ...] = ()
    retryable: bool = False
    stop_current_job: bool = False
    diagnostic_message: str = ''

    def record(self) -> dict:
        result = asdict(self)
        result['evidence_codes'] = list(self.evidence_codes)
        return result


def is_text_representation(data: bytes) -> bool:
    """Only text/error representations are examined, never keyword-scan audio."""
    stripped = data.lstrip(b'\xef\xbb\xbf \r\n\t')
    return stripped.startswith((b'<', b'{', b'['))


def classify_access(body: bytes | str = b'', *, headers=None, status: int | None = None,
                    resource_kind: str = 'page', has_content: bool = False) -> AccessDecision:
    """Classify at most 64 KiB. Layout validation remains the parser's job."""
    def decision(kind, evidence, message, *, provider='unknown'):
        return AccessDecision(kind, resource_kind, status, provider, tuple(evidence),
                              False, True, f'{kind}: {message}')
    header = {str(k).lower(): str(v).strip() for k, v in (headers or {}).items()}
    if header.get('cf-mitigated', '').lower() == 'challenge':
        return decision('needs_human_verification', ['header_cf_mitigated_challenge'],
                        'manual verification required; automatic requests stopped', provider='cloudflare')

    if isinstance(body, str):
        data = body[:PROBE_LIMIT].encode('utf-8')[:PROBE_LIMIT]
    else:
        data = body[:PROBE_LIMIT]
    if is_text_representation(data):
        # Replacement here is ONLY diagnostic probing, never dialogue decoding.
        soup = BeautifulSoup(data.decode('utf-8', errors='replace'), 'html.parser')
        titles = [n.get_text(' ', strip=True).casefold() for n in soup.select('title, h1')]
        headline = ' '.join(titles)
        challenge_widget = bool(soup.select(
            '[id^="cf-chl-"], [id*="challenge"], [class*="captcha"], '
            '[id*="captcha"], input[name*="captcha"], form[action*="challenge"], '
            'script[src*="challenge-platform"], script[src*="captcha"]'))
        password_form = any(form.select_one('input[type="password"]') for form in soup.select('form'))
        # Populated tables or article prose rule out a *complete* gate. This is
        # generic body evidence, not a site-specific parser or authorization claim.
        structured = has_content or len(soup.select('table tr td')) >= 3
        for n in soup(['script', 'style', 'noscript']):
            n.decompose()
        visible = soup.get_text(' ', strip=True).casefold()
        gate_words = ('verify you are human', 'verify that you are human', 'human verification',
                      'checking your browser', 'just a moment', 'security check',
                      '\u4eba\u673a\u9a8c\u8bc1', '\u9a8c\u8bc1\u60a8\u662f\u771f\u4eba',
                      '\u5b89\u5168\u9a8c\u8bc1', '\u8bbf\u95ee\u9a8c\u8bc1')
        gate_head = any(w in headline for w in gate_words)
        gate_prompt = any(w in visible for w in gate_words)
        if not structured:
            if challenge_widget and (gate_head or gate_prompt):
                return decision('needs_human_verification', ['gate_prompt', 'challenge_structure'],
                                'verification gate HTML; provider is unconfirmed')
            if gate_head and gate_prompt and len(visible) < 5000:
                return decision('suspected_verification', ['gate_heading', 'gate_only_body'],
                                'suspected verification gate; not retried automatically')
            denied = ('access denied', 'request blocked', 'access forbidden', 'permission denied',
                      '\u8bbf\u95ee\u88ab\u62d2\u7edd', '\u8bbf\u95ee\u53d7\u9650')
            if (any(w in headline for w in denied) or
                (len(visible) < 1200 and any(visible.startswith(w) for w in denied))):
                return decision('access_restricted', ['denial_gate'], 'access is explicitly denied')
            if password_form and (any(w in headline for w in ('log in', 'login', 'sign in', '\u767b\u5f55'))
                                  or len(visible) < 1200):
                return decision('authentication_required', ['password_login_gate'],
                                'authentication required; no browser credentials are imported')
    if status in {401, 407}:
        return decision('authentication_required', ['http_proxy_auth' if status == 407 else 'http_auth'],
                        f'HTTP {status}; authentication required, not proof of a CAPTCHA')
    if status in {403, 451}:
        return decision('access_restricted', ['http_denied'],
                        f'HTTP {status}; access denied, not proof of a CAPTCHA')
    if status is not None and status not in {200, 301, 302, 303, 307, 308}:
        return AccessDecision('unexpected_response', resource_kind, status,
                              retryable=status in TEMPORARY,
                              diagnostic_message=f'HTTP {status}: response is not a normal body')
    return AccessDecision(resource_kind=resource_kind, http_status=status)


def require_access(decision: AccessDecision) -> None:
    if decision.stop_current_job:
        from .errors import AccessError
        raise AccessError(decision)
