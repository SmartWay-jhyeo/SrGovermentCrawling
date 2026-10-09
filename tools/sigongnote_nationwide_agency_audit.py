"""Four targeted stored JSON field reads, not an API call or a bulk JSON rescan."""
import csv,json,sys
from pathlib import Path
from sigongnote_market_stage1 import connect
from sigongnote_nationwide_rules import OBSERVED_COMBINED_NAME

ROOT=Path(__file__).resolve().parents[1]

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    m=json.loads((ROOT/'.local/sigongnote_stage1/snapshot_verified.json').read_text(encoding='utf-8'))
    c=connect(ROOT/'.local/sigongnote_stage1/derived.sqlite3');s=connect(Path(m['path']));out=[]
    for year in ('2023','2024','2025','2026'):
        r=c.execute('SELECT no,ord,year,org,orgname FROM base WHERE year=? AND orgname LIKE ? LIMIT 1',(year,OBSERVED_COMBINED_NAME+'%')).fetchone()
        if not r:continue
        raw=s.execute('SELECT item_json,item_sha256,last_response_id FROM bf_notice_revision WHERE bid_ntce_no=? AND bid_ntce_ord=?',(r['no'],r['ord'])).fetchone()
        item=json.loads(raw['item_json'])
        out.append(dict(bid_ntce_no=r['no'],bid_ntce_ord=r['ord'],notice_year=r['year'],stored_institution_code=r['org'],stored_institution_name=r['orgname'],
          raw_item_institution_code=item.get('dminsttCd',''),raw_item_institution_name=item.get('dminsttNm',''),
          same_code=item.get('dminsttCd')==r['org'],same_name=item.get('dminsttNm')==r['orgname'],
          source_response_id=raw['last_response_id'],source_item_sha256=raw['item_sha256'],
          scope='연도별 1개 목적 확인; 전체 원문 JSON 검증 아님',
          unresolved='표기 갱신의 발생 원인·당시 기관 코드와 조직 유효기간 미확인; 과거 전남/광주로 자동 복원하지 않음'))
    p=ROOT/'outputs/sigongnote_market_nationwide/historical_name_source_examples.csv'
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    print(json.dumps(dict(targeted_raw_records=len(out),same_code=sum(r['same_code'] for r in out),same_name=sum(r['same_name'] for r in out)),ensure_ascii=False))
    c.close();s.close()

if __name__=='__main__':main()
