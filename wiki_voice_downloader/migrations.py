"""Schema 1 -> 2 conversion; every write happens under the existing directory lock.

Old journals are converted, not discarded; Store.recover subsequently checks the
old MP3/new MP3 hashes using the preserved parent/new record semantics.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import uuid
from .errors import StorageError
from .models import utc_now
from .views import legacy_reference


def upgrade_data(old: dict) -> dict:
    from .storage import validate_metadata
    validate_metadata(old)
    if old['schema_version']==2: return copy.deepcopy(old)
    data=copy.deepcopy(old)
    def item_upgrade(item):
        item['media_format']='mp3' if item['downloaded_at'] else None
        item['format_hint']='mp3'
        item['codec']='mpeg-layer-iii' if item['downloaded_at'] else None
        item['validation_level']='frame-boundaries' if item['downloaded_at'] else None
        item['references']={'legacy':legacy_reference(item)}
        if 'pending_commit' in item: item_upgrade(item['pending_commit']['new'])
    for item in data['items']: item_upgrade(item)
    data['schema_version']=2
    data['site_id']=data['parser_id']
    data['export_views']={'legacy/*':{'section_id':'legacy','text_language':None,
        'members':{str(i['id']):{'preferred_reference':'legacy','references':['legacy']} for i in data['items']}}}
    data['active_export_view']='legacy/*'
    validate_metadata(data)
    return data


def backup_files(directory: Path, filenames: list[str], label: str, checkpoint=lambda _:None) -> Path:
    from .storage import ensure_plain, fsync_directory, validate_directory_path
    validate_directory_path(directory)
    root=directory/'.wvd_backups'; ensure_plain(root)
    paths=[]
    for filename in filenames:
        if Path(filename).name!=filename: raise StorageError('storage_conflict: unsafe backup source')
        source=directory/filename; ensure_plain(source)
        if source.exists():
            if not source.is_file(): raise StorageError('storage_conflict: backup source is not a regular file')
            paths.append(source)
    try:
        root.mkdir(exist_ok=True)
        stamp=utc_now().replace(':','').replace('+','_')
        destination=root/(label+'_'+stamp+'_'+uuid.uuid4().hex[:12]); destination.mkdir()
        for source in paths:
            # Exclusive creation: an existing backup is never overwritten.
            with source.open('rb') as reader, (destination/source.name).open('xb') as writer:
                import shutil, os
                shutil.copyfileobj(reader,writer,1024*1024); writer.flush(); os.fsync(writer.fileno())
        fsync_directory(destination); fsync_directory(root)
        checkpoint('after_backup')
        return destination
    except OSError as exc:
        raise StorageError(f'migration_failed: backup failed ({type(exc).__name__}); original files preserved') from None


def migrate_confirmed(directory: Path,data: dict,checkpoint=lambda _:None) -> dict:
    if data['schema_version']==2: return data
    from .storage import validate_metadata, atomic_write
    import json
    validate_metadata(data)
    new=upgrade_data(data)
    backup=backup_files(directory,['metadata.json',data['txt_filename']],'schema1',checkpoint)
    new['migration']={'from_schema':1,'migrated_at':utc_now(),
        'backup_directory':str(backup.relative_to(directory)),
        'original_metadata_sha256':hashlib.sha256((directory/'metadata.json').read_bytes()).hexdigest(),
        'note':'metadata and TXT backup only; not an audio snapshot; legacy commit journal preserved'}
    validate_metadata(new)
    checkpoint('before_migration_write')
    raw=(json.dumps(new,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
    atomic_write(directory/'metadata.json',raw,checkpoint,'before_migration_replace')
    checkpoint('after_migration_replace')
    return new
