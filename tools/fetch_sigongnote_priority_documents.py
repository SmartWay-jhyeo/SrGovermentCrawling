"""Fetch only the explicitly approved, saved 79 attachment URLs. No retries/API keys."""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
import httpx
from prepare_sigongnote_stage2 import link_check

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'outputs/sigongnote_market_stage2/attachment_download_plan.csv'
WORK=ROOT/'.local/sigongnote_stage2_documents'
OUT=ROOT/'outputs/sigongnote_market_stage2_documents'
MAX_REQUESTS=79
MAX_FILE=20*1024*1024
MAX_TOTAL=200*1024*1024


def utc():return dt.datetime.now(dt.timezone.utc).isoformat()


def file_kind(head):
    if head.startswith(b'%PDF-'):return 'pdf'
    if head.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):return 'compound'
    if head.startswith(b'PK\x03\x04'):return 'zip'
    if head.lstrip().startswith(b'{\\rtf'):return 'rtf'
    if re.search(rb'(?i)<(?:!doctype\s+html|html|head|script)',head):return 'html'
    return 'unknown'


def filename(header):
    match=re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)",header)
    if match:s=unquote(match.group(1))
    else:
        match=re.search(r'filename\s*=\s*"?([^";]+)',header,re.I);s=unquote(match.group(1)) if match else ''
        try:s=s.encode('latin1').decode('utf-8')
        except (UnicodeError,ValueError):pass
    return re.sub(r'[\x00-\x1f]', '', s).strip()[:240]


def append(entry):
    with (WORK/'fetch_ledger.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')


def load_ledger():
    path=WORK/'fetch_ledger.jsonl'
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def export(plan,ledger):
    final={e['document_id']:e for e in ledger if e['event']=='result'}
    starts={e['document_id']:e for e in ledger if e['event']=='reserved'}
    global_connect_block=sum(e.get('reason')=='NETWORK_CONNECT_FAILURE' for e in final.values())>=3
    rows=[]
    for p in plan:
        doc=p['document_id'];r=final.get(doc,{})
        status=r.get('fetch_status','INTERRUPTED_NO_RETRY' if doc in starts else 'SKIPPED_NETWORK_BLOCK' if global_connect_block else 'NOT_ATTEMPTED')
        rows.append({**p,'approval_status':'승인(사용자: 진행해)','fetch_status':status,
                     'http_status':r.get('http_status',''),'file_name':r.get('file_name',''),
                     'error_reason':r.get('reason','공통 연결실패 3회 후 미시도; 개별 링크 유효성 미확인' if global_connect_block and doc not in starts else '한정 예산 내 미시도' if doc not in starts else '예약 후 완료기록 없음; 재시도 금지'),
                     'attempt_count':int(doc in starts),'received_bytes':r.get('received_bytes',0),
                     'sha256':r.get('sha256',''),'file_kind':r.get('file_kind',''),
                     'local_path':r.get('local_path',''),'received_utc':r.get('received_utc',''),
                     'content_type':r.get('content_type',''),'access_tool':starts.get(doc,{}).get('tool','httpx' if doc in starts else ''),
                     'transfer_bytes_observed':r.get('transfer_bytes_observed',doc in starts and starts[doc].get('tool')!='web_read')})
    with (OUT/'attachment_fetch_results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    summary=dict(planned=len(plan),attempts=len(starts),statuses=dict(collections.Counter(r['fetch_status'] for r in rows)),
                 received_bytes=sum(r.get('received_bytes',0) for r in final.values()),max_requests=MAX_REQUESTS,
                 max_file_bytes=MAX_FILE,max_total_bytes=MAX_TOTAL,automatic_retries=0,api_calls=0,
                 tls_verification=True,redirect_following=False,updated_utc=utc())
    summary.update(httpx_attempts=sum(e.get('tool','httpx')=='httpx' for e in starts.values()),web_read_attempts=sum(e.get('tool')=='web_read' for e in starts.values()),
                   downloaded_files=sum(r['fetch_status']=='DOWNLOADED' for r in rows),byte_accounting='직접 HTTP 읽기 관측값; web 도구 내부 전송량/HTTP 횟수 미제공',request_accounting='원문 URL별 논리적 시도 예약 수; web 내부 HTTP 횟수는 미확인')
    (OUT/'fetch_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--download-approved',action='store_true');parser.add_argument('--limit',type=int,default=79);parser.add_argument('--resume-unattempted',action='store_true',help='네트워크 문제 해결 후 미시도 URL만 진행; 이미 시도한 URL 재호출 없음')
    args=parser.parse_args();sys.stdout.reconfigure(encoding='utf-8')
    if not args.download_approved:raise SystemExit('Explicit --download-approved required; no request made')
    assert 1<=args.limit<=79
    WORK.mkdir(parents=True,exist_ok=True);(WORK/'downloads').mkdir(exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    with PLAN.open(encoding='utf-8-sig',newline='') as f:plan=list(csv.DictReader(f))
    assert len(plan)==79 and len({p['document_id'] for p in plan})==79
    plan_sha=hashlib.sha256(PLAN.read_bytes()).hexdigest()
    auth=WORK/'authorization.json'
    if auth.exists():assert json.loads(auth.read_text(encoding='utf-8'))['plan_sha256']==plan_sha
    else:auth.write_text(json.dumps(dict(user_authorization='진행해: 직전 23개 공고·79개 기존 첨부 제안 승인',plan_sha256=plan_sha,recorded_utc=utc(),max_requests=MAX_REQUESTS,max_file_bytes=MAX_FILE,max_total_bytes=MAX_TOTAL),ensure_ascii=False,indent=2),encoding='utf-8')
    ledger=load_ledger();attempted={e['document_id'] for e in ledger if e['event']=='reserved'}
    total=sum(e.get('received_bytes',0) for e in ledger if e['event']=='result')
    # First attachments across different notices establish access before further documents.
    order=['P03','P13','P16','P17','P18','P19','P20','P21','P22','P23']
    planned=sorted(plan,key=lambda p:(int(p['attachment_index']),order.index(p['priority_id']) if p['priority_id'] in order else 30+int(p['priority_id'][1:])))
    failures=collections.Counter()
    if not args.resume_unattempted:
        for e in ledger:
            if e.get('reason') in ('NETWORK_CONNECT_FAILURE','HTTP_403','HTTP_429'):failures[e['reason']]+=1
    client=httpx.Client(timeout=httpx.Timeout(20,connect=12),follow_redirects=False,verify=True,headers={'User-Agent':'SigongNote-Document-Review/1.0','Accept':'*/*'})
    try:
        for p in planned:
            if p['document_id'] in attempted:continue
            if len(attempted)>=min(args.limit,MAX_REQUESTS) or total>=MAX_TOTAL:break
            if any(n>=3 for n in failures.values()):break
            status=link_check(p['attachment_url'],p['bid_ntce_no'],p['bid_ntce_ord'])
            if not status.startswith('형식') or p['proposed_in_scope']!='1':continue
            reservation=dict(event='reserved',document_id=p['document_id'],priority_id=p['priority_id'],reserved_utc=utc())
            append(reservation);ledger.append(reservation);attempted.add(p['document_id'])
            result=dict(event='result',document_id=p['document_id'],priority_id=p['priority_id'],fetch_status='FAILED',http_status='',received_bytes=0,reason='',received_utc=utc())
            data=bytearray();started=time.monotonic()
            try:
                with client.stream('GET',p['attachment_url']) as response:
                    result.update(http_status=response.status_code,content_type=response.headers.get('content-type','').split(';')[0],file_name=filename(response.headers.get('content-disposition','')))
                    if response.status_code!=200:
                        result['reason']='HTTP_'+str(response.status_code)
                    elif response.headers.get('content-length','').isdigit() and int(response.headers['content-length'])>min(MAX_FILE,MAX_TOTAL-total):
                        result['reason']='DECLARED_SIZE_LIMIT'
                    else:
                        for block in response.iter_bytes(chunk_size=65536):
                            result['received_bytes']+=len(block);total+=len(block)
                            if len(data)+len(block)>MAX_FILE or total>MAX_TOTAL:
                                result['reason']='STREAM_SIZE_LIMIT';break
                            if time.monotonic()-started>45:
                                result['reason']='TOTAL_TIME_LIMIT';break
                            data.extend(block)
                        if not result['reason']:
                            kind=file_kind(data[:4096]);result.update(file_kind=kind,sha256=hashlib.sha256(data).hexdigest())
                            if kind in ('pdf','compound','zip','rtf'):
                                dest=WORK/'downloads'/(p['document_id']+'.bin');dest.write_bytes(data)
                                result.update(fetch_status='DOWNLOADED',local_path=str(dest.relative_to(ROOT)))
                            else:result['reason']='NON_DOCUMENT_'+kind.upper()
            except httpx.ConnectError:result['reason']='NETWORK_CONNECT_FAILURE'
            except httpx.TimeoutException:result['reason']='NETWORK_TIMEOUT'
            except httpx.HTTPError as exc:result['reason']='HTTP_CLIENT_'+type(exc).__name__
            except OSError as exc:result['reason']='LOCAL_IO_'+type(exc).__name__
            if result['reason'] in ('NETWORK_CONNECT_FAILURE','HTTP_403','HTTP_429'):failures[result['reason']]+=1
            append(result);ledger.append(result);export(plan,ledger)
            print(json.dumps({k:result.get(k,'') for k in ('priority_id','document_id','fetch_status','http_status','file_kind','received_bytes','reason')},ensure_ascii=False),flush=True)
            time.sleep(.2)
    finally:
        client.close();summary=export(plan,ledger)
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
