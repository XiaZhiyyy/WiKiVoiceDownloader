"""Manual browser actions are stubbed; these tests do not solve any verification."""
import copy
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from wiki_voice_downloader.cli import main
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.storage import read_metadata, DirectoryLock, StorageError
from wiki_voice_downloader.models import TextVariant
from wiki_voice_downloader.text_format import parse_record
from .helpers import Response
from .test_cli import answers
from .r2_helpers import FIXTURE, PAGE, GATE, PAYLOAD, StrictSession, document_page, candidates, snapshot, run


def lines(directory):
    p=directory/'Shinano.txt'
    return [parse_record(s) for s in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def identities(directory):
    return [(i['id'],i['filename'],i['sha256']) for i in read_metadata(directory)['items']]


def test_real_fixture_95_success_one_failure_then_only_failed_media_requested(tmp_path):
    page=document_page();cs=candidates(page);d=tmp_path/'Shinano'
    result,session,waits=run(d,page,{cs[37].source_url:[Response(status=404)]})
    assert (result.downloaded,result.failed,result.unexecuted)==(95,1,0)
    before=identities(d)
    assert len(lines(d))==95 and len(session.calls)==96 and waits==[]
    result,session,_=run(d,page)
    assert result.downloaded==1 and result.existing==95
    assert [c[0] for c in session.calls]==[cs[37].source_url]
    after=identities(d)
    assert after[:37]==before[:37] and after[38:]==before[38:]
    assert len(lines(d))==96
    for row,c in zip(lines(d),cs):
        assert row[3]==next(t.text for t in c.texts if t.language=='ja')
        assert '[ja]' not in row[3]


def test_import_en_jp_en_views_preserve_audio_and_source_texts(tmp_path):
    d=tmp_path/'Shinano';result,session,_=run(d,document_page(server='en'))
    old=(d/'Shinano.txt').read_bytes();before=identities(d)
    assert result.downloaded==96
    result,session,_=run(d,document_page(server='jp'))
    assert result.existing==96 and session.calls==[] and identities(d)==before
    assert read_metadata(d)['active_export_view']=='jp/ja'
    assert any(p.read_bytes()==old for p in (d/'.wvd_backups').rglob('Shinano.txt'))
    refs=[r for i in read_metadata(d)['items'] for r in i['references'].values()]
    assert sum(r['section_id']=='jp' and any(t['language']=='en' for t in r['texts']) for r in refs)==29
    result,session,_=run(d,document_page(server='en'))
    assert session.calls==[] and identities(d)==before and (d/'Shinano.txt').read_bytes()==old


def test_http_then_local_same_page_same_dataset(tmp_path):
    page=document_page(server='en');cs=candidates(page)
    session=StrictSession({PAGE:[Response(FIXTURE.read_bytes(),headers={'Content-Type':'text/html'})],
                           **{c.source_url:[Response(PAYLOAD)] for c in cs}})
    code=main([PAGE,'--server','en'],app_dir=tmp_path,input_fn=answers('A'),output_fn=lambda _:None,
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==0 and len(session.calls)==97
    d=tmp_path/'downloads/Shinano';before=identities(d)
    session2=StrictSession({})
    code=main([PAGE,'--server','jp','--html-file',str(FIXTURE)],app_dir=tmp_path,
              input_fn=answers('A','1'),output_fn=lambda _:None,client_factory=lambda cfg:HTTPClient(cfg,session=session2))
    assert code==0 and not session2.calls and identities(d)==before
    data=read_metadata(d)
    assert data['last_request_context']['resolved_url'] is None
    assert data['last_acquisition']['resolved_url_observed'] is False

@pytest.mark.parametrize('open_browser',[False,True])
def test_503_handoff_only_one_page_request_no_wait_and_explicit_browser(tmp_path,open_browser):
    cs=candidates(document_page());response=Response(GATE,status=503,headers={'cf-mitigated':'challenge'})
    session=StrictSession({PAGE:[response],**{c.source_url:[Response(PAYLOAD)] for c in cs}})
    opened=[];waits=[];output=[]
    code=main([PAGE+'#tabber-Japanese_Server'],app_dir=tmp_path,output_fn=output.append,
              input_fn=answers('2' if open_browser else '1',str(FIXTURE),'A'),
              browser_open=lambda url:opened.append(url) or True,
              client_factory=lambda cfg:HTTPClient(cfg,session=session,sleeper=waits.append))
    assert code==0 and waits==[] and response.closed
    assert len(session.calls)==97 and sum(a==PAGE for a,_ in session.calls)==1
    assert opened==([PAGE+'#tabber-Japanese_Server'] if open_browser else [])
    assert any('local_html' in s for s in output)

@pytest.mark.parametrize('mode',['dry_run','noninteractive'])
def test_http_verification_noninteractive_or_dryrun_no_prompt_no_browser(tmp_path,mode):
    session=StrictSession({PAGE:[Response(GATE,status=503)]});output=[]
    code=main([PAGE,'--server','jp']+(['--dry-run'] if mode=='dry_run' else []),
              app_dir=tmp_path,interactive=(mode=='dry_run'),output_fn=output.append,
              input_fn=lambda _:pytest.fail('No manual prompt'),browser_open=lambda _:pytest.fail('No browser'),
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==1 and len(session.calls)==1 and any('--html-file' in s for s in output)
    assert not (tmp_path/'downloads').exists()
    if mode=='dry_run':assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('mode',['cancel_menu','bad_then_cancel','bad_then_eof','bad_then_interrupt','bad_then_good'])
def test_handoff_bad_input_retries_only_file_and_cancellation_preserves_data(tmp_path,mode):
    # An existing usable English dataset, including a user-owned .part file.
    d=tmp_path/'downloads/Shinano';run(d,document_page(server='en'))
    (d/'audio/unmanaged.part').write_bytes(b'user')
    before=snapshot(d)
    bad=tmp_path/'wrong.html';bad.write_bytes(GATE)
    missing=tmp_path/'missing.html'
    inputs={'cancel_menu':['0'],'bad_then_cancel':['1',str(bad),'0'],
            'bad_then_eof':['1',str(missing)],'bad_then_interrupt':['1',str(bad)],
            'bad_then_good':['1',str(missing),str(bad),str(FIXTURE),'A','3']}[mode]
    it=iter(inputs)
    def ask(_):
        try:return next(it)
        except StopIteration:
            if mode=='bad_then_interrupt':raise KeyboardInterrupt
            raise EOFError
    session=StrictSession({PAGE:[Response(GATE,status=503)]})
    code=main([PAGE,'--server','jp'],app_dir=tmp_path,input_fn=ask,output_fn=lambda _:None,
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==({'bad_then_eof':1,'bad_then_interrupt':130}.get(mode,0))
    assert len(session.calls)==1 and snapshot(d)==before
    with DirectoryLock(d):pass


def test_browser_failure_still_allows_import_without_new_page_request(tmp_path):
    session=StrictSession({PAGE:[Response(GATE,status=503)]})
    def browser(_):raise OSError('Private path must not appear')
    output=[]
    code=main([PAGE,'--server','jp'],app_dir=tmp_path,browser_open=browser,output_fn=output.append,
              input_fn=answers('2','missing.html','0'),
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==0 and len(session.calls)==1
    assert 'Private path must not appear' not in '\n'.join(output)
    assert any(PAGE in s for s in output)


def test_media_3_blocked_preserves_first_two_pending_ids_then_resumes(tmp_path):
    page=document_page();cs=candidates(page);d=tmp_path/'Shinano'
    routes={c.source_url:[lambda:pytest.fail('Must stop before subsequent media')] for c in cs[3:]}
    routes[cs[2].source_url]=[Response(GATE,status=503,headers={'cf-mitigated':'challenge'})]
    result,session,waits=run(d,page,routes)
    assert (result.downloaded,result.failed,result.blocked,result.unexecuted)==(2,1,1,93)
    assert result.total==result.downloaded+result.existing+result.failed+result.unexecuted
    assert len(session.calls)==3 and waits==[] and result.exit_code==1
    data=read_metadata(d)
    assert data['next_id']==97 and len(data['items'])==96
    assert data['items'][2]['status']=='failed'
    assert data['items'][2]['last_access_decision']['kind']=='needs_human_verification'
    assert all(i['status']=='pending' for i in data['items'][3:])
    assert len(lines(d))==2 and not (d/'audio/003.ogg').exists()
    assert not (d/'audio/003.ogg.part').exists()
    before=identities(d)[:2]
    with DirectoryLock(d):pass
    result,session,_=run(d,page)
    assert result.downloaded==94 and result.existing==2 and len(session.calls)==94
    assert identities(d)[:2]==before and len(lines(d))==96
    assert 'last_access_decision' not in read_metadata(d)['items'][2]


def test_overwrite_blocked_preserves_old_valid_file_and_old_body(tmp_path):
    page=document_page();cs=candidates(page);d=tmp_path/'Shinano';run(d,page)
    before=identities(d);txt=(d/'Shinano.txt').read_bytes()
    page.groups[0].candidates[0].texts=[TextVariant('ja','replacement must not commit')]
    result,session,_=run(d,page,{cs[0].source_url:[Response(GATE,status=403)]},overwrite=True)
    assert result.stopped and result.blocked==1 and len(session.calls)==1
    assert identities(d)==before and (d/'Shinano.txt').read_bytes()==txt
    assert read_metadata(d)['items'][0]['status']=='completed'


def test_empty_target_view_cannot_replace_old_english_or_make_backup(tmp_path):
    d=tmp_path/'Shinano';run(d,document_page(server='en'))
    original=(d/'Shinano.txt').read_bytes()+b'USER NOTE\n';(d/'Shinano.txt').write_bytes(original)
    before=identities(d)
    page=document_page();c=page.groups[0].candidates[0]
    # Real layout, deliberate synthetic new URL so there is no reusable JP item.
    c.source_url='https://azurlane.netojuu.com/images/test/new_only.ogg'
    page.groups=page.groups[:1];page.groups[0].candidates=[c]
    result,session,_=run(d,page,{c.source_url:[Response(GATE,status=503)]})
    assert result.blocked==1 and len(session.calls)==1
    data=read_metadata(d)
    assert data['active_export_view']=='en/en' and data['requested_export_view']=='jp/ja'
    assert (d/'Shinano.txt').read_bytes()==original
    assert not (d/'.wvd_backups').exists()
    assert identities(d)[:96]==before and data['next_id']==98
    result,session,_=run(d,page)
    assert result.downloaded==1 and data['items'][96]['id']==97
    assert read_metadata(d)['active_export_view']=='jp/ja' and len(lines(d))==1
    assert list((d/'.wvd_backups').rglob('Shinano.txt'))[0].read_bytes()==original
    assert lines(d)[0][0]=='097.ogg'


def test_reused_target_can_activate_before_new_media_is_blocked(tmp_path):
    d=tmp_path/'Shinano';run(d,document_page(server='en'))
    page=document_page();c=page.groups[0].candidates[1]
    c.source_url='https://azurlane.netojuu.com/images/test/new_only.ogg'
    page.groups=page.groups[:1];page.groups[0].candidates=page.groups[0].candidates[:2]
    result,session,_=run(d,page,{c.source_url:[Response(GATE,status=503)]})
    assert result.existing==1 and result.blocked==1 and len(session.calls)==1
    assert read_metadata(d)['active_export_view']=='jp/ja'
    assert len(lines(d))==1 and lines(d)[0][3].startswith('\u91cd\u685c')

@pytest.mark.parametrize('case',['verification','other_character','missing_server','encoding_error','shell','no_safe_audio'])
def test_invalid_import_preserves_entire_existing_dataset(tmp_path,case):
    d=tmp_path/'downloads/Shinano';run(d,document_page(server='en'))
    before=snapshot(d);path=tmp_path/'invalid.html';soup=BeautifulSoup(FIXTURE.read_text(encoding="utf-8"),'html.parser')
    if case=='verification':raw=GATE
    elif case=='other_character':raw=FIXTURE.read_bytes().replace(b'/wiki/Shinano',b'/wiki/Other')
    elif case=='missing_server':raw=str(soup.select_one('#tabber-English_Server')).encode()
    elif case=='encoding_error':raw=FIXTURE.read_text(encoding="utf-8").encode('utf-16')
    elif case=='shell':raw=b'<html><div id="app"></div></html>'
    else:raw=FIXTURE.read_bytes().replace(b'https://azurlane.netojuu.com/images/',b'file:///')
    path.write_bytes(raw)
    session=StrictSession({})
    code=main([PAGE,'--server','jp','--html-file',str(path)],app_dir=tmp_path,
              input_fn=lambda _:pytest.fail('invalid input must stop before any question'),output_fn=lambda _:None,
              client_factory=lambda cfg:HTTPClient(cfg,session=session))
    assert code==1 and snapshot(d)==before and session.calls==[]

@pytest.mark.parametrize('point',['after_plan','after_stage','after_audio_replace','after_metadata_complete',
                                 'before_view_activation','after_view_activation','before_txt_replace'])
def test_r2_view_transition_faults_recover_without_new_ids(tmp_path,point):
    d=tmp_path/'Shinano';run(d,document_page(server='en'))
    page=document_page();c=page.groups[0].candidates[0]
    c.source_url='https://azurlane.netojuu.com/images/test/new_single.ogg'
    page.groups=page.groups[:1];page.groups[0].candidates=[c]
    class SimulatedCrash(BaseException):pass
    armed={'on':True}
    def checkpoint(label):
        if label==point and armed['on']:
            armed['on']=False
            raise SimulatedCrash(label)
    with pytest.raises(SimulatedCrash):run(d,page,checkpoint=checkpoint)
    with DirectoryLock(d):pass
    before=read_metadata(d)
    assert before['next_id']==98 and before['items'][-1]['id']==97
    result,session,_=run(d,page)
    after=read_metadata(d)
    assert after['active_export_view']=='jp/ja' and after['next_id']==98
    assert len(lines(d))==1 and lines(d)[0][0]=='097.ogg'
    assert len(session.calls)<=1 and all('new_single.ogg' in c[0] for c in session.calls)
    assert list((d/'.wvd_backups').rglob('Shinano.txt'))


def test_empty_selection_service_refuses_before_creating_directory(tmp_path):
    from wiki_voice_downloader.service import DownloadService
    from wiki_voice_downloader.downloader import Downloader
    from wiki_voice_downloader.config import Config
    from wiki_voice_downloader.errors import ParseError
    session=StrictSession({});client=HTTPClient(Config(),session=session)
    with pytest.raises(ParseError):
        DownloadService(Downloader(client)).run(document_page(),[],tmp_path/'NoCreate','Shinano','koumakan_azurlane')
    assert not (tmp_path/'NoCreate').exists() and not session.calls
