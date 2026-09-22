"""Pure, server-scoped Quotes parser. Only a configured, observed layout is accepted.

The bundled panel selector comes from the development contract, not a live HTTP
capture. No JavaScript, guessed API, transclusion or whole-page audio fallback.
"""
from __future__ import annotations
import copy
import hashlib
from urllib.parse import urlsplit, unquote
from bs4 import BeautifulSoup, Tag, NavigableString
import soupsieve
from ..errors import AppError, ParseError
from ..models import PageInfo, ParsedPage, VoiceGroup, VoiceSection, VoiceCandidate, TextVariant
from ..profile_loader import builtin_profile
from ..site_policy import SitePolicy
from ..text_format import extract_text, normalize_line
from ..urls import request_url

class KoumakanAzurLaneParser:
    parser_id='koumakan_azurlane'
    display_name='Koumakan Azur Lane Quotes'

    def __init__(self, profile=None):
        self.profile=profile or builtin_profile('koumakan_azurlane')
        self.parser_id=self.profile.parser_id
        self.display_name=self.profile.data.get('display_name',self.display_name)
        self.policy=SitePolicy(self.profile)
        self.selectors=self.profile.data['selectors']

    def supports(self,url):
        return self.policy.supports(url)

    def fragment_section(self, fragment):
        hint=unquote(fragment).removeprefix('tabber-').replace('_',' ')
        for label,server in self.profile.data['server_defaults'].items():
            if hint in {label,server['id']}: return server['id']
        return None

    def _clean(self,node):
        clone=copy.deepcopy(node)
        for selector in self.selectors['ignore_nodes']:
            for ignored in clone.select(selector): ignored.decompose()
        return clone

    def _texts(self,cell):
        cell=self._clean(cell)
        nodes=cell.select(self.selectors['language_node'])
        def language(node):
            return normalize_line(str(node.get('lang') or node.get('data-lang') or '')).lower() or None
        node_ids = {id(n) for n in nodes}
        # Keep nested translations as separate variants; exclude them from the
        # parent's body instead of leaking English into a Japanese span.
        nodes=[n for n in nodes if not any(id(p) in node_ids and language(p)==language(n)
               for p in n.parents if isinstance(p,Tag))]
        def body(node):
            lang = language(node)
            clone = copy.deepcopy(node)
            for nested in clone.select(self.selectors['language_node']):
                if nested.parent is not None and language(nested) != lang:
                    nested.decompose()
            return extract_text(clone)
        by_lang={}
        for node in nodes:
            lang=language(node)
            if body(node): by_lang.setdefault(lang,[]).append(node)
        values=[]; ambiguous=[]
        for lang,parts in by_lang.items():
            distinct=list(dict.fromkeys(body(n) for n in parts))
            if len(distinct)==1:
                values.append(TextVariant(lang,distinct[0])); continue
            # Only immediately adjacent inline fragments of one language are
            # a continuous body. Separate paragraphs/versions are ambiguous.
            continuous=all(n.name in {'span','em','strong','b','i'} and n.parent is parts[0].parent for n in parts)
            joined=body(parts[0])
            if continuous:
                for left,right in zip(parts,parts[1:]):
                    between=''; current=left.next_sibling
                    while current is not None and current is not right:
                        if not isinstance(current,NavigableString) or str(current).strip():
                            continuous=False; break
                        between+=str(current); current=current.next_sibling
                    if current is not right: continuous=False
                    if not continuous: break
                    joined+=between+body(right)
            if continuous: values.append(TextVariant(lang,normalize_line(joined)))
            else: ambiguous.append(lang or 'unknown')
        if not nodes:
            text=extract_text(cell)
            if text: values.append(TextVariant(None,text))
        return values,ambiguous

    def _server(self,panel,soup):
        labels=[]
        for attr in ('id','title','data-title','data-tab-title','aria-label'):
            value=panel.get(attr)
            if value: labels.append(unquote(str(value)).removeprefix('tabber-').replace('_',' '))
        for ident in str(panel.get('aria-labelledby','')).split():
            label=soup.find(id=ident)
            if label: labels.append(extract_text(label))
        matches=[]
        for name,server in self.profile.data['server_defaults'].items():
            if name in labels: matches.append((name,server))
        return matches[0] if len(matches)==1 else None

    def parse(self,html,page_url):
        if not self.supports(page_url): raise ParseError('unsupported_site: not a supported character Quotes URL','unsupported_site')
        soup=BeautifulSoup(html,'html.parser')
        from ..access_detection import classify_access
        decision = classify_access(html, has_content=bool(soup.select(self.selectors['quote_table'])))
        if decision.stop_current_job:
            raise ParseError(decision.diagnostic_message, decision.kind)
        panels=soup.select(self.selectors['server_panel'])
        panels=[p for p in panels if not any(isinstance(q,Tag) and soupsieve.match(self.selectors['server_panel'],q) for q in p.parents)]
        if not panels: raise ParseError('unsupported_layout: no configured server panels; raw HTTP layout has not been verified','unsupported_layout')
        sections=[]; warnings=[]; order=0; skipped=0
        known_sections=set()
        cols=self.profile.data['columns']; width=max(cols.values())+1
        headers=self.profile.data.get('column_headers',{'event':['Event'],'audio':['VO'],'transcription':['Transcription']})
        for panel in panels:
            found=self._server(panel,soup)
            if not found:
                warnings.append('unsupported_layout: unrecognized server panel was not merged'); continue
            label,server=found; sid=server['id']; lang=server['text_language']
            if sid in known_sections: raise ParseError('ambiguous_pairing: duplicate server roots','ambiguous_pairing')
            known_sections.add(sid); section=VoiceSection(sid,label,lang,[]); sections.append(section)
            current=None; heading_counts={}; table_index=0
            selector=self.selectors['group_heading']+', '+self.selectors['quote_table']
            for node in panel.select(selector):
                # Ignore nested panels and nested tables as independent layouts.
                ancestors=list(node.parents)
                nearest=next((p for p in ancestors if isinstance(p,Tag) and soupsieve.match(self.selectors['server_panel'],p)),None)
                if nearest is not panel: continue
                if node.find_parent('table') is not None: continue
                if soupsieve.match(self.selectors['group_heading'],node):
                    title=extract_text(self._clean(node)) or 'Unlabelled group'
                    headline=node.find(id=True)
                    anchor=str(node.get('id') or (headline.get('id') if headline else '') or '')
                    base=anchor or 'heading-'+hashlib.sha256(title.encode()).hexdigest()[:16]
                    count=heading_counts.get(base,0)+1; heading_counts[base]=count
                    if not anchor or count>1: warnings.append(f'group_locator_fallback: {sid}/{base}/{count}')
                    gid=f'{sid}:{base}'+(f':{count}' if count>1 else '')
                    current=VoiceGroup(gid,title,len(section.groups)); section.groups.append(current)
                    continue
                table_index+=1
                if current is None:
                    warnings.append(f'unsupported_layout: {sid} table {table_index} has no local group heading')
                    current=VoiceGroup(f'{sid}:unlabelled-{table_index}',f'Unlabelled group {table_index}',len(section.groups))
                    section.groups.append(current)
                rows=[r for r in node.select(self.selectors['row']) if r.name=='tr' and r.find_parent('table') is node]
                header_ok=False; events={}
                for row_index,row in enumerate(rows):
                    cells=row.find_all(['td','th'],recursive=False)
                    if len(cells)!=width or any(str(c.get('colspan','1'))!='1' or str(c.get('rowspan','1'))!='1' for c in cells):
                        if row.select(self.selectors['audio_link']): warnings.append(f'unsupported_layout: {sid}/{current.group_id} row {row_index} has invalid columns')
                        continue
                    if all(extract_text(self._clean(cells[index])).casefold() in {x.casefold() for x in headers[key]} for key,index in cols.items()):
                        header_ok=True; continue
                    if not header_ok:
                        continue
                    audio=cells[cols['audio']]; transcript=cells[cols['transcription']]
                    links=[a for a in audio.select(self.selectors['audio_link']) if a.find_parent('tr') is row and a.find_parent('table') is node]
                    if not links:
                        if extract_text(transcript): skipped+=1
                        continue
                    sources={}
                    for link in links:
                        try:
                            raw = str(link.get('href') or link.get('src') or '')
                            if not urlsplit(raw).scheme and not raw.startswith(('/', '//')) and '_files/' in raw.replace('\\', '/'):
                                raise AppError('snapshot_local_resource: browser rewrote a media URL; re-save body with original remote links')
                            url=self.policy.check(request_url(raw,page_url),'audio')
                            sources.setdefault(url,[]).append(link)
                        except AppError as exc: warnings.append(str(exc))
                    category=extract_text(self._clean(cells[cols['event']])) or 'Uncategorized'
                    events[category]=events.get(category,0)+1
                    for url,source_links in sources.items():
                        diagnostics=[]; target=transcript
                        if len(sources)>1:
                            # Controlled explicit one-to-one child keys, not positional zip.
                            keys=set()
                            for link in source_links:
                                for ancestor in [link,*link.parents]:
                                    if ancestor is audio: break
                                    if isinstance(ancestor,Tag) and ancestor.get('data-quote-id'):
                                        keys.add(str(ancestor['data-quote-id'])); break
                            targets=[t for t in transcript.select('[data-quote-id]') if str(t['data-quote-id']) in keys]
                            all_keys=[]
                            for grouped in sources.values():
                                key=None
                                for ancestor in [grouped[0],*grouped[0].parents]:
                                    if ancestor is audio: break
                                    if isinstance(ancestor,Tag) and ancestor.get('data-quote-id'): key=str(ancestor['data-quote-id']); break
                                all_keys.append(key)
                            if len(keys)==1 and len(targets)==1 and None not in all_keys and len(set(all_keys))==len(sources): target=targets[0]
                            else: target=None; diagnostics.append('ambiguous_pairing: multiple audio sources without unique child keys')
                        texts,ambiguous=self._texts(target) if target is not None else ([],[lang])
                        for bad in ambiguous: diagnostics.append('ambiguous_pairing: language '+bad)
                        present=any(t.language==lang and t.text for t in texts)
                        if lang in ambiguous or target is None: status='ambiguous_pairing'
                        elif not present: status='missing_target_language'; diagnostics.append('missing_target_language: '+lang)
                        else: status='present'
                        suffix=urlsplit(url).path.rsplit('.',1)[-1].lower()
                        candidate=VoiceCandidate(url,current.group_id,current.name,category,texts,status,order,diagnostics,
                            section_id=sid,text_language=lang,
                            occurrence_locator=f'{current.group_id}/event:{category}/occurrence:{events[category]}',
                            format_hint=suffix if suffix in {'mp3','ogg'} else None)
                        current.candidates.append(candidate); warnings.extend(diagnostics); order+=1
                if not header_ok: warnings.append(f'unsupported_layout: {sid} table {table_index} lacks the configured header')
            section.groups=[g for g in section.groups if g.candidates]
        if not sections: raise ParseError('missing_section: no recognized server sections','missing_section')
        path=unquote(urlsplit(request_url(page_url)).path).rstrip('/')
        name=path.removeprefix('/wiki/').removesuffix('/Quotes').replace('_',' ')
        heading=soup.select_one('#firstHeading') or soup.find('h1')
        heading_name=extract_text(self._clean(heading)).removesuffix('/Quotes').strip() if heading else ''
        names=list(dict.fromkeys([name]+([heading_name] if heading_name and heading_name!=name else [])))
        identity=f'{self.profile.site_id}:url:{urlsplit(page_url).hostname}{path}'
        info=PageInfo(name,names,page_url,page_url,identity,identity_aliases=[identity],name_reliable=len(names)==1)
        # Groups are intentionally empty until one server is explicitly selected.
        return ParsedPage(info,[],list(dict.fromkeys(warnings)),skipped,sections=sections,
                          profile_fingerprint=self.profile.fingerprint,profile_id=self.profile.site_id)
