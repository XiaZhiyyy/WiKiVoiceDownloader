"""Synthetic access responses, not samples from the real target provider."""
from hashlib import sha256
import pytest
import requests
from wiki_voice_downloader.access_detection import classify_access, PROBE_LIMIT
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.errors import AccessError, InvalidMedia, NetworkError
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser
from .helpers import Response, Session, mp3, url, PAGE_URL
from .koumakan_helpers import make_html, PAGE as SYNTHETIC_PAGE
from .r2_helpers import GATE, PAYLOAD, StrictSession

@pytest.mark.parametrize('status', [200, 302, 429, 503])
@pytest.mark.parametrize('kind', ['page', 'audio'])
def test_strong_header_precedes_status_and_redirect(status, kind):
    address=PAGE_URL if kind=='page' else url('A')
    response=Response(b'ignored',status=status,headers={'CF-Mitigated':'challenge', 'Location':url('B'),'Retry-After':'300'})
    session=StrictSession({address:[response]});waits=[]
    with HTTPClient(Config(),session=session,sleeper=waits.append) as client:
        with pytest.raises(AccessError) as error:
            client.perform(address,kind,lambda *_:pytest.fail('consumer must not run'))
    e=error.value
    assert e.reason=='needs_human_verification' and not e.retryable and e.stop_site
    assert e.decision.provider=='cloudflare' and e.decision.http_status==status
    assert e.attempts==1 and len(session.calls)==1 and not waits and response.closed

@pytest.mark.parametrize('status',[200,403,429,503])
def test_unknown_gate_detected_before_retries(status):
    response=Response(GATE,status=status);session=StrictSession({url('A'):[response]});waits=[]
    client=HTTPClient(Config(),session=session,sleeper=waits.append)
    with pytest.raises(AccessError) as error:
        client.perform(url('A'),'audio',lambda *_:pytest.fail('consumer called'))
    assert error.value.decision.provider=='unknown'
    assert len(session.calls)==1 and waits==[] and response.closed

@pytest.mark.parametrize('status,kind',[(401,'authentication_required'),(407,'authentication_required'),
                                       (403,'access_restricted'),(451,'access_restricted')])
def test_plain_restriction_is_not_a_captcha(status,kind):
    d=classify_access(b'<html>Denied</html>',status=status,resource_kind='audio')
    assert d.kind==kind and d.provider=='unknown' and d.stop_current_job

@pytest.mark.parametrize('headers',[{'Server':'cloudflare'},{'cf-ray':'example'},{'Content-Type':'audio/ogg'}])
def test_ordinary_headers_never_prove_a_challenge(headers):
    assert classify_access(headers=headers,status=200).kind=='normal'

@pytest.mark.parametrize('addition',[
    '<p>A note about CAPTCHA, Cloudflare, access denied and human verification.</p>',
    '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>',
    '<div id="comments"><form><div class="g-recaptcha"></div></form></div>',
])
def test_normal_voice_body_mentions_and_widgets_do_not_block(addition):
    html=make_html().replace('</html>',addition+'</html>')
    assert classify_access(html).kind=='normal'
    assert len(KoumakanAzurLaneParser().parse(html,SYNTHETIC_PAGE).sections)==3

@pytest.mark.parametrize('body',[b'<html>captcha</html>',b'<html>Cloudflare</html>',b'<html>captcha access denied</html>'])
def test_keyword_only_media_is_invalid_but_not_site_stopping(tmp_path,body):
    client=HTTPClient(Config(),session=StrictSession({url('A'):[Response(body)]}))
    with pytest.raises(InvalidMedia) as e:
        Downloader(client).download(url('A'),tmp_path/'a.part',PAGE_URL)
    assert not e.value.stop_site

@pytest.mark.parametrize('data',[mp3(400),PAYLOAD, (b'ID3\x04\0\0\0\0\0\x04TEST'+mp3(400))])
def test_prefix_replay_preserves_all_bytes_once(tmp_path,data):
    response=Response(data,headers={'Content-Type':'application/octet-stream'})
    session=StrictSession({url('A'):[response]});waits=[]
    client=HTTPClient(Config(),session=session,sleeper=waits.append)
    result=Downloader(client).download(url('A'),tmp_path/'a.part',PAGE_URL)
    assert (tmp_path/'a.part').read_bytes()==data
    assert result.sha256==sha256(data).hexdigest() and result.size_bytes==len(data)
    assert len(session.calls)==1 and waits==[] and response.closed

@pytest.mark.parametrize('status,expected_waits',[(503,[1]),(429,[7])])
def test_ordinary_retry_still_works(tmp_path,status,expected_waits):
    bad=Response(b'<html>Temporary maintenance</html>',status=status,
                 headers={'Retry-After':'7'} if status==429 else {})
    session=StrictSession({url('A'):[bad,Response()]});waits=[]
    client=HTTPClient(Config(),session=session,sleeper=waits.append)
    result=Downloader(client).download(url('A'),tmp_path/'a.part',PAGE_URL)
    assert result.attempts==2 and waits==expected_waits and bad.closed


def test_timeout_then_verification_records_two_attempts():
    response=Response(GATE,status=503,headers={'cf-mitigated':'challenge'})
    session=StrictSession({PAGE_URL:[requests.Timeout('secret'),response]});waits=[]
    client=HTTPClient(Config(),session=session,sleeper=waits.append)
    with pytest.raises(AccessError) as e:client.fetch_page(PAGE_URL)
    assert e.value.attempts==2 and len(session.calls)==2 and waits==[1] and response.closed


def test_suspected_gate_is_stopping_but_not_provider_assertion():
    d=classify_access('<html><title>Just a moment</title><p>Checking your browser</p></html>')
    assert d.kind=='suspected_verification' and d.stop_current_job and d.provider=='unknown'


def test_login_gate_is_not_human_verification():
    d=classify_access('<html><title>Login</title><form><input type="password"></form></html>')
    assert d.kind=='authentication_required'


def test_probe_bounded_on_huge_error_and_closed():
    class Metered(Response):
        read_bytes=0
        def iter_content(self,chunk_size):
            for chunk in super().iter_content(chunk_size):
                self.read_bytes+=len(chunk)
                yield chunk
    response=Metered(b'<html>'+b'x'*2000000,status=404)
    client=HTTPClient(Config(),session=StrictSession({PAGE_URL:[response]}))
    with pytest.raises(NetworkError):client.fetch_page(PAGE_URL)
    assert response.read_bytes<=PROBE_LIMIT+8192 and response.closed


def test_probe_time_boundary_and_closure(monkeypatch):
    import wiki_voice_downloader.http_client as module
    clock=iter([0, 31])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    response=Response(GATE);client=HTTPClient(Config(max_attempts=1),session=StrictSession({PAGE_URL:[response]}))
    with pytest.raises(NetworkError,match='access_probe_timeout'):client.fetch_page(PAGE_URL)
    assert response.closed


def test_diagnostic_never_records_challenge_tokens_or_headers():
    d=classify_access(GATE,headers={'cf-mitigated':'challenge','Set-Cookie':'SECRET','Location':'https://bad/?token=SECRET'},status=503)
    assert 'SECRET' not in str(d.record()) and 'cookie' not in str(d.record()).lower()


@pytest.mark.parametrize('status,reason',[(401,'authentication_required'),(407,'authentication_required'),
                                        (403,'access_restricted'),(451,'access_restricted')])
def test_defensive_status_fallback_preserves_access_reason(monkeypatch,status,reason):
    # Even if the probe is replaced by another valid pass-through implementation,
    # the defensive status check must not mislabel a denial as Retry-After.
    import wiki_voice_downloader.http_client as module
    monkeypatch.setattr(module,'probe_response',lambda response,*_:response)
    response=Response(b'',status=status)
    session=StrictSession({PAGE_URL:[response]});waits=[]
    with HTTPClient(Config(),session=session,sleeper=waits.append) as client:
        with pytest.raises(NetworkError) as error:
            client.fetch_page(PAGE_URL)
    assert error.value.reason==reason and error.value.stop_site
    assert error.value.attempts==1 and response.closed and len(session.calls)==1 and not waits
