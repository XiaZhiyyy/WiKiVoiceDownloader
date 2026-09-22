#!/usr/bin/env python3
"""Read-only check of a user-provided Quotes HTML file. No network or audio I/O."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from bs4 import BeautifulSoup
from wiki_voice_downloader.parsers.koumakan_azurlane import KoumakanAzurLaneParser

EXPECTED='315b4e347c5f93592d8ecdf6727580dd817693179e1e45974e9f1a8650d550b3'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('html',type=Path)
    ap.add_argument('--page-url',default='https://azurlane.koumakan.jp/wiki/Shinano/Quotes')
    args=ap.parse_args()
    try:
        raw=args.html.read_bytes();html=raw.decode('utf-8-sig')
        page=KoumakanAzurLaneParser().parse(html,args.page_url)
        soup=BeautifulSoup(html,'html.parser')
        report={'sha256':hashlib.sha256(raw).hexdigest(),'matches_task_fingerprint':hashlib.sha256(raw).hexdigest()==EXPECTED,
                'source':'explicit local input, not an online fetch','sections':[],'warnings':page.warnings}
        sets=[]
        for section in page.sections:
            candidates=[c for g in section.groups for c in g.candidates]
            urls={c.source_url for c in candidates};sets.append(urls)
            report['sections'].append({'id':section.section_id,'groups':[{ 'name':g.name,'unique_audio':g.unique_count} for g in section.groups],
                'unique_audio':len(urls),'candidates':len(candidates),
                'ja_rows':sum(any(t.language=='ja' and t.text for t in c.texts) for c in candidates),
                'ja_with_en_rows':sum(any(t.language=='ja' for t in c.texts) and any(t.language=='en' for t in c.texts) for c in candidates)})
        report['server_url_sets_equal']=bool(sets) and all(s==sets[0] for s in sets)
        report['ogg_link_occurrences']=sum(urlsplit(str(a['href'])).path.lower().endswith('.ogg') for a in soup.select('a[href]'))
        panel=soup.select_one('#tabber-Japanese_Server')
        report['jp_hidden_audio_rows']=(sum(bool(row.select('a.sm2_button[href]')) and row.has_attr('hidden')
                                          for row in panel.select('table.alshipquote tr')) if panel else None)
        passed=None
        if report['matches_task_fingerprint']:
            jp=next((s for s in report['sections'] if s['id']=='jp'),None)
            passed=(len(report['sections'])==3 and all(s['unique_audio']==96 for s in report['sections'])
                    and report['server_url_sets_equal'] and report['ogg_link_occurrences']==576
                    and jp is not None and [g['unique_audio'] for g in jp['groups']]==[30,9,8,12,13,14,10]
                    and jp['ja_rows']==96 and jp['ja_with_en_rows']==29 and report['jp_hidden_audio_rows']==66)
        report['exact_fixture_regression_passed']=passed
        if passed is None:report['note']='Different input fingerprint: counts are observations, not proof of the specified fixture regression.'
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 1 if passed is False else 0
    except (OSError,UnicodeError,ValueError) as exc:
        print(f'Cannot read/parse fixture: {type(exc).__name__}',file=sys.stderr);return 1
    except Exception as exc:
        from wiki_voice_downloader.errors import AppError
        if isinstance(exc,AppError):print(str(exc),file=sys.stderr);return 1
        raise
if __name__=='__main__':raise SystemExit(main())
