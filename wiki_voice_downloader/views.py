"""Media-independent, provenance-scoped references and cumulative export views."""
from __future__ import annotations
import copy
from dataclasses import asdict
import hashlib
import json
import logging
from .models import VoiceCandidate
from .text_format import render_text

log=logging.getLogger(__name__)
STATUSES={'present','missing','ambiguous','missing_target_language','ambiguous_pairing'}

def view_key(section_id='legacy',language=None):
    return section_id+'/'+(language or '*')


def reference_from_candidate(candidate: VoiceCandidate) -> dict:
    # Legacy keeps a primary compatibility reference, matching v1.0 semantics.
    identity=[candidate.section_id,candidate.group_id,candidate.occurrence_locator,
              candidate.source_url]
    rid=('legacy' if candidate.section_id=='legacy' else
         'ref-'+hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24])
    return {'reference_id':rid,'section_id':candidate.section_id,
            'group_id':candidate.group_id,'group_name':candidate.group_name,
            'category':candidate.category,'texts':[asdict(t) for t in candidate.texts],
            'text_status':candidate.text_status,'occurrence_locator':candidate.occurrence_locator,
            'source_url':candidate.source_url,'diagnostics':list(candidate.diagnostics)}


def candidate_references(candidate):
    return copy.deepcopy(candidate.references or [reference_from_candidate(candidate)])


def legacy_reference(item):
    return {'reference_id':'legacy','section_id':'legacy',
            **{key:copy.deepcopy(item[key]) for key in ('group_id','group_name','category','texts','text_status','source_url')},
            'occurrence_locator':'schema1:'+str(item['id']),
            'diagnostics':copy.deepcopy(item.get('diagnostics',[]))}


def merge_references(item,candidate,*,overwrite=False,source_page_url=None):
    """Return added reference/language counts; normal mode retains changed values."""
    added=languages=0
    for incoming in candidate_references(candidate):
        rid=incoming['reference_id']
        if source_page_url is not None: incoming['source_page_url']=source_page_url
        existing=item['references'].get(rid)
        if existing is None:
            item['references'][rid]=incoming; added+=1; languages+=len(incoming['texts'])
            continue
        if overwrite:
            # Only this selected reference is replaced, not another server's text.
            item['references'][rid]=incoming
            continue
        old_languages={t['language']:t['text'] for t in existing['texts'] if t['text'].strip()}
        for text in incoming['texts']:
            if not text['text'].strip(): continue
            lang=text['language']
            if lang not in old_languages:
                existing['texts']=[t for t in existing['texts'] if t['language']!=lang or t['text'].strip()]
                existing['texts'].append(copy.deepcopy(text)); old_languages[lang]=text['text']; languages+=1
            elif old_languages[lang]!=text['text']:
                message='remote_text_changed: retained previous '+str(lang)+' in '+rid
                if message not in existing['diagnostics']: existing['diagnostics'].append(message)
                log.warning('%s id=%s',message,item['id'])
        target=candidate.text_language
        if target is None:
            if existing['texts']: existing['text_status']='present'
        elif any(t['language']==target and t['text'].strip() for t in existing['texts']):
            existing['text_status']='present'
        if not existing['texts'] and incoming['text_status'] in {'ambiguous','ambiguous_pairing'}:
            existing['text_status']=incoming['text_status']
    return added,languages


def choose_body(item,member,language):
    """Only selected references in this view can fill a missing target body."""
    primary=item['references'][member['preferred_reference']]
    refs=[primary]+[item['references'][rid] for rid in member['references'] if rid!=member['preferred_reference']]
    if language is None:
        return render_text(primary['texts'])
    for ref in refs:
        # Ambiguity about another language must not discard a reliable target.
        values=list(dict.fromkeys(t['text'] for t in ref['texts']
                    if t['language']==language and t['text'].strip()))
        if len(values)==1 and ref['text_status'] not in {'ambiguous','ambiguous_pairing'}:
            return values[0]
    return '[\u65e0\u53f0\u8bcd]'


def validate_reference(ref,rid,source_url):
    if not isinstance(ref,dict) or ref.get('reference_id')!=rid: raise ValueError('invalid reference id')
    for key in ('section_id','group_id','group_name','category'):
        if not isinstance(ref.get(key),str) or not ref[key]: raise ValueError('invalid reference '+key)
    if ref.get('source_url')!=source_url: raise ValueError('reference source disagrees with media')
    if not isinstance(ref.get('occurrence_locator'),str): raise ValueError('invalid occurrence locator')
    if ref.get('text_status') not in STATUSES: raise ValueError('invalid reference text status')
    if not isinstance(ref.get('texts'),list): raise ValueError('invalid reference texts')
    for text in ref['texts']:
        if not isinstance(text,dict) or set(text)!={'language','text'} or not isinstance(text['text'],str) or (text['language'] is not None and not isinstance(text['language'],str)):
            raise ValueError('invalid reference language variant')
    if not isinstance(ref.get('diagnostics'),list) or not all(isinstance(v,str) for v in ref['diagnostics']):
        raise ValueError('invalid reference diagnostics')


def validate_views(data):
    views=data.get('export_views')
    if not isinstance(views,dict) or not views or data.get('active_export_view') not in views:
        raise ValueError('invalid active export view')
    if data.get('requested_export_view', data['active_export_view']) not in views:
        raise ValueError('invalid requested export view')
    items={str(i['id']):i for i in data['items']}
    for key,view in views.items():
        if not isinstance(view,dict) or not isinstance(view.get('section_id'),str): raise ValueError('invalid export section')
        lang=view.get('text_language')
        if lang is not None and (not isinstance(lang,str) or not lang): raise ValueError('invalid export language')
        if key!=view_key(view['section_id'],lang): raise ValueError('export view key mismatch')
        members=view.get('members')
        if not isinstance(members,dict): raise ValueError('invalid view members')
        for number,member in members.items():
            if number not in items or not isinstance(member,dict): raise ValueError('unknown view media id')
            refs=items[number]['references']; ids=member.get('references')
            if (not isinstance(ids,list) or not ids or not all(isinstance(r,str) for r in ids)
                or len(ids)!=len(set(ids)) or member.get('preferred_reference') not in ids):
                raise ValueError('invalid preferred reference')
            for rid in ids:
                if rid not in refs or refs[rid]['section_id']!=view['section_id']:
                    raise ValueError('cross-section or dangling export reference')
