from typing import Protocol
from ..models import ParsedPage

class WikiParser(Protocol):
    parser_id: str
    display_name: str
    def supports(self, url: str) -> bool: ...
    def parse(self, html: str, page_url: str) -> ParsedPage: ...
