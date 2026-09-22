import copy
import json
from pathlib import Path
import pytest
from wiki_voice_downloader.storage import read_metadata,Store,StorageError
from wiki_voice_downloader.text_format import parse_record
from wiki_voice_downloader.models import TextVariant
from .helpers import Response,mp3
from .koumakan_helpers import run,parsed,make_html,source,MEDIA


def records(directory):
    return [parse_record(line) for line in (directory/'FixtureCharacter.txt').read_text(encoding='utf-8').splitlines()]

def hashes(directory):
    data=read_metadata(directory)
    return [(i['id'],i['filename'],i['sha256']) for i in data['items']]


def test_english_to_japanese_to_english_zero_additional_audio_requests(tmp_path):
    directory=tmp_path/'FixtureCharacter'
    first,network=run(directory,parsed(server='en'))
    assert first.downloaded==8 and len(network.calls)==8
    old=(directory/'FixtureCharacter.txt').read_bytes(); before=hashes(directory)
    # A user's view edit is backed up before recovery's derived TXT rebuild.
    (directory/'FixtureCharacter.txt').write_bytes(old+b'USER NOTE\n')
    second,network=run(directory,parsed(server='jp'))
    assert second.downloaded==0 and second.existing==8 and network.calls==[] and second.audio_requests==0
    assert hashes(directory)==before
    assert all(r[3].startswith('\u65e5\u6587') and 'translation' not in r[3] and '[ja]' not in r[3] for r in records(directory))
    backups=list((directory/'.wvd_backups').rglob('FixtureCharacter.txt'))
    assert len(backups)==1 and backups[0].read_bytes()==old+b'USER NOTE\n'
    data=read_metadata(directory); refs=data['items'][0]['references'].values()
    assert len(list(refs))==2
    text_sources={(r['section_id'],t['language']):t['text'] for r in refs for t in r['texts']}
    assert text_sources['en','en']=='English original 1'
    assert text_sources['jp','en']=='JP translation 1'
    assert text_sources['jp','ja'].startswith('\u65e5\u65871')
    third,network=run(directory,parsed(server='en'))
    assert network.calls==[] and hashes(directory)==before
    assert (directory/'FixtureCharacter.txt').read_bytes()==old


def test_server_memberships_accumulate_only_actually_selected_groups(tmp_path):
    d=tmp_path/'FixtureCharacter';run(d,parsed(server='en'))
    result,net=run(d,parsed(),groups=[0])
    assert len(records(d))==3 and result.existing==3 and not net.calls
    run(d,parsed(),groups=[2]);assert len(records(d))==4
    run(d,parsed(),groups=[0]);assert len(records(d))==4
    assert len(read_metadata(d)['items'])==8


def test_missing_target_language_is_not_download_failure(tmp_path):
    p=parsed();c=p.groups[0].candidates[0]
    c.texts=[TextVariant('en','Translation only')];c.text_status='missing_target_language'
    d=tmp_path/'FixtureCharacter';summary,_=run(d,p,groups=[0])
    assert summary.downloaded==3 and summary.failed==0 and summary.missing_text_saved==1
    assert records(d)[0][3]=='[\u65e0\u53f0\u8bcd]'
    p2=parsed();summary,net=run(d,p2,groups=[0])
    assert net.calls==[] and records(d)[0][3].startswith('\u65e5\u6587')
    ref=next(iter(read_metadata(d)['items'][0]['references'].values()))
    assert ref['texts'][0]=={'language':'en','text':'Translation only'}
    assert summary.languages_added>=1


def test_normal_continue_retains_remote_text_change_successful_override_updates(tmp_path):
    d=tmp_path/'FixtureCharacter';p=parsed();p.groups[0].candidates=p.groups[0].candidates[:1]
    run(d,p,groups=[0]);old=records(d)[0]
    changed=copy.deepcopy(p);changed.groups[0].candidates[0].texts=[TextVariant('ja','new remote line'),TextVariant('en','new translation')]
    summary,net=run(d,changed,groups=[0]);assert not net.calls and records(d)[0]==old
    ref=next(iter(read_metadata(d)['items'][0]['references'].values()))
    assert any('remote_text_changed' in s for s in ref['diagnostics'])
    summary,_=run(d,changed,groups=[0],overwrite=True,routes={source(1):[Response(status=404)]})
    assert summary.failed==1 and records(d)[0]==old
    summary,_=run(d,changed,groups=[0],overwrite=True)
    assert summary.downloaded==1 and records(d)[0][3]=='new remote line'


def test_duplicate_url_missing_body_fills_only_from_selected_same_section(tmp_path):
    p=parsed(make_html().replace(source(4),source(1)))
    p.groups[0].candidates[0].texts=[TextVariant('en','not a Japanese body')]
    p.groups[0].candidates[0].text_status='missing_target_language'
    d=tmp_path/'FixtureCharacter';summary,_=run(d,p,groups=[0,1])
    rows=records(d)
    assert summary.total==4 and rows[0][1]=='Fixture Group 0'
    assert rows[0][3].startswith('\u65e5\u65874')
    assert len(read_metadata(d)['items'][0]['references'])==2


def test_96_synthetic_media_failure_resume_preserves_ids_and_bytes(tmp_path):
    # This is a deliberately artificial 96-media stress fixture, NOT the absent
    # Shinano browser DOM. Group counts differ from the document's regression fixture.
    p=parsed(make_html(counts=(10,11,12,13,14,15,21)))
    d=tmp_path/'FixtureCharacter'
    summary,net=run(d,p,routes={source(37):[Response(status=404)]})
    assert summary.total==96 and summary.downloaded==95 and summary.failed==1
    assert len(net.calls)==96 and len(records(d))==95
    data=read_metadata(d);assert data['next_id']==97 and len(data['items'])==96
    assert data['items'][36]['id']==37 and data['items'][36]['status']=='failed'
    before={f.name:f.read_bytes() for f in (d/'audio').glob('*.ogg')}
    summary,net=run(d,p)
    assert len(net.calls)==1 and net.calls[0][0]==source(37)
    assert summary.downloaded==1 and summary.existing==95
    assert len(records(d))==96
    assert all((d/'audio'/name).read_bytes()==value for name,value in before.items())
    for index,row in enumerate(records(d),1):
        assert row[0]==f'{index:03d}.ogg'
        assert row[3]==f'\u65e5\u6587{index} Zzzz 123 (abc) A|B\\C &lt;'


def test_96_synthetic_en_jp_view_switch_no_audio_requests(tmp_path):
    html=make_html(counts=(10,11,12,13,14,15,21));d=tmp_path/'FixtureCharacter'
    summary,_=run(d,parsed(html,server='en')); assert summary.downloaded==96
    before=hashes(d)
    summary,net=run(d,parsed(html,server='jp'))
    assert summary.existing==96 and summary.audio_requests==0 and net.calls==[]
    assert hashes(d)==before and len(records(d))==96
    assert all(row[3].startswith('\u65e5\u6587') for row in records(d))

@pytest.mark.parametrize('suffix',[None,'mp3'])
def test_first_commit_detects_actual_format_independent_of_hint(tmp_path,suffix):
    p=parsed();c=p.groups[0].candidates[0]
    c.source_url='https://azurlane.netojuu.com/images/test/unknown'+('.'+suffix if suffix else '')
    c.format_hint=suffix;p.groups[0].candidates=[c]
    d=tmp_path/'FixtureCharacter';summary,_=run(d,p,groups=[0])
    item=read_metadata(d)['items'][0]
    assert summary.downloaded==1 and item['filename']=='001.ogg' and item['media_format']=='ogg'
    assert item['frame_count'] is None and records(d)[0][0]=='001.ogg'
    again,net=run(d,p,groups=[0]);assert net.calls==[] and again.existing==1


def test_existing_source_format_change_keeps_old_ogg_and_reference(tmp_path):
    p=parsed();p.groups[0].candidates=p.groups[0].candidates[:1]
    d=tmp_path/'FixtureCharacter';run(d,p,groups=[0]);before=hashes(d);text=records(d)
    p.groups[0].candidates[0].texts=[TextVariant('ja','must not be committed')]
    summary,_=run(d,p,groups=[0],overwrite=True,routes={source(1):[Response(mp3())]})
    assert summary.failed==1 and summary.downloaded==0
    assert hashes(d)==before and records(d)==text and not (d/'audio'/'001.mp3').exists()
    assert 'source_format_changed' in summary.failures[0][1]


def test_mixed_formats_share_one_numeric_sequence_and_natural_1000(tmp_path):
    p=parsed();p.groups[0].candidates=p.groups[0].candidates[:2]
    d=tmp_path/'FixtureCharacter';summary,_=run(d,p,groups=[0],routes={source(1):[Response(mp3())]})
    assert [r[0] for r in records(d)]==['001.mp3','002.ogg']
    data=read_metadata(d);data['next_id']=1000
    (d/'metadata.json').write_text(json.dumps(data),encoding='utf-8')
    p=parsed();run(d,p,groups=[0]);assert records(d)[-1][0]=='1000.ogg'
    assert records(d)[0][0]=='001.mp3'

class Crash(RuntimeError): pass

@pytest.mark.parametrize('point',['after_plan','after_stage','after_audio_replace','after_metadata_complete','before_txt_replace'])
def test_ogg_commit_interruptions_recover_without_reallocation(tmp_path,point):
    d=tmp_path/'FixtureCharacter';p=parsed();p.groups[0].candidates=p.groups[0].candidates[:1]
    armed={'on':False}
    def checkpoint(name):
        # Let the empty initialization TXT exist before interrupting the selected plan.
        if name=='after_plan': armed['on']=True
        if name==point and (point!='before_txt_replace' or armed['on']): raise Crash(point)
    with pytest.raises(Crash):run(d,p,groups=[0],checkpoint=checkpoint)
    summary,net=run(d,p,groups=[0]);data=read_metadata(d)
    assert len(data['items'])==1 and data['items'][0]['id']==1 and data['next_id']==2
    assert records(d)[0][0]=='001.ogg' and data['items'][0]['status']=='completed'
    if point in {'after_stage','after_audio_replace','after_metadata_complete'}: assert net.calls==[]


def test_unknown_hint_journal_remembers_staging_filename(tmp_path):
    d=tmp_path/'FixtureCharacter';p=parsed();c=p.groups[0].candidates[0]
    c.source_url=source(1).removesuffix('.ogg');c.format_hint=None;p.groups[0].candidates=[c]
    def checkpoint(name):
        if name=='after_stage':raise Crash(name)
    with pytest.raises(Crash):run(d,p,groups=[0],checkpoint=checkpoint)
    data=read_metadata(d);item=data['items'][0]
    assert item['filename'] is None and item['pending_commit']['part_filename']=='001.media.part'
    summary,net=run(d,p,groups=[0]);assert not net.calls and summary.existing==1
    assert records(d)[0][0]=='001.ogg'


def test_unmanaged_same_id_with_other_extension_is_not_overwritten(tmp_path):
    d=tmp_path/'FixtureCharacter';p=parsed();p.groups[0].candidates=p.groups[0].candidates[:1]
    run(d,p,groups=[0]);path=d/'audio'/'002.mp3';path.write_bytes(b'user owned')
    with pytest.raises(StorageError,match='storage_conflict'):run(d,parsed(),groups=[0])
    assert path.read_bytes()==b'user owned'


def test_view_state_recovery_after_metadata_before_txt(tmp_path):
    d=tmp_path/'FixtureCharacter';run(d,parsed(server='en'));armed={'on':False}
    def checkpoint(name):
        if name=='after_plan':armed['on']=True
        if name=='before_txt_replace' and armed['on']:raise Crash(name)
    with pytest.raises(Crash):run(d,parsed(),checkpoint=checkpoint)
    assert read_metadata(d)['active_export_view']=='jp/ja'
    assert records(d)[0][3].startswith('English original')
    summary,net=run(d,parsed());assert not net.calls and records(d)[0][3].startswith('\u65e5\u6587')
