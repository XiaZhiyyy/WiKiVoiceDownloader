"""Artificial fixtures only; these are NOT the missing user-supplied DOM."""
from pathlib import Path
import copy
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser
from wiki_voice_downloader.models import PageRequestContext
from wiki_voice_downloader.section_selection import select_section
from wiki_voice_downloader.selection import selected_candidates
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.service import DownloadService
from .helpers import Session, Response

PAGE='https://azurlane.koumakan.jp/wiki/FixtureCharacter/Quotes'
BASE='https://azurlane.netojuu.com/images/test/'
MEDIA=Path(__file__).parent/'fixtures'/'media'

def source(i): return BASE+f'line_{i}.ogg'

def make_html(counts=(3,2,1,2), servers=('en','cn','jp')):
    labels={'en':'English Server','cn':'Chinese Server','jp':'Japanese Server'}
    pieces=['<html><h1 id="firstHeading">FixtureCharacter/Quotes</h1><div class="mw-parser-output">']
    for server in servers:
        pieces.append(f'<article class="tabber__panel" id="tabber-{labels[server].replace(" ","_")}" aria-selected="{str(server=="en").lower()}">')
        index=0
        for group,count in enumerate(counts):
            pieces.append(f'<h3><span id="Group_{group}">Fixture Group {group}</span><span class="mw-editsection">edit</span></h3>')
            pieces.append('<table class="alshipquote"><tbody><tr><td>Event</td><td>VO</td><td>Transcription</td></tr>')
            for _ in range(count):
                index+=1; url=source(index)
                text=(f'<span lang="ja">\u65e5\u6587{index} Zzzz 123 (abc) A|B\\C &amp;lt;<sup class="reference">[EN 1]</sup></span><span lang="en">JP translation {index}</span>' if server=='jp' else
                      f'<span lang="zh">\u4e2d\u6587{index}</span>' if server=='cn' else f'<span lang="en">English original {index}</span>')
                hidden=' hidden="until-found" style="display:none"' if index%2==0 else ''
                pieces.append(f'<tr{hidden}><td>Event {index}</td><td><a class="sm2_button" href="{url}">Play</a><a class="internal" href="{url}">file</a></td><td>{text}</td></tr>')
            pieces.append('<tr><td>Ship Description</td><td></td><td><span lang="ja">description only</span></td></tr><tr><td colspan="3">References</td></tr></tbody></table>')
        pieces.append('</article>')
    pieces.append('</div></html>')
    return ''.join(pieces)

def parsed(html=None,server='jp',profile=None):
    parser=KoumakanAzurLaneParser(profile)
    page=parser.parse(html or make_html(),PAGE)
    select_section(page,parser,PageRequestContext(PAGE,PAGE,explicit_server=server),lambda _: (_ for _ in ()).throw(EOFError()),lambda _:None)
    return page

def run(directory, page=None, groups=None, routes=None, overwrite=False,checkpoint=lambda _:None):
    page=page or parsed(); groups=groups if groups is not None else list(range(len(page.groups)))
    candidates,duplicates,_=selected_candidates(page.groups,groups)
    payload=(MEDIA/'vorbis.ogg').read_bytes()
    effective={c.source_url:[Response(payload,headers={'Content-Type':'audio/ogg'})] for c in candidates}
    if routes: effective.update(routes)
    session=Session(effective)
    client=HTTPClient(Config(retry_backoff_seconds=0),session=session,sleeper=lambda _:None,
                      policy=KoumakanAzurLaneParser().policy)
    runner=DownloadService(Downloader(client),checkpoint=checkpoint)
    result=runner.run(page,candidates,directory,'FixtureCharacter','koumakan_azurlane',
                      overwrite=overwrite,duplicates=duplicates)
    return result,session
