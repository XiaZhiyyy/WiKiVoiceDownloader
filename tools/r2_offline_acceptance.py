#!/usr/bin/env python3
"""Explicit offline regression probe for the supplied Shinano DOM.

No real HTTP is permitted. Media bytes come from the documented generated tone
fixture. All simulated downloads are written into temporary directories. This is
not an online/media-playability/Windows acceptance test.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bs4 import BeautifulSoup
from wiki_voice_downloader.models import utc_now
from wiki_voice_downloader.storage import read_metadata
from wiki_voice_downloader.text_format import parse_record, extract_text
from wiki_voice_downloader.cli import main as cli_main
from wiki_voice_downloader.http_client import HTTPClient
from tests.r2_helpers import FIXTURE, PAGE, GATE, StrictSession, Response, document_page, candidates, run, snapshot


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--html-file',type=Path,default=FIXTURE)
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args()
    report={'at':utc_now(),'input_basename':args.html_file.name,
            'input_sha256':hashlib.sha256(args.html_file.read_bytes()).hexdigest(),
            'input_kind':'local browser-DOM fixture','media':'generated tone stubs, not real game audio',
            'network':'only strict in-memory responses; unexpected URLs fail','success':False}
    with tempfile.TemporaryDirectory(prefix='wvd_r2_probe_') as temp:
        root=Path(temp);page=document_page(args.html_file);cs=candidates(page)
        assert len(cs)==96
        expected={}
        soup=BeautifulSoup(args.html_file.read_text(encoding='utf-8-sig'),'html.parser')
        for row in soup.select('#tabber-Japanese_Server table.alshipquote tr'):
            cells=row.find_all(['td','th'],recursive=False)
            if len(cells)!=3:continue
            link=cells[1].select_one('a.sm2_button[href]');span=cells[2].select_one('span[lang="ja"]')
            if link is None or span is None:continue
            node=copy.deepcopy(span)
            for ref in node.select('sup.reference,.mw-references-wrap'):ref.decompose()
            expected[str(link['href'])]=extract_text(node)
        d=root/'first/Shinano'
        first,network,_=run(d,page,{cs[37].source_url:[Response(status=404)]})
        assert (first.downloaded,first.failed)==(95,1)
        old={i['source_key']:(i['id'],i['sha256']) for i in read_metadata(d)['items'] if i['status']=='completed'}
        retry,retry_network,_=run(d,page)
        assert retry.downloaded==1 and retry.existing==95 and len(retry_network.calls)==1
        data=read_metadata(d)
        assert all((i['id'],i['sha256'])==old[i['source_key']] for i in data['items'] if i['source_key'] in old)
        rows=[parse_record(line) for line in (d/'Shinano.txt').read_text(encoding="utf-8").splitlines()]
        source={i['filename']:i['source_key'] for i in data['items']}
        assert len(rows)==96 and all(row[3]==expected[source[row[0]]] for row in rows)
        report['first_download']={'success':first.downloaded,'failed':first.failed,'http_stub_calls':len(network.calls),
                                 'retry_downloaded':retry.downloaded,'retry_reused':retry.existing,'retry_stub_calls':len(retry_network.calls),
                                 'all_96_texts_equal_independent_ja_spans':True,'stable_old_ids_and_hashes':True}
        d=root/'views/Shinano';en=document_page(args.html_file,'en')
        initial,_,_=run(d,en);before=[(i['id'],i['filename'],i['sha256']) for i in read_metadata(d)['items']]
        jp,jpnet,_=run(d,page);again,ennet,_=run(d,en)
        assert jpnet.calls==ennet.calls==[]
        assert before==[(i['id'],i['filename'],i['sha256']) for i in read_metadata(d)['items']]
        report['view_switch']={'en_first':initial.downloaded,'jp_reused':jp.existing,'jp_stub_calls':len(jpnet.calls),
                               'back_to_en_reused':again.existing,'back_to_en_stub_calls':len(ennet.calls),
                               'same_ids_filenames_hashes':True}
        d=root/'blocked/Shinano'
        blocked,blocknet,waits=run(d,page,{cs[2].source_url:[Response(GATE,status=503,headers={'cf-mitigated':'challenge'})]})
        assert (blocked.downloaded,blocked.failed,blocked.blocked,blocked.unexecuted)==(2,1,1,93)
        assert len(blocknet.calls)==3 and waits==[]
        recovery,recovery_net,_=run(d,page)
        assert recovery.downloaded==94 and recovery.existing==2
        report['media_gate']={'downloaded':2,'failed_including_blocked':1,'blocked':1,'unexecuted':93,
                              'stub_calls':len(blocknet.calls),'sleeper_calls':len(waits),
                              'recovery_downloaded':recovery.downloaded,'recovery_reused':recovery.existing,
                              'recovery_stub_calls':len(recovery_net.calls)}
        before=snapshot(root);net=StrictSession({})
        code=cli_main([PAGE,'--server','jp','--html-file',str(args.html_file),'--dry-run'],
                      app_dir=root,interactive=False,output_fn=lambda _:None,
                      client_factory=lambda cfg:HTTPClient(cfg,session=net))
        assert code==0 and snapshot(root)==before and net.calls==[]
        report['local_dry_run']={'exit_code':code,'all_network_calls':len(net.calls),'tree_bytes_and_mtimes_unchanged':True}
    report['success']=True
    with args.report.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
