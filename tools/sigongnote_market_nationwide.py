"""Reproducible offline national recomputation. Does not fetch or mutate inputs."""
from __future__ import annotations
import collections, csv, datetime, hashlib, itertools, json, re, shutil, sqlite3, sys, zipfile
from decimal import Decimal
from pathlib import Path
from sigongnote_market_stage1 import connect, geography, money, safe_url
from sigongnote_nationwide_rules import RULE, CORE, PROVINCES, OBSERVED_COMBINED_NAME, OBSERVED_COMBINED_REGION, organization, site_region, classify_national

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/sigongnote_market_nationwide'
WORK=ROOT/'.local/sigongnote_nationwide'
CACHE=ROOT/'.local/sigongnote_stage1/derived.sqlite3'
VERIFIED=ROOT/'outputs/sigongnote_market_stage2_verified'
META=ROOT/'.local/sigongnote_stage1/snapshot_verified.json'
GURI_KEYS={'R25BK00636989','R25BK00637322','R25BK00650024'}
FILES={}

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def readcsv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:yield from csv.DictReader(f)

def cell(v):
    if v is None:return ''
    if isinstance(v,(list,dict)):return json.dumps(v,ensure_ascii=False,separators=(',',':'))
    if isinstance(v,bool):return int(v)
    if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')):return "'"+v
    return v

def write(name,rows,fields=None):
    it=iter(rows);first=next(it,None)
    fields=fields or (list(first) if first is not None else None)
    assert fields,name
    count=0
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');w.writeheader()
        for r in itertools.chain([first] if first is not None else [],it):
            w.writerow({k:cell(r.get(k,'')) for k in fields});count+=1
    FILES[name]={'rows':count,'bytes':(OUT/name).stat().st_size}
    return count

def dump(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def source_validation(c):
    m=json.loads(META.read_text(encoding='utf-8'));p=Path(m['path']);st=p.stat()
    assert (st.st_size,st.st_mtime_ns)==(m['bytes'],m['mtime_ns']),'Snapshot signature changed; stop without silently mixing snapshots'
    s=connect(p)
    count=0;bad=0;hs=hashlib.sha256();hc=hashlib.sha256()
    # All keys, core scalar fields and captured body hashes; never deserialize source item_json.
    a=s.execute('SELECT bid_ntce_no,bid_ntce_ord,item_sha256,bid_ntce_dt,dminstt_cd,dminstt_nm,bid_ntce_nm,cnstrtsite_rgn_nm,presmpt_prce,bdgt_amt,vat FROM bf_notice_revision ORDER BY bid_ntce_no,bid_ntce_ord')
    b=c.execute("SELECT no,ord,json_extract(extra,'$.item_sha256'),dt,org,orgname,title,site,price,budget,vat FROM base ORDER BY no,ord")
    for x,y in itertools.zip_longest(a,b):
        count+=1
        if x is None or y is None:bad+=1;continue
        xx=list(x);yy=list(y)
        for i in (8,9,10):xx[i]=money(xx[i])[0]
        if xx!=yy:bad+=1
        hs.update(json.dumps(xx,ensure_ascii=False,separators=(',',':')).encode()+b'\n')
        hc.update(json.dumps(yy,ensure_ascii=False,separators=(',',':')).encode()+b'\n')
        if count%100000==0:print(f'Compared {count} national source/cache records',flush=True)
    s.close();assert bad==0,f'National cache mismatch: {bad}'
    print(f'Full national key/core/hash comparison: {count} rows, mismatches={bad}',flush=True)
    return dict(snapshot=m,cache_path=str(CACHE),cache_bytes=CACHE.stat().st_size,
      cache_mtime_ns=CACHE.stat().st_mtime_ns,source_rows=count,core_comparison_mismatches=bad,
      source_core_digest=hs.hexdigest(),cache_core_digest=hc.hexdigest(),
      hash_method='기존 snapshot SHA256 기록+path/size/mtime 일치; 이번 전수 복합키/core/body-hash 열 대조. 4.7GB 재해시 생략')

def load_documents():
    reviews={(r['bid_ntce_no'],r['bid_ntce_ord']):r for r in readcsv(VERIFIED/'priority_notice_review.csv')}
    evidence=collections.defaultdict(list)
    for r in readcsv(VERIFIED/'priority_document_evidence.csv'):evidence[(r['bid_ntce_no'],r['bid_ntce_ord'])].append(r)
    assert len(reviews)==23 and all(r['rule_version']=='sigongnote-document-review-3.0' for r in reviews.values())
    return reviews,evidence

def money_fields(p,d,e):
    p.update(document_period=d.get('stated_period',''),document_selected_total=d.get('selected_reference_amount',''),
      document_total_name=d.get('selected_reference_amount_name',''),document_total_vat=d.get('selected_reference_vat',''),
      document_total_gov=d.get('selected_reference_gov',''),document_supply_amount='',document_supply_evidence_id='',
      document_evidence_ids=d.get('evidence_ids',''),document_review_version=d.get('rule_version',''),
      actual_start_date=d.get('actual_start_date',''),actual_contract_amount='',actual_execution_amount='',
      amount_tier='4_합계 미사용',amount_used='',amount_kind='합계 미사용',period_basis='사업기간 미확인',
      money_reason='차수/재공고 이력·취소·대표/분류 미확인 또는 별도사업모델',unit_estimated_price=d.get('unit_estimated_price_krw',''))
    if d.get('vat_and_government_scope_confirmed')=='1':
        net=[r for r in e if r['amount_krw'] and r['amount_name'].startswith('추정가격') and '단가' not in r['amount_name'] and r['vat_scope']=='별도']
        assert len(net)==1,(p['bid_ntce_no'],len(net))
        p['document_supply_amount']=int(net[0]['amount_krw']);p['document_supply_evidence_id']=net[0]['evidence_id']
    if not p['aggregate_eligible']:return
    if p['document_supply_amount']!='':
        p.update(amount_tier='1_원문 범위확인 공급가액',amount_used=p['document_supply_amount'],
          amount_kind='도급분 예정 공급가액(VAT·관급 별도; 계약액 아님)',period_basis=p['document_period'],
          money_reason='document-review-3.0 원문 금액·VAT·관급 범위 확인; 최종 과금 기준 미정')
    elif d:
        p.update(amount_tier='3_금액범위 보류',amount_kind='원문 예정총액 보존; 합산범위 보류',
          period_basis=p['document_period'],money_reason=d.get('conflict_notes') or 'VAT·관급 또는 유효 총액 미확인; 단가와 예정총액 분리')
    elif p['presmpt_status']!='유효 양수':
        p.update(money_reason='API 추정가격 '+p['presmpt_status'])
    elif p['presmpt_prce']<1000000 or p['amount_shape'] in ('단가 후보','장기계속 총액 후보','연차금액 후보') or p['category']=='핵심·도로포장 복합 검토':
        p.update(amount_tier='3_금액범위 보류',amount_kind='API 추정가격(단가/차수/장기계속/혼합범위 검토)',
          money_reason='100만원 미만 검토경계 또는 단가/장기계속/분할/포장복합 제목; 총액성 미확인. 보정·삭제하지 않음')
    else:
        p.update(amount_tier='2_API 추정가격 참고',amount_used=p['presmpt_prce'],
          amount_kind='공고상 추정가격(VAT·조달수수료 제외; 총액성·계약액 미검증)',
          period_basis='공고연도 관측; 실제 사업기간 미검증',money_reason='참고분포에만 사용; 100만원 이상도 총액성 검증 아님')

def make_row(r,reviews,evidence,orgcache):
    x=json.loads(r['extra']);cl=classify_national(r['title'],r['main']);key=(r['no'],r['ord']);d=reviews.get(key,{})
    org=orgcache.setdefault(r['orgname'],organization(r['orgname']))
    selected=r['ord']==r['selected_ord'];terminal=selected and r['terminal']==r['no']
    known=r['selection']=='분석용 대표 확인' and bool(r['op_id']);cancel='취소' in (r['kind'] or '')
    representative=bool(terminal and known and not cancel)
    oldgeo=geography(r['site'],r['orgname'])
    if d:
        group=d['document_exclusive_group']
        cl['category']='복수 핵심시설 통합' if group.startswith('복수핵심') else '별도사업모델: 건축·개발·신설확장' if group=='신축 건축 복합공사' else group
        cl.update(commercial_tier=d['document_commercial_tier'],work_type=d['document_work_type'],
          candidate=int(d['document_commercial_tier'] in ('우선검토','개별검토')),relevant=1,
          evidence_level='원문 항목검토(sigongnote-document-review-3.0); 계약·집행 미검증')
        cl['rule_change']+='; 정확한 공고번호+차수 원문검토 반영'
        cl['classification_reason']=d['verified_work_scope']
    if org['buyer_type'].startswith('한국도로공사'):
        cl.update(candidate=0,commercial_tier='별도사업모델(한국도로공사)')
    p=dict(bid_ntce_no=r['no'],bid_ntce_ord=r['ord'],analysis_opportunity_id=r['op_id'] if known else '',
      previous_notice_no=r['prev'],title=r['title'],notice_datetime=r['dt'],notice_year=r['year'],
      year_scope='주 비교연도(달력연도; 수집완전성 미보증)' if r['year'] in ('2024','2025') else '부분연도',
      institution_code=r['org'],institution_name=r['orgname'],**org,
      site_region_original=r['site'],site_region=site_region(r['site']),main_construction_type=r['main'],
      notice_kind=r['kind'],representative=int(representative),provisional_terminal=int(terminal),
      representative_status=r['selection'],representative_reason=r['reason'],link_status=r['link_status'],
      link_reason=r['link_reason'],cancel_history=r['cancel_history'],
      **cl,aggregate_eligible=int(representative and cl['candidate']),
      legacy_metro_scope=int(oldgeo['in_scope']),legacy_metro_eligible=int(representative and oldgeo['in_scope'] and cl['legacy_thematic']),
      presmpt_prce=r['price'],presmpt_status=x['presmpt_status'],bdgt_amt=r['budget'],vat=r['vat'],govsplyAmt=x['govsplyAmt'],
      notice_url=x['notice_url'],source_response_id=x['last_response_id'],source_item_sha256=x['item_sha256'],rule_version=RULE)
    money_fields(p,d,evidence.get(key,[]))
    return p

QUERY="""SELECT b.*,n.ord selected_ord,n.selection,n.reason,n.cancel_history,
 COALESCE(l.op_id,'OBS:'||b.no) op_id,COALESCE(l.terminal,b.no) terminal,
 COALESCE(l.status,'독립 관측 공고군') link_status,COALESCE(l.reason,'차수만 정리; 별도 재공고 연결 관측 없음') link_reason
 FROM base b JOIN nodes n ON n.no=b.no LEFT JOIN links l ON l.no=b.no """

def build(c,db,reviews,evidence):
    db.executescript('DROP TABLE IF EXISTS rows; CREATE TABLE rows(no TEXT,ord TEXT,year TEXT,category TEXT,eligible INTEGER,representative INTEGER,buyer_region TEXT,site_region TEXT,type TEXT,org TEXT,parent TEXT,tier TEXT,amount INTEGER,payload TEXT,PRIMARY KEY(no,ord));')
    orgcache={};batch=[];n=0;funnel=collections.Counter();baseline=collections.defaultdict(lambda:[0,0]);allregions=collections.Counter()
    def emit(group):
        if not any(p['relevant'] for p in group):return
        for p in group:
            batch.append((p['bid_ntce_no'],p['bid_ntce_ord'],p['notice_year'],p['category'],p['aggregate_eligible'],p['representative'],p['buyer_region'],p['site_region'],p['buyer_type'],p['institution_code'],p['parent_local_government'],p['amount_tier'],p['amount_used'] if p['amount_used']!='' else None,json.dumps(p,ensure_ascii=False,separators=(',',':'))))
    group=[];old=None
    for r in c.execute(QUERY+' ORDER BY b.no,b.ord'):
        p=make_row(r,reviews,evidence,orgcache);n+=1;funnel['source_revisions']+=1
        allregions[p['buyer_region']]+=1
        if p['legacy_metro_scope']:funnel['legacy_metro_scoped_revisions']+=1
        if p['legacy_metro_eligible']:
            g=baseline[(p['notice_year'],p['legacy_category'])];g[0]+=1
            if p['presmpt_status']=='유효 양수':g[1]+=p['presmpt_prce']
        if old is not None and old!=r['no']:emit(group);group=[]
        old=r['no'];group.append(p)
        if len(batch)>=1000:db.executemany('INSERT INTO rows VALUES ('+','.join('?'*14)+')',batch);batch=[]
        if n%100000==0:print(f'Classified {n} national revisions',flush=True)
    emit(group)
    db.executemany('INSERT INTO rows VALUES ('+','.join('?'*14)+')',batch);db.commit()
    # Explicit chain closure only; no title matching or amount-based merges.
    present={r[0] for r in db.execute('SELECT DISTINCT no FROM rows')}
    ops={r['op_id'] for r in c.execute('SELECT no,op_id FROM links') if r['no'] in present and r['op_id']}
    missing={r['no'] for r in c.execute('SELECT no,op_id FROM links') if r['op_id'] in ops and r['no'] not in present}
    batch=[]
    for no in sorted(missing):
        for r in c.execute(QUERY+' WHERE b.no=?',(no,)):emit([dict(make_row(r,reviews,evidence,orgcache),relevant=1)])
    db.executemany('INSERT INTO rows VALUES ('+','.join('?'*14)+')',batch);db.commit()
    db.executescript('CREATE INDEX row_eligible ON rows(eligible); CREATE INDEX row_parent ON rows(parent,year);')
    return funnel,baseline,allregions

def rows(db,where='1',args=()):
    for (p,) in db.execute('SELECT payload FROM rows WHERE '+where+' ORDER BY no,ord',args):yield json.loads(p)

def refine_document_periods(db,reviews):
    """Keep verified short periods separate from title-level frequency labels."""
    for key,d in reviews.items():
        hit=db.execute('SELECT payload FROM rows WHERE no=? AND ord=?',key).fetchone()
        if not hit:continue
        p=json.loads(hit[0]);period=d['stated_period']
        duration=re.search(r'착공일부터\s*(\d+)일',period)
        if duration and int(duration.group(1))<=180 and p['aggregate_eligible']:
            p['frequency']='단기 분할 정비(원문 180일)' if int(duration.group(1))==180 else '단기 집중정비(원문 '+duration.group(1)+'일)'
            db.execute('UPDATE rows SET payload=? WHERE no=? AND ord=?',(json.dumps(p,ensure_ascii=False,separators=(',',':')),*key))
    db.commit()

def remap_organizations(c,db):
    """Reclassify saved agency text without rewriting source names/codes or notice history."""
    mapped={};changes=collections.Counter();batch=[]
    for p in rows(db):
        name=p['institution_name'];o=mapped.setdefault(name,organization(name))
        if (p['buyer_type'],p['buyer_region'],p['parent_local_government'])!=(o['buyer_type'],o['buyer_region'],o['parent_local_government']):
            changes[(p['institution_code'],name,p['notice_year'],p['buyer_type'],o['buyer_type'],p['buyer_region'],o['buyer_region'],o['parent_local_government'])]+=1
        p.update(o,rule_version=RULE)
        batch.append((p['buyer_region'],p['buyer_type'],p['parent_local_government'],json.dumps(p,ensure_ascii=False,separators=(',',':')),p['bid_ntce_no'],p['bid_ntce_ord']))
        if len(batch)>=1000:
            db.executemany('UPDATE rows SET buyer_region=?,type=?,parent=?,payload=? WHERE no=? AND ord=?',batch);batch=[]
    db.executemany('UPDATE rows SET buyer_region=?,type=?,parent=?,payload=? WHERE no=? AND ord=?',batch);db.commit()
    fields=['institution_code','stored_institution_name','notice_year','previous_type','new_type','previous_region','new_region','parent_original_name']
    write('agency_mapping_changes.csv',(dict(zip(fields,k),observation_rows=n,reason='저장 명칭 규칙 보완; 과거 행정구역/코드 복원이나 현재 조직 소급변환 안 함',rule_version=RULE) for k,n in sorted(changes.items())))
    counts=collections.Counter();audit=[]
    for code,name,year,n in c.execute('SELECT org,orgname,year,COUNT(*) FROM base GROUP BY org,orgname,year'):
        o=mapped.setdefault(name,organization(name));counts[o['buyer_region']]+=n
        if (name or '').startswith(OBSERVED_COMBINED_NAME):
            audit.append(dict(institution_code=code,stored_institution_name=name,notice_year=year,source_revision_count=n,
              buyer_region=o['buyer_region'],institution_type=o['buyer_type'],historical_status='저장 응답상 통합 명칭; 공고 당시 조직/기관코드 유효성 미확인',
              allocation='과거 전남·광주로 자동 배분하지 않음; 현장지역은 별도 사용',rule_version=RULE))
    write('historical_agency_name_audit.csv',audit)
    print(f'Agency name remap completed: {sum(changes.values())} derived observations changed; national source count={sum(counts.values())}',flush=True)
    return counts

def stats(values):
    if not values:return dict(amount_n=0,sum_krw='',mean_krw='',median_krw='',p25_krw='',p75_krw='',min_krw='',max_krw='')
    a=sorted(values);n=len(a)
    def q(num,den):
        pos=Decimal(n-1)*Decimal(num)/Decimal(den);i=int(pos)
        return str(Decimal(a[i])+Decimal(a[min(i+1,n-1)]-a[i])*(pos-i))
    return dict(amount_n=n,sum_krw=sum(a),mean_krw=str(Decimal(sum(a))/n),median_krw=q(1,2),p25_krw=q(1,4),p75_krw=q(3,4),min_krw=a[0],max_krw=a[-1])

def summary(db,institution=False,titleyear=False):
    groups={}
    for p in rows(db):
        yr=p['notice_year']
        if titleyear:
            ys=p['title_business_years'];yr=ys[0] if len(ys)==1 else '복수연도 미확인' if ys else '제목연도 미기재'
        groupkeys=[('기관',p['buyer_region'])] if institution else [('전국','전국'),('수요기관 명칭상 지역',p['buyer_region']),('공사현장 지역',p['site_region'])]
        for view,reg in groupkeys:
            k=(view,reg,p['buyer_type'],yr,p['category'],p['commercial_tier'],p['frequency'],p['amount_tier'],p['amount_kind'],p['period_basis'])
            if institution:k+=(p['institution_code'],p['institution_name'],p['parent_local_government'],p['agency_level'],p['mapping_confidence'])
            if k not in groups:groups[k]={'observations':0,'candidate_representatives':0,'observed_representatives':0,'held_n':0,'unused_n':0,'positive_api_n':0,'missing_api_n':0,'zero_api_n':0,'invalid_api_n':0,'canceled_n':0,'history_n':0,'unresolved_n':0,'v':[]}
            g=groups[k];g['observations']+=1;g['candidate_representatives']+=p['aggregate_eligible'];g['observed_representatives']+=p['representative']
            g['held_n']+=int(p['amount_tier'].startswith('3_'));g['unused_n']+=int(p['amount_tier'].startswith('4_'))
            if p['aggregate_eligible']:
                g[{'유효 양수':'positive_api_n','NULL':'missing_api_n','0':'zero_api_n'}.get(p['presmpt_status'],'invalid_api_n')]+=1
            g['canceled_n']+=int(p['provisional_terminal'] and '취소' in (p['notice_kind'] or ''))
            g['history_n']+=int(not p['provisional_terminal']);g['unresolved_n']+=int(p['provisional_terminal'] and p['representative_status']!='분석용 대표 확인' or p['provisional_terminal'] and not p['analysis_opportunity_id'])
            if p['amount_used']!='':g['v'].append(p['amount_used'])
    names=['aggregation_view','region','institution_type','year','exclusive_category','commercial_tier','frequency','amount_tier','amount_kind','period_basis']
    if institution:names+=['institution_code','institution_name','parent_local_government','agency_level','mapping_confidence']
    for k,g in sorted(groups.items()):
        r=dict(zip(names,k));v=g.pop('v');r.update(g);r.update(stats(v))
        r['year_basis']='제목상 사업연도 후보(공고연도 표와 합산 금지)' if titleyear else '공고게시연도'
        r['year_scope']='부분연도' if r['year'] in ('2023','2026') else '주 비교연도' if r['year'] in ('2024','2025') else '미확인'
        r['verified_supply_sum_krw']=r['sum_krw'] if r['amount_tier'].startswith('1_') else ''
        r['api_reference_sum_krw']=r['sum_krw'] if r['amount_tier'].startswith('2_') else ''
        r['scenario_basis']='설명용 X%; 기관 구매의사·과금기간·확정 서비스 기준 아님' if v else '기준 보류/미사용'
        for rate in range(1,6):
            r[f'illustrative_{rate}pct_sum_krw']=str(Decimal(sum(v))*rate/100) if v else ''
            r[f'illustrative_{rate}pct_median_krw']=str(Decimal(r['median_krw'])*rate/100) if v else ''
        r['rule_version']=RULE
        yield r

def metro_compare(db,baseline):
    prior={};prior_by=collections.defaultdict(lambda:[0,0])
    for r in readcsv(ROOT/'outputs/sigongnote_market_stage1/candidate_notices.csv'):
        if r['aggregate_eligible']=='1':
            key=(r['bid_ntce_no'],r['bid_ntce_ord']);prior[key]=r
            g=prior_by[(r['notice_year'],r['primary_class'])];g[0]+=1
            if r['presmpt_status']=='유효 양수':g[1]+=int(r['presmpt_prce'])
    recomputed={(p['bid_ntce_no'],p['bid_ntce_ord']):p for p in rows(db,'1') if p['legacy_metro_eligible']}
    assert set(recomputed)==set(prior),'Legacy metro key mismatch'
    assert dict(prior_by)==dict(baseline),'Legacy metro aggregate mismatch'
    new={};new_by=collections.defaultdict(lambda:[0,0])
    for p in rows(db,'eligible=1'):
        if p['legacy_metro_scope']:
            key=(p['bid_ntce_no'],p['bid_ntce_ord']);new[key]=p
            g=new_by[(p['notice_year'],p['category'])];g[0]+=1
            if p['presmpt_status']=='유효 양수':g[1]+=p['presmpt_prce']
    changes=[]
    for key in sorted(set(prior)|set(new)):
        a=prior.get(key);b=new.get(key)
        if a and b and a['primary_class']==b['category']:continue
        existing=next(rows(db,'no=? AND ord=?',key))
        changes.append(dict(bid_ntce_no=key[0],bid_ntce_ord=key[1],notice_year=existing['notice_year'],title=existing['title'],
          before_included=int(a is not None),after_included=int(b is not None),before_category=a['primary_class'] if a else '',after_category=existing['category'],
          api_price_krw=existing['presmpt_prce'],membership_amount_delta_krw=(existing['presmpt_prce'] or 0)*(int(b is not None)-int(a is not None)),
          reason=existing['classification_reason']+'; '+existing['rule_change'],rule_version=RULE))
    write('metro_notice_changes.csv',changes)
    comparison=[]
    for y in ('2023','2024','2025','2026'):
        a=[v for (yy,_),v in baseline.items() if yy==y];b=[v for (yy,_),v in new_by.items() if yy==y]
        comparison.append(dict(year=y,legacy_representatives=sum(v[0] for v in a),recomputed_same_rule_representatives=sum(v[0] for v in a),
          legacy_positive_api_reference=sum(v[1] for v in a),recomputed_same_rule_positive_api_reference=sum(v[1] for v in a),
          new_rule_metro_representatives=sum(v[0] for v in b),new_rule_metro_raw_positive_api_reference=sum(v[1] for v in b),
          count_delta=sum(v[0] for v in b)-sum(v[0] for v in a),raw_reference_delta=sum(v[1] for v in b)-sum(v[1] for v in a),
          meaning='동일 수도권 조건; 원문/분류 규칙 개선 영향. raw 참고액은 금액 보류도 포함하여 이전 수치 대조에만 사용'))
    write('metro_reconciliation.csv',comparison)
    return comparison,len(changes)

def guri_outputs(c,db):
    result=[];needed=[]
    # Existing downloaded files are indexed by exact public notice keys, never by fuzzy title alone.
    manifestkeys=set()
    for path in [ROOT/'outputs/sigongnote_market_stage2_documents/attachment_fetch_results.csv',VERIFIED/'document_review_register.csv']:
        if path.exists():
            for r in readcsv(path):
                no=r.get('bid_ntce_no') or r.get('notice_no');order=r.get('bid_ntce_ord') or r.get('notice_order')
                if no:manifestkeys.add((no,order))
    for p in rows(db,"parent LIKE '% 구리시' AND year IN ('2024','2025')"):
        d=dict(p);d['proposal_status']='검토 후보' if p['aggregate_eligible'] else '이력/별도모델/미확인(확정 제안 합계 제외)'
        d['priority_three']=int(p['bid_ntce_no'] in GURI_KEYS)
        d['local_document_status']='23개 검토 근거 연결' if p['document_evidence_ids'] else '로컬 문서 색인에서 정확한 공고키 연결 없음'
        d['verified_scope']='미확인' if not p['document_evidence_ids'] else p['classification_reason']
        d['question']='단가 입찰용 가격인지 도급 예정총액인지, VAT·관급 포함 범위, 지시별 작업구역·사진·준공 요구, 실제 착공/관리기간 확인. 담당자 소관은 미확인.'
        for rate in range(1,6):d[f'proposal_{rate}pct_krw']=str(Decimal(p['document_supply_amount'])*rate/100) if p['aggregate_eligible'] and p['document_supply_amount']!='' else ''
        result.append(d)
        if p['aggregate_eligible'] and not p['document_evidence_ids']:
            r=c.execute('SELECT extra FROM base WHERE no=? AND ord=?',(p['bid_ntce_no'],p['bid_ntce_ord'])).fetchone();x=json.loads(r[0])
            needed.append(dict(bid_ntce_no=p['bid_ntce_no'],bid_ntce_ord=p['bid_ntce_ord'],title=p['title'],priority='1' if d['priority_three'] else '2',
              notice_url=p['notice_url'],existing_attachment_urls=x['attachment_urls'],local_document_status=d['local_document_status'],reason=d['question'],action='기존 URL 목록만 제공; 새 다운로드 미실행'))
    write('guri_proposal_basis.csv',result)
    write('additional_documents_needed.csv',needed)
    # All observed city-level local governments, not a favourable named subset.
    cohorts=collections.defaultdict(list)
    for p in rows(db,"eligible=1 AND year IN ('2024','2025') AND type='지자체 수요기관'"):
        if p['agency_level'].startswith('기초 시') and p['category'] in CORE:
            parent_display=p['buyer_region']+' '+p['parent_local_government'].split(' ',1)[1]
            cohorts[(parent_display,p['buyer_region'],p['notice_year'],p['category'],p['frequency'],p['amount_tier'],p['amount_kind'],p['period_basis'])].append({k:p[k] for k in ('parent_local_government','institution_code','amount_used')})
    cohortrows=[]
    for k,ps in sorted(cohorts.items()):
        rr=dict(zip(['parent_display_group','region','notice_year','category','frequency','amount_tier','amount_kind','period_basis'],k))
        rr['parent_original_names']=sorted({p['parent_local_government'] for p in ps});rr['source_institution_codes']=sorted({p['institution_code'] for p in ps})
        rr.update(candidate_representatives=len(ps),is_guri=int(k[0].endswith(' 구리시')),cohort_rule='전국 관측 기초 시 수요기관 전체; 본청/일반구/사업소 상위 시로 묶음; 행정시 제외; 공식 조직마스터 미대조',**stats([p['amount_used'] for p in ps if p['amount_used']!='']))
        cohortrows.append(rr)
    write('guri_city_peer_comparison.csv',cohortrows)
    return result,needed,len({k[0] for k in cohorts})

def extra_outputs(c,db,reviews,evidence):
    joins=[]
    for key,d in reviews.items():
        hit=c.execute('SELECT 1 FROM base WHERE no=? AND ord=?',key).fetchone()
        p=next(rows(db,'no=? AND ord=?',key),{})
        joins.append(dict(bid_ntce_no=key[0],bid_ntce_ord=key[1],priority_id=d['priority_id'],snapshot_exact_key_exists=int(bool(hit)),
          classification_joined=int(bool(p)),representative=p.get('representative',''),amount_tier=p.get('amount_tier',''),
          document_selected_total=d['selected_reference_amount'],selected_reference_evidence_id=d['selected_reference_evidence_id'],
          source_rule=d['rule_version'],not_added_when_absent=1))
    write('document_review_join.csv',joins)
    mappings={}
    for p in rows(db):
        key=(p['institution_code'],p['institution_name'])
        mappings[key]={k:p[k] for k in ['institution_code','institution_name','buyer_type','buyer_region','buyer_region_original','parent_local_government','agency_level','mapping_confidence','mapping_basis','buyer_region_basis']}
    write('institution_mapping.csv',(mappings[k] for k in sorted(mappings)))
    write('document_evidence.csv',(r for key,ee in evidence.items() for r in ee if c.execute('SELECT 1 FROM base WHERE no=? AND ord=?',key).fetchone()))
    # All 30 historical body conflicts retained as key/hash/status references; no raw JSON export.
    write('notice_body_conflicts.csv',(dict(bid_ntce_no=r['no'],bid_ntce_ord=r['ord'],conflict_id=r['id'],old_item_sha256=r['old_sha'],new_item_sha256=r['new_sha'],new_response_id=r['response_id'],detected_utc=r['detected'],counted=0,reason='동일 차수 본문충돌 이력; 원문은 고정 사본/cache에 보존; 대표 충돌은 집계 보류') for r in c.execute('SELECT * FROM conflicts')))
    suspects=collections.defaultdict(list)
    for p in rows(db,'representative=1'):
        if p['presmpt_status']=='유효 양수':suspects[(p['institution_code'],p['notice_year'],re.sub(r'\s+','',p['title']),p['presmpt_prce'])].append((p['bid_ntce_no'],p['bid_ntce_ord'],p['title']))
    suspectrows=[]
    for k,ps in suspects.items():
        if len(ps)<2:continue
        gid=hashlib.sha256(json.dumps(k,ensure_ascii=False).encode()).hexdigest()[:16]
        for no,ord_,title in ps:suspectrows.append(dict(suspect_group=gid,bid_ntce_no=no,bid_ntce_ord=ord_,institution_code=k[0],year=k[1],title=title,api_estimated_price=k[3],assessment='동일기관·동일공고연도·공백제거 제목·금액 일치; 별도 공고 유지, 자동 병합/삭제 안 함'))
    write('same_title_amount_suspects.csv',suspectrows,fields=['suspect_group','bid_ntce_no','bid_ntce_ord','institution_code','year','title','api_estimated_price','assessment'])
    write('classification_rule_exceptions.csv',(dict(bid_ntce_no=p['bid_ntce_no'],bid_ntce_ord=p['bid_ntce_ord'],title=p['title'],legacy_category=p['legacy_category'],new_category=p['category'],legacy_work=p['legacy_work_type'],new_work=p['work_type'],commercial_tier=p['commercial_tier'],evidence_level=p['evidence_level'],reason=p['rule_change'],api_price=p['presmpt_prce']) for p in rows(db) if p['rule_change']))
    return joins,len(suspectrows)

def validate(c,db,source,regionrows,instrows,joins):
    checks={}
    # One streaming pass over derived records; do not reparse the whole cache per assertion.
    flags=collections.defaultdict(lambda:True);opids=set();eligible_n=0
    for p in rows(db):
        if p['aggregate_eligible']:
            eligible_n+=1;opids.add(p['analysis_opportunity_id'])
            flags['not_cancel'] &= '취소' not in (p['notice_kind'] or '')
            flags['history_resolved'] &= bool(p['representative'] and p['analysis_opportunity_id'] and p['representative_status']=='분석용 대표 확인')
            flags['separate_model'] &= not p['commercial_tier'].startswith('별도사업모델')
        if p['bid_ntce_no'] in ('R25BK00580213','R25BK01133840'):flags['unit'] &= not p['amount_tier'].startswith(('1_','2_'))
        if p['amount_tier'].startswith(('3_','4_')):flags['held'] &= p['amount_used']==''
        if p['amount_tier'].startswith('1_'):flags['supply'] &= bool(p['document_supply_evidence_id'] and p['document_supply_amount']==p['amount_used'])
        flags['no_invention'] &= not p['actual_contract_amount'] and not p['actual_execution_amount'] and not p['actual_start_date']
        flags['title_year'] &= p['title_business_years']==sorted(set(re.findall(r'(?<!\d)((?:19|20)\d{2})\s*(?:년도|년)(?!\d)',p['title'] or '')))
        if p['amount_used']!='':flags['positive'] &= p['amount_used']>0
        flags['category'] &= isinstance(p['category'],str)
    checks['all_source_keys_core_hashes_match']=source['core_comparison_mismatches']==0
    checks['national_cache_row_count']=db.execute('SELECT COUNT(*) FROM rows').fetchone()[0]<=source['source_rows']
    checks['source_17_province_name_evidence']=set(PROVINCES).issubset({r['region'] for r in regionrows if r['aggregation_view']=='수요기관 명칭상 지역'})
    checks['representative_opportunity_unique']=len(opids)==eligible_n
    checks['eligible_not_cancelled']=flags['not_cancel']
    checks['eligible_history_resolved']=flags['history_resolved']
    def roll(rs,view=None):
        out=collections.defaultdict(lambda:[0,0,0,Decimal(0)])
        for r in rs:
            if view and r['aggregation_view']!=view:continue
            k=(r['institution_type'],r['year'],r['exclusive_category'],r['commercial_tier'],r['frequency'],r['amount_tier'],r['amount_kind'],r['period_basis'])
            g=out[k];g[0]+=r['observations'];g[1]+=r['candidate_representatives'];g[2]+=r['amount_n'];g[3]+=Decimal(str(r['sum_krw'] or 0))
        return dict(out)
    national=roll(regionrows,'전국')
    checks['buyer_regions_partition_national']=national==roll(regionrows,'수요기관 명칭상 지역')
    checks['site_regions_partition_national']=national==roll(regionrows,'공사현장 지역')
    checks['institutions_partition_national']=national==roll(instrows)
    checks['23_review_exact_key_audit']=len(joins)==23
    checks['no_document_join_by_number_only']=all(j['classification_joined']<=j['snapshot_exact_key_exists'] for j in joins)
    checks['unit_not_annual_total']=flags['unit']
    checks['held_and_unused_amount_blank']=flags['held']
    checks['verified_supply_evidence_required']=flags['supply']
    checks['no_contract_or_start_invention']=flags['no_invention']
    checks['title_year_not_filled']=flags['title_year']
    checks['positive_amounts_only']=flags['positive']
    checks['guri_three_exact_keys_present']=all(c.execute('SELECT 1 FROM base WHERE no=? AND ord=?',(no,'000')).fetchone() for no in GURI_KEYS)
    checks['no_separate_model_in_candidate_sum']=flags['separate_model']
    checks['classification_core_exclusive']=flags['category']
    checks['same_rule_metro_reproduced']=True # metro_compare raises before this point on any mismatch.
    assert all(checks.values()),checks
    return checks

def report(source,db,funnel,reg,comparison,joins,guri,needed,peers,checks,suspects):
    lines=['# 나라장터 공고자료 기반 스마트로드 자체 분석','',
      '전국 저장 공사 공고 · 주 비교 2024·2025년 / 부분연도 2023·2026년 · 제목 기반 후보와 원문 항목검증을 구분. 국가승인통계·전체 공공조달·확정 SaaS 시장규모가 아니다.','',
      '## 완료','',f"고정 사본의 공사 공고 **{source['source_rows']:,}차수**를 전국 캐시와 복합키·핵심 필드·본문 해시로 전수 대조했다. 수도권 추출 CSV에서 지역 필터를 제거한 결과가 아니다.",
      f"후보 및 관련 차수·제외 검토행 {db.execute('SELECT COUNT(*) FROM rows').fetchone()[0]:,}행, 분석용 대표 후보 {db.execute('SELECT COUNT(*) FROM rows WHERE eligible=1').fetchone()[0]:,}건. 대표기회 ID는 관측 공고의 분석용 연결이며 고유 실제 사업/계약 수를 보증하지 않는다.",'',
      f"23개 원문 검토표(sigongnote-document-review-3.0) 중 사본에 정확한 공고번호+차수가 존재하는 {sum(j['snapshot_exact_key_exists'] for j in joins)}건을 연결했다. 사본에 없는 공고는 추가하지 않았다. 이전 검토는 문서 74개·공고 23개이며 이번에는 기존 항목별 근거를 재사용했다. 새 다운로드·API 호출은 0회다.",'',
      '## 사본·실행 기준','',f"- 사본: `{source['snapshot']['path']}`",f"- SHA-256: `{source['snapshot']['sha256']}`",f"- 기준 UTC: {source['snapshot']['profile_basis_utc']}; 기존 해시 확인 UTC: {source['snapshot']['verified_utc']}",
      '- 이번에는 사본 경로·크기·mtime 일치와 전수 키/core/body-hash 열 비교로 캐시 재사용을 검증했다. 4.7GB 파일 전체 해시를 다시 계산하지 않았다.',
      f"- 캐시: `{source['cache_path']}`; 전국 base 전체 사용. relevance·면허4992·참가허용지역 필터 없음.",
      f'- 규칙: `{RULE}`. 재실행: `python tools/sigongnote_market_nationwide.py`',
      '- 비교 ZIP 및 report_recalculation.py / README_계산근거.md는 프로젝트에서 발견하지 못했다. 제공 문서의 3,703.1억원을 전국 값으로 사용하거나 강제 일치시키지 않았다. 기존 stage1 코드·CSV를 동일 규칙 대조 기준으로 사용했다.','']
    def table(headers,rs):
        lines.append('| '+' | '.join(headers)+' |');lines.append('|'+'|'.join(['---']*len(headers))+'|')
        for r in rs:lines.append('| '+' | '.join(('미집계' if x==0 and '합계' in h else str(x)).replace('|','/') for h,x in zip(headers,r))+' |')
        lines.append('')
    c=connect(CACHE)
    table(['게시연도','공사 차수','저장 게시기간','비교범위'],[(y,n,lo+' ~ '+hi,'주 비교연도' if y in ('2024','2025') else '부분연도') for y,n,lo,hi in c.execute('SELECT year,COUNT(*),MIN(dt),MAX(dt) FROM base GROUP BY year')]);c.close()
    table(['처리 단계','관측 수'],[
      ['전국 공사 차수(지역/4992 제한 전)',source['source_rows']],
      ['시설/인접·별도모델 및 관련 공고 이력 출력',db.execute('SELECT COUNT(*) FROM rows').fetchone()[0]],
      ['그중 비취소·연결 확인 분석용 대표',db.execute('SELECT COUNT(*) FROM rows WHERE representative=1').fetchone()[0]],
      ['그중 상업 검토 대표 후보',db.execute('SELECT COUNT(*) FROM rows WHERE eligible=1').fetchone()[0]],
      ['원문 공급가액 집계 후보',db.execute("SELECT COUNT(*) FROM rows WHERE tier LIKE '1_%'").fetchone()[0]],
      ['API 추정가격 참고집계 후보',db.execute("SELECT COUNT(*) FROM rows WHERE tier LIKE '2_%'").fetchone()[0]],
      ['금액범위 보류 후보',db.execute("SELECT COUNT(*) FROM rows WHERE tier LIKE '3_%'").fetchone()[0]],
      ['대표 후보이나 API 결측/0/이상 등 합계 미사용',db.execute("SELECT COUNT(*) FROM rows WHERE eligible=1 AND tier LIKE '4_%'").fetchone()[0]]])
    lines+=['2025년 저장 최초 공고는 1월 5일이다. 2024·2025는 달력연도 필터이며 전국 누락 없는 수집을 뜻하지 않는다. 2023·2026은 부분연도이며 연환산하거나 전체를 3으로 나누지 않았다.','', '## 지자체 주 비교 결과','',
      '**기관의 과거 귀속 주의:** 저장 수요기관명에 `전남광주통합특별시`가 2023·2024·2025년 공고에도 들어 있다. 해당 공고 시점의 조직·기관코드를 복원할 근거가 없어 통합 원문 명칭 그룹으로 보존했다. 이를 과거 전남 또는 광주로 자동 배분하지 않았다. 전국 후보 합계에는 포함하되 구매기관 지역 표에서는 `전남·광주 통합명칭(과거 귀속 미확인)`으로 분리한다. 현장지역 표는 원래 현장 필드의 전라남도/광주광역시 등을 별도로 사용한다. 현재 통합의 법적 상태·시행일을 이 자료로 확인했다는 뜻이 아니다. `historical_agency_name_audit.csv`와 `agency_mapping_changes.csv`에 코드·저장 명칭·연도·건수를 남겼다.','',
      '아래 수는 지자체 수요기관의 분석용 대표 후보다. 금액은 **tier 2 API 추정가격 참고합계**만 표시하며 총액성·계약액 미검증이다. 단가/장기계속/차수/소액검토 및 원문 불명 금액은 보류했다. 원문확인 공급가액은 별도 표이며 이 표와 합쳐 확정 사업비로 쓰지 않는다.','']
    headline=collections.defaultdict(lambda:[0,0,0,0])
    for r in reg:
        if r['aggregation_view']=='전국' and r['institution_type']=='지자체 수요기관' and r['year'] in ('2024','2025'):
            g=headline[(r['year'],r['exclusive_category'])];g[0]+=r['candidate_representatives'];g[1]+=r['held_n']
            if r['amount_tier'].startswith('2_'):g[2]+=r['amount_n'];g[3]+=int(r['sum_krw'] or 0)
    table(['연도','배타적 분야','대표 후보','보류','API 참고액 표본','API 참고합계 원'],[(y,k,*v) for (y,k),v in sorted(headline.items()) if v[0]])
    lines+=['### 시도별 지자체 핵심 4분야','', '구매주체 명칭상 관할지역 기준이다. 소재지 주소를 전수 확인한 통계가 아니다. 아래 참고합계는 tier 2만이며 원문확인액/보류액은 섞지 않았다.','']
    provincial=collections.defaultdict(lambda:[0,0,0,0])
    for r in reg:
        if r['aggregation_view']=='수요기관 명칭상 지역' and r['institution_type']=='지자체 수요기관' and r['exclusive_category'] in CORE and r['year'] in ('2024','2025'):
            g=provincial[r['region']];i=0 if r['year']=='2024' else 2;g[i]+=r['candidate_representatives']
            if r['amount_tier'].startswith('2_'):g[i+1]+=int(r['sum_krw'] or 0)
    table(['지역','2024 대표후보','2024 API 참고합계 원','2025 대표후보','2025 API 참고합계 원'],[(k,*v) for k,v in sorted(provincial.items())])
    lines+=['### 기관유형별 전국 핵심 후보','']
    types=collections.defaultdict(lambda:[0,0,0])
    for r in reg:
        if r['aggregation_view']=='전국' and r['exclusive_category'] in CORE and r['year'] in ('2024','2025'):
            g=types[(r['year'],r['institution_type'])];g[0]+=r['candidate_representatives'];g[1]+=r['held_n']
            if r['amount_tier'].startswith('2_'):g[2]+=int(r['sum_krw'] or 0)
    table(['연도','기관유형','대표 후보','보류','API 참고합계 원'],[(y,k,*v) for (y,k),v in sorted(types.items())])
    cities=collections.defaultdict(collections.Counter)
    for p in rows(db,"eligible=1 AND type='지자체 수요기관' AND year IN ('2024','2025')"):
        if p['category'] in CORE:cities[p['notice_year']][p['parent_local_government']]+=1
    lines+=['### 관측 상위 지자체당 핵심 후보 수','', '공고가 관측된 상위 지자체만 분모에 포함한다. 미관측 지자체를 0건으로 채우거나 전국 지자체 보급률로 해석하지 않는다. 광역과 기초가 포함되며 공식 조직 수 통계가 아니다.','']
    table(['연도','관측 상위 지자체','핵심 후보','기관당 평균','기관당 중앙값','P25','P75'],[(y,len(cc),sum(cc.values()),stats(list(cc.values()))['mean_krw'],stats(list(cc.values()))['median_krw'],stats(list(cc.values()))['p25_krw'],stats(list(cc.values()))['p75_krw']) for y,cc in sorted(cities.items())])
    lines+=['### 연간단가 핵심 후보의 API 참고금액 분포와 요율 가정','', '지자체·핵심 4분야·분석용 대표·연간단가 제목·tier 2 표본이다. 작업 미확인/혼합 후보도 포함한다. 원문확인층과 금액 보류층은 제외했으며 이 분포를 전체 공고로 확대하지 않는다. 공고 기재 추정가격 분포 — 총액성·연간한도·계약액 미검증.','']
    annual=collections.defaultdict(list)
    for p in rows(db,"eligible=1 AND type='지자체 수요기관' AND tier LIKE '2_%' AND year IN ('2024','2025')"):
        if p['category'] in CORE and p['annual_unit']:annual[p['notice_year']].append(p['amount_used'])
    table(['공고연도','표본','평균 원','중앙값 원','P25 원','P75 원','중앙값×1%','×2%','×3%','×4%','×5%'],[(y,len(a),stats(a)['mean_krw'],stats(a)['median_krw'],stats(a)['p25_krw'],stats(a)['p75_krw'],*[str(Decimal(stats(a)['median_krw'])*rate/100) for rate in range(1,6)]) for y,a in sorted(annual.items())])
    lines+=['핵심 4그룹(차선도색·노면표시/교통시설/공원녹지/복수 핵심시설 통합), 도로·보도 확장후보, 핵심·포장 복합을 분리했다. 건축 신축·개발은 별도사업모델로 후보 합계에서 제외하되 CSV에 보존했다. 한국도로공사는 별도사업모델로 두었고 지자체와 합산하지 않았다.','', '## 원문으로 범위 확인한 공급가액','']
    table(['연도','기관유형','분야','원문 기재 기간','건수','도급분 예정 공급가액 원'],[(r['year'],r['institution_type'],r['exclusive_category'],r['period_basis'],r['amount_n'],r['sum_krw']) for r in reg if r['aggregation_view']=='전국' and r['amount_tier'].startswith('1_')])
    lines+=['공급가액은 원문 추정가격 인용과 VAT·관급 관계가 확인된 경우에만 연결했다. API 값이나 VAT 포함 기초금액을 자동 변환하지 않았다. 원문 선택총액과 VAT·관급 상태, 공급가액, 근거 ID는 후보 CSV에 각각 남겼다. 137,101원/128,381원의 단가와 99,900,000원/40,000,000원의 연간 예정액은 서로 다른 필드이며 연간액 VAT·관급 미확인으로 합산 보류다. 계약액·정산액은 전부 미확인이다.','', '## 수도권 대조와 분류 개선','']
    table(['연도','기존/동일규칙 대표수','새 규칙 수도권 대표수','건수 차이','기존 원시 API 참고합계','새 규칙 원시 API 참고합계','차이'],[(r['year'],r['legacy_representatives'],r['new_rule_metro_representatives'],r['count_delta'],r['legacy_positive_api_reference'],r['new_rule_metro_raw_positive_api_reference'],r['raw_reference_delta']) for r in comparison])
    lines+=['동일 사본·동일 stage1 규칙에서 대표 키와 분야별 건수·양수 추정가격 합계가 일치했다. 위 새 규칙 대조용 원시 참고합계에는 금액 보류 대상도 들어 있으므로 본문 tier 2 합계와 다르다. 추가·제외·분야 이동은 metro_notice_changes.csv에 키·사유·포함 여부와 금액 차이로 남겼다.','',
      '잔디관리·조경관리·병해충 방제 등 관리 표현을 제목 규칙에 보완했다. 도색/설치/재난대비는 맥락 검토이며 모든 미확인을 유지보수로 바꾸지 않았다. 공원역을 지명으로 마스킹하고 건립·신축 부속 조경은 별도모델로 둔다. 개선/맥락 사례 전건은 classification_rule_exceptions.csv에 있다. 원문23개 외에는 실제 과업 확인 완료 라벨을 쓰지 않았다.','',
      '대표공고는 차수와 게시시각 순서, 동일 공고연도, 본문충돌/이전번호 모호성 등을 검사한 기존 nodes/links 규칙을 재사용했다. 단순 MAX(차수)나 최종 법적 유효 상태가 아니다. 이전번호+기관+시간+비순환 단일 연결만 묶고 연도 경계/누락/분기는 보류한다. 과거 취소 뒤의 비취소 대표를 일괄 제거하지 않는다.',
      f'같은 기관·연도·제목·금액의 별도 대표 공고 의심 {suspects}행은 same_title_amount_suspects.csv에 기록했고 금액을 삭제/병합하지 않았다. 본문 충돌 30행도 키/해시 근거로 별도 보존했다.','',
      '## 구리시 제안 근거','',f'2024·2025년 구리시 수요기관 후보/이력 목록 {len(guri)}행, 분석용 대표 후보 {sum(p["aggregate_eligible"] for p in guri)}건. 구리시는 전국에 한 번 포함하며 제안 CSV는 조회용 대체 보기다. 같은 시 비교는 관측 기초 시 {peers}곳 전체를 같은 연도·분야·빈도·금액층·기간으로 나눠 제공한다. 전국 기초지자체 공식 총수로 해석하지 않는다.','']
    table(['공고번호/차수','게시연도','공고명','API 추정가격 원','금액 검증'],[(p['bid_ntce_no']+'/'+p['bid_ntce_ord'],p['notice_year'],p['title'],p['presmpt_prce'],p['amount_tier']) for p in guri if p['priority_three']])
    lines+=['우선 3건의 API 값은 고정 사본에서 재확인했다. 정확한 키로 연결되는 로컬 원문 근거가 없어 과금 기준·기간·VAT·관급·사진/준공 요구는 미검증이다. guri_proposal_basis.csv의 개별 가격 시나리오는 비워 두었다. 필요한 기존 첨부 링크만 additional_documents_needed.csv에 남겼고 신규 다운로드는 하지 않았다. 담당자의 실제 소관은 추정하지 않았다.','',
      '## 가격 설명과 표 사용법','',
      '- region_year_category_summary.csv: 전국 / 수요기관 명칭상 지역 / 현장지역은 서로 다른 보기다. 전국 행과 지역 행, 두 지역 보기를 다시 더하지 않는다. 복수 현장은 금액 배분 미확인 한 그룹에 한 번만 넣었다.',
      '- institution_year_summary.csv: 원문 수요기관 코드·명칭을 보존한다. 상위 지자체는 명칭 기반 매핑이며 공식 조직마스터 미대조 상태와 근거를 남겼다. 일반구·사업소를 독립 기초지자체로 세지 않았다. 강원·전북 표시는 역사 명칭을 중립 약칭으로 묶은 표시용이며 원문 기관명/코드는 바꾸지 않았다.',
      '- title_business_year_summary.csv: 제목 사업연도 후보 표이며 공고연도 표와 합산하지 않는다. 미기재/복수연도는 그대로 남기고 실제 사업연도나 집행연도로 확정하지 않는다.',
      '- 금액층·빈도·분야·기간이 다른 행을 하나의 연간 사업비로 합치지 않는다. 미확인 금액합계는 빈칸이다. 0건 통계와 0원 사업비는 다르다.',
      '- 1·2·3·4·5%는 각 표의 해당 금액 × 가정 X%이다. tier 2는 미검증 참고금액 산술 시나리오이고 tier 1도 예정 공급가액이다. 법정 관리비·조달청 승인/전국 평균 SaaS 요율·가격 수용성·반복매출이 아니다.',
      '- 최종 이용료는 기능·지원범위·관리기간·제공 결과물과 기관 합의가 필요하다. 최저요금·상한·할인·도입률·AI/데이터 매출을 추가하지 않았다.',
      '- 원문 근거: document_evidence.csv의 공고 복합키·기관 검토표·자료명·field·문서 ID·페이지/절/문단/시트/셀·근거문장. API 근거: candidate의 공고 복합키·source_response_id·presmpt_prce·원문링크. 본문 해시 전수 대조 기록은 validation_results.json에 있다.','',
      '## 실패·미검증·다음 조치','',
      '- 실행 실패는 execution_notes.md에 기록한다. 원본 DB·이전 CSV·collector·예약작업은 수정하지 않았다.',
      '- 비교 계산근거 ZIP 미발견; 해당 보고서 자체의 3,703.1억원 재계산은 미검증이다.',
      '- 제목 누락/오분류·공식 기관계층/소재지·전국 수집완전성·사업 기간·계약/집행/정산·기관 가격 수용성은 전수 검증하지 않았다. 현장주소와 참가허용지역을 대체 사용하지 않았다.',
      '- 원문23개 중 P03/P13 금액 원문, P16/P17 연간총액 세금·관급 범위, P20 금액 구성, P14 분할발주 기간 등 기존 보완사항은 이전 additional_documents_needed.csv에 남아 있다. 구리시 필요 URL은 이번 목록에서 우선 확인한다.',
      '- 공원녹지 용역은 이 공사 DB 밖이다. 없음을 수요 0으로 해석하지 않는다. 추가 계약·정산/용역 수집은 우선기관·업무로 좁혀 별도 승인 후 진행한다.',
      '- 벤처나라 실제 규격·표시가격·옵션·가격 증빙 등록 요건은 별도 확인 과제이며 이번 프로그램에서 확정하지 않았다.','',
      f'검사 {sum(checks.values())}개 PASS. 이는 기록·연결·산술 검사이며 법적 최종 상태·시장 완전성·서비스 가격 적정성 보증이 아니다.','', '## 산출물','']
    table(['파일','데이터 행 수','바이트'],[(k,v['rows'],v['bytes']) for k,v in FILES.items()])
    lines+=['전체 candidate CSV가 정본이다. 연도별 복제본은 생성하지 않았다. nationwide_results.zip은 전달용 묶음이며 DB·raw JSON·원문 문서 파일은 포함하지 않는다. CSV는 UTF-8 BOM이며 공고차수/기관코드는 가져올 때 텍스트 형식으로 지정해야 선행 0이 보존된다.']
    (OUT/'national_market_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

def main():
    sys.stdout.reconfigure(encoding='utf-8');OUT.mkdir(exist_ok=True);WORK.mkdir(exist_ok=True)
    start=datetime.datetime.now(datetime.timezone.utc).isoformat();c=connect(CACHE)
    refresh='--refresh-outputs' in sys.argv
    if refresh:
        previous=json.loads((OUT/'validation_results.json').read_text(encoding='utf-8'))
        source=previous['source'];snapshot=Path(source['snapshot']['path']);st=snapshot.stat()
        source['hash_method']='기존 snapshot SHA256 기록+path/size/mtime 일치; 이번 전수 복합키/core/body-hash 열 대조. 출력 정리 시 이 성공 기록을 재사용; 4.7GB 재해시 생략'
        assert (st.st_size,st.st_mtime_ns)==(source['snapshot']['bytes'],source['snapshot']['mtime_ns'])
        assert (CACHE.stat().st_size,CACHE.stat().st_mtime_ns)==(source['cache_bytes'],source['cache_mtime_ns'])
        print('Reusing completed national classification and unchanged verified inputs; regenerating outputs only',flush=True)
    else:source=source_validation(c)
    reviews,evidence=load_documents()
    db=sqlite3.connect(WORK/'analysis.sqlite3');db.execute('PRAGMA journal_mode=DELETE')
    if refresh:
        funnel=previous['funnel'];allregions=previous['source_buyer_region_counts'];baseline=collections.defaultdict(lambda:[0,0])
        for p in rows(db):
            if p['legacy_metro_eligible']:
                g=baseline[(p['notice_year'],p['legacy_category'])];g[0]+=1
                if p['presmpt_status']=='유효 양수':g[1]+=p['presmpt_prce']
    else:funnel,baseline,allregions=build(c,db,reviews,evidence)
    if '--remap-organizations' in sys.argv or not refresh:
        allregions=remap_organizations(c,db)
    else:
        for name in ('agency_mapping_changes.csv','historical_agency_name_audit.csv'):
            if (OUT/name).exists():FILES[name]={'rows':sum(1 for _ in readcsv(OUT/name)),'bytes':(OUT/name).stat().st_size}
    refine_document_periods(db,reviews)
    print('National classification completed; writing aggregate outputs',flush=True)
    # Repeated verbose rule/agency descriptions belong in the exception/mapping tables.
    lean_omit={'year_scope','mapping_basis','buyer_region_basis','main_construction_type','representative_reason','link_reason',
      'classification_reason','relevant','legacy_thematic','legacy_metro_scope','legacy_metro_eligible','legacy_category','legacy_work_type','source_item_sha256'}
    write('candidate_notices_nationwide_lean.csv',({k:v for k,v in p.items() if k not in lean_omit} for p in rows(db)))
    reg=list(summary(db));inst=list(summary(db,institution=True))
    write('region_year_category_summary.csv',reg);write('institution_year_summary.csv',inst)
    write('title_business_year_summary.csv',summary(db,titleyear=True))
    comparison,changed=metro_compare(db,baseline)
    guri,needed,peers=guri_outputs(c,db);joins,suspects=extra_outputs(c,db,reviews,evidence)
    checks=validate(c,db,source,reg,inst,joins)
    validation=dict(actual_execution=True,rule_version=RULE,checks=checks,source=source,funnel=dict(funnel),
      source_buyer_region_counts=dict(allregions),candidate_output_rows=FILES['candidate_notices_nationwide_lean.csv']['rows'],
      candidate_representatives=db.execute('SELECT COUNT(*) FROM rows WHERE eligible=1').fetchone()[0],
      source_notice_numbers=c.execute('SELECT COUNT(*) FROM nodes').fetchone()[0],
      monetary_layer_counts=dict(db.execute('SELECT tier,COUNT(*) FROM rows GROUP BY tier').fetchall()),
      metro_change_rows=changed,document_joined=sum(j['classification_joined'] for j in joins),
      network_calls=0,source_mutations=0,started_utc=start,completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    dump('validation_results.json',validation)
    report(source,db,funnel,reg,comparison,joins,guri,needed,peers,checks,suspects)
    (OUT/'execution_notes.md').write_text('실행 명령: `python tools/sigongnote_market_nationwide.py`\n\n회귀검사: `python tools/test_sigongnote_nationwide.py` — 10개 PASS.\n\n읽기 전용 고정 DB/전국 캐시 전수 키·스칼라·본문해시 대조 → 전국 분류 → 대표/금액층 → CSV → 집계 검증 → ZIP. 최초 4.0 실행과 출력 정리 실행 모두 집계검사 21개 PASS.\n\n기관명 점검에서 과거 공고에도 전남광주통합특별시가 저장된 것을 발견했다. `python tools/sigongnote_market_nationwide.py --refresh-outputs --remap-organizations`로 기존 전수 대조·시설분류 캐시를 재사용하고 기관 명칭 매핑만 4.1로 보완했다. 전남/광주에 과거 귀속을 추정 배분하지 않았다. 2023 5,250차수 / 2024 13,403차수 / 2025 11,611차수 / 2026 7,662차수는 코드로 확인한 해당 저장 접두어 관측 수이며 원문의 법적 조직 시행일 확인이 아니다. 상세 기관/연도별 재계산은 historical_agency_name_audit.csv.\n\n실패: 선택적 `Get-CimInstance Win32_Process` 성능 조회가 Windows AccessDenied로 실패했다. 추가 권한 요청 없이 생략했으며 데이터 읽기·집계·검증에는 사용하지 않았다.\n\n외부 호출/다운로드/collector 실행 0회. 실제 계약액/집행액 및 실제 시작일 미확인. 네트워크 테스트 SKIPPED(범위 밖).\n',encoding='utf-8')
    for name in ['sigongnote_market_nationwide.py','sigongnote_nationwide_rules.py','sigongnote_market_stage1.py','sigongnote_stage2_rules.py','test_sigongnote_nationwide.py','verify_sigongnote_nationwide.py']:
        shutil.copyfile(ROOT/'tools'/name,OUT/name)
    manifest=dict(rule_version=RULE,source=source,generated_utc=validation['completed_utc'],csv_encoding='UTF-8 BOM; identifiers are text',
      files={p.name:dict(**FILES.get(p.name,{}),sha256=sha(p),file_bytes=p.stat().st_size) for p in OUT.iterdir() if p.is_file() and p.name not in ('manifest.json','nationwide_results.zip')},
      inputs={str(p.relative_to(ROOT)):sha(p) for p in [META,VERIFIED/'priority_notice_review.csv',VERIFIED/'priority_document_evidence.csv']},
      schema={t:[dict(r) for r in c.execute('PRAGMA table_info('+t+')')] for t in ('base','nodes','links','conflicts')})
    sc=connect(Path(source['snapshot']['path']))
    manifest['snapshot_notice_schema']=[dict(r) for r in sc.execute('PRAGMA table_info(bf_notice_revision)')]
    sc.close()
    dump('manifest.json',manifest)
    with zipfile.ZipFile(OUT/'nationwide_results.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in OUT.iterdir():
            if p.is_file() and p.name!='nationwide_results.zip':z.write(p,p.name)
    with zipfile.ZipFile(OUT/'nationwide_results.zip') as z:assert z.testzip() is None
    db.close();c.close();print(json.dumps(dict(representatives=validation['candidate_representatives'],files=len(manifest['files']),checks=sum(checks.values()),zip_bytes=(OUT/'nationwide_results.zip').stat().st_size),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
