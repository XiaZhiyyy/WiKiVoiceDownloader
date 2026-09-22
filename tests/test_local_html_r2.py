"""Local source is read-only input, not a browser authorization token."""
from pathlib import Path
import hashlib
import json
import os
import socket
import pytest
from bs4 import BeautifulSoup
from wiki_voice_downloader.cli import main
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.errors import SnapshotError, ParseError, AccessError
from wiki_voice_downloader.models import PageRequestContext
from wiki_voice_downloader.page_sources import LocalHtmlPageSource, parse_document
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser
from wiki_voice_downloader.section_selection import select_section
from wiki_voice_downloader.storage import read_metadata
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.text_format import parse_record
from .helpers import Response
from .test_cli import answers
from .koumakan_helpers import make_html, PAGE as OTHER_PAGE
from .r2_helpers import FIXTURE, PAGE, GATE, PAYLOAD, StrictSession, document_page, candidates, snapshot


def load(path, encoding=None, config=None, url=PAGE):
    p=KoumakanAzurLaneParser();ctx=PageRequestContext(url,url)
    document=LocalHtmlPageSource(path,config or Config(),p.policy,encoding).load(ctx)
    return parse_document(document,p,ctx)


def test_derived_real_fixture_fingerprint_and_counts():
    data=json.loads((FIXTURE.parent/'provenance.json').read_text(encoding="utf-8"))
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest()==data['derived_sha256']
    page=load(FIXTURE);soup=BeautifulSoup(FIXTURE.read_text(encoding="utf-8"),'html.parser')
    assert len(page.sections)==3
    sets=[{c.source_url for g in s.groups for c in g.candidates} for s in page.sections]
    assert sets[0]==sets[1]==sets[2] and len(sets[0])==96
    jp=page.sections[2]
    assert [g.unique_count for g in jp.groups]==[30,9,8,12,13,14,10]
    cs=[c for g in jp.groups for c in g.candidates]
    assert sum(any(t.language=='ja' for t in c.texts) for c in cs)==96
    assert sum(any(t.language=='en' for t in c.texts) for c in cs)==29
    assert len(soup.select('a[href$=".ogg"]'))==576
    assert sum(bool(row.select('a.sm2_button')) for row in soup.select('#tabber-Japanese_Server tr[hidden]'))==66
    assert cs[0].category=='Self Introduction'
    assert cs[0].source_url.endswith('/a/a7/Shinano_SelfIntroJP.ogg')
    assert next(t.text for t in cs[0].texts if t.language=='ja').startswith('\u91cd\u685c\u306b\u5c5e\u3059\u3001\u7a7a\u6bcd\u4fe1\u6fc3\u3068\u7533\u3059\u3002')


def test_real_fixture_provenance_without_fictitious_http():
    page=document_page();record=page.acquisition
    assert record['acquisition_kind']=='local_html' and record['resolved_url'] is None
    assert record['resolved_url_observed'] is False
    assert record['effective_page_url']==PAGE and record['loaded_at']
    assert record['snapshot_basename']==FIXTURE.name and str(FIXTURE.parent) not in str(record)
    assert record['identity_status']=='declaration_matches_untrusted'
    assert 'http_status' not in record and 'captcha_passed' not in record

@pytest.mark.parametrize('codec,bom',[('utf-8',False),('utf-8-sig',True),('shift_jis',False),('utf-16',True)])
def test_explicit_text_encodings_and_bom(tmp_path,codec,bom):
    text=make_html(counts=(1,),servers=('jp',))
    p=tmp_path/'input.txt';p.write_bytes(text.encode(codec))
    page=load(p, None if codec.startswith('utf-8') else codec,url=OTHER_PAGE)
    assert next(t.text for t in page.sections[0].groups[0].candidates[0].texts if t.language=='ja').startswith('\u65e5\u65871')

@pytest.mark.parametrize('codec',['not_a_codec','rot_13','base64_codec','zlib_codec'])
def test_invalid_or_non_text_codec_rejected(tmp_path,codec):
    with pytest.raises(SnapshotError):load(tmp_path/'missing.html',codec)


def test_wrong_encoding_no_replacement(tmp_path):
    p=tmp_path/'input.txt';p.write_bytes(make_html().encode('utf-16'))
    with pytest.raises(SnapshotError,match='strict decoding'):load(p)

@pytest.mark.parametrize('data',[b'%PDF-1.0\n<html>',b'PK\x03\x04<html>',b'\x89PNG<html>',b'\xff\xd8\xff<html>',
                                 b'GIF89a<html>',b'RIFF<html>',b'MIME-Version: 1.0\nContent-Type: multipart/related\n<html>',
                                 b'not html',b'<a href="a.ogg">Play</a>'])
def test_unsupported_representation_even_with_html_suffix(tmp_path,data):
    path=tmp_path/'input.html';path.write_bytes(data)
    with pytest.raises(SnapshotError):load(path)

@pytest.mark.parametrize('kind',['missing','directory','over_limit','mhtml_suffix'])
def test_regular_file_and_reading_limit(tmp_path,kind):
    path=tmp_path/('input.mhtml' if kind=='mhtml_suffix' else 'input.html')
    if kind=='directory':path.mkdir()
    if kind=='over_limit':path.write_bytes(b'<html>'+b'x'*5000)
    if kind=='mhtml_suffix':path.write_text('<html>Test</html>', encoding="utf-8")
    with pytest.raises(SnapshotError):load(path,config=Config(max_page_size_mb=.001))


def test_fifo_rejected_without_blocking(tmp_path):
    if not hasattr(os,'mkfifo'):
        # Native Windows uses a named-pipe/device input rejection in its manual checklist.
        return
    path=tmp_path/'pipe.html';os.mkfifo(path)
    with pytest.raises(SnapshotError):load(path)


def test_unicode_spaces_and_quoted_path(tmp_path):
    path=tmp_path/'\u4e2d\u6587 \u65e5\u672c\u8a9e';path.mkdir();path=path/'quotes file.txt';path.write_bytes(FIXTURE.read_bytes())
    p=load('"'+str(path)+'"')
    assert p.acquisition['snapshot_basename']=='quotes file.txt'

@pytest.mark.parametrize('replacement',[
    ('/wiki/Shinano','/wiki/OtherCharacter'),
    ('<div class="mw-parser-output">','<div class="mw-parser-output"><link rel="canonical" href="https://evil.invalid/wiki/Shinano/Quotes">'),
])
def test_explicit_snapshot_identity_mismatch(tmp_path,replacement):
    p=tmp_path/'wrong.html';p.write_text(FIXTURE.read_text(encoding="utf-8").replace(*replacement), encoding="utf-8")
    with pytest.raises(SnapshotError,match='snapshot_identity_mismatch'):load(p)


def test_selected_panel_fragment_has_limited_coverage(tmp_path):
    soup=BeautifulSoup(FIXTURE.read_text(encoding="utf-8"),'html.parser')
    p=tmp_path/'panel.txt';p.write_text(str(soup.select_one('#tabber-Japanese_Server')), encoding="utf-8")
    page=document_page(p)
    assert len(candidates(page))==96
    assert page.acquisition['coverage_status']=='selected_panel'
    assert page.acquisition['identity_status']=='user_supplied_unverified'

@pytest.mark.parametrize('body',[GATE,b'<html><title>Access denied</title>Access denied</html>',
                                 b'<html><title>Login</title><form><input type="password"></form></html>'])
def test_local_gate_or_login_rejected(tmp_path,body):
    p=tmp_path/'bad.html';p.write_bytes(body)
    with pytest.raises(AccessError) as error:load(p)
    assert error.value.decision.http_status is None


def test_empty_shell_has_specific_error(tmp_path):
    p=tmp_path/'shell.html';p.write_text('<html><body><div id="app"></div></body></html>', encoding="utf-8")
    with pytest.raises(ParseError,match='unexpected_page'):load(p)

@pytest.mark.parametrize('target',['file:///private/a.ogg','data:audio/ogg,abc','blob:https://azurlane.netojuu.com/abc',
                                  'http://127.0.0.1/a.ogg','https://192.168.1.1/a.ogg','https://azurlane.netojuu.com.evil/a.ogg',
                                  'quotes_files/a.ogg','javascript:alert(1)'])
def test_unsafe_resources_never_become_candidates(tmp_path,target):
    from .koumakan_helpers import source
    p=tmp_path/'input.html';p.write_text(make_html(counts=(1,),servers=('jp',)).replace(source(1),target), encoding="utf-8")
    page=load(p,url=OTHER_PAGE)
    assert not page.sections[0].groups
    assert page.warnings


def test_malicious_base_auxiliary_and_forms_never_execute_or_fetch(tmp_path,monkeypatch):
    def forbidden(*a,**kw):pytest.fail('No network or browser access is allowed')
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    p=tmp_path/'input.html';p.write_text(FIXTURE.read_text(encoding="utf-8")+'<base href="http://127.0.0.1/"><iframe src="https://evil.invalid/"></iframe><script>throw 1;</script><meta http-equiv="refresh" content="0;url=https://evil.invalid">', encoding="utf-8")
    page=load(p)
    assert all(c.source_url.startswith('https://azurlane.netojuu.com/images/') for s in page.sections for g in s.groups for c in g.candidates)


def test_local_dry_run_zero_network_and_no_output(tmp_path,monkeypatch):
    def forbidden(*a,**kw):pytest.fail('Network forbidden')
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    output=[]
    code=main([PAGE,'--server','jp','--html-file',str(FIXTURE),'--dry-run'],
              app_dir=tmp_path,output_fn=output.append,interactive=False,browser_open=forbidden)
    assert code==0 and not list(tmp_path.iterdir())
    assert any('96' in line for line in output)


def test_html_file_normal_mode_really_downloads_without_page_requests(tmp_path):
    cs=candidates(document_page());session=StrictSession({c.source_url:[Response(PAYLOAD)] for c in cs})
    code=main([PAGE+'#tabber-Japanese_Server','--html-file',str(FIXTURE)],app_dir=tmp_path,
              input_fn=answers('A'),output_fn=lambda _:None,
              client_factory=lambda config:HTTPClient(config,session=session))
    d=tmp_path/'downloads/Shinano';data=read_metadata(d)
    assert code==0 and len(session.calls)==96 and all(call[0]!=PAGE for call in session.calls)
    assert data['last_acquisition']['resolved_url'] is None
    rows=[parse_record(line) for line in (d/'Shinano.txt').read_text(encoding="utf-8").splitlines()]
    assert len(rows)==96 and all(row[0].endswith('.ogg') for row in rows)
    assert all(row[3]==next(t.text for t in c.texts if t.language=='ja') for row,c in zip(rows,cs))
    # Same local file reuses exactly the existing media.
    session2=StrictSession({})
    code=main([PAGE,'--server','jp','--html-file',str(FIXTURE)],app_dir=tmp_path,
              input_fn=answers('A','1'),output_fn=lambda _:None,
              client_factory=lambda config:HTTPClient(config,session=session2))
    assert code==0 and session2.calls==[]
    before=snapshot(tmp_path)
    code=main([PAGE,'--server','jp','--html-file',str(FIXTURE),'--dry-run'],app_dir=tmp_path,
              interactive=False,output_fn=lambda _:None,client_factory=lambda config:HTTPClient(config,session=StrictSession({})))
    assert code==0 and before==snapshot(tmp_path)

@pytest.mark.parametrize('argv,expected',[
    (['--html-file',str(FIXTURE),'--dry-run'],'missing_source_url'),
    ([PAGE,'--html-encoding','utf-8','--dry-run'],'--html-encoding requires'),
    ([PAGE,'--html-file',str(FIXTURE),'--dry-run'],'\u8f93\u5165\u5df2\u7ed3\u675f'),
])
def test_noninteractive_required_choices_do_not_wait(tmp_path,argv,expected):
    output=[]
    code=main(argv,app_dir=tmp_path,interactive=False,output_fn=output.append,
              input_fn=lambda _:pytest.fail('must not prompt'))
    assert code==1 and any(expected in line for line in output) and not list(tmp_path.iterdir())

@pytest.mark.parametrize('path',['//server/share/input.html',r'\\server\share\input.txt',r'\\.\pipe\input.html'])
def test_network_device_paths_rejected_before_stat(monkeypatch,path):
    original = Path.stat
    def guarded_stat(self, *args, **kwargs):
        if str(self).startswith(('//', '\\\\')):
            pytest.fail('must reject network/device namespace before filesystem access')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path,'stat',guarded_stat)
    with pytest.raises(SnapshotError):load(path)


def test_empty_html_file_argument_does_not_fall_back_to_http(tmp_path):
    session=StrictSession({})
    code=main([PAGE,'--server','jp','--html-file','','--dry-run'],app_dir=tmp_path,
              interactive=False,output_fn=lambda _:None,client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==1 and not session.calls and not list(tmp_path.iterdir())


def test_clear_foreign_quotes_heading_is_identity_mismatch(tmp_path):
    p=tmp_path/'wrong.html';p.write_text(make_html(), encoding="utf-8")
    with pytest.raises(SnapshotError,match='snapshot_identity_mismatch'):load(p)


def test_explicit_server_overrides_fragment_and_saved_ui_state(tmp_path):
    session=StrictSession({});output=[]
    code=main([PAGE+'#tabber-English_Server','--server','jp','--html-file',str(FIXTURE),'--dry-run'],
              app_dir=tmp_path,interactive=False,output_fn=output.append,
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==0 and not session.calls
    assert any('--server' in line and '\u4f18\u5148' in line for line in output)
    assert any('Japanese Server' in line for line in output)
