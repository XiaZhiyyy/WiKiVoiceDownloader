"""Trusted local JSON profiles; strategies are static, never dynamically imported."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import soupsieve
from .config import application_dir
from .errors import ConfigError

STRATEGIES = frozenset({'biligame_blhx', 'koumakan_quotes'})

@dataclass(frozen=True)
class SiteProfile:
    data: dict
    fingerprint: str
    origin: str

    @property
    def site_id(self):
        return self.data['site_id']

    @property
    def parser_id(self):
        return self.data['parser_id']


def _fail(field, detail):
    raise ConfigError(f'profile.{field}: {detail}')


def _host(value, field):
    if (not isinstance(value, str) or value != value.lower()
        or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,63}', value)
        or value.endswith('.local') or len(value) > 253):
        _fail(field, 'expected an exact public hostname (no wildcard, port or scheme)')


def validate_profile(data: dict) -> None:
    if not isinstance(data, dict):
        _fail('', 'expected an object')
    required = {'profile_schema_version','site_id','parser_id','strategy','page',
                'audio_sources','selectors','columns','server_defaults','media_formats'}
    optional = {'display_name','column_headers'}
    if required - data.keys():
        _fail('', 'missing fields: ' + ', '.join(sorted(required-data.keys())))
    if data.keys() - required - optional:
        _fail('', 'unknown fields: ' + ', '.join(sorted(data.keys()-required-optional)))
    if type(data['profile_schema_version']) is not int or data['profile_schema_version'] != 1:
        _fail('profile_schema_version', 'only version 1 is supported')
    for key in ('site_id','parser_id'):
        if not isinstance(data[key], str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,63}',data[key]):
            _fail(key,'expected a stable lowercase identifier')
    if not isinstance(data['strategy'], str) or data['strategy'] not in STRATEGIES:
        _fail('strategy','unknown registered strategy')
    if 'display_name' in data and (not isinstance(data['display_name'], str) or not data['display_name'].strip()):
        _fail('display_name', 'expected a nonempty string')
    page=data['page']
    if not isinstance(page,dict) or set(page) != {'hosts','path_pattern'}:
        _fail('page','expected hosts and path_pattern')
    if not isinstance(page['hosts'],list) or not page['hosts']:
        _fail('page.hosts','expected a nonempty list')
    for host in page['hosts']: _host(host,'page.hosts')
    pattern=page['path_pattern']
    try:
        if not isinstance(pattern,str) or len(pattern)>512 or not pattern.startswith('^') or not pattern.endswith('$'):
            raise ValueError
        re.compile(pattern)
    except (ValueError,re.error): _fail('page.path_pattern','expected a bounded anchored regular expression')
    if not isinstance(data['audio_sources'],list) or not data['audio_sources']:
        _fail('audio_sources','expected a nonempty list')
    for i,source in enumerate(data['audio_sources']):
        if not isinstance(source,dict) or set(source)!={'host','path_prefix'}:
            _fail(f'audio_sources[{i}]','expected host and path_prefix')
        _host(source['host'],f'audio_sources[{i}].host')
        prefix=source['path_prefix']
        if (not isinstance(prefix,str) or not prefix.startswith('/') or not prefix.endswith('/')
            or '..' in prefix or '\\' in prefix or '?' in prefix or '#' in prefix or '%' in prefix):
            _fail(f'audio_sources[{i}].path_prefix','expected a literal absolute directory prefix')
    selectors=data['selectors']
    required_sel={'quote_table','audio_link','language_node','ignore_nodes'}
    allowed_sel=required_sel | {'server_panel','group_heading','row'}
    if data['strategy']=='koumakan_quotes': required_sel=allowed_sel
    if not isinstance(selectors,dict) or required_sel-selectors.keys() or selectors.keys()-allowed_sel:
        _fail('selectors','missing or unknown selector fields')
    for key,value in selectors.items():
        values=value if key=='ignore_nodes' else [value]
        if not isinstance(values,list): _fail(f'selectors.{key}','expected a list')
        for css in values:
            try:
                if not isinstance(css,str) or not css.strip() or len(css)>2048: raise ValueError
                soupsieve.compile(css)
            except (ValueError,TypeError,soupsieve.SelectorSyntaxError):
                _fail(f'selectors.{key}','invalid CSS selector')
    columns=data['columns']
    if not isinstance(columns,dict): _fail('columns','expected an object')
    if data['strategy']=='koumakan_quotes' and set(columns)!={'event','audio','transcription'}:
        _fail('columns','expected event/audio/transcription indices')
    if any(type(v) is not int or not 0<=v<=15 for v in columns.values()) or len(set(columns.values()))!=len(columns):
        _fail('columns','indices must be distinct integers from 0 through 15')
    if 'column_headers' in data:
        headers=data['column_headers']
        if not isinstance(headers,dict) or set(headers)!=set(columns): _fail('column_headers','must match columns')
        for key,values in headers.items():
            if not isinstance(values,list) or not values or not all(isinstance(v,str) and v.strip() for v in values):
                _fail('column_headers.'+key,'expected nonempty label list')
    servers=data['server_defaults']
    if not isinstance(servers,dict): _fail('server_defaults','expected an object')
    ids=set()
    for name,server in servers.items():
        if (not isinstance(name,str) or not name or not isinstance(server,dict)
            or set(server)!={'id','text_language'}): _fail('server_defaults','invalid server description')
        for key in ('id','text_language'):
            if not isinstance(server[key],str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',server[key]):
                _fail(f'server_defaults.{name}.{key}','invalid identifier')
        if server['id'] in ids: _fail('server_defaults','duplicate section id')
        ids.add(server['id'])
    if data['strategy']=='koumakan_quotes' and not servers: _fail('server_defaults','must not be empty')
    formats=data['media_formats']
    if not isinstance(formats,list) or not formats or any(not isinstance(v, str) or v not in {'mp3','ogg'} for v in formats):
        _fail('media_formats','supported values are mp3 and ogg')


def profile_from_dict(data: dict, origin='in-memory') -> SiteProfile:
    validate_profile(data)
    raw=json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return SiteProfile(json.loads(raw),hashlib.sha256(raw).hexdigest(),origin)


def _pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out: _fail(key,'duplicate JSON field')
        out[key]=value
    return out


def read_profile(path: Path) -> SiteProfile:
    try:
        if path.stat().st_size>256*1024: _fail(str(path.name),'exceeds 256 KiB')
        return profile_from_dict(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs),str(path))
    except (OSError,UnicodeError,ValueError,TypeError) as exc:
        raise ConfigError(f'profile {path.name}: cannot load ({type(exc).__name__})') from None


def builtin_directory() -> Path:
    # __file__ is also the bundled module location in PyInstaller; never write here.
    return Path(__file__).resolve().parent/'site_profiles'


def load_profiles(override_dir: str | Path | None=None, app_dir: Path | None=None) -> list[SiteProfile]:
    profiles={p.site_id:p for p in (read_profile(f) for f in sorted(builtin_directory().glob('*.json')))}
    if not profiles: raise ConfigError('profile: bundled JSON resources are missing')
    if override_dir is not None:
        path=Path(override_dir).expanduser()
        if not path.is_absolute(): path=(app_dir or application_dir())/path
        if not path.is_dir(): raise ConfigError('site_profile_dir: directory does not exist')
        seen=set()
        for file in sorted(path.glob('*.json')):
            p=read_profile(file)
            if p.site_id in seen: _fail('site_id','duplicate external profile')
            seen.add(p.site_id); profiles[p.site_id]=p
    parser_ids=[p.parser_id for p in profiles.values()]
    if len(parser_ids)!=len(set(parser_ids)): _fail('parser_id','must be unique across profiles')
    return list(profiles.values())


def builtin_profile(site_id: str) -> SiteProfile:
    return read_profile(builtin_directory()/(site_id+'.json'))
