"""Read attachment filenames for exactly the approved 23 composite notice keys."""
import collections
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_stage2_documents'
WORK=ROOT/'.local/sigongnote_stage2_documents'


def main():
    path=Path((ROOT/'.local/data_profile/snapshot_path.txt').read_text(encoding='utf-8').strip())
    c=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);c.execute('PRAGMA query_only=ON')
    with (ROOT/'outputs/sigongnote_market_stage2/attachment_download_plan.csv').open(encoding='utf-8-sig',newline='') as f:plan=list(csv.DictReader(f))
    cache={};rows=[]
    for r in plan:
        key=(r['bid_ntce_no'],r['bid_ntce_ord'])
        if key not in cache:
            found=c.execute('SELECT item_json FROM bf_notice_revision WHERE bid_ntce_no=? AND bid_ntce_ord=?',key).fetchone()
            x=json.loads(found[0]) if found else {}
            cache[key]={x.get(f'ntceSpecDocUrl{i}'):x.get(f'ntceSpecFileNm{i}','') for i in range(1,11) if x.get(f'ntceSpecDocUrl{i}')}
        name=cache[key].get(r['attachment_url'],'')
        rows.append(dict(priority_id=r['priority_id'],bid_ntce_no=key[0],bid_ntce_ord=key[1],document_id=r['document_id'],attachment_index=r['attachment_index'],attachment_filename_from_notice=name,filename_extension=Path(name).suffix.lower(),metadata_source='고정 사본 공고 item_json의 ntceSpecDocUrlN 정확 일치 + ntceSpecFileNmN',content_verified=False))
    with (OUT/'attachment_metadata.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    c.close()
    print(json.dumps(dict(composite_keys_read=len(cache),attachment_rows=len(rows),extensions=dict(collections.Counter(r['filename_extension'] for r in rows)),pdf_documents=[{k:r[k] for k in ('priority_id','document_id','attachment_filename_from_notice')} for r in rows if r['filename_extension']=='.pdf']),ensure_ascii=False))


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');main()
