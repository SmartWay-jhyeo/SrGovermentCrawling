import argparse,csv,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('priorities',nargs='+');p.add_argument('--mode',default='main');p.add_argument('--key',default='');p.add_argument('--start',type=int,default=0);p.add_argument('--stop',type=int,default=100);a=p.parse_args()
    inv=ROOT/'.local/sigongnote_stage2_verified/inventory.json'
    if inv.exists():docs=json.loads(inv.read_text(encoding='utf-8'))
    else:
        docs=[]
        for r in csv.DictReader((ROOT/'outputs/sigongnote_market_stage2_documents/attachment_metadata.csv').open(encoding='utf-8-sig')):
            path=ROOT/'.local/sigongnote_stage2_verified'/(r['document_id']+'.json')
            if path.exists():docs.append(dict(document_key=r['document_id'],priority_id=r['priority_id'],document_name=r['attachment_filename_from_notice'],text_cache=str(path.relative_to(ROOT))))
    for d in docs:
        if d['priority_id'] not in a.priorities or 'text_cache' not in d:continue
        if a.key and a.key not in d['document_key']:continue
        if a.mode=='main' and not any(t in d['document_name'] for t in ('공고문','공고 ','공사입찰설명서','수의견적','공사설명서','공사설계설명서','설계설명서','공사내용서','내역서','공사설계')):continue
        blocks=json.loads((ROOT/d['text_cache']).read_text(encoding='utf-8'))
        print('\n',d['priority_id'],d['document_key'],d['document_name'])
        if a.mode=='sheetmoney':
            if d.get('actual_format') not in ('XLS','XLSX'):continue
            ids=[i for i,b in enumerate(blocks[:40]) if re.search('총공사|도급액|관급액|부가가치세|공급가액|노면|도색',re.sub(r'\s','',b['text']))][:10]
        elif a.mode=='facts':
            ids=[i for i,b in enumerate(blocks[:200]) if re.search('공사예정금액|기초금액:|기초금액 :|추정금액|총도급예정|공사기간:|공사기간 :|공사의 공사기간|본 공사의 목적|원상복구|공사목적|공사위치:|공사위치 :|공사장소|공사예정가격|총공사비|총사업비',b['text'])][:15]
        elif a.mode=='workflow':
            ids=[i for i,b in enumerate(blocks) if re.search('작업지시|사진|공사지시|공사감독관.*지시|수시|민원|긴급|물량|보고서',b['text'])][:18]
        else:ids=range(a.start,min(a.stop,len(blocks)))
        for i in ids:
            text=blocks[i]['text']
            text=re.sub(r'https?://\S+','[링크 생략]',text)
            text=re.sub(r'[\w.\-]+@[\w.\-]+','[이메일 생략]',text)
            text=re.sub(r'\b0\d{1,2}[-) ]\d{3,4}[- ]\d{4}\b','[연락처 생략]',text)
            if re.search('담당자|담당.*전화|전화.*담당|주무관|☎|☏|연락처',text):continue
            print(f'[{i}] {blocks[i]["loc"]}: {text[:1400]}')
if __name__=='__main__':main()
