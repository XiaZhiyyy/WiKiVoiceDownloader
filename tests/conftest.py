from pathlib import Path
from urllib.parse import urlsplit
import pytest
import requests
from wiki_voice_downloader.parsers.biligame_blhx import BiligameBlhxParser

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://wiki.biligame.com/blhx/OfflineCharacter"

@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    original = requests.sessions.Session.request
    def guarded(self, method, url, *args, **kwargs):
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("Default tests must not make external HTTP requests")
        return original(self, method, url, *args, **kwargs)
    monkeypatch.setattr(requests.sessions.Session, "request", guarded)

@pytest.fixture
def html():
    return (FIXTURES / "multiskin.html").read_text(encoding="utf-8")

@pytest.fixture
def page(html):
    return BiligameBlhxParser().parse(html, PAGE_URL)
