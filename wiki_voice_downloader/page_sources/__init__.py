"""Page acquisition only. No browser session, parser copy, or persistence."""
from .base import PageDocument, parse_document
from .http_source import HttpPageSource
from .local_html_source import LocalHtmlPageSource

__all__ = ['PageDocument', 'parse_document', 'HttpPageSource', 'LocalHtmlPageSource']
