"""Offline stage-two commercial review using preserved CSVs and a read-only cache."""
from __future__ import annotations
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

from sigongnote_stage2_rules import RULE, array, commercial, integer, truth
from prepare_sigongnote_stage2 import prepare

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'outputs/sigongnote_market_stage1'
OUT = ROOT/'outputs/sigongnote_market_stage2'
WORK = ROOT/'.local/sigongnote_stage2'
CACHE = ROOT/'.local/sigongnote_stage1/derived.sqlite3'
PRIORITY = ROOT/'docs/시공노트_2차검토_우선확인공고.csv'


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()


def signature(path):
    s=path.stat();return dict(path=str(path.resolve()),bytes=s.st_size,mtime_ns=s.st_mtime_ns)


def cell(value):
    if value is None:return ''
    if isinstance(value,bool):return '1' if value else '0'
    if isinstance(value,(list,dict)):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+value
    return value


def write_csv(name,rows,fields=None):
    iterator=iter(rows);first=next(iterator,None)
    if fields is None:fields=list(first) if first else []
    assert fields
    count=0
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');writer.writeheader()
        if first is not None:
            writer.writerow({k:cell(first.get(k,'')) for k in fields});count+=1
        for row in iterator:
            writer.writerow({k:cell(row.get(k,'')) for k in fields});count+=1
    return dict(rows=count,bytes=(OUT/name).stat().st_size)


def quantiles(values):
    if not values:return dict(sum='',mean='',median='',p25='',p75='',minimum='',maximum='')
    values=sorted(values)
    def percentile(p):
        pos=(len(values)-1)*p;i=int(pos)
        return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(pos-i)
    return dict(sum=sum(values),mean=sum(values)/len(values),median=percentile(.5),p25=percentile(.25),p75=percentile(.75),minimum=values[0],maximum=values[-1])


def observed_representative(row):
    return (truth(row.get('is_opportunity_representative')) and truth(row.get('in_scope'))
            and row.get('representative_status')=='분석용 대표 확인'
            and bool(row.get('analysis_opportunity_id')) and '취소' not in (row.get('notice_kind') or ''))


def augment(row,origin,priority_keys):
    additions=commercial(row)
    is_repr=observed_representative(row)
    key=(row['bid_ntce_no'],row['bid_ntce_ord'])
    additions.update(s2_source_membership=origin,s2_priority_input_match=key in priority_keys,
                     s2_observed_representative=is_repr,
                     s2_reference_aggregate_eligible=bool(is_repr and additions['s2_commercial_candidate']),
                     s2_not_a_confirmed_market=True,s2_document_review_status='승인대기/미검토' if key in priority_keys else '미검토',
                     s2_baseline_aggregate_eligible=truth(row.get('aggregate_eligible')) if origin=='1차 CSV 보존행' else False,
                     s2_baseline_field_hash=hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    assert not set(additions).intersection(row)
    return {**row,**additions}


def load(db,cache,priorities):
    priority_keys={(r['공고번호'],r['공고차수']) for r in priorities}
    db.executescript('DROP TABLE IF EXISTS rows; CREATE TABLE rows(seq INTEGER PRIMARY KEY,obs TEXT UNIQUE,no TEXT,ord TEXT,origin TEXT,before_n INTEGER,after_n INTEGER,payload TEXT);CREATE INDEX row_key ON rows(no,ord);')
    keys=set();seq=0;batch=[]
    with (SOURCE/'candidate_notices.csv').open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames
        for row in reader:
            seq+=1;key=(row['bid_ntce_no'],row['bid_ntce_ord']);keys.add(key)
            p=augment(row,'1차 CSV 보존행',priority_keys)
            batch.append((seq,p['observation_id'],*key,p['s2_source_membership'],int(p['s2_baseline_aggregate_eligible']),int(p['s2_reference_aggregate_eligible']),json.dumps(p,ensure_ascii=False)))
            if len(batch)>=500:db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?,?)',batch);batch=[]
    db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?,?)',batch);db.commit()
    print('Preserved baseline CSV rows:',seq,flush=True)
    # One sequential pass avoids the random-index joins used in the earlier review sample.
    pending={};scanned=0
    for record in cache.execute('SELECT no,ord,payload FROM classified NOT INDEXED'):
        scanned+=1;key=(record['no'],record['ord'])
        if key in keys:continue
        p=json.loads(record['payload']);review=commercial(p)
        if review['s2_commercial_candidate'] or key in priority_keys:pending[key]=p
    wanted={k[0] for k in pending};nodes={};links={}
    for node in cache.execute('SELECT no,ord,selection,reason,cancel_history,nrev FROM nodes NOT INDEXED'):
        if node['no'] in wanted:nodes[node['no']]=dict(node)
    for link in cache.execute('SELECT * FROM links'):
        if link['no'] in wanted:links[link['no']]=dict(link)
    batch=[]
    for key,p in sorted(pending.items()):
        no,order=key;n=nodes.get(no);link=links.get(no)
        assert n is not None
        op_id=link['op_id'] if link else 'OBS:'+no
        terminal=link['terminal'] if link else no
        known=n['selection']=='분석용 대표 확인' and bool(op_id)
        selected=order==n['ord'];terminal_row=selected and terminal==no
        p.update(analysis_opportunity_id=op_id if known else '',provisional_family_id='FAMILY:'+no,
                 is_family_representative=selected,is_opportunity_representative=terminal_row and known,
                 is_provisional_terminal=terminal_row,representative_status=n['selection'],representative_reason=n['reason'],
                 renotice_link_status=link['status'] if link else '독립 관측 공고군',renotice_link_reason=link['reason'] if link else '명시적 재공고 연결 관측 없음',
                 family_revision_count=n['nrev'],aggregate_eligible=False,reference_aggregate_eligible=False,
                 cancellation_assessment='취소 관측' if '취소' in (p['notice_kind'] or '') else '과거 취소·후속 비취소 관측' if n['cancel_history'] and selected else '비취소 관측',
                 inclusion_status='1차 CSV 밖의 캐시 판정 보존; 2차 상업 검토 재탐색',confirmed_market_eligible=False,verified_business_amount_eligible=False,
                 amount_kind='공고상 추정가격(원; 부가가치세·조달수수료 제외)',amount_usability='총액성·계약액 미검증; 2차 대표 여부 참조',
                 license_fetch_state='이번 추가 후보에서는 보조면허 재조회 미실행',region_fetch_state='이번 추가 후보에서는 허용지역 재조회 미실행')
        original={field:cell(p.get(field,'')) for field in fields}
        result=augment(original,'1차 제외 캐시 재검토(1차 CSV 외)',priority_keys)
        seq+=1;batch.append((seq,result['observation_id'],*key,result['s2_source_membership'],0,int(result['s2_reference_aggregate_eligible']),json.dumps(result,ensure_ascii=False)))
        if len(batch)>=500:db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?,?)',batch);batch=[]
    db.executemany('INSERT INTO rows VALUES (?,?,?,?,?,?,?,?)',batch);db.commit()
    return fields,dict(classified_rows_scanned=scanned,supplemental_rows=len(pending),baseline_rows=seq-len(pending),combined_rows=seq)


def rows(db):
    for record in db.execute('SELECT payload FROM rows ORDER BY seq'):yield json.loads(record[0])


def summaries(db,basis):
    groups={}
    for p in rows(db):
        year=p['notice_year'] if basis=='notice_year' else p['s2_title_year_candidate_bucket']
        stratum='상업 후보 추정가격 참고집계' if p['s2_reference_aggregate_eligible'] else '별도사업모델/범위 밖(대상합계 제외)' if p['s2_observed_representative'] else '이력/취소/대표 미확인(대상합계 제외)'
        names=['buyer_segment','region','geography_scope_status','dminstt_cd','dminstt_nm','buyer_type','parent_local_government','agency_mapping_status']
        dims=[p.get(k,'') for k in names]+[year,p['s2_commercial_scope'],p['s2_exclusive_group'],p['s2_commercial_tier'],p['work_type'],p['amount_shape'],p['classification_status'],p['s2_source_membership'],stratum]
        key=tuple(dims)
        g=groups.setdefault(key,dict(rows=0,before=0,after=0,annual=0,unverified=0,values=[],missing=0,zero=0,anomaly=0))
        g['rows']+=1;g['before']+=int(p['s2_baseline_aggregate_eligible']);g['after']+=int(p['s2_reference_aggregate_eligible'])
        if p['s2_reference_aggregate_eligible']:
            g['annual']+=int(truth(p['annual_unit_candidate']));g['unverified']+=1
            value=integer(p['presmpt_prce'])
            if p['presmpt_status']=='유효 양수' and value is not None and value>0:g['values'].append(value)
            elif p['presmpt_status']=='NULL':g['missing']+=1
            elif p['presmpt_status']=='0':g['zero']+=1
            else:g['anomaly']+=1
    result=[]
    field_names=['buyer_segment','region','geography_scope_status','institution_code','institution_name_original','institution_type','parent_local_government','agency_mapping_status','year_value','commercial_scope','exclusive_group','commercial_tier','legacy_work_type','amount_shape','legacy_classification_status','source_membership','aggregation_stratum']
    for key,g in sorted(groups.items()):
        r=dict(zip(field_names,key));r.update(year_basis=basis,period_label='공고 달력연도 주 비교' if basis=='notice_year' and r['year_value'] in ('2024','2025') else '공고 저장기간 부분연도' if basis=='notice_year' else '제목 사업연도 후보(실제 집행/관리기간 아님)',observation_rows=g['rows'],baseline_representative_count=g['before'],candidate_representative_count=g['after'],annual_unit_candidate_count=g['annual'],unverified_amount_count=g['unverified'],reference_amount_sample_n=len(g['values']),amount_missing_count=g['missing'],amount_zero_count=g['zero'],amount_anomalous_count=g['anomaly'],reference_amount_kind='공고 기재 추정가격 — 총액성·계약액 미검증',reference_amount_unit='KRW',verified_billing_basis_sample_n=0,verified_billing_basis_sum='',verified_billing_basis_status='미검증; 0원 아님',price_X='미정',rule_version=RULE)
        r.update({'reference_'+k:v for k,v in quantiles(g['values']).items()});result.append(r)
    return result


def comparison(db):
    grouped={};details=[];transitions=collections.Counter()
    for p in rows(db):
        key=(p['notice_year'],p['buyer_segment'],p['core_or_expansion'],p['primary_class'],p['work_type'],p['amount_shape'],p['s2_commercial_scope'],p['s2_exclusive_group'],p['s2_commercial_tier'],p['s2_source_membership'])
        g=grouped.setdefault(key,dict(observations=0,before=0,after=0,before_values=[],after_values=[]))
        g['observations']+=1;b=p['s2_baseline_aggregate_eligible'];a=p['s2_reference_aggregate_eligible'];g['before']+=int(b);g['after']+=int(a)
        price=integer(p['presmpt_prce'])
        if p['presmpt_status']=='유효 양수' and price is not None and price>0:
            if b:g['before_values'].append(price)
            if a:g['after_values'].append(price)
        action='참고집계 유지(상업 검토축 추가)' if b and a else '대상합계에서 분리' if b else '1차 제외에서 상업 검토 복원' if a else '이력/미확인/범위 밖 보존'
        transitions[action]+=1
        details.append(dict(observation_id=p['observation_id'],bid_ntce_no=p['bid_ntce_no'],bid_ntce_ord=p['bid_ntce_ord'],title=p['title'],notice_year=p['notice_year'],title_business_years=p['title_business_years'],institution_code=p['dminstt_cd'],institution_name=p['dminstt_nm'],legacy_primary_class=p['primary_class'],legacy_work_type=p['work_type'],legacy_classification_status=p['classification_status'],commercial_tier=p['s2_commercial_tier'],exclusive_group=p['s2_exclusive_group'],commercial_scope=p['s2_commercial_scope'],facility_query_tags=p['s2_facility_query_tags'],change_action=action,before_reference_eligible=b,after_reference_eligible=a,presmpt_prce=p['presmpt_prce'],amount_shape=p['amount_shape'],reason=p['s2_commercial_reason'],evidence_level=p['s2_evidence_level'],rule_version=RULE))
    result=[]
    fields=['notice_year','buyer_segment','legacy_scope','legacy_primary_class','legacy_work_type','amount_shape','commercial_scope','exclusive_group','commercial_tier','source_membership']
    for key,g in sorted(grouped.items()):
        r=dict(zip(fields,key));r.update(observation_rows=g['observations'],before_representative_count=g['before'],after_representative_count=g['after'],representative_delta=g['after']-g['before'],before_reference_sample_n=len(g['before_values']),after_reference_sample_n=len(g['after_values']),before_reference_sum=sum(g['before_values']) if g['before_values'] else '',after_reference_sum=sum(g['after_values']) if g['after_values'] else '',reference_sum_delta=sum(g['after_values'])-sum(g['before_values']) if g['before_values'] or g['after_values'] else '',amount_meaning='구성 변경에 따른 미검증 추정가격 참고값 차이; 실제 시장 증감/과금액 아님',rule_version=RULE);result.append(r)
    return result,details,dict(transitions)


def priority_evidence(db,priorities,plan):
    case_rows=[];facts=[];by_key=collections.defaultdict(list)
    for r in plan:by_key[(r['bid_ntce_no'],r['bid_ntce_ord'])].append(r)
    fields_to_verify=('금액종류·단위·총액성','연간발주한도','실제사업기간','실제관리기간','관급재료 범위','VAT 범위','대표 작업내용','작업구역','과금 기준 후보금액')
    for i,r in enumerate(priorities,1):
        key=(r['공고번호'],r['공고차수']);record=db.execute('SELECT payload FROM rows WHERE no=? AND ord=? ORDER BY seq LIMIT 1',key).fetchone();p=json.loads(record[0]) if record else {}
        case_rows.append(dict(priority_id=f'P{i:02}',bid_ntce_no=key[0],bid_ntce_ord=key[1],title=r['공고명'],original_institution=r['수요기관'],notice_year=p.get('notice_year',''),title_business_years=p.get('title_business_years',''),legacy_primary_class=p.get('primary_class',''),legacy_work_type=p.get('work_type',''),commercial_tier=p.get('s2_commercial_tier',''),exclusive_group=p.get('s2_exclusive_group',''),before_reference_eligible=p.get('s2_baseline_aggregate_eligible',False),after_reference_eligible=p.get('s2_reference_aggregate_eligible',False),recorded_presmpt_prce=p.get('presmpt_prce',''),recorded_bdgt_amt=p.get('bdgt_amt',''),recorded_vat=p.get('vat',''),recorded_govsplyAmt=p.get('govsplyAmt',''),budget_price_vat_residual=p.get('s2_budget_price_vat_residual',''),small_annual_amount_review=p.get('s2_small_annual_amount_review',False),reason=p.get('s2_commercial_reason',''),review_question=r['검토이유'],source_response_id=p.get('source_response_id',''),notice_url=p.get('notice_url',''),attachment_urls=[x['attachment_url'] for x in by_key[key]],document_status='SKIPPED_APPROVAL_PENDING',document_name='',page_or_table='',evidence_sentence='',verified_amount='',verified_business_period='',verified_management_period='',rule_version=RULE))
        for field in fields_to_verify:
            facts.append(dict(priority_id=f'P{i:02}',bid_ntce_no=key[0],bid_ntce_ord=key[1],fact_name=field,verification_status='未確認'.replace('未確認','미확인'),reason='원문 미확보; 다운로드 승인 대기',verified_value='',unit='',document_id='',document_name='',page_or_table='',evidence_sentence='',existing_metadata_title=p.get('title',''),metadata_is_not_document_evidence=True,source_response_id=p.get('source_response_id',''),rule_version=RULE))
    return case_rows,facts


def validate(db,old_fields,funnel,notice_summary,title_summary,priorities,files,initial):
    checks={};totals=collections.Counter();opportunities=set();positive=[];legacy_values=[];priority_seen=set();changes=[]
    for p in rows(db):
        totals['rows']+=1;totals['before']+=int(p['s2_baseline_aggregate_eligible']);totals['after']+=int(p['s2_reference_aggregate_eligible'])
        original={k:p.get(k,'') for k in old_fields}
        if p['s2_source_membership']=='1차 CSV 보존행':
            good=hashlib.sha256(json.dumps(original,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==p['s2_baseline_field_hash'];changes.append(good)
        if p['s2_priority_input_match']:priority_seen.add((p['bid_ntce_no'],p['bid_ntce_ord']))
        val=integer(p['presmpt_prce'])
        if p['s2_baseline_aggregate_eligible'] and p['presmpt_status']=='유효 양수':legacy_values.append(val)
        if p['s2_reference_aggregate_eligible']:
            assert p['analysis_opportunity_id'] not in opportunities;opportunities.add(p['analysis_opportunity_id'])
            assert '취소' not in p['notice_kind'] and p['representative_status']=='분석용 대표 확인'
            assert p['s2_commercial_tier'] in ('우선검토','개별검토')
            if p['presmpt_status']=='유효 양수':positive.append(val)
        assert p['s2_price_X']=='미정' and p['s2_billing_basis_candidate_amount']=='' and p['s2_service_subscription_count']==''
    source_manifest=json.loads((SOURCE/'output_manifest.json').read_text(encoding='utf-8'))
    checks['baseline_row_count']=funnel['baseline_rows']==source_manifest['files']['candidate_notices.csv']['rows']
    checks['baseline_field_values_preserved']=all(changes) and len(changes)==funnel['baseline_rows']
    checks['baseline_representative_count']=totals['before']==source_manifest['overall']['representative_candidates']
    checks['baseline_positive_reference_sample']=len(legacy_values)==source_manifest['overall']['positive_reference_amount_rows']
    with (SOURCE/'institution_year_summary.csv').open(encoding='utf-8-sig',newline='') as f:
        baseline_sum=sum(integer(r['reference_sum']) or 0 for r in csv.DictReader(f))
    checks['baseline_reference_sum_preserved']=sum(legacy_values)==baseline_sum
    checks['all_classified_rows_examined']=funnel['classified_rows_scanned']==source_manifest['funnel']['geography_or_buyer_scope']
    checks['priority_all_exact_keys_present']=priority_seen=={(p['공고번호'],p['공고차수']) for p in priorities}
    checks['one_representative_per_opportunity']=len(opportunities)==totals['after']
    checks['known_non_cancelled_representatives_only']=True
    checks['separate_models_not_in_candidate_reference_sums']=True
    checks['prices_and_subscription_counts_not_inferred']=True
    checks['notice_groups_count_reconcile']=sum(r['candidate_representative_count'] for r in notice_summary)==totals['after']
    checks['notice_groups_rows_reconcile']=sum(r['observation_rows'] for r in notice_summary)==totals['rows']
    checks['notice_reference_sample_reconcile']=sum(r['reference_amount_sample_n'] for r in notice_summary)==len(positive)
    checks['notice_reference_sum_reconcile']=sum(r['reference_sum'] or 0 for r in notice_summary)==sum(positive)
    checks['title_groups_count_no_year_duplication']=sum(r['candidate_representative_count'] for r in title_summary)==totals['after']
    checks['title_groups_reference_sum_reconcile']=sum(r['reference_sum'] or 0 for r in title_summary)==sum(positive)
    checks['verified_money_not_zero_filled']=all(r['verified_billing_basis_sum']=='' for r in notice_summary)
    checks['protected_file_metadata_unchanged']=all(signature(Path(p))==sig for p,sig in initial.items())
    checks['priority_input_file_sha_unchanged']=digest(PRIORITY)==json.loads((OUT/'priority_preparation.json').read_text(encoding='utf-8'))['priority_sha256']
    bad=0
    for name in ('candidate_notices_v2.csv','institution_notice_year_summary.csv','institution_title_year_summary.csv','priority_notice_review.csv'):
        with (OUT/name).open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);assert not any(re.search(r'(?i)item_json|request_url|telno|email|ceonm|bizno|servicekey',k) for k in reader.fieldnames)
            count=0
            for row in reader:
                count+=1
                if any(re.search(r'(?i)(?:servicekey|api[_-]?key|access[_-]?token)=',v) or v.startswith(('=','+','@','\t','\r')) for v in row.values()):bad+=1
            assert count==files[name]['rows']
    checks['csv_row_counts_checked']=True;checks['safe_field_surface']=bad==0
    return checks,dict(totals),dict(baseline_reference_sum=baseline_sum,candidate_reference_sum=sum(positive),candidate_reference_sample_n=len(positive))


def report(db,funnel,checks,totals,amounts,transitions,files,snapshot):
    lines=[]
    def w(s=''):lines.append(s)
    def table(headers,values):
        w('| '+' | '.join(headers)+' |');w('| '+' | '.join('---' for _ in headers)+' |')
        for v in values:w('| '+' | '.join(str(x).replace('|',' / ') for x in v)+' |')
        w()
    w('# 시공노트 2차 상업적 대상 검토');w();w('## 완료');w()
    w(f'규칙 `{RULE}`. 기존 시설·작업·금액·게시연도 분류와 1차 CSV 열을 그대로 보존하고 `s2_` 상업 검토축을 추가했다. 우선검토/개별검토는 영업 검토의 우선순위 제안이며 실제 과업 확인 완료·구매 의사·확정 시장이 아니다. 별도사업모델은 대상 합계에 포함하지 않는다. X 및 최종 과금 기준금액은 미정이다.');w()
    w('1차 원 작성자의 26개 검사 기록, 사용자 제공 독립 재대조 17개 통과 기록, 이번 2차 코드 검사는 서로 다른 증거다. 사용자 검토 수치를 테스트 정답으로 복사하지 않고 기존 CSV/캐시에서 다시 계산했다.');w()
    table(['입력','근거'],[['기존 사본',snapshot['path']],['기존 사본 SHA-256',snapshot['sha256']],['기존 진단 기준 UTC',snapshot['profile_basis_utc']],['기존 파생 캐시',str(CACHE)],['우선확인 목록',str(PRIORITY)],['보존 방식','기존 CSV와 캐시 읽기 전용; 별도 디렉터리 출력']])
    w('4.7GB 원본 DB의 전체 내용·해시와 원 수집기의 완전성을 이번 단계에서 다시 검증하지 않았다. 기존 고정 사본 검증 기록을 사용하고 파일 크기·수정시각을 대조했다. 원본 DB/JSON/수집기/예약작업/기존 CSV/다른 프로세스는 변경하지 않았다. 기존 파생 캐시의 수도권 범위 전 행을 순차적으로 재검토했으며 원본 item_json이나 업체 인적정보를 적재하지 않았다.');w()
    table(['전수 대조 단계','행/건 수'],list(funnel.items())+[['기존 분석용 대표',totals['before']],['2차 상업 검토 대표',totals['after']],['미검증 양수 추정가격 참고 표본',amounts['candidate_reference_sample_n']]])
    table(['변화 유형(관측행 기준)','행 수'],list(transitions.items()))
    w('분류 변경 전후표는 기존 전체 대표 집합과 2차 상업적 검토 집합의 구성 차이를 보여 준다. 기존 시설/작업 레이블 자체는 덮어쓰지 않았다. `classification_comparison.csv`에 기관군·게시연도·기존 시설/작업·금액성·새 배타분류별 전후 건수와 추정가격 참고합계 차이가 있고, `classification_change_log.csv`에는 공고별 이유가 있다. 이는 실제 발주시장 규모의 증가·감소가 아니다.');w()
    w('## 상업 검토 규칙과 합산');w()
    w('- 우선검토: 대상시설과 연간/상시/분산 작업 맥락이 함께 관측되고 유지·도색 작업 근거가 있는 경우. 도색 때문에 기존 work_type=미확인을 유지보수로 바꾸지 않는다.\n- 개별검토: 설치, 혼합, 집중 개보수, 반복/관리 흐름 또는 현장 범위가 불명확한 경우. 설치만으로 일괄 제외하지 않지만 유지보수 확정도 하지 않는다.\n- 별도사업모델: 신축·건립·건축 복합화·개발·명시적 신설확장 맥락. 부속 조경을 기존 공원녹지 관리시장으로 합산하지 않는다.\n- 범위 밖/근거 부족: 역명 제거 후 핵심/도로 시설 근거가 없는 사례. 미검출을 수요 0으로 단정하지 않는다.');w()
    w('`s2_facility_query_tags`는 중복 조회용 태그이고 서로 더할 수 없다. `s2_exclusive_group`은 한 공고당 하나다. 복수 핵심시설은 통합 핵심 그룹으로 1회 합산한다. 명시적 도로포장 작업이 함께 있는 것은 핵심·인접 복합으로 별도 분리한다. 중앙분리대 수목관리 맥락에서는 중앙분리대가 작업 위치일 가능성을 표시하고 교통시설 작업을 자동 확정하지 않는다.');w()
    group_counts=collections.Counter()
    for p in rows(db):
        if p['s2_reference_aggregate_eligible']:group_counts[(p['buyer_segment'],p['notice_year'],p['s2_commercial_scope'],p['s2_commercial_tier'],p['s2_exclusive_group'])]+=1
    table(['기관군','게시연도','범위','상업 검토','배타분류','대표 후보'],[[*k,v] for k,v in sorted(group_counts.items(),key=lambda x:(x[0][0],{'2024':0,'2025':1,'2023':2,'2026':3}.get(x[0][1],4),*x[0][2:]))])
    w('## 기간·금액과 기관 표');w()
    w('`institution_notice_year_summary.csv`는 공고게시연도 기준이다. 2024/2025를 주 비교연도로, 2023/2026을 저장기간 부분연도로 표시한다. `institution_title_year_summary.csv`는 제목 사업연도 후보라는 별도 관점이며 두 표를 더하지 않는다. 제목 연도가 없으면 미기재, 복수면 복수연도 미확인으로 한 버킷에 둔다. 실제 사업/관리기간 필드는 원문 확인 전 빈칸이다. 차수·공구·구역·반기 표기를 보존하고 이용권 수를 추정하지 않는다.');w()
    w('수요기관 코드·원래 명칭·상위 지자체 매핑·현장/기관 범위를 유지한다. 제목에 남동구가 있어도 종합건설본부 계약을 남동구 계약으로 이전하지 않는다. 중앙기관·공기업·기관유형 미확인은 지자체와 별도 기관군이다. 공식 과거 기관 계층 및 담당 과는 계속 미확인이다.');w()
    w('금액 표는 기존 presmpt_prce를 그대로 사용한 `reference_*`이다. NULL/0/이상값을 구분하고 예산/VAT/관급으로 추정가격을 채우거나 보정하지 않았다. `s2_budget_price_vat_residual`은 예산−추정가격−VAT의 산술 차액만 보여 주며 관급재료나 과금기준으로 해석하지 않는다. 연간단가 중 100만원 미만 양수는 검토표시만 했으며 단가 확정·삭제·배율 보정을 하지 않았다. 작업유형과 금액성별로 분리해 장기계속·단가·혼합의 불확실성을 숨기지 않는다.');w()
    w('`institution_verified_amount_summary.csv` 및 `verified_price_hypotheses.csv`는 검증 자료가 없으면 헤더만 제공한다. 검증 완료 집계가 비어 있다는 것은 0원 시장이라는 의미가 아니다. 실제 검증한 과금 기준 후보금액이 없으므로 이번 단계에서 표본 가격 숫자를 만들지 않는다. 검증 후의 비교식은 `검증한 기준금액 후보 × 가정 X / 100`이며 X를 확정하거나 계약액을 추정하지 않는다. 사용자 설명의 5%를 기본 요율로 설정하지 않았다.');w()
    w('## 우선확인 23건과 원문 근거');w()
    prep=json.loads((OUT/'priority_preparation.json').read_text(encoding='utf-8'))
    table(['항목','관측'],[['입력 행',prep['priority_rows']],['공고번호+차수 연결',prep['matched_keys']],['제목/기관/연도/금액 불일치 행',prep['field_mismatch_rows']],['기존 첨부 링크',prep['distinct_attachments']],['외부 접속','0회; 승인 전 SKIPPED'],['검증 완료 기준금액 표본','없음(0원 의미 아님)']])
    w('`priority_notice_review.csv`는 23건의 전후 분류·가격 필드·확인 질문·기존 링크를 보존한다. `priority_document_evidence.csv`는 금액종류/연간한도/사업·관리기간/관급/VAT/대표 작업/작업구역/기준금액 후보별 미확인 상태와 이유를 남긴다. 실제 문서명·페이지/표·근거문장은 문서를 확보하기 전 채우지 않았다. 링크 구문과 식별키 확인은 접속 성공·문서 내용 검증이 아니다. 접속을 안 한 것을 조회 실패나 성공으로 바꾸지 않는다.');w()
    w('검토 본문의 공항동 문화체육센터 차수 000과 제공 우선 CSV의 차수 001은 구별했다. 우선확인은 제공 CSV의 001을 정확히 연결하며 기존 목록의 000도 이력으로 유지한다. 제목 유사도나 CSV 물리행 번호만으로 버전을 바꾸지 않는다.');w()
    w('다운로드 제안은 `attachment_download_plan.csv`의 저장된 79개 링크에 한정하며 승인 전 실행하지 않는다. 제안 한도는 최대 79회 요청, 파일당 20MiB·총 200MiB, 재시도·외부 링크 확대·추가 API 호출 없음이다. 원문 내 매크로·스크립트·명령은 실행하지 않는다.');w()
    w('## 실패');w();w('최종 산술/보존 검사 실패: '+(', '.join(k for k,v in checks.items() if not v) or '없음')+'. 원문 취득은 승인 전 미실행이며 실패한 접속으로 기록하지 않는다.');w()
    w('## 미검증');w();w('실제 유지관리 작업흐름, 금액 총액성·연간한도·VAT/관급 범위·계약액·정산액·과금 기준 후보금액·실제 사업/관리기간은 미검증이다. 공원녹지 용역과 실제 계약/정산 자료는 현 공사 공고 범위 밖이며 미수집을 수요 0으로 보지 않는다. 이번 자료는 전체 공공조달 시장 또는 확정 SaaS 시장규모가 아니다.');w()
    w('## 다음 조치');w();w('승인한 첨부만 열람하여 23건 원문 근거표에 문서명·페이지/표·직접 근거와 실패 사유를 기록한다. 연간한도/기간/금액 범위를 확인한 표본만 별도 기준금액 후보 및 가격 가설 비교에 사용한다. 용역·계약·정산의 추가 수집은 우선검토 기관과 업무로 좁힌 별도 승인 대상으로 남긴다.');w()
    w('## 재실행과 검사');w();w('```powershell');w('.\\.venv\\Scripts\\python.exe tools\\prepare_sigongnote_stage2.py');w('.\\.venv\\Scripts\\python.exe tools\\test_sigongnote_stage2.py');w('.\\.venv\\Scripts\\python.exe tools\\sigongnote_market_stage2.py');w('```');w()
    w('원본 4.7GB DB·JSON 전수 재검사·전체 앱 테스트·실연동 API 테스트는 이번 작업에서 SKIPPED. 분류 회귀검사와 저장 CSV/캐시의 전수 산술 대조를 수행했다. 원 작성자 검사 기록과 이번 검사는 validation_results.json에 구별한다.');w()
    table(['검사','결과'],[[k,'PASS' if v else 'FAIL'] for k,v in checks.items()])
    table(['파일','데이터 행/문서 줄 수','바이트'],[[k,v['rows'],v['bytes']] for k,v in files.items()])
    w('CSV는 UTF-8 BOM이며 공고차수·기관코드는 문자열로 가져온다. candidate_notices_v2.csv와 연도별 분할본은 같은 자료의 대체본이다. 기존 1차 CSV와 2차 보완 CSV도 버전 비교용이며 합산하지 않는다.')
    (OUT/'analysis_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8');OUT.mkdir(parents=True,exist_ok=True);WORK.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((SOURCE/'output_manifest.json').read_text(encoding='utf-8'))
    protected=[*SOURCE.glob('*'),CACHE,PRIORITY,ROOT/'tools/sigongnote_market_stage1.py',ROOT/'tools/test_sigongnote_market_stage1.py']
    snapshot_path=Path(manifest['snapshot']['path'])
    if snapshot_path.exists():
        assert snapshot_path.stat().st_size==manifest['snapshot']['bytes'] and snapshot_path.stat().st_mtime_ns==manifest['snapshot']['mtime_ns']
        protected.append(snapshot_path)
    initial={str(p):signature(p) for p in protected if p.is_file()}
    input_hashes={str(SOURCE/'candidate_notices.csv'):digest(SOURCE/'candidate_notices.csv'),str(PRIORITY):digest(PRIORITY)}
    if not (OUT/'priority_preparation.json').exists():prepare()
    priorities=list(csv.DictReader(PRIORITY.open(encoding='utf-8-sig',newline='')))
    plan=list(csv.DictReader((OUT/'attachment_download_plan.csv').open(encoding='utf-8-sig',newline='')))
    assert not (Path(str(CACHE)+'-wal').exists() and Path(str(CACHE)+'-wal').stat().st_size)
    cache=sqlite3.connect(CACHE.resolve().as_uri()+'?mode=ro&immutable=1',uri=True);cache.row_factory=sqlite3.Row;cache.execute('PRAGMA query_only=ON')
    db=sqlite3.connect(WORK/'analysis_v2_0.sqlite3');db.row_factory=sqlite3.Row
    old_fields,funnel=load(db,cache,priorities);cache.close()
    print('Classification scan finished:',json.dumps(funnel),flush=True)
    first=json.loads(db.execute('SELECT payload FROM rows ORDER BY seq LIMIT 1').fetchone()[0]);fields=list(first)
    files={'candidate_notices_v2.csv':write_csv('candidate_notices_v2.csv',rows(db),fields)}
    # Write all year alternatives in one sequential pass, not four table scans.
    handles={};writers={};counts=collections.Counter()
    try:
        for p in rows(db):
            year=p['notice_year'];name=f'candidate_notices_v2_{year}.csv'
            if year not in handles:
                handles[year]=(OUT/name).open('w',encoding='utf-8-sig',newline='');writers[year]=csv.DictWriter(handles[year],fieldnames=fields);writers[year].writeheader()
            writers[year].writerow({k:cell(p.get(k,'')) for k in fields});counts[year]+=1
    finally:
        for f in handles.values():f.close()
    for year,count in counts.items():
        name=f'candidate_notices_v2_{year}.csv';files[name]=dict(rows=count,bytes=(OUT/name).stat().st_size)
    notice=summaries(db,'notice_year');title=summaries(db,'title_year_candidate')
    files['institution_notice_year_summary.csv']=write_csv('institution_notice_year_summary.csv',notice)
    files['institution_title_year_summary.csv']=write_csv('institution_title_year_summary.csv',title)
    compared,details,transitions=comparison(db)
    files['classification_comparison.csv']=write_csv('classification_comparison.csv',compared)
    files['classification_change_log.csv']=write_csv('classification_change_log.csv',details)
    cases,facts=priority_evidence(db,priorities,plan)
    files['priority_notice_review.csv']=write_csv('priority_notice_review.csv',cases)
    files['priority_document_evidence.csv']=write_csv('priority_document_evidence.csv',facts)
    files['institution_verified_amount_summary.csv']=write_csv('institution_verified_amount_summary.csv',[],['institution_code','institution_name','verified_period','verified_amount_kind','verified_basis_sample_n','verified_basis_sum','verification_evidence'])
    files['verified_price_hypotheses.csv']=write_csv('verified_price_hypotheses.csv',[],['bid_ntce_no','bid_ntce_ord','verified_billing_basis_candidate','basis_definition','period','assumed_X_percent','hypothetical_fee_excluding_vat','evidence','status'])
    for name in ('priority_input_check.csv','attachment_download_plan.csv'):
        with (OUT/name).open(encoding='utf-8-sig',newline='') as f:n=sum(1 for _ in csv.DictReader(f))
        files[name]=dict(rows=n,bytes=(OUT/name).stat().st_size)
    notes=OUT/'execution_notes.md'
    if notes.exists():files[notes.name]=dict(rows=len(notes.read_text(encoding='utf-8').splitlines()),bytes=notes.stat().st_size)
    checks,totals,amounts=validate(db,old_fields,funnel,notice,title,priorities,files,initial)
    checks['year_shard_row_count']=sum(counts.values())==totals['rows']
    evidence=dict(checks=checks,rule_version=RULE,prior_author_checks='1차 validation_results.json의 26개 true: 이번 검사가 아님',user_independent_checks='사용자 검토문 17개 PASS 보고: 코드/원본 미제공으로 별도 실행 확인 아님',this_run_totals=totals,reference_amounts=amounts,external_calls=0)
    (OUT/'validation_results.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    for _ in range(4):
        report(db,funnel,checks,totals,amounts,transitions,files,manifest['snapshot'])
        p=OUT/'analysis_summary.md';entry=dict(rows=len(p.read_text(encoding='utf-8').splitlines()),bytes=p.stat().st_size)
        if files.get(p.name)==entry:break
        files[p.name]=entry
    result=dict(rule_version=RULE,input_hashes=input_hashes,protected_file_signatures=initial,original_snapshot=manifest['snapshot'],funnel=funnel,totals=totals,reference_amounts=amounts,transitions=transitions,files=files,checks=checks,external_calls=0,original_writes=0,completed_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    (OUT/'output_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(OUT/'sigongnote_market_stage2.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in [*files,'validation_results.json','output_manifest.json','priority_preparation.json']:archive.write(OUT/name,arcname=name)
    db.close()
    print(json.dumps(dict(totals=totals,checks_passed=sum(checks.values()),checks_failed=sum(not x for x in checks.values()),files=files),ensure_ascii=False),flush=True)
    if not all(checks.values()):raise RuntimeError('2차 집계 검사 실패')


if __name__=='__main__':main()
