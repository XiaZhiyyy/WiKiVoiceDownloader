import copy
import json
from pathlib import Path
import sys
import pytest
from wiki_voice_downloader.cli import main
from wiki_voice_downloader.errors import AppError,ConfigError
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.profile_loader import builtin_profile,load_profiles,profile_from_dict,builtin_directory
from wiki_voice_downloader.site_policy import SitePolicy
from wiki_voice_downloader.storage import read_metadata,StorageError
from wiki_voice_downloader.parsers.registry import all_parsers
from .helpers import Session,Response
from .koumakan_helpers import PAGE,source,make_html,MEDIA,parsed,run
from .test_cli import answers,snapshots


def factory(holder,html=None):
    def make(config):
        routes={PAGE:[Response((html or make_html()).encode(),headers={'Content-Type':'text/html'})]}
        routes.update({source(i):[Response((MEDIA/'opus.ogg').read_bytes(),headers={'Content-Type':'audio/ogg'})] for i in range(1,9)})
        session=Session(routes);holder.append(session)
        return HTTPClient(config,session=session,sleeper=lambda _:None)
    return make


def test_cli_fragment_request_without_fragment_and_japanese_txt(tmp_path):
    holder=[];out=[]
    code=main([PAGE+'#tabber-Japanese_Server'],app_dir=tmp_path,input_fn=answers('1'),
              output_fn=out.append,client_factory=factory(holder))
    assert code==0 and holder[0].calls[0][0]==PAGE
    d=tmp_path/'downloads'/'FixtureCharacter';data=read_metadata(d)
    assert data['active_export_view']=='jp/ja'
    assert data['last_request_context']['fragment_hint']=='tabber-Japanese_Server'
    assert 'translation' not in (d/'FixtureCharacter.txt').read_text()
    assert all(call[1]['headers'].get('Referer')==PAGE for call in holder[0].calls[1:])


def test_cli_dry_run_with_conflicting_explicit_server_is_completely_read_only(tmp_path):
    holder=[];out=[]
    code=main([PAGE+'#tabber-English_Server','--server','jp','--dry-run'],app_dir=tmp_path,
              input_fn=answers('A'),output_fn=out.append,client_factory=factory(holder))
    assert code==0 and len(holder[0].calls)==1 and list(tmp_path.iterdir())==[]
    assert any('precedence' in v or '--server' in v for v in out)
    assert any('candidate' in v or '\u5019\u9009' in v for v in out)


def test_cli_existing_view_dry_run_and_cancel_do_not_backup_or_modify(tmp_path):
    d=tmp_path/'downloads'/'FixtureCharacter';run(d,parsed(server='en'))
    before=snapshots(d);holder=[]
    code=main([PAGE,'--server','jp','--dry-run'],app_dir=tmp_path,input_fn=answers('A','1'),
              output_fn=lambda _:None,client_factory=factory(holder))
    assert code==0 and snapshots(d)==before and len(holder[0].calls)==1
    code=main([PAGE,'--server','jp'],app_dir=tmp_path,input_fn=answers('A','3'),
              output_fn=lambda _:None,client_factory=factory(holder))
    assert code==0 and snapshots(d)==before
    assert not (d/'.wvd_backups').exists()


def test_cli_missing_server_fails_before_any_audio(tmp_path):
    holder=[]
    code=main([PAGE,'--server','jp','--dry-run'],app_dir=tmp_path,input_fn=answers(),
              output_fn=lambda _:None,client_factory=factory(holder,make_html(servers=('en',))))
    assert code==1 and len(holder[0].calls)==1 and list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('url,kind',[
 ('https://azurlane.netojuu.com.evil.example/images/x.ogg','audio'),
 ('https://evilazurlane.netojuu.com/images/x.ogg','audio'),
 ('https://azurlane.netojuu.com:8000/images/x.ogg','audio'),
 ('https://azurlane.netojuu.com/private/x.ogg','audio'),
 ('https://azurlane.netojuu.com/images/../private/x.ogg','audio'),
 ('https://azurlane.netojuu.com/images/%2e%2e/x.ogg','audio'),
 ('https://azurlane.netojuu.com/images/%252e%252e/x.ogg','audio'),
 ('https://azurlane.netojuu.com/images/%00x.ogg','audio'),
 ('https://u:password@azurlane.netojuu.com/images/x.ogg','audio'),
 ('https://azurlane.koumakan.jp/w/index.php?title=A/Quotes','page'),
 ('https://azurlane.koumakan.jp/wiki/A/Quotes?title=B','page'),
 ('https://azurlane.koumakan.jp/wiki/File:A/Quotes','page'),
 ('file:///tmp/local','page'),
])
def test_koumakan_source_policy_rejects_escape_forms(url,kind):
    with pytest.raises(AppError):SitePolicy(builtin_profile('koumakan_azurlane')).check(url,kind)


def test_audio_case_and_query_signatures_preserved():
    policy=SitePolicy(builtin_profile('koumakan_azurlane'))
    url='https://azurlane.netojuu.com/images/A/Voice.ogg?token=X%2FY&b=2&a=1'
    assert policy.check(url+'#ignored','audio')==url


def test_redirect_rejects_new_host_and_tls_downgrade():
    from wiki_voice_downloader.errors import NetworkError
    policy=SitePolicy(builtin_profile('koumakan_azurlane'))
    for target in ('https://evil.example/x','http://azurlane.koumakan.jp/wiki/FixtureCharacter/Quotes'):
        session=Session({PAGE:[Response(status=302,headers={'Location':target})]})
        from wiki_voice_downloader.config import Config
        client=HTTPClient(Config(),session=session,policy=policy)
        with pytest.raises(NetworkError):client.fetch_page(PAGE)
        assert len(session.calls)==1


def test_external_profile_directory_relative_to_application_not_config_or_cwd(tmp_path,monkeypatch):
    app=tmp_path/'app';app.mkdir();over=app/'profiles';over.mkdir()
    data=copy.deepcopy(builtin_profile('koumakan_azurlane').data)
    data['selectors']['audio_link']='a.changed[href]'
    (over/'quotes.json').write_text(json.dumps(data),encoding='utf-8')
    other=tmp_path/'elsewhere';other.mkdir();monkeypatch.chdir(other)
    profiles=load_profiles('profiles',app)
    assert next(p for p in profiles if p.site_id=='koumakan_azurlane').data['selectors']['audio_link']=='a.changed[href]'
    assert builtin_directory().is_dir()


def test_additional_local_profile_uses_static_strategy_without_code_loading(tmp_path):
    data=copy.deepcopy(builtin_profile('koumakan_azurlane').data)
    data.update(site_id='fixture_quotes',parser_id='fixture_quotes')
    data['page']['hosts']=['fixture.example'];data['audio_sources'][0]['host']='audio.fixture.example'
    (tmp_path/'fixture.json').write_text(json.dumps(data),encoding='utf-8')
    parsers=all_parsers(load_profiles(tmp_path))
    custom=next(p for p in parsers if p.parser_id=='fixture_quotes')
    assert custom.supports('https://fixture.example/wiki/Hero/Quotes')
    assert not custom.supports(PAGE)
    assert not custom.policy.supports('https://fixture.example/wiki/Hero/Quotes?title=other')


def test_packaging_spec_bundles_json_and_profiles_read_under_simulated_frozen(tmp_path,monkeypatch):
    from wiki_voice_downloader.config import application_dir
    root=Path(__file__).resolve().parent.parent
    spec=(root/'WikiVoiceDownloader.spec').read_text()
    assert 'wiki_voice_downloader/site_profiles' in spec and 'glob("*.json")' in spec
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'WikiVoiceDownloader.exe'))
    assert application_dir()==tmp_path
    assert len(load_profiles())>=2  # resource __file__, not cwd / executable


def test_symlink_ancestor_and_backup_directory_refused(tmp_path):
    target=tmp_path/'real';target.mkdir();alias=tmp_path/'alias';alias.symlink_to(target,target_is_directory=True)
    with pytest.raises(StorageError):run(alias/'FixtureCharacter')
    d=tmp_path/'FixtureCharacter';run(d,parsed(server='en'))
    (d/'.wvd_backups').symlink_to(target,target_is_directory=True)
    with pytest.raises(StorageError):run(d,parsed())
    assert list(target.iterdir())==[]


def test_staged_recovery_does_not_overwrite_new_unowned_file(tmp_path):
    from .test_views_and_ogg_storage import Crash
    p=parsed();p.groups[0].candidates=p.groups[0].candidates[:1]
    d=tmp_path/'FixtureCharacter'
    def checkpoint(point):
        if point=='after_stage':raise Crash(point)
    with pytest.raises(Crash):run(d,p,groups=[0],checkpoint=checkpoint)
    file=d/'audio'/'001.ogg';file.write_bytes(b'user-owned foreign data')
    with pytest.raises(StorageError,match='storage_conflict'):run(d,p,groups=[0])
    assert file.read_bytes()==b'user-owned foreign data'


def test_nested_translation_does_not_leak_to_japanese():
    html=make_html(counts=(1,),servers=('jp',))
    html=html.replace(' &amp;lt;',' &amp;lt;<span lang="en">nested translation</span>')
    c=parsed(html).groups[0].candidates[0]
    ja=next(t.text for t in c.texts if t.language=='ja')
    assert 'translation' not in ja and ja.endswith('&lt;')


@pytest.mark.parametrize('value',[None,123,'',[]])
def test_optional_profile_display_name_is_strictly_typed(value):
    data=copy.deepcopy(builtin_profile('koumakan_azurlane').data)
    data['display_name']=value
    with pytest.raises(ConfigError,match='display_name'):
        profile_from_dict(data)
