import copy
import json
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from wiki_voice_downloader.models import PageRequestContext
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser
from wiki_voice_downloader.profile_loader import builtin_profile, profile_from_dict, load_profiles
from wiki_voice_downloader.section_selection import select_section
from wiki_voice_downloader.selection import selected_candidates
from wiki_voice_downloader.errors import ConfigError, ParseError
from .koumakan_helpers import make_html,parsed,PAGE,source


def test_three_sections_groups_hidden_rows_and_language_provenance():
    parser=KoumakanAzurLaneParser(); raw=parser.parse(make_html(),PAGE)
    assert [s.section_id for s in raw.sections]==['en','cn','jp']
    assert raw.groups==[]  # do not deduplicate all servers before selection
    p=parsed(); assert [g.unique_count for g in p.groups]==[3,2,1,2]
    candidates,duplicates,raw_count=selected_candidates(p.groups,[3,0,2,1])
    assert len(candidates)==8 and duplicates==0 and raw_count==8
    assert candidates[0].source_url==source(1)
    assert candidates[0].texts[0].text=='\u65e5\u65871 Zzzz 123 (abc) A|B\\C &lt;'
    assert candidates[0].texts[1].text=='JP translation 1'
    assert candidates[1].source_url==source(2)  # hidden until found is retained
    assert len({c.group_id for c in candidates})==4


def test_fragment_and_explicit_priority_ignore_aria_state():
    parser=KoumakanAzurLaneParser(); p=parser.parse(make_html(),PAGE); out=[]
    ctx=PageRequestContext(PAGE+'#tabber-Japanese_Server',PAGE,fragment_hint='tabber-Japanese_Server')
    select_section(p,parser,ctx,lambda _: (_ for _ in ()).throw(AssertionError('must not ask')),out.append)
    assert p.selected_section=='jp'
    ctx.explicit_server='en'; select_section(p,parser,ctx,lambda _:None,out.append)
    assert p.selected_section=='en' and any('--server' in s for s in out)

@pytest.mark.parametrize('fragment',['','unknown-fragment'])
def test_no_known_hint_interactively_selects_one_server(fragment):
    parser=KoumakanAzurLaneParser(); p=parser.parse(make_html(),PAGE)
    answers=iter(['A','3'])
    select_section(p,parser,PageRequestContext(PAGE,PAGE,fragment_hint=fragment),lambda _:next(answers),lambda _:None)
    assert p.selected_section=='jp'

@pytest.mark.parametrize('html',[make_html(servers=('en',)),make_html(counts=(0,))])
def test_missing_or_empty_requested_section_never_falls_back(html):
    with pytest.raises(ParseError,match='missing_section'): parsed(html)


def test_missing_ja_does_not_mislabel_english():
    soup=BeautifulSoup(make_html(),'html.parser')
    jp=soup.select_one('#tabber-Japanese_Server')
    for n in jp.select('span[lang="ja"]'): n.decompose()
    p=parsed(str(soup)); c=p.groups[0].candidates[0]
    assert c.text_status=='missing_target_language'
    assert [t.language for t in c.texts]==['en']


def test_unknown_language_saved_as_unknown():
    html=make_html().replace('lang="ja"','class="unmarked"')
    p=parsed(html)
    # Annotated English remains English; no inferred Japanese.
    assert all(t.language!='ja' for t in p.groups[0].candidates[0].texts)
    html=make_html(servers=('jp',)).replace('<span lang="en">JP translation 1</span>','').replace('lang="ja"','class="unmarked"')
    assert parsed(html).groups[0].candidates[0].texts[0].language is None


def test_duplicate_url_refs_keep_server_group_context():
    html=make_html().replace(source(4),source(1))
    p=parsed(html); candidates,duplicates,_=selected_candidates(p.groups,[0,1])
    assert duplicates==1 and len(candidates)==4
    assert len(candidates[0].references)==2
    assert candidates[0].references[0]['group_id']!=candidates[0].references[1]['group_id']
    assert all(r['section_id']=='jp' for r in candidates[0].references)


def test_same_header_ids_across_servers_are_namespaced():
    p=KoumakanAzurLaneParser().parse(make_html(),PAGE)
    ids=[g.group_id for s in p.sections for g in s.groups]
    assert len(ids)==len(set(ids))


def test_missing_heading_does_not_steal_heading_from_previous_server():
    soup=BeautifulSoup(make_html(),'html.parser')
    soup.select_one('#tabber-Japanese_Server h3').decompose()
    p=parsed(str(soup))
    assert p.groups[0].name.startswith('Unlabelled')
    assert p.groups[0].group_id.startswith('jp:')


def test_wrong_columns_rejected_and_profile_only_change_handles_layout(tmp_path):
    html=make_html(); soup=BeautifulSoup(html,'html.parser')
    for a in soup.select('a.sm2_button'): a['class']=['new_play']
    for table in soup.select('table.alshipquote'):
        table['class']=['new_table']
        for row in table.select('tr'):
            cells=row.find_all('td',recursive=False)
            if len(cells)==3:
                for c in cells: c.extract()
                row.extend([cells[2],cells[0],cells[1]])
    with pytest.raises(ParseError): parsed(str(soup))
    data=copy.deepcopy(builtin_profile('koumakan_azurlane').data)
    data['selectors']['audio_link']='a.new_play[href]'; data['selectors']['quote_table']='table.new_table'
    data['columns']={'event':1,'audio':2,'transcription':0}
    profile=profile_from_dict(data)
    before=parsed(html); after=parsed(str(soup),profile=profile)
    def fingerprint(page): return [(c.source_url,c.category,c.texts) for g in page.groups for c in g.candidates]
    assert fingerprint(before)==fingerprint(after)
    (tmp_path/'override.json').write_text(json.dumps(data),encoding='utf-8')
    loaded=load_profiles(tmp_path)
    assert next(p for p in loaded if p.site_id=='koumakan_azurlane').fingerprint==profile.fingerprint

@pytest.mark.parametrize('field,value',[
    ('strategy','os.system'),('profile_schema_version',99),
    ('selectors.audio_link','a['),('columns.audio',True),('columns.audio',-1),
    ('columns.audio',0),('audio_sources',[{'host':'*.example.com','path_prefix':'/'}]),
    ('media_formats',['wav']),('page.path_pattern','['),
])
def test_invalid_profile_precise_failure(field,value):
    data=copy.deepcopy(builtin_profile('koumakan_azurlane').data)
    parts=field.split('.'); target=data
    for p in parts[:-1]: target=target[p]
    target[parts[-1]]=value
    with pytest.raises(ConfigError,match='profile'): profile_from_dict(data)


def test_explicit_child_mapping_vs_ambiguous_multiple_audio():
    html=make_html(counts=(1,),servers=('jp',)); soup=BeautifulSoup(html,'html.parser')
    row=soup.select('table tr')[1]; cells=row.find_all('td',recursive=False)
    cells[1].clear(); cells[2].clear()
    for i in (1,2):
        audio=soup.new_tag('div',attrs={'data-quote-id':str(i)})
        a=soup.new_tag('a',attrs={'class':'sm2_button','href':source(i)}); a.string='Play'; audio.append(a); cells[1].append(audio)
        text=soup.new_tag('div',attrs={'data-quote-id':str(i)}); span=soup.new_tag('span',lang='ja');span.string='line '+str(i);text.append(span);cells[2].append(text)
    candidates=parsed(str(soup)).groups[0].candidates
    assert [c.texts[0].text for c in candidates]==['line 1','line 2']
    for div in cells[1].select('[data-quote-id]'): del div['data-quote-id']
    candidates=parsed(str(soup)).groups[0].candidates
    assert len(candidates)==2 and all(c.text_status=='ambiguous_pairing' and not c.texts for c in candidates)


def test_nested_table_not_recounted_as_audio_rows():
    soup=BeautifulSoup(make_html(counts=(1,),servers=('jp',)),'html.parser')
    table=soup.select_one('table'); nested=copy.deepcopy(table); table.select('tr')[1].select('td')[2].append(nested)
    assert len(parsed(str(soup)).groups[0].candidates)==1


def test_separate_same_language_versions_are_ambiguous():
    html=make_html(counts=(1,),servers=('jp',))
    html=html.replace('<span lang="ja">','<p><span lang="ja">',1).replace('</span><span lang="en">','</span></p><p><span lang="ja">other version</span></p><span lang="en">',1)
    c=parsed(html).groups[0].candidates[0]
    assert c.text_status=='ambiguous_pairing' and not any(t.language=='ja' for t in c.texts)


def test_non_shinano_name_and_identity_same_across_fragments():
    parser=KoumakanAzurLaneParser()
    a=parser.parse(make_html(),PAGE+'#tabber-English_Server')
    b=parser.parse(make_html(),PAGE+'#tabber-Japanese_Server')
    assert a.info.character_name=='FixtureCharacter'
    assert a.info.page_identity==b.info.page_identity
