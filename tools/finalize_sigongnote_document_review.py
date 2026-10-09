"""Record inaccessible approved documents honestly, preserving all earlier CSVs."""
import collections
import csv
import datetime as dt
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/sigongnote_market_stage2'
OUT=ROOT/'outputs/sigongnote_market_stage2_documents'
WORK=ROOT/'.local/sigongnote_stage2_documents'


def read(name,directory=OUT):
    with (directory/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def write(name,rows,fields=None):
    fields=fields or list(rows[0])
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    return dict(rows=len(rows),bytes=(OUT/name).stat().st_size)


def case_status(attachments):
    failed=sum(r['fetch_status']=='FAILED' for r in attachments)
    skipped=sum(r['fetch_status'].startswith('SKIPPED') or r['fetch_status']=='NOT_ATTEMPTED' for r in attachments)
    downloaded=sum(r['fetch_status']=='DOWNLOADED' for r in attachments)
    return dict(failed=failed,skipped=skipped,downloaded=downloaded,
                reason='승인 완료. '+('해당 공고의 첨부 접속 실패 관측; ' if failed else '공통 연결차단으로 해당 공고 첨부는 미시도; ')+
                       '원문 내용 미확인. 링크 소멸/자료 부존재/금액 0으로 해석하지 않음')


def main():
    fetches=read('attachment_fetch_results.csv');metadata=read('attachment_metadata.csv')
    names={r['document_id']:r for r in metadata};by_key=collections.defaultdict(list)
    for r in fetches:
        r.update(attachment_filename_from_notice=names[r['document_id']]['attachment_filename_from_notice'],
                 filename_source='기존 공고 메타데이터(원문 미열람)',document_content_verified='0')
        by_key[(r['bid_ntce_no'],r['bid_ntce_ord'])].append(r)
    assert not any(r['fetch_status']=='DOWNLOADED' for r in fetches), '다운로드 성공 문서는 별도 실제 내용 검토 필요; 미확인 일괄 처리 금지'
    files={'attachment_fetch_results.csv':write('attachment_fetch_results.csv',fetches)}
    files['attachment_metadata.csv']=dict(rows=len(metadata),bytes=(OUT/'attachment_metadata.csv').stat().st_size)
    cases=read('priority_notice_review.csv',SOURCE);facts=read('priority_document_evidence.csv',SOURCE)
    cache=ROOT/'.local/sigongnote_stage2/analysis_v2_0.sqlite3'
    conn=sqlite3.connect(cache.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);conn.execute('PRAGMA query_only=ON')
    institutional=collections.defaultdict(lambda:dict(candidates=0,attempted=0,failed=0,skipped=0))
    period_rows=[]
    for p in cases:
        key=(p['bid_ntce_no'],p['bid_ntce_ord']);docs=by_key[key];status=case_status(docs)
        record=conn.execute("SELECT json_extract(payload,'$.dminstt_cd'),json_extract(payload,'$.buyer_type') FROM rows WHERE no=? AND ord=? ORDER BY seq LIMIT 1",key).fetchone()
        p.update(document_status='BLOCKED_SOURCE_UNAVAILABLE',approval_status='승인됨(사용자: 진행해)',
                 failed_attachment_count=status['failed'],skipped_attachment_count=status['skipped'],downloaded_attachment_count=0,
                 document_content_review_status='미실시(원문 미확보)',block_reason=status['reason'],
                 expected_document_names=[r['attachment_filename_from_notice'] for r in docs],
                 filename_evidence_scope='공고 메타데이터의 파일명만 확인; 본문 확인 아님',
                 institution_code=record[0] if record else '',institution_type=record[1] if record else '',
                 amount_verification_status='미확인(원문 접근 불가)',price_X='미정')
        assert p['verified_amount']=='' and p['verified_business_period']=='' and p['verified_management_period']==''
        group=institutional[(p['institution_code'],p['original_institution'],p['institution_type'],p['notice_year'])]
        group['candidates']+=1;group['attempted']+=int(status['failed']>0);group['failed']+=status['failed'];group['skipped']+=status['skipped']
        period_rows.append(dict(bid_ntce_no=key[0],bid_ntce_ord=key[1],notice_year=p['notice_year'],title_business_year_candidates=p['title_business_years'],
                                verified_business_start='',verified_business_end='',verified_management_start='',verified_management_end='',verified_business_year='',
                                verified_billing_basis_candidate='',billing_basis_definition='',service_subscription_count='',verified_facts_count=0,
                                verification_status='BLOCKED_SOURCE_UNAVAILABLE',evidence_document_id='',page_or_table='',evidence_sentence='',price_X='미정'))
    conn.close()
    for f in facts:
        key=(f['bid_ntce_no'],f['bid_ntce_ord']);status=case_status(by_key[key])
        f.update(verification_status='미확인(원문 미확보)',reason=status['reason'],approval_status='승인됨',
                 access_failure_document_ids=[r['document_id'] for r in by_key[key] if r['fetch_status']=='FAILED'],
                 unattempted_document_ids=[r['document_id'] for r in by_key[key] if r['fetch_status'].startswith('SKIPPED')],
                 candidate_filenames_from_notice=[r['attachment_filename_from_notice'] for r in by_key[key]],
                 filename_is_not_content_evidence='1')
        assert all(f[k]=='' for k in ('verified_value','document_name','page_or_table','evidence_sentence'))
    files['priority_notice_review.csv']=write('priority_notice_review.csv',cases)
    files['priority_document_evidence.csv']=write('priority_document_evidence.csv',facts)
    files['candidate_period_amount_updates.csv']=write('candidate_period_amount_updates.csv',period_rows)
    institution_rows=[]
    for (code,name,typ,year),n in sorted(institutional.items()):
        institution_rows.append(dict(institution_code=code,institution_name_original=name,institution_type=typ,notice_year=year,
                                     sample_scope='우선확인 23건만; 전체 기관 후보표 아님',priority_candidate_count=n['candidates'],notices_with_failed_access=n['attempted'],
                                     failed_attachment_count=n['failed'],unattempted_attachment_count=n['skipped'],verified_amount_sample_n=0,
                                     verified_amount_sum='',amount_status='미확인; 0원 아님',verified_period_count=0,price_X='미정'))
    files['institution_verification_status.csv']=write('institution_verification_status.csv',institution_rows)
    for name in ('institution_verified_amount_summary.csv','verified_price_hypotheses.csv'):
        with (SOURCE/name).open(encoding='utf-8-sig',newline='') as f:fields=next(csv.reader(f))
        files[name]=write(name,[],fields)
    filename_flags=[]
    for p in cases:
        for a in by_key[(p['bid_ntce_no'],p['bid_ntce_ord'])]:
            import re
            candidate_years=re.findall(r'(?<!\d)(?:19|20)\d{2}(?=년)',a['attachment_filename_from_notice'])
            if candidate_years and p['notice_year'] not in candidate_years:
                filename_flags.append(dict(bid_ntce_no=p['bid_ntce_no'],bid_ntce_ord=p['bid_ntce_ord'],document_id=a['document_id'],
                                           attachment_filename_from_notice=a['attachment_filename_from_notice'],notice_year=p['notice_year'],filename_years=candidate_years,
                                           finding='파일명 연도와 공고연도 불일치 후보; 과년도 내역/파일명 잔존 등 원인 미확인',verified_business_year=''))
    files['filename_review_flags.csv']=write('filename_review_flags.csv',filename_flags,['bid_ntce_no','bid_ntce_ord','document_id','attachment_filename_from_notice','notice_year','filename_years','finding','verified_business_year'])
    ledger=[json.loads(line) for line in (WORK/'fetch_ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    reserved=[e for e in ledger if e['event']=='reserved'];done=[e for e in ledger if e['event']=='result']
    auth=json.loads((WORK/'authorization.json').read_text(encoding='utf-8'))
    original=json.loads((SOURCE/'output_manifest.json').read_text(encoding='utf-8'))
    frozen=original['original_snapshot'];frozen_path=Path(frozen['path'])
    checks=dict(planned_attachment_count=len(fetches)==79,metadata_exact_document_ids=set(names)=={r['document_id'] for r in fetches},
                priority_composite_keys_preserved={(r['bid_ntce_no'],r['bid_ntce_ord']) for r in cases}==set(by_key),
                no_retried_document_ids=len({e['document_id'] for e in reserved})==len(reserved),
                logical_attempts_within_approved_budget=len(reserved)<=auth['max_requests'],
                all_attempts_have_results={e['document_id'] for e in reserved}=={e['document_id'] for e in done},
                attempts_subset_of_approved_plan={e['document_id'] for e in reserved}.issubset(names),
                failures_not_marked_downloaded=all(r['fetch_status']!='DOWNLOADED' for r in fetches),
                skipped_not_counted_as_failed=sum(r['fetch_status']=='FAILED' for r in fetches)==len(reserved),
                no_http_success_codes_inferred=all(r['http_status']=='' for r in fetches),
                no_document_quotes_or_pages_fabricated=all(f['evidence_sentence']=='' and f['page_or_table']=='' and f['verified_value']=='' for f in facts),
                no_verified_period_or_fee_inferred=all(r['verified_business_start']=='' and r['verified_billing_basis_candidate']=='' and r['service_subscription_count']=='' for r in period_rows),
                money_unknown_not_zero=all(r['verified_amount_sum']=='' for r in institution_rows),
                year_from_filenames_not_assumed=all(r['verified_business_year']=='' for r in filename_flags),
                facts_complete_per_priority=len(facts)==9*len(cases),
                institution_counts_reconcile=sum(r['priority_candidate_count'] for r in institution_rows)==len(cases),
                no_saved_raw_downloads=not list((WORK/'downloads').glob('*.bin')),
                source_plan_sha_unchanged=hashlib.sha256((SOURCE/'attachment_download_plan.csv').read_bytes()).hexdigest()==auth['plan_sha256'],
                previous_stage2_file_sizes_unchanged=all((SOURCE/name).stat().st_size==meta['bytes'] for name,meta in original['files'].items()),
                frozen_snapshot_metadata_unchanged=frozen_path.stat().st_size==frozen['bytes'] and frozen_path.stat().st_mtime_ns==frozen['mtime_ns'])
    summary=json.loads((OUT/'fetch_summary.json').read_text(encoding='utf-8'))
    (OUT/'validation_results.json').write_text(json.dumps(dict(checks=checks,content_verification='BLOCKED: 실제 원문 미확보',network_requests_not_tests=True),ensure_ascii=False,indent=2),encoding='utf-8')
    report=f'''# 시공노트 우선확인 공고 원문 검증 — 승인 후 접속 결과

## 완료

사용자의 “진행해”를 직전 23개 공고·기존 첨부 79개에 대한 승인으로 기록하고 실행했다. 기존 1차/2차 CSV·DB·캐시·수집기·예약작업은 보존했다. 이번 결과는 `outputs/sigongnote_market_stage2_documents/`에 별도로 저장했으며, 이전 보고서의 “승인 대기”는 당시 이력이다. 현재 상태는 **승인 완료, 원문 취득 차단**이다.

- 우선확인 공고: {len(cases)}건, 공고번호+차수 정확 연결.
- 첨부 메타데이터: {len(metadata)}개. 고정 사본에서 해당 23개 복합키의 파일명 필드만 확인했다. 4.7GB DB 전체 재검사/전체 재수집은 하지 않았다.
- 논리적 접속 시도: {len(reserved)}개 URL(직접 HTTP 3개, 웹 읽기 도구 1개), 동일 URL 재시도 0회, API 호출 0회.
- 본문 파일 확보: 0개. 페이지/표·직접 근거·확정 금액/기간을 만들지 않았다.
- 검사: {sum(checks.values())}개 PASS. 이는 기록·보존·미확인 처리 검사이며 원문 내용 검증 통과가 아니다.

## 실패

| 공고/첨부 | 경로 | 실제 관측 |
| --- | --- | --- |
| R25BK00675946-000 / e2aa332d7330342c | 직접 HTTP | 연결 실패; HTTP 상태코드 없음 |
| 20241226229-000 / 59ff7b86a2af835c | 직접 HTTP | 연결 실패; HTTP 상태코드 없음 |
| R25BK00580213-000 / 5ba9c0c346d8804c | 직접 HTTP | 연결 실패; HTTP 상태코드 없음 |
| R26BK01402848-000 / 7370b49a77077ff1 | 웹 읽기 도구 | 해당 저장 URL 접근 불가; 문서 본문 반환 없음 |

직접 연결이 세 번 실패한 뒤 같은 경로로 나머지를 반복하지 않았다. 아직 시도하지 않은 승인 목록의 PDF 하나를 별도 읽기 도구로 확인했으나 역시 접근할 수 없었다. **4개 접속 실패와 75개 연결차단에 따른 미시도**를 분리했다. 미시도 URL을 개별 조회 실패·자료 없음으로 기록하지 않았다. 승인 문제가 아니라 현재 실행 환경에서 원문을 확보할 수 없는 상태다. 제공기관이 파일을 삭제했거나 키가 잘못됐다는 원인은 확인되지 않았다.

직접 HTTP에서 관측한 수신 본문은 0바이트이고 로컬 저장 원문은 0개다. 웹 도구의 내부 HTTP 횟수/전송량은 제공되지 않으므로 합산 네트워크 바이트를 0이라고 단정하지 않는다. 제안한 79회는 URL별 논리적 시도 예약에 적용했다. 직접 다운로드에는 파일당 20MiB·총 200MiB 한도와 TLS 검증/리다이렉트 미추적을 적용했다.

## 미검증

금액 종류·단위·연간 발주한도·사업기간·관리기간·관급·VAT 범위·실제 작업내용·작업구역·과금 기준 후보금액은 원문 미확보로 미확인이다. 23건 × 9항목 = 207행의 근거표에 사유를 남겼다. 문서명 후보는 공고 메타데이터의 파일명으로 별도 표시하고 실제 열람한 문서/페이지/근거문장으로 격상하지 않았다.

파일명 연도 불일치 후보 {len(filename_flags)}개를 `filename_review_flags.csv`에 남겼다. 특히 노후포장도로 노면표시 공고의 내역 파일명에 다른 연도가 들어 있는 경우에도 실제 사업연도를 바꾸지 않았다. 파일명 잔존/과년도 내역 사용 여부 등은 원문 확인이 필요하다.

`institution_verification_status.csv`는 우선확인 23건의 기관별 확인 상태이며 전체 후보 기관 집계를 대체하지 않는다. 검증된 금액·가격 표는 헤더만 남겼다. **검증 표본 없음은 0원 시장이라는 뜻이 아니다. X와 최종 과금 기준금액은 미정이다.** 기존 미검증 `reference_*`와 제목/시설/상업 검토 분류는 이번 접속 실패 때문에 변경하지 않았다. 공원녹지 용역·계약·정산 추가 수집도 하지 않았다.

## 다음 조치

네트워크 접근이 가능한 실행 환경에서 아래 명령으로 아직 시도하지 않은 75개 URL만 이어갈 수 있다. 이미 시도한 4개 URL은 재호출하지 않으며 승인 범위 밖 링크/추가 API를 탐색하지 않는다. 원문 파일이 로컬로 제공되면 정확한 공고번호+차수·첨부 ID에 연결해 확인할 수 있다. 현재 79개 계획의 승인 자체는 기록되어 있으며 다시 승인을 요구하는 상태가 아니다.

```powershell
.\\.venv\\Scripts\\python.exe tools\\fetch_sigongnote_priority_documents.py --download-approved --resume-unattempted
```

실제 확보한 원문에서 확인한 값만 페이지/표·직접 근거와 함께 기록한 뒤, 검증된 기간/기준금액 후보 표본에 한해 가격 가설을 비교한다. 사용자 설명의 5%를 기본 요율로 채택하거나 계약액을 추정하지 않는다.

## 실행·재현

```powershell
.\\.venv\\Scripts\\python.exe tools\\fetch_sigongnote_priority_documents.py --download-approved --limit 3
.\\.venv\\Scripts\\python.exe tools\\sigongnote_document_metadata.py
.\\.venv\\Scripts\\python.exe tools\\test_sigongnote_document_access.py
.\\.venv\\Scripts\\python.exe tools\\finalize_sigongnote_document_review.py
```

웹 읽기 도구는 별도 1회 사용했고 예약/실패 결과를 `.local/sigongnote_stage2_documents/fetch_ledger.jsonl`에 남겼다. 다운로드 승인은 같은 폴더 authorization.json의 계획 SHA-256에 연결했다. 전체 API/앱 테스트는 이번 범위 밖으로 SKIPPED, 실제 문서 파싱은 원문 미확보로 BLOCKED다. 매크로·문서 내부 코드 실행은 하지 않았다. 시스템 Python의 PDF 파서 사용 가능 여부만 확인했으며 성공적으로 문서를 판독했다고 표시하지 않았다.
'''
    (OUT/'document_review_summary.md').write_text(report,encoding='utf-8')
    for name in ('document_review_summary.md','fetch_summary.json','validation_results.json'):
        path=OUT/name;files[name]=dict(rows=len(path.read_text(encoding='utf-8').splitlines()),bytes=path.stat().st_size)
    result=dict(status='BLOCKED_SOURCE_ACCESS',authorization='승인됨',prior_stage2=str(SOURCE),source_plan_sha256=auth['plan_sha256'],
                planned_documents=len(fetches),priority_notices=len(cases),logical_attempts=len(reserved),failed_attempts=len(done),not_attempted=len(fetches)-len(reserved),
                verified_documents=0,verified_amount_sample_n=0,price_X='미정',original_writes=0,api_calls=0,checks=checks,files=files,finished_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    (OUT/'output_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(OUT/'sigongnote_document_review.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*files,'output_manifest.json']:z.write(OUT/name,arcname=name)
    with zipfile.ZipFile(OUT/'sigongnote_document_review.zip') as z:assert z.testzip() is None
    print(json.dumps(dict(status=result['status'],attempts=len(reserved),failed=len(done),skipped=result['not_attempted'],checks_passed=sum(checks.values()),checks_failed=sum(not v for v in checks.values()),verified_documents=0),ensure_ascii=False))
    assert all(checks.values())


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');main()
