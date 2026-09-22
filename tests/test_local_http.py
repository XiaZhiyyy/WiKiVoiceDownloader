"""Real Requests streaming against a loopback-only server; no external network."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import time
import pytest
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.errors import NetworkError
from wiki_voice_downloader.http_client import HTTPClient
from .helpers import mp3, PAGE_URL

SERVER = r"""
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
payload = bytes.fromhex("fffb9000") + b"\0" * 413
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect.mp3":
            self.send_response(302)
            self.send_header("Location", "/audio.mp3")
            self.end_headers()
            return
        data = payload
        encoding = None
        kind = "audio/mpeg"
        if self.path == "/gzip.mp3":
            data = gzip.compress(payload)
            encoding = "gzip"
        if self.path == "/page":
            data = "<html><title>\u79bb\u7ebf\u9875\u9762</title></html>".encode("utf-8")
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(data[:97] if self.path == "/cut.mp3" else data)
        self.wfile.flush()
        self.close_connection = True
    def log_message(self, *args):
        pass
server = HTTPServer(("127.0.0.1", 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_address[1]), encoding="ascii")
server.serve_forever()
"""

@pytest.fixture
def loopback(tmp_path, monkeypatch):
    port_path = tmp_path / "port.txt"
    proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not port_path.exists():
            if proc.poll() is not None:
                pytest.fail("Loopback server failed: " + proc.stderr.read().decode(errors="replace"))
            if time.monotonic() > deadline:
                pytest.fail("Loopback server startup timed out")
            time.sleep(0.01)  # Test server startup only, never production download pacing.
        port = int(port_path.read_text(encoding="ascii"))
        # Deliberate test-only injection. Production allowlist remains strict.
        import wiki_voice_downloader.http_client as module
        monkeypatch.setattr(module, "check_resource_url", lambda url, kind: url)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stderr.close()

@pytest.mark.integration
def test_real_requests_preserves_decoded_mp3_bytes(loopback, tmp_path):
    with HTTPClient(Config(retry_backoff_seconds=0)) as client:
        target = tmp_path / "001.mp3.part"
        result = Downloader(client).download(loopback + "/gzip.mp3", target, PAGE_URL)
    assert target.read_bytes() == mp3() and result.size_bytes == 417

@pytest.mark.integration
def test_real_truncated_connection_retries_not_disk_error(loopback, tmp_path):
    with HTTPClient(Config(retry_backoff_seconds=0)) as client:
        with pytest.raises(NetworkError) as caught:
            Downloader(client).download(loopback + "/cut.mp3", tmp_path / "001.part", PAGE_URL)
    assert caught.value.attempts == 3 and caught.value.retryable

@pytest.mark.integration
def test_real_relative_redirect_returns_final_url(loopback, tmp_path):
    with HTTPClient(Config()) as client:
        result = Downloader(client).download(loopback + "/redirect.mp3", tmp_path / "001.part", PAGE_URL)
    assert result.resolved_audio_url == loopback + "/audio.mp3"

@pytest.mark.integration
def test_real_utf8_page_fetch(loopback):
    with HTTPClient(Config()) as client:
        html, resolved = client.fetch_page(loopback + "/page")
    assert "离线页面" in html and resolved.endswith("/page")
