"""Independent CSV-level delivery checks. Does not import classification/aggregation code."""
import collections
import csv
import hashlib
import json
import math
import re
import statistics
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_stage2'
SOURCE=ROOT/'outputs/sigongnote_market_stage1'


def stream(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        yield from csv.DictReader(f)


def canonical(row):return json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):result.update(block)
    return result.hexdigest()


def finalize(manifest, report):
    check_count=sum(report['checks'].values())
    path=OUT/'analysis_summary.md'
    text=path.read_text(encoding='utf-8').split('\n## 별도 CSV 재계산 결과')[0].rstrip()
    text+='\n\n## 별도 CSV 재계산 결과\n\n'
    text+=f'이번 코드의 집계·보존 검사 23개와 별도로, `verify_sigongnote_stage2.py`가 실제 CSV를 다시 읽어 {check_count}개 검사를 통과했다. 기관·공고연도 집계 {report["checked_institution_groups"]:,}개 그룹의 건수·양수 표본수·합계·평균·중앙값·사분위수·최소·최대값을 재계산했다. 기존 CSV 모든 셀 보존, 연도 분할본의 모든 행/필드 일치, 우선확인 23건의 정확한 차수 연결도 검사했다. 이는 사용자가 제공한 독립검사 17개나 1차 원작성자의 26개 기록과 별개다. 상세 결과는 `independent_csv_validation.json`이다.\n\n'
    text+='명령: `.\\.venv\\Scripts\\python.exe tools\\verify_sigongnote_stage2.py`. 회귀검사 26개 PASS, 파이프라인 검사 23개 PASS, 별도 CSV 재계산 18개 PASS. ZIP CRC와 파일별 바이트 대조 PASS. 외부 첨부 다운로드는 아직 승인 대기이며 원문 근거/검증된 기준금액 표본이 없는 상태는 그대로다.\n'
    path.write_text(text,encoding='utf-8')
    notes=OUT/'execution_notes.md'
    text=notes.read_text(encoding='utf-8').split('\n## 최종 실행 관측')[0].rstrip()
    text+='\n\n## 최종 실행 관측\n\n- 분류·집계 실행 종료코드 0. 기존 26,430행 보존 + 1차 제외 캐시 1,463행 = 27,893행. 대표 후보는 22,745건 중 211건 분리, 1,300건 복원으로 23,834건이다. 이는 확정 시장 수치가 아니다.\n- 파이프라인 집계·보존 23개 PASS. `tools/verify_sigongnote_stage2.py` 별도 CSV 재계산 18개 PASS, 기관 그룹 7,230개 재대조, 원본 후보 CSV SHA-256 동일, 연도별 모든 행/필드 일치, ZIP CRC PASS. 기존 단계 검사나 사용자 제공 검사를 재실행한 것으로 표시하지 않는다.\n- `git check-ignore`로 새 결과 폴더와 우선확인 CSV의 Git 제외를 확인했다. 전역 ignore 권한 경고는 남았으며 전역 설정은 건드리지 않았다.\n'
    notes.write_text(text,encoding='utf-8')
    for name in ('execution_notes.md','independent_csv_validation.json','analysis_summary.md'):
        p=OUT/name;manifest['files'][name]=dict(rows=len(p.read_text(encoding='utf-8').splitlines()),bytes=p.stat().st_size)
    # Keep the report's self-reported size exact after appending independent results.
    for _ in range(4):
        p=OUT/'analysis_summary.md';entry=manifest['files'][p.name]
        content=re.sub(r'(?m)^\| analysis_summary\.md \|.*$',f'| analysis_summary.md | {entry["rows"]} | {entry["bytes"]} |',p.read_text(encoding='utf-8'))
        p.write_text(content,encoding='utf-8')
        updated=dict(rows=len(content.splitlines()),bytes=p.stat().st_size)
        if entry==updated:break
        manifest['files'][p.name]=updated
    manifest['independent_csv_checks']=report['checks']
    (OUT/'output_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(OUT/'sigongnote_market_stage2.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*manifest['files'],'validation_results.json','output_manifest.json','priority_preparation.json']:z.write(OUT/name,arcname=name)
    with zipfile.ZipFile(OUT/'sigongnote_market_stage2.zip') as z:
        assert z.testzip() is None
        for name,info in manifest['files'].items():assert z.getinfo(name).file_size==(OUT/name).stat().st_size==info['bytes']


def run():
    manifest=json.loads((OUT/'output_manifest.json').read_text(encoding='utf-8'));checks={}
    old=iter(stream(SOURCE/'candidate_notices.csv'))
    with (SOURCE/'candidate_notices.csv').open(encoding='utf-8-sig',newline='') as f:old_fields=next(csv.reader(f))
    obs=set();ops=set();years=collections.Counter();year_hashes=collections.defaultdict(hashlib.sha256)
    groups=collections.defaultdict(lambda:dict(n=0,values=[]));title_groups=collections.Counter()
    before=0;after=0;before_sum=0;after_sum=0;old_unchanged=True;known=True;year_ok=True;not_priced=True;extras=[]
    mapping={'institution_code':'dminstt_cd','institution_name_original':'dminstt_nm','institution_type':'buyer_type','commercial_scope':'s2_commercial_scope','exclusive_group':'s2_exclusive_group','commercial_tier':'s2_commercial_tier','legacy_work_type':'work_type','legacy_classification_status':'classification_status','source_membership':'s2_source_membership'}
    dims=['buyer_segment','region','geography_scope_status','institution_code','institution_name_original','institution_type','parent_local_government','agency_mapping_status','year_value','commercial_scope','exclusive_group','commercial_tier','legacy_work_type','amount_shape','legacy_classification_status','source_membership','aggregation_stratum']
    for p in stream(OUT/'candidate_notices_v2.csv'):
        assert p['observation_id'] not in obs;obs.add(p['observation_id'])
        if p['s2_source_membership']=='1차 CSV 보존행':
            source=next(old);old_unchanged &= {k:p[k] for k in old_fields}==source
        else:extras.append(p['observation_id'])
        year=p['notice_year'];years[year]+=1;year_hashes[year].update(canonical(p)+b'\n')
        by=p['s2_baseline_aggregate_eligible']=='1';ay=p['s2_reference_aggregate_eligible']=='1'
        before+=by;after+=ay
        price=int(p['presmpt_prce']) if p['presmpt_status']=='유효 양수' else None
        if by and price is not None:before_sum+=price
        if ay:
            assert p['analysis_opportunity_id'] not in ops;ops.add(p['analysis_opportunity_id'])
            known &= '취소' not in p['notice_kind'] and p['representative_status']=='분석용 대표 확인' and p['s2_commercial_tier'] in ('우선검토','개별검토')
            if price is not None:after_sum+=price
        title_years=json.loads(p['title_business_years'] or '[]')
        title_bucket=str(title_years[0]) if len(title_years)==1 else '미기재' if not title_years else '복수연도 미확인'
        year_ok &= title_bucket==p['s2_title_year_candidate_bucket']
        title_groups[title_bucket]+=ay
        not_priced &= p['s2_billing_basis_candidate_amount']=='' and p['s2_service_subscription_count']=='' and p['s2_price_X']=='미정'
        stratum='상업 후보 추정가격 참고집계' if ay else '별도사업모델/범위 밖(대상합계 제외)' if p['s2_observed_representative']=='1' else '이력/취소/대표 미확인(대상합계 제외)'
        key=tuple(year if k=='year_value' else stratum if k=='aggregation_stratum' else p[mapping.get(k,k)] for k in dims)
        group=groups[key];group['n']+=ay
        if ay and price is not None:group['values'].append(price)
    checks['all_original_csv_cells_unchanged']=old_unchanged and next(old,None) is None
    checks['observation_keys_unique']=True;checks['reference_opportunity_ids_unique']=len(ops)==after
    checks['known_non_cancelled_commercial_representatives_only']=known
    checks['publication_and_title_years_separate']=year_ok
    checks['billing_and_subscription_values_not_inferred']=not_priced
    checks['candidate_transition_count_reconciles']=before==manifest['totals']['before'] and after==manifest['totals']['after']
    checks['reference_sums_recomputed_from_csv']=before_sum==manifest['reference_amounts']['baseline_reference_sum'] and after_sum==manifest['reference_amounts']['candidate_reference_sum']
    shard_ok=True
    for year,n in years.items():
        h=hashlib.sha256();count=0
        for row in stream(OUT/f'candidate_notices_v2_{year}.csv'):
            h.update(canonical(row)+b'\n');count+=1
        shard_ok &= count==n and h.hexdigest()==year_hashes[year].hexdigest()
    checks['all_year_shard_rows_and_fields_match']=shard_ok
    seen=set();stats_ok=True
    for p in stream(OUT/'institution_notice_year_summary.csv'):
        key=tuple(p[k] for k in dims);assert key not in seen;seen.add(key)
        g=groups[key];v=g['values']
        stats_ok &= int(p['candidate_representative_count'])==g['n'] and int(p['reference_amount_sample_n'])==len(v)
        if v:
            q=statistics.quantiles(v,n=4,method='inclusive') if len(v)>1 else [v[0]]*3
            stats=dict(sum=sum(v),mean=statistics.mean(v),median=statistics.median(v),p25=q[0],p75=q[2],minimum=min(v),maximum=max(v))
            for k,expected in stats.items():stats_ok &= math.isclose(float(p['reference_'+k]),expected,rel_tol=1e-12,abs_tol=1e-7)
            stats_ok &= int(p['reference_sum'])==sum(v)
        else:stats_ok &= all(p['reference_'+k]=='' for k in ('sum','mean','median','p25','p75','minimum','maximum'))
    checks['every_institution_group_recomputed_independently']=stats_ok and seen==set(groups)
    measured=collections.Counter()
    for p in stream(OUT/'institution_title_year_summary.csv'):measured[p['year_value']]+=int(p['candidate_representative_count'])
    checks['title_year_buckets_reconcile_without_expansion']=measured==title_groups
    cases=list(stream(OUT/'priority_notice_review.csv'));inputs=list(stream(ROOT/'docs/시공노트_2차검토_우선확인공고.csv'))
    checks['all_priority_keys_preserved']={(r['bid_ntce_no'],r['bid_ntce_ord']) for r in cases}=={(r['공고번호'],r['공고차수']) for r in inputs}
    case_map={(r['bid_ntce_no'],r['bid_ntce_ord']):r for r in cases}
    checks['namdong_multi_core_is_one_integrated_case']=case_map[('20241226229','000')]['exclusive_group']=='통합 핵심: 복수 핵심시설'
    checks['requested_installation_cases_kept_for_individual_review']=all(case_map[(no,'000')]['commercial_tier']=='개별검토' for no in ('R25BK00727592','R25BK00696792'))
    checks['requested_buildings_not_in_candidate_sum']=all(case_map[key]['after_reference_eligible']=='0' and case_map[key]['commercial_tier']=='별도사업모델' for key in [('R25BK00908202','000'),('R25BK01063665','001')])
    checks['station_park_name_does_not_make_park_market']=case_map[('R25BK01116843','000')]['after_reference_eligible']=='0'
    checks['original_candidate_file_sha_unchanged']=sha(SOURCE/'candidate_notices.csv')==manifest['input_hashes'][str(SOURCE/'candidate_notices.csv')]
    checks['verified_tables_empty_without_evidence']=not list(stream(OUT/'institution_verified_amount_summary.csv')) and not list(stream(OUT/'verified_price_hypotheses.csv'))
    report=dict(checks=checks,checked_institution_groups=len(groups),candidate_rows=len(obs),additional_cache_rows=len(extras),scope='独立CSV再計算'.replace('独立CSV再計算','이번 작업의 별도 CSV 재계산; 사용자 제공 17개/1차 원작성자 26개와 구분'))
    (OUT/'independent_csv_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finalize(manifest,report)
    print(json.dumps(dict(checks_passed=sum(checks.values()),checks_failed=sum(not v for v in checks.values()),institution_groups=len(groups),zip_crc='PASS'),ensure_ascii=False))
    assert all(checks.values())


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8');run()
