"""Record facts from the one user-supplied notice; never modifies prior stage outputs."""
import csv
import hashlib
import json
from pathlib import Path
import sys

from extract_hwp_notice_text import read_hwp

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_stage2_documents/local_document_review_20261008'
NOTICE_NO='R25BK01133840'; ORD='000'; DOC_ID='fb414d38ad6d2162'

def write_csv(path, rows, fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    source=next(p for p in (ROOT/'outputs').glob('*.hwp') if p.stat().st_size==92672)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    paragraphs=read_hwp(source)
    facts=[
      dict(field='notice_identity',value='R25BK01133840-000',status='document_verified',location='공고문 본문; 사전 승인 목록의 해당 첨부',evidence='2025년 교통노면표시 유지보수 연간단가(관리도로 북부권)',interpretation='공고번호·차수는 승인 목록과 일치'),
      dict(field='work_scope',value='차선재도색 공사 1식',status='document_verified',location='P7',evidence='공사개요 : 차선재도색 공사 1식',interpretation='공고문 기재 작업범위; 상세 물량내역은 미확인'),
      dict(field='stated_completion_period',value='착공일로부터 2025-12-19까지',status='partially_verified',location='P6',evidence='공사기간 : 착공일로부터 ~ 2025. 12. 19.까지',interpretation='종료일 확인; 착공일과 실제 관리기간은 특정되지 않음'),
      dict(field='unit_estimated_price_ex_vat_krw',value='137101',status='document_verified',location='P8',evidence='단가추정금액 : 금150,811원 (추정가격 금137,101원+부가가치세 금13,710원)',interpretation='단가 기준 추정가격; 실제 계약액·연간 과금 기준으로 보지 않음'),
      dict(field='unit_vat_krw',value='13710',status='document_verified',location='P8',evidence='단가추정금액 : 금150,811원 (추정가격 금137,101원+부가가치세 금13,710원)',interpretation='위 단가에 명시된 VAT'),
      dict(field='unit_base_price_incl_vat_krw',value='150811',status='document_verified',location='P9-P10',evidence='본 공사의 기초금액은 금150,811원 입니다',interpretation='단가 입찰 기초금액; P10에서 단가 입찰임을 명시; 총액으로 해석하지 않음'),
      dict(field='annual_total_expected_contract_amount_krw',value='99900000',status='document_verified_tax_scope_unknown',location='P11',evidence='연간 총도급예정액은 금99,900,000원 입니다.',interpretation='공고상 연간 도급 예정액; VAT 포함 여부·물량/한도 산식·실제 계약액 미확인'),
      dict(field='government_supplied_material_scope',value='',status='not_stated_in_notice',location='공고문 본문 P1-P87 검토; 내역서 미확보',evidence='',interpretation='관급자재 범위·금액 근거 없음; 공란/0원으로 단정하지 않음'),
    ]
    text='\n'.join(paragraphs)
    for fact in facts:
        if fact['evidence']: assert fact['evidence'] in text, fact['field']
    OUT.mkdir(parents=True,exist_ok=True)
    write_csv(OUT/'verified_document_facts.csv',facts,list(facts[0]))
    with (ROOT/'outputs/sigongnote_market_stage2_documents/priority_notice_review.csv').open(encoding='utf-8-sig',newline='') as f:
        notices=list(csv.DictReader(f))
    statuses=[]
    for n in notices:
        matched=n.get('bid_ntce_no')==NOTICE_NO and str(n.get('bid_ntce_ord','')).zfill(3)==ORD
        statuses.append({'priority_id':n.get('priority_id',''),'bid_ntce_no':n.get('bid_ntce_no',''),'bid_ntce_ord':n.get('bid_ntce_ord',''),
                         'local_document_review_status':'CONTENT_READ' if matched else 'SOURCE_NOT_PRESENT_LOCALLY',
                         'document_id':DOC_ID if matched else '', 'notice_facts_verified':8 if matched else 0,
                         'note':'provided HWP read; other listed source documents unavailable locally' if matched else 'not downloaded or locally present; no content verified'})
    assert len(statuses)==23 and sum(r['local_document_review_status']=='CONTENT_READ' for r in statuses)==1
    write_csv(OUT/'priority_notice_document_status.csv',statuses,list(statuses[0]))
    manifest={'status':'PARTIAL_DOCUMENT_VERIFICATION','notices_in_priority_list':23,'notice_documents_read':1,'notice_documents_not_available_locally':22,
              'approved_attachment_urls':79,'provided_original_files_read':1,'other_approved_attachments_not_read':78,
              'source_file':str(source.relative_to(ROOT)),'source_bytes':source.stat().st_size,'source_sha256':sha,
              'parser':'tools/extract_hwp_notice_text.py; local HWP5 text records only; embedded scripts/macros not executed',
              'facts':len(facts),'all_quote_checks_passed':True,'market_size_verified':False,'contract_amount_verified':False,'price_X':'미정'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    report='''# 우선확인 공고 로컬 원문 추가 검토

## 완료

사용자가 outputs에 둔 HWP 공고문 1개(92,672바이트)를 승인 목록의 R25BK01133840-000 / 첨부 ID fb414d38ad6d2162에 연결해 읽었다. HWP5 본문 텍스트 스트림을 읽었으며 내장 스크립트와 매크로는 실행하지 않았다. 원본은 수정하지 않았다. 인용문은 페이지가 아닌 추출된 본문 문단 번호다.

- 작업: P7 “공사개요 : 차선재도색 공사 1식”.
- 기간: P6 “공사기간 : 착공일로부터 ~ 2025. 12. 19.까지”. 종료일은 확인되지만 착공일과 실제 관리기간은 알 수 없다.
- 단가 추정가격: P8 “단가추정금액 : 금150,811원 (추정가격 금137,101원+부가가치세 금13,710원)”. 추정가격 137,101원은 단가 기준이며 VAT 별도다.
- 단가 기초금액: P9-P10에서 기초금액 150,811원으로 투찰하라고 명시한다. 총액으로 해석하지 않는다.
- 연간 도급 예정액: P11 “연간 총도급예정액은 금99,900,000원 입니다.” VAT 포함 여부와 수량/한도 산식이 기재되지 않았다. 계약 체결액이 아니다.
- 관급: 공고문에서 관급자재 범위와 금액 근거를 찾지 못했다. 상세 내역서가 없어 0원이나 관급 없음으로 판정하지 않았다.

필드별 인용은 verified_document_facts.csv에 기록했다. 나머지 22개 우선확인 공고 자료는 로컬 outputs에서 찾지 못해 미검증으로 유지했다. 승인된 79개 첨부 중 로컬 제공 문서는 1개이며 나머지 78개는 아직 본문을 읽지 못했다. 이전 접속 실패 기록은 변경하지 않았다.

## 미검증과 가격 해석

99,900,000원은 공고문에 기재된 예정액 표기일 뿐 실제 계약액이나 확정 과금 기준금액이 아니다. 단가와 연간 합계의 산출 연결, VAT 처리, 관급 범위, 실제 계약·집행·정산은 미확인이다. 후보 CSV의 기존 reference 계열 값은 그대로 두었다. 시장규모를 확정하거나 X를 정하지 않았다.

## 다음 조치

다른 우선확인 공고의 첨부 원문이 로컬에 추가되면 동일 방식으로 공고번호+차수에 연결하고 확인된 사실만 근거와 함께 보완한다. 남은 첨부의 네트워크 재접속은 이전 진단의 실행환경 TCP 443 차단이 해결되어야 한다.
'''
    (OUT/'local_document_review_addendum.md').write_text(report,encoding='utf-8')
    print(json.dumps({'status':manifest['status'],'notices_read':1,'notices_pending':22,'attachments_read':1,'attachments_pending':78,'quote_checks':len(facts),'all_quotes_present':True,'source_sha256':sha},ensure_ascii=False))

if __name__=='__main__': main()
