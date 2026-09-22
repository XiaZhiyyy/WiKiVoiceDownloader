from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import logging
from pathlib import Path
import pytest
import requests
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.errors import ConfigError, InvalidMedia, NetworkError
from wiki_voice_downloader.http_client import HTTPClient, retry_after_seconds
from wiki_voice_downloader.validation import validate_mp3
from wiki_voice_downloader.urls import redact_url
from .helpers import Response, Session, PAGE_URL, url, mp3

def download(tmp_path, routes=None, **config):
    session = Session(routes)
    waits = []
    client = HTTPClient(Config(**config), session=session, sleeper=waits.append)
    result = Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    return result, session, waits

def test_D01_D02_D04_short_valid_mp3_and_octet_stream(tmp_path):
    result, session, waits = download(tmp_path, {url("A"): [Response(headers={"Content-Type": "application/octet-stream"})]})
    assert result.size_bytes == 417 and result.frame_count == 1
    assert (tmp_path / "001.mp3.part").read_bytes() == mp3()
    assert session.responses[0].closed and waits == []

@pytest.mark.parametrize("data", [b"", b"ID3", b"ID3\x04\0\0\0\0\0\0", b"<html>error</html>", b'{"error": "oops"}'])
def test_D03_empty_html_json_and_tag_only_rejected(tmp_path, data):
    with pytest.raises(InvalidMedia):
        download(tmp_path, {url("A"): [Response(data)]})

@pytest.mark.parametrize("tag", [
    b"",
    b"ID3\x03\x00\x00\x00\x00\x00\x04TEST",
    b"ID3\x04\x00\x00\x00\x00\x00\x04TEST",
    b"ID3\x02\x00\x00\x00\x00\x00\x04TEST",
])
def test_mp3_with_and_without_id3(tmp_path, tag):
    file = tmp_path / "audio.mp3"
    file.write_bytes(tag + mp3(2) + b"TAG" + b"\0" * 125)
    assert validate_mp3(file).frame_count == 2

def test_id3v24_footer(tmp_path):
    header = b"ID3\x04\x00\x10\x00\x00\x00\x04"
    footer = b"3DI\x04\x00\x10\x00\x00\x00\x04"
    file = tmp_path / "audio.mp3"
    file.write_bytes(header + b"TEST" + footer + mp3())
    assert validate_mp3(file).frame_count == 1

@pytest.mark.parametrize("data", [
    mp3()[:-1],
    mp3() + b"bad",
    b"ID3\x04\x00\x00\x80\x00\x00\x00" + mp3(),
    b"ID3\x04\x00\x00\x00\x00\x7f\x7f" + mp3(),
    b"OggS" + b"\0" * 500,
    b"RIFF" + b"\0" * 500,
    b"fLaC" + b"\0" * 500,
    bytes.fromhex("fffb0000") + b"\0" * 413,
])
def test_structural_corruption_and_unsupported_formats(tmp_path, data):
    file = tmp_path / "audio.mp3"
    file.write_bytes(data)
    with pytest.raises(InvalidMedia):
        validate_mp3(file)

def test_D05_timeout_then_success_exact_attempts(tmp_path):
    result, session, waits = download(tmp_path, {url("A"): [
        requests.Timeout("sensitive raw error"), requests.Timeout(), Response()
    ]})
    assert result.attempts == 3 and len(session.calls) == 3 and waits == [1, 2]

def test_D06_three_total_attempts_not_three_retries(tmp_path):
    session = Session({url("A"): [requests.Timeout()] * 5})
    waits = []
    client = HTTPClient(Config(), session=session, sleeper=waits.append)
    with pytest.raises(NetworkError) as caught:
        Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    assert caught.value.attempts == 3 and len(session.calls) == 3
    assert waits == [1, 2]

@pytest.mark.parametrize("status,stop", [(404, False), (410, False), (401, True), (403, True), (451, True)])
def test_D07_permanent_vs_site_restricted(tmp_path, status, stop):
    session = Session({url("A"): [Response(status=status)]})
    client = HTTPClient(Config(), session=session, sleeper=lambda _: pytest.fail("Unexpected retry"))
    with pytest.raises(NetworkError) as caught:
        Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    assert len(session.calls) == 1
    assert caught.value.stop_site is stop

def test_D08_retry_after_seconds_honored(tmp_path):
    result, session, waits = download(tmp_path, {url("A"): [
        Response(status=429, headers={"Retry-After": "7"}), Response()
    ]})
    assert result.attempts == 2 and waits == [7]

def test_D08_retry_after_http_date():
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    value = format_datetime(now + timedelta(seconds=19), usegmt=True)
    assert retry_after_seconds(value, now) == 19
    assert retry_after_seconds(format_datetime(now - timedelta(seconds=2)), now) == 0
    assert retry_after_seconds("invalid") is None
    assert retry_after_seconds("-1") is None

def test_D08_retry_after_date_used_by_http_loop(tmp_path):
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    result, _, waits = download(tmp_path, {url("A"): [
        Response(status=503, headers={"Retry-After": value}), Response()
    ]})
    assert result.attempts == 2 and len(waits) == 1 and 18 <= waits[0] <= 20

def test_D08_long_retry_after_stops_without_truncating(tmp_path):
    session = Session({url("A"): [Response(status=429, headers={"Retry-After": "301"})]})
    waits = []
    client = HTTPClient(Config(), session=session, sleeper=waits.append)
    with pytest.raises(NetworkError) as caught:
        Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    assert caught.value.stop_site and waits == [] and len(session.calls) == 1

def test_D08_persistent_429_stops_site(tmp_path):
    session = Session({url("A"): [Response(status=429, headers={"Retry-After": "0"}) for _ in range(3)]})
    client = HTTPClient(Config(), session=session, sleeper=lambda _: pytest.fail("Zero retry wait"))
    with pytest.raises(NetworkError) as caught:
        Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    assert len(session.calls) == 3 and caught.value.stop_site

def test_D09_normal_success_never_sleeps_and_no_range(tmp_path):
    session = Session()
    client = HTTPClient(Config(), session=session, sleeper=lambda _: pytest.fail("Success must not sleep"))
    downloader = Downloader(client)
    for index in range(3):
        downloader.download(url("A"), tmp_path / f"{index}.part", PAGE_URL)
    assert len(session.calls) == 3
    assert all("Range" not in call[1]["headers"] for call in session.calls)
    assert all(call[1]["timeout"] == (10, 30) for call in session.calls)
    assert all(call[1]["allow_redirects"] is False for call in session.calls)

def test_D10_stream_interrupted_then_clean_restart(tmp_path):
    broken = Response(mp3(3), error=requests.exceptions.ChunkedEncodingError("interrupted"))
    result, session, waits = download(tmp_path, {url("A"): [broken, Response()]})
    assert result.attempts == 2 and broken.closed
    assert (tmp_path / "001.mp3.part").read_bytes() == mp3()

def test_D10_identity_content_length_mismatch(tmp_path):
    session = Session({url("A"): [Response(headers={"Content-Length": "1000"})] * 3})
    client = HTTPClient(Config(retry_backoff_seconds=0), session=session)
    with pytest.raises(InvalidMedia):
        Downloader(client).download(url("A"), tmp_path / "001.mp3.part", PAGE_URL)
    assert not (tmp_path / "001.mp3").exists()

def test_decoded_gzip_length_not_compared_to_wire_length(tmp_path):
    result, _, _ = download(tmp_path, {url("A"): [Response(headers={
        "Content-Length": "32", "Content-Encoding": "gzip"
    })]})
    assert result.size_bytes == 417

def test_chunked_ignores_conflicting_content_length(tmp_path):
    result, _, _ = download(tmp_path, {url("A"): [Response(headers={
        "Content-Length": "1", "Transfer-Encoding": "chunked"
    })]})
    assert result.size_bytes == 417

def test_D11_proxy_default_disabled_and_explicit_applies(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://should.not.be.used:8000")
    client = HTTPClient(Config())
    assert client.session.trust_env is False and client.session.proxies == {}
    assert client.session.verify is True
    assert client.session.get_adapter("https://").max_retries.total == 0
    client.close()
    client = HTTPClient(Config(proxy="http://alice:secret@proxy.example:8000"))
    assert client.session.proxies["http"] == client.session.proxies["https"]
    assert "secret" in client.session.proxies["https"]
    client.close()

@pytest.mark.parametrize("proxy", ["socks5://localhost:1234", "http://host:bad", "not-a-url", 1, "http://a/path"])
def test_D11_invalid_proxy_rejected_before_requests(proxy):
    with pytest.raises(ConfigError):
        Config(proxy=proxy).validate()

def test_D11_credentials_and_query_not_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="wiki_voice_downloader")
    session = Session({url("A"): [requests.exceptions.ProxyError("http://alice:secret@proxy.example:8000")] * 3})
    client = HTTPClient(Config(proxy="http://alice:secret@proxy.example:8000", retry_backoff_seconds=0),
                        session=session)
    with pytest.raises(NetworkError):
        Downloader(client).download(url("A"), tmp_path / "a.part", PAGE_URL)
    assert "secret" not in caplog.text and "alice" not in caplog.text
    assert redact_url("https://u:p@host/path?token=SECRET&x=Y#f") == "https://host/path?token=<redacted>&x=<redacted>"

def test_bad_redirect_not_requested(tmp_path):
    response = Response(status=302, headers={"Location": "https://patchwiki.biligame.com.evil.test/bad.mp3"})
    session = Session({url("A"): [response]})
    client = HTTPClient(Config(), session=session)
    with pytest.raises(NetworkError) as caught:
        Downloader(client).download(url("A"), tmp_path / "001.part", PAGE_URL)
    assert caught.value.stop_site and len(session.calls) == 1 and response.closed

def test_good_redirect_keeps_requested_identity(tmp_path):
    result, session, _ = download(tmp_path, {url("A"): [
        Response(status=302, headers={"Location": url("B")})
    ]})
    assert result.resolved_audio_url == url("B")
    assert [call[0] for call in session.calls] == [url("A"), url("B")]

def test_200_challenge_stops(tmp_path):
    with pytest.raises(NetworkError) as caught:
        download(tmp_path, {url("A"): [Response(b'<html><title>Verify you are human</title><form id="challenge">captcha</form></html>' )]})
    assert caught.value.stop_site

def test_resource_size_cap_is_only_an_upper_limit(tmp_path):
    with pytest.raises(InvalidMedia):
        download(tmp_path, max_file_size_mb=0.0001)

def test_response_closed_and_partial_retained_on_ctrl_c(tmp_path):
    session = Session({url("A"): [Response(mp3(), error=KeyboardInterrupt())]})
    client = HTTPClient(Config(), session=session)
    with pytest.raises(KeyboardInterrupt):
        Downloader(client).download(url("A"), tmp_path / "001.part", PAGE_URL)
    assert session.responses[0].closed
    assert (tmp_path / "001.part").exists()

def test_challenge_marker_after_initial_chunk_still_stops_site(tmp_path):
    data = b"<html><head>" + b" " * 300 + b'<title>Verify you are human</title></head><body><form id="challenge">captcha</form></body></html>' 
    with pytest.raises(NetworkError) as caught:
        download(tmp_path, {url("A"): [Response(data)]})
    assert caught.value.stop_site

def test_transport_read_timeout_not_misreported_as_disk_failure(tmp_path):
    result, _, waits = download(tmp_path, {url("A"): [
        Response(mp3(2), error=requests.ReadTimeout()), Response()
    ]})
    assert result.attempts == 2 and waits == [1]
