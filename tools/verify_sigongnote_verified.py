"""Independent CSV/source-cache checks, no network or mutation of source evidence."""
import collections,csv,hashlib,json,re,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_stage2_verified'
def read(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
    sys.stdout.reconfigure(encoding='utf-8')
    notices=read('priority_notice_review.csv');evidence=read('priority_document_evidence.csv');summary=read('institution_verified_amount_summary.csv');register=read('document_review_register.csv')
    evid={r['evidence_id']:r for r in evidence};docs={r['document_key']:r for r in register};byid={r['priority_id']:r for r in notices}
    manifest=json.loads((OUT/'output_manifest.json').read_text(encoding='utf-8'))
    checks={}
    checks['input_hashes_unchanged']=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in manifest['input_sha256'].items())
    checks['23_exact_keys_and_unique']=len(notices)==len(byid)==len({(r['bid_ntce_no'],r['bid_ntce_ord']) for r in notices})==23
    checks['unique_evidence_ids']=len(evid)==len(evidence)
    checks['evidence_notice_revision_join']=all((e['bid_ntce_no'],e['bid_ntce_ord'])==(byid[e['priority_id']]['bid_ntce_no'],byid[e['priority_id']]['bid_ntce_ord']) for e in evidence)
    checks['evidence_document_notice_join']=all((e['bid_ntce_no'],e['bid_ntce_ord'])==(docs[e['document_key']]['bid_ntce_no'],docs[e['document_key']]['bid_ntce_ord']) for e in evidence if e['document_key'])
    checks['source_file_references_exist']=all((ROOT/r['source_local_path']).is_file() for r in register)
    checks['selected_amount_ref_exact']=all(evid[n['selected_reference_evidence_id']]['amount_krw']==n['selected_reference_amount'] for n in notices if n['selected_reference_amount'])
    checks['selected_not_unit_cached_or_conflicted']=all('단가' not in evid[n['selected_reference_evidence_id']]['amount_name'] and not evid[n['selected_reference_evidence_id']]['verification_status'].startswith('CACHED') for n in notices if n['selected_reference_amount'])
    members=[pid for s in summary for pid in s['priority_ids'].split(';')]
    checks['each_notice_in_exactly_one_institution_group']=collections.Counter(members)==collections.Counter(byid.keys())
    fields=('original_institution','institution_code','institution_type','notice_year','title_business_years','document_exclusive_group','document_commercial_tier','stated_period','selected_reference_amount_name','selected_reference_vat','selected_reference_gov','selected_reference_status')
    checks['homogeneous_period_amount_and_status']=all(all(byid[pid][k]==s[k] for k in fields) for s in summary for pid in s['priority_ids'].split(';'))
    checks['group_count_and_decimal_sum']=all(int(s['notice_count'])==len(s['priority_ids'].split(';')) and int(s['amount_sample_n'])==sum(bool(byid[p]['selected_reference_amount']) for p in s['priority_ids'].split(';')) and (Decimal(s['reference_sum_krw'])==sum(Decimal(byid[p]['selected_reference_amount']) for p in s['priority_ids'].split(';') if byid[p]['selected_reference_amount']) if s['reference_sum_krw'] else int(s['amount_sample_n'])==0) for s in summary)
    checks['unknowns_not_zero']=all(byid[p]['selected_reference_amount']=='' for p in ('P03','P13'))
    checks['no_actual_contract_or_start_or_rate_invented']=all(all(n[k]=='' for k in ('actual_start_date','actual_contract_amount','actual_execution_amount','fee_basis_amount','fee_X')) for n in notices)
    checks['separate_multi_core_one_group']=all(byid[p]['document_exclusive_group']=='복수핵심 통합(교통시설+노면표시)' for p in ('P13','P14','P15'))
    checks['new_construction_separate']=all(byid[p]['document_commercial_tier']=='별도사업모델' for p in ('P18','P19'))
    checks['180day_and_28day_not_annual_totals']=all(byid[p]['annual_total_explicit']=='' for p in ('P14','P23'))
    checks['annual_amounts_remain_with_unknown_scope']=all(byid[p]['annual_total_explicit'] and byid[p]['selected_reference_status']=='AMOUNT_CONFIRMED_SCOPE_PARTIAL' for p in ('P16','P17'))
    checks['raw_amount_matches_quoted_number']=all(e['amount_krw'] in [v.replace(',','') for v in re.findall(r'(?<![\d.])\d[\d,]*(?:\.\d+)?',e['evidence_text'])] for e in evidence if e['amount_krw']!='')
    checks['reviewed_document_count_matches_evidence']=manifest['counts']['reviewed_documents_with_evidence']==len({e['document_key'] for e in evidence if e['document_key']})
    checks['source_scope_count_matches']=manifest['counts']['vat_and_government_scope_confirmed_notices']==sum(n['vat_and_government_scope_confirmed']=='1' for n in notices)
    checks['no_urls_or_contacts_in_evidence']=all(not re.search(r'https?://|serviceKey\s*=|\b0\d{1,2}[-) ]\d{3,4}[- ]\d{4}\b|[\w.\-]+@[\w.\-]+',e['evidence_text'],re.I) for e in evidence)
    checks['all_five_required_outputs_nonempty']=all((OUT/n).is_file() and (OUT/n).stat().st_size>0 for n in ('document_review_summary.md','priority_notice_review.csv','priority_document_evidence.csv','institution_verified_amount_summary.csv','additional_documents_needed.csv'))
    result={'checks':checks,'passed':sum(checks.values()),'failed':[k for k,v in checks.items() if not v],'scope':'CSV/source metadata independent reconciliation; not final contract or nationwide completeness verification'}
    (OUT/'independent_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if result['failed']:raise SystemExit(1)
    report=OUT/'document_review_summary.md'
    text=report.read_text(encoding='utf-8').split('\n## 독립 재대조 실행 결과')[0]
    text+='\n## 독립 재대조 실행 결과\n\n'
    text+=f'python tools/verify_sigongnote_verified.py 실행: {result["passed"]}개 PASS, 실패 0개. 생성기의 27개 검사와 별도로 CSV를 다시 읽어 복합키/문서 연결, 금액 근거, 기관 그룹별 Decimal 합계와 각 공고 1회 포함을 확인했다. independent_validation.json에 결과를 저장했다.\n\n'
    text+='실행 도중 보고서 카운트 필드명 오타 1회와 독립 검사기의 Counter에 dict를 넘긴 오류 1회를 수정했다. 해당 실행을 성공으로 남기지 않았으며 수정 후 재생성·재대조를 완료했다. 이 수정은 원본 데이터와 과거 실패 기록에 영향을 주지 않는다.\n'
    report.write_text(text,encoding='utf-8')
    for p in sorted(OUT.iterdir()):
        if not p.is_file() or p.name=='output_manifest.json':continue
        rows=len(read(p.name)) if p.suffix=='.csv' else None
        manifest['files'][p.name]={'bytes':p.stat().st_size,'data_rows':rows}
    manifest['independent_validation_passed']=result['passed']
    (OUT/'output_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
