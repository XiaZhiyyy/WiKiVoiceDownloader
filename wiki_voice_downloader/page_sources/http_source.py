from __future__ import annotations
import hashlib
from .base import PageDocument
from ..models import utc_now

class HttpPageSource:
    def __init__(self, client, policy):
        self.client, self.policy = client, policy

    def load(self, context):
        url = self.policy.check(context.request_url, 'page')
        html, resolved = self.client.fetch_page(url)
        effective = self.policy.check(resolved, 'page')
        return PageDocument(
            html, 'http', context.input_url, effective, context.fragment_hint,
            effective, True,
            getattr(self.client, 'last_page_sha256', None) or hashlib.sha256(html.encode('utf-8')).hexdigest(),
            utc_now(), identity_status='http_response_observed')
