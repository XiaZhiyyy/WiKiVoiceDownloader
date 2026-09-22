from collections import defaultdict
from pathlib import Path
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.service import DownloadService
from wiki_voice_downloader.selection import selected_candidates

BASE = "https://patchwiki.biligame.com/images/blhx/test/"
PAGE_URL = "https://wiki.biligame.com/blhx/OfflineCharacter"

def url(letter):
    return BASE + letter + ".mp3"

def mp3(frames=1, payload=0):
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz stereo; each complete frame is 417 bytes.
    # Synthetic structural fixture, not a recording from the Wiki.
    return (bytes.fromhex("fffb9000") + bytes([payload]) * 413) * frames

class Response:
    def __init__(self, data=None, *, status=200, headers=None, error=None, final_url=None):
        self.data = mp3() if data is None else data
        self.status_code = status
        self.headers = {"Content-Type": "audio/mpeg", "Content-Length": str(len(self.data))}
        if headers:
            self.headers.update(headers)
        self.error = error
        self.url = final_url or ""
        self.closed = False

    def iter_content(self, chunk_size):
        for pos in range(0, len(self.data), min(chunk_size, 97)):
            yield self.data[pos:pos + min(chunk_size, 97)]
            if self.error:
                raise self.error
        if self.error:
            raise self.error

    def close(self):
        self.closed = True

class Session:
    def __init__(self, routes=None):
        self.routes = {key: list(value) for key, value in (routes or {}).items()}
        self.headers, self.proxies = {}, {}
        self.calls = []
        self.responses = []
        self.closed = False

    def get(self, address, **kwargs):
        self.calls.append((address, kwargs))
        choices = self.routes.get(address)
        outcome = choices.pop(0) if choices else Response()
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            outcome = outcome()
        if not outcome.url:
            outcome.url = address
        self.responses.append(outcome)
        return outcome

    def close(self):
        self.closed = True

def service(routes=None, checkpoint=lambda point: None, **config_values):
    session = Session(routes)
    waits = []
    cfg = Config(retry_backoff_seconds=0, **config_values)
    client = HTTPClient(cfg, session=session, sleeper=waits.append)
    return DownloadService(Downloader(client), checkpoint=checkpoint), session, waits

def run_selection(directory, page, groups, routes=None, *, overwrite=False, checkpoint=lambda point: None):
    candidates, duplicates, _ = selected_candidates(page.groups, groups)
    runner, session, waits = service(routes, checkpoint)
    summary = runner.run(page, candidates, directory, page.info.character_name,
                         "biligame_blhx", overwrite=overwrite, duplicates=duplicates)
    return summary, session

def wrap(inner, name="原皮"):
    return f"""<html><head><meta charset="utf-8"></head><body>
    <h1 id="firstHeading">合成角色</h1><h2>舰船台词</h2>
    <section data-skin-name="{name}"><table class="table-ShipWordsTable">
    <tbody>{inner}</tbody></table></section></body></html>"""

def block(letter="A", text="测试", extra="", language="zh"):
    line = f'<p class="ship_word_line" data-lang="{language}">{text}</p>' if text is not None else ""
    return f'<div class="ship_word_block" {extra}>{line}<div class="sm-audio-src"><a href="{url(letter)}">音频</a></div></div>'
