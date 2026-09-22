#!/usr/bin/env python3
"""Explicit online smoke test, separate from offline pytest.

Use only on a normally accessible public character page. At most three selected
media are requested; game audio is kept only in a temporary directory. No proxy
rotation, browser, API guessing, TLS bypass or access-limit bypass is performed.
"""
from pathlib import Path
import argparse
from dataclasses import asdict
import hashlib
import json
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from wiki_voice_downloader import __version__, __revision__
from wiki_voice_downloader.page_sources import HttpPageSource, LocalHtmlPageSource, parse_document
from wiki_voice_downloader.config import load_config,application_dir
from wiki_voice_downloader.profile_loader import load_profiles
from wiki_voice_downloader.parsers.registry import get_parser
from wiki_voice_downloader.models import PageRequestContext,utc_now
from wiki_voice_downloader.section_selection import select_section
from wiki_voice_downloader.selection import selected_candidates
from wiki_voice_downloader.http_client import HTTPClient
from wiki_voice_downloader.downloader import Downloader
from wiki_voice_downloader.errors import AppError
from wiki_voice_downloader.urls import request_url,redact_message,redact_url
from urllib.parse import urlsplit


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('url');ap.add_argument('--config');ap.add_argument('--server',choices=['jp','cn','en'])
    ap.add_argument('--html-file',type=Path,help='read explicit body HTML; page requests are zero')
    ap.add_argument('--html-encoding',help='only with --html-file')
    ap.add_argument('--audio-limit',type=int,choices=range(4),default=0,help='0 means page-only; maximum 3 unique media')
    ap.add_argument('--report',type=Path,required=True,help='explicit report output file')
    ap.add_argument('--save-html',type=Path,help='optional initial HTTP HTML fixture, never an inferred browser DOM')
    args=ap.parse_args()
    report={'app_version':__version__,'revision':__revision__,'at':utc_now(),'input_url':redact_url(args.url),
            'requested_audio_limit':args.audio_limit,'success':False,'media':[]}
    code=1
    try:
        config,_=load_config(args.config);profiles=load_profiles(config.site_profile_dir,application_dir())
        parser=get_parser(args.url,profiles=profiles)
        with HTTPClient(config,policy=parser.policy) as client:
            if args.html_encoding is not None and args.html_file is None:
                raise AppError('--html-encoding requires --html-file')
            if args.html_file is not None and args.save_html is not None:
                raise AppError('--save-html is only for explicitly capturing HTTP input')
            url=request_url(args.url)
            context=PageRequestContext(args.url,url,fragment_hint=urlsplit(args.url).fragment,explicit_server=args.server)
            source=(LocalHtmlPageSource(args.html_file,config,parser.policy,args.html_encoding)
                    if args.html_file is not None else HttpPageSource(client,parser.policy))
            document=source.load(context)
            page=parse_document(document,parser,context)
            html,resolved=document.html,document.effective_page_url
            report.update(acquisition=document.provenance(),html_sha256=document.content_sha256,
                          profile=parser.profile.site_id,profile_fingerprint=parser.profile.fingerprint)
            if args.save_html:
                # Explicit opt-in with exclusive create prevents accidental overwrite.
                with args.save_html.open('x',encoding='utf-8',newline='\n') as f:f.write(html)
                report['html_capture']=str(args.save_html)
            def no_input(_):raise AppError('Use --server or a recognized URL fragment for noninteractive smoke tests')
            select_section(page,parser,context,no_input,lambda message:None)
            candidates,duplicates,raw_count=selected_candidates(page.groups,list(range(len(page.groups))))
            report.update(server=page.selected_section,text_language=page.text_language,
                          groups=[{'name':g.name,'unique_count':g.unique_count} for g in page.groups],
                          unique_media=len(candidates),duplicate_references=duplicates,raw_candidates=raw_count,
                          diagnostics=page.warnings,preview=[{'category':c.category,'source_url':redact_url(c.source_url),
                              'texts':[asdict(t) for t in c.texts],'text_status':c.text_status} for c in candidates[:3]])
            with tempfile.TemporaryDirectory(prefix='wvd_smoke_') as tmp:
                downloader=Downloader(client)
                for index,candidate in enumerate(candidates[:args.audio_limit],1):
                    result=downloader.download(candidate.source_url,Path(tmp)/f'{index:03d}.media.part',resolved)
                    row=asdict(result);row['resolved_audio_url']=redact_url(row['resolved_audio_url'])
                    report['media'].append(row)
            report['actual_audio_http_requests']=client.audio_request_count
            report['success']=True;code=0
    except (AppError,OSError,UnicodeError) as exc:
        report['error_type']=type(exc).__name__
        report['actual_attempts']=getattr(exc,'attempts',None)
        if hasattr(exc,'decision'):report['access_decision']=exc.decision.record()
        report['error']=redact_message(str(exc)) if isinstance(exc,AppError) else type(exc).__name__
        report['note']='Normal access failed; this is not a successful online acceptance test.'
    try:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    except OSError as exc:
        print(f'Cannot create report without overwriting an existing file: {type(exc).__name__}',file=sys.stderr);return 1
    print(json.dumps(report,ensure_ascii=False,indent=2));return code
if __name__=='__main__':raise SystemExit(main())
