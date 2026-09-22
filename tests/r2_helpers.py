from pathlib import Path
from wiki_voice_downloader.config import Config
from wiki_voice_downloader.models import PageRequestContext
from wiki_voice_downloader.page_sources import LocalHtmlPageSource, parse_document
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser
from wiki_voice_downloader.section_selection import select_section
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.service import DownloadService
from wiki_voice_downloader.selection import selected_candidates
from .helpers import Session, Response

FIXTURE = Path(__file__).parent / 'fixtures/koumakan/shinano_quotes_sanitized.html'
PAGE = 'https://azurlane.koumakan.jp/wiki/Shinano/Quotes'
PAYLOAD = (Path(__file__).parent/'fixtures/media/vorbis.ogg').read_bytes()
GATE = b'<html><title>Verify you are human</title><form id="challenge-form"><input name="captcha"></form></html>'

class StrictSession(Session):
    def get(self, address, **kwargs):
        assert self.routes.get(address), 'Unexpected request: '+address
        return super().get(address, **kwargs)

def document_page(path=FIXTURE, server='jp'):
    parser = KoumakanAzurLaneParser()
    context = PageRequestContext(PAGE, PAGE, explicit_server=server)
    doc = LocalHtmlPageSource(path, Config(), parser.policy).load(context)
    page = parse_document(doc, parser, context)
    select_section(page, parser, context, lambda _: (_ for _ in ()).throw(AssertionError('unexpected question')), lambda _:None)
    return page

def candidates(page):
    return selected_candidates(page.groups, list(range(len(page.groups))))[0]

def run(directory, page=None, routes=None, overwrite=False, checkpoint=lambda _:None):
    page=page or document_page()
    items=candidates(page)
    effective={c.source_url:[Response(PAYLOAD, headers={'Content-Type':'audio/ogg'})] for c in items}
    effective.update(routes or {})
    session=StrictSession(effective)
    waits=[]
    client=HTTPClient(Config(), session=session, sleeper=waits.append, policy=KoumakanAzurLaneParser().policy)
    result=DownloadService(Downloader(client),checkpoint=checkpoint).run(
        page,items,directory,'Shinano','koumakan_azurlane',overwrite=overwrite)
    return result,session,waits

def snapshot(directory):
    return {str(p.relative_to(directory)):(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}
