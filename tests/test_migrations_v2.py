import copy
import json
import os
from pathlib import Path
import shutil
import pytest
from wiki_voice_downloader.storage import read_metadata,validate_metadata,StorageError
from wiki_voice_downloader.migrations import upgrade_data
from wiki_voice_downloader.cli import main
from .helpers import run_selection,url,mp3
from .test_cli import snapshots,answers,client_factory

FIXTURE=Path(__file__).parent/'fixtures'/'legacy_v1'


def legacy(tmp_path,name='LegacyCharacter'):
    directory=tmp_path/name
    shutil.copytree(FIXTURE/name,directory)
    return directory


def test_actual_v1_generated_fixture_migrates_preserving_ids_bytes_and_legacy_text(tmp_path,page):
    d=legacy(tmp_path);old=read_metadata(d);raw=(d/'metadata.json').read_bytes()
    audio=(d/'audio'/'001.mp3').read_bytes();old_txt=(d/'LegacyCharacter.txt').read_bytes()
    assert old['schema_version']==1 and [i['status'] for i in old['items']]==['completed','failed','completed']
    result,network=run_selection(d,page,[0])
    new=read_metadata(d)
    assert new['schema_version']==2 and new['active_export_view']=='legacy/*'
    assert new['next_id']==old['next_id']==4 and [i['id'] for i in new['items']]==[1,2,3]
    assert result.downloaded==1 and result.existing==2
    assert [call[0] for call in network.calls]==[url('B')]
    assert (d/'audio'/'001.mp3').read_bytes()==audio
    assert new['items'][0]['texts']==old['items'][0]['texts']
    backups=list((d/'.wvd_backups').iterdir());assert len(backups)==1
    assert (backups[0]/'metadata.json').read_bytes()==raw
    assert (backups[0]/'LegacyCharacter.txt').read_bytes()==old_txt
    run_selection(d,page,[0]);assert len(list((d/'.wvd_backups').iterdir()))==1


def test_dry_run_old_schema_has_no_writes_or_backups(tmp_path,page,html):
    root=tmp_path/'downloads';root.mkdir();d=legacy(root)
    before=snapshots(tmp_path)
    code=main(['https://wiki.biligame.com/blhx/OfflineCharacter','--dry-run'],
              input_fn=answers('1','1'),output_fn=lambda _:None,
              client_factory=client_factory(html),app_dir=tmp_path)
    assert code==0 and snapshots(tmp_path)==before and read_metadata(d)['schema_version']==1
    assert not (d/'.wvd_backups').exists()


def test_cancel_old_schema_does_not_migrate(tmp_path,page,html):
    root=tmp_path/'downloads';root.mkdir();d=legacy(root);before=snapshots(d)
    code=main(['https://wiki.biligame.com/blhx/OfflineCharacter'],input_fn=answers('1','3'),
              output_fn=lambda _:None,client_factory=client_factory(html),app_dir=tmp_path)
    assert code==0 and snapshots(d)==before and read_metadata(d)['schema_version']==1


def test_legacy_staged_overwrite_is_converted_not_discarded(tmp_path,page):
    d=legacy(tmp_path,'PendingCharacter');old=read_metadata(d)
    assert 'pending_commit' in old['items'][0]
    result,network=run_selection(d,page,[0])
    new=read_metadata(d);first=new['items'][0]
    assert new['schema_version']==2 and 'pending_commit' not in first
    assert first['texts']==[{'language':'zh','text':'staged legacy replacement'}]
    assert first['references']['legacy']['texts']==first['texts']
    assert (d/'audio'/'001.mp3').read_bytes()==mp3(payload=1)
    assert [c[0] for c in network.calls]==[url('B')]


def test_corrupt_legacy_staged_file_retains_old_valid_version(tmp_path,page):
    d=legacy(tmp_path,'PendingCharacter');old=read_metadata(d)['items'][0]
    (d/'audio'/'001.mp3.part').write_bytes(b'bad staging')
    run_selection(d,page,[0]);new=read_metadata(d)['items'][0]
    assert new['sha256']==old['sha256'] and new['texts']==old['texts']
    assert (d/'audio'/'001.mp3').read_bytes()==mp3()

class Crash(RuntimeError):pass

@pytest.mark.parametrize('point',['after_backup','before_migration_write','before_migration_replace','after_migration_replace'])
def test_migration_fault_checkpoints_are_restartable(tmp_path,page,point):
    d=legacy(tmp_path);before=(d/'metadata.json').read_bytes()
    def checkpoint(name):
        if name==point:raise Crash(point)
    with pytest.raises(Crash):run_selection(d,page,[0],checkpoint=checkpoint)
    during=read_metadata(d)
    assert during['schema_version']==(2 if point=='after_migration_replace' else 1)
    if point!='after_migration_replace':assert (d/'metadata.json').read_bytes()==before
    result,_=run_selection(d,page,[0]);data=read_metadata(d)
    assert data['next_id']==4 and len(data['items'])==3 and result.cumulative==3


def test_backup_failure_never_replaces_old_schema(tmp_path,page,monkeypatch):
    d=legacy(tmp_path);before=(d/'metadata.json').read_bytes();original=Path.open
    def no_backup(self,*args,**kwargs):
        if '.wvd_backups' in self.parts and args and args[0]=='xb':raise PermissionError('injected')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',no_backup)
    with pytest.raises(StorageError,match='backup failed'):run_selection(d,page,[0])
    assert (d/'metadata.json').read_bytes()==before and read_metadata(d)['schema_version']==1

@pytest.mark.parametrize('change',['unknown_schema','broken_id','unknown_journal'])
def test_invalid_old_data_never_migrates_or_resets(tmp_path,page,change):
    d=legacy(tmp_path);data=read_metadata(d)
    if change=='unknown_schema':data['schema_version']=900
    elif change=='broken_id':data['items'][0]['filename']='../../evil.mp3'
    else:data['items'][0]['pending_commit']={'mystery':{}}
    (d/'metadata.json').write_text(json.dumps(data),encoding='utf-8');before=snapshots(d)
    with pytest.raises(StorageError):run_selection(d,page,[0])
    # A lock may be created; existing dataset bytes are never replaced.
    assert all(p.exists() and p.read_bytes()==blob for p,(blob,_) in ((d/path,v) for path,v in before.items()))
    assert not (d/'.wvd_backups').exists()


def test_unknown_extension_fields_preserved_and_upgrade_in_memory_is_read_only(tmp_path):
    d=legacy(tmp_path);data=read_metadata(d);before=snapshots(d)
    data['custom_diagnostic']={'note':'keep me'};data['items'][0]['custom_flag']='keep too'
    new=upgrade_data(data)
    assert data['schema_version']==1 and new['schema_version']==2
    assert new['custom_diagnostic']==data['custom_diagnostic'] and new['items'][0]['custom_flag']=='keep too'
    assert snapshots(d)==before
    assert upgrade_data(new)==new

@pytest.mark.parametrize('mutate',[
    lambda d:d['export_views']['legacy/*']['members']['1'].update(preferred_reference='missing'),
    lambda d:d['items'][0]['references']['legacy'].update(section_id='jp'),
    lambda d:d['items'][0].update(filename='001.ogg'),
    lambda d:d.update(next_id=1),
    lambda d:d['items'].append(copy.deepcopy(d['items'][0])),
])
def test_v2_invariants_reject_cross_reference_and_format_corruption(tmp_path,mutate):
    d=legacy(tmp_path);data=upgrade_data(read_metadata(d));mutate(data)
    with pytest.raises(StorageError):validate_metadata(data)
