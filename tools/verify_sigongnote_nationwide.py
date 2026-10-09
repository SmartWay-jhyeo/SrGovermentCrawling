"""Independent CSV arithmetic and inclusion checks; does not access raw DB/JSON."""
import collections,csv,json,hashlib,sys,zipfile
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_nationwide'

def read(name):
    with (OUT/name).open(encoding='utf-8-sig',newline='') as f:yield from csv.DictReader(f)

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    groups=collections.defaultdict(lambda:[0,0,[]]);keys=set();ops=set();checks={};n=0
    money_layers=collections.Counter();guri_keys=set();docs=0;good=True;net_evidence={}
    for e in read('document_evidence.csv'):
        if e['amount_krw']:net_evidence[e['evidence_id']]=(e['bid_ntce_no'],e['bid_ntce_ord'],int(e['amount_krw']))
    for r in read('candidate_notices_nationwide_lean.csv'):
        n+=1;k=(r['bid_ntce_no'],r['bid_ntce_ord']);assert k not in keys;keys.add(k)
        if r['aggregate_eligible']=='1':
            assert r['analysis_opportunity_id'] not in ops;ops.add(r['analysis_opportunity_id'])
            assert r['representative']=='1' and '취소' not in r['notice_kind'] and r['commercial_tier'] in ('우선검토','개별검토')
        if r['amount_tier'].startswith(('3_','4_')):assert r['amount_used']==''
        if r['amount_tier'].startswith('1_'):
            assert net_evidence[r['document_supply_evidence_id']]==(k[0],k[1],int(r['amount_used']));docs+=1
        if r['bid_ntce_no'] in ('R25BK00580213','R25BK01133840'):
            assert not r['amount_used'] and r['document_selected_total'] in ('40000000','99900000')
        if r['bid_ntce_no']=='R25BK00731627' and r['bid_ntce_ord']=='000':assert r['frequency']=='단기 분할 정비(원문 180일)'
        if r['bid_ntce_no']=='R26BK01402848' and r['bid_ntce_ord']=='000':assert r['frequency']=='단기 집중정비(원문 28일)'
        if r['parent_local_government'].endswith(' 구리시') and r['notice_year'] in ('2024','2025'):guri_keys.add(k)
        money_layers[r['amount_tier']]+=1
        gkey=tuple(r[x] for x in ['buyer_type','notice_year','category','commercial_tier','frequency','amount_tier','amount_kind','period_basis'])
        g=groups[gkey];g[0]+=1;g[1]+=int(r['aggregate_eligible'])
        if r['amount_used']:g[2].append(int(r['amount_used']))
    checks['all_csv_notice_composite_keys_unique']=n==len(keys)
    checks['included_representatives_unique_and_resolved']=True
    checks['verified_supply_values_match_exact_notice_evidence']=True
    checks['held_unit_and_unknown_amounts_not_summed']=True
    def q(a,num,den):
        pos=Decimal(len(a)-1)*num/den;i=int(pos);return Decimal(a[i])+Decimal(a[min(i+1,len(a)-1)]-a[i])*(pos-i)
    checked=0;aggregates=collections.defaultdict(lambda:[0,0,0,Decimal(0)])
    for r in read('region_year_category_summary.csv'):
        ag=aggregates[r['aggregation_view']];ag[0]+=int(r['observations']);ag[1]+=int(r['candidate_representatives']);ag[2]+=int(r['amount_n']);ag[3]+=Decimal(r['sum_krw'] or '0')
        if r['aggregation_view']!='전국':continue
        k=tuple(r[x] for x in ['institution_type','year','exclusive_category','commercial_tier','frequency','amount_tier','amount_kind','period_basis'])
        count,eligible,v=groups[k];a=sorted(v)
        assert (int(r['observations']),int(r['candidate_representatives']),int(r['amount_n']))==(count,eligible,len(a))
        if a:
            expected=dict(sum_krw=Decimal(sum(a)),mean_krw=Decimal(sum(a))/len(a),median_krw=q(a,1,2),p25_krw=q(a,1,4),p75_krw=q(a,3,4),min_krw=Decimal(a[0]),max_krw=Decimal(a[-1]))
            for col,value in expected.items():assert Decimal(r[col])==value,(k,col)
            for rate in range(1,6):
                assert Decimal(r[f'illustrative_{rate}pct_sum_krw'])==Decimal(sum(a))*rate/100
                assert Decimal(r[f'illustrative_{rate}pct_median_krw'])==q(a,1,2)*rate/100
        else:assert all(r[col]=='' for col in ['sum_krw','mean_krw','median_krw','p25_krw','p75_krw'])
        checked+=1
    checks['national_all_group_counts_and_distribution_recomputed']=checked==len(groups)
    checks['scenario_rates_are_arithmetic_only_1_to_5pct']=True
    checks['buyer_and_site_partitions_equal_national']=all(v==aggregates['전국'] for v in aggregates.values())
    inst=[0,0,0,Decimal(0)]
    for r in read('institution_year_summary.csv'):
        inst[0]+=int(r['observations']);inst[1]+=int(r['candidate_representatives']);inst[2]+=int(r['amount_n']);inst[3]+=Decimal(r['sum_krw'] or '0')
    checks['institution_count_amount_reconcile']=inst==aggregates['전국']
    guri=list(read('guri_proposal_basis.csv'))
    checks['guri_is_subset_not_added_to_national']=guri_keys=={(r['bid_ntce_no'],r['bid_ntce_ord']) for r in guri}
    checks['unverified_guri_has_no_proposal_price']=all(all(not r[f'proposal_{rate}pct_krw'] for rate in range(1,6)) for r in guri if not r['document_supply_amount'])
    joins=list(read('document_review_join.csv'));checks['review23_exact_keys_no_insertion']=len(joins)==23 and all(r['not_added_when_absent']=='1' for r in joins)
    for r in read('metro_reconciliation.csv'):
        assert r['legacy_representatives']==r['recomputed_same_rule_representatives'] and r['legacy_positive_api_reference']==r['recomputed_same_rule_positive_api_reference']
    checks['legacy_metro_reconciliation']=True
    title_total=sum(int(r['candidate_representatives']) for r in read('title_business_year_summary.csv') if r['aggregation_view']=='전국')
    checks['title_year_view_preserves_candidates']=title_total==len(ops)
    checks['no_empty_mandatory_output']=all((OUT/f).stat().st_size>100 for f in ['national_market_summary.md','region_year_category_summary.csv','institution_year_summary.csv','candidate_notices_nationwide_lean.csv','guri_proposal_basis.csv','manifest.json','validation_results.json'])
    with zipfile.ZipFile(OUT/'nationwide_results.zip') as z:
        checks['zip_crc_valid']=z.testzip() is None
        checks['zip_no_source_database_or_document_originals']=all(not name.lower().endswith(('.sqlite3','.hwp','.hwpx','.pdf','.xls','.xlsx','.bin')) for name in z.namelist())
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    filesok=True
    for name,m in manifest['files'].items():
        h=hashlib.sha256()
        with (OUT/name).open('rb') as f:
            for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
        filesok&=h.hexdigest()==m['sha256']
    checks['manifest_file_hashes_match']=filesok
    result=dict(checks=checks,candidate_rows=n,representatives=len(ops),verified_supply_candidates=docs,national_distribution_groups=checked,method='독립 CSV 재계산; 원 작성 생성기의 검사와 구분; 원문 법적 상태 검증 아님')
    (OUT/'independent_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert all(checks.values()),checks
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
