"""Offline, streaming procurement candidate analysis. No collector imports/network."""
from __future__ import annotations
import argparse
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.local/sigongnote_stage1'
OUT = ROOT / 'outputs/sigongnote_market_stage1'
RULE = 'sigongnote-stage1-1.1'
PROVINCES = ('서울특별시', '인천광역시', '경기도')
CORE = ('차선도색·노면표시', '교통시설 유지보수', '공원녹지 유지보수')
CLEAR, REVIEW, EXCLUDE = '근거가 명확한 후보', '검토 필요', '제외'

def clean(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()

def money(v):
    if v is None or str(v).strip() == '': return None, 'NULL'
    try:
        n=Decimal(str(v).replace(',','').strip())
        if not n.is_finite() or n!=n.to_integral_value() or n<0 or n>9223372036854775807:
            return None,'이상값'
        return int(n),'0' if n==0 else '유효 양수'
    except InvalidOperation: return None,'이상값'

def safe_url(v):
    s=str(v or '').strip()
    try:
        u=urlsplit(s)
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password: return ''
        if re.search(r'(?i)(servicekey|api[_-]?key|access[_-]?token|authorization|email|telno|phoneno)=',u.query): return ''
        return s
    except ValueError: return ''

def title_years(title):
    return sorted(set(re.findall(r'(?<!\d)((?:19|20)\d{2})\s*(?:년도|년)(?!\d)',title)))

def organization(name):
    """Name-derived historical text mapping, never an authoritative agency registry."""
    s=clean(name); province=next((p for p in PROVINCES if s.startswith(p)), '')
    if re.search(r'교육청|교육지원청|학교|대학교|교육연구',s):
        return ('기타기관(교육)', '', province, '원문 기관명 교육 표기', '원문명칭 기반')
    if re.search(r'한국\S*(?:공사|공단)|(?:도시|주택도시|시설관리|도시관리|환경|교통|관광|철도)공(?:사|단)|서울교통공사|서울시설공단|서울주택도시공사|경기주택도시공사',s):
        return ('공기업·공단 후보', '', province, '원문 기관명 공사/공단 표기; 법정 유형 미검증','원문명칭 기반')
    if re.search(r'^(?:국토교통부|환경부|국방부|해양수산부|문화체육관광부|법무부|농림축산식품부|조달청|경찰청|산림청|육군|해군|공군)|경찰서|지방국토관리청|국토관리사무소',s):
        return ('중앙기관 후보', '', province, '원문 기관명 중앙기관 표기; 조직 마스터 미대조','원문명칭 기반')
    # Do not mistake regional public entities for the local government itself.
    if re.search(r'재단|공단|공사$|공사\s|의료원|병원|공기업|협회|법인|주식회사|농협|공동주택|아파트',s):
        return ('기타기관/유형 미확인','',province,'별도 법인/공공기관 표기; 지자체로 합치지 않음','미확인')
    m=re.match(r'^(서울특별시|인천광역시|경기도)(?:\s+|$)(.*)$',s)
    if m:
        p,rest=m.groups(); first=rest.split(' ')[0] if rest else ''
        if p=='경기도' and re.fullmatch(r'[가-힣]+(?:시|군)',first): parent=p+' '+first
        elif p in ('서울특별시','인천광역시') and re.fullmatch(r'[가-힣]+(?:구|군)',first): parent=p+' '+first
        elif not rest or re.match(r'(?:본청|의회|청|도|시|군|구|건설|도로|도시|공원|녹지|산림|환경|상수도|하수도|수도|물|한강|푸른|북부|남부|동부|서부|중부|경제자유|소방|안전|교통|종합|사업|농업|인재|자치|보건|미래|체육|문화|해양|수산|팔당|광교|농수산)',rest): parent=p
        else: return ('기관유형 미확인','',p,'수도권 명칭은 있으나 지자체 직속/별도기관 여부 불명','미확인')
        return ('수도권 지자체 수요기관 후보',parent,p,'수요기관 원문 접두어: '+parent+'; 현재 조직으로 소급하지 않음','원문명칭 명시(공식 계층 미검증)')
    return ('기타기관/유형 미확인','','','수도권 지자체 명칭 근거 없음','미확인')

def geography(site, orgname):
    typ,parent,oprov,obasis,mapping=organization(orgname)
    s=clean(site); caps=[]
    for p,pattern in [('서울특별시',r'서울(?:특별시|시)?'),('인천광역시',r'인천(?:광역시|시)?'),('경기도',r'경기도|경기(?=\s|$|[,/])')]:
        if re.search(pattern,s): caps.append(p)
    other=bool(re.search(r'부산|대구|광주광역|대전|울산|세종|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주|전국',s))
    local=typ=='수도권 지자체 수요기관 후보'
    if caps and other: region='수도권·비수도권 혼합'; basis='현장 원문에 수도권 및 다른 지역 병존'; geo_review=True
    elif caps: region=caps[0] if len(caps)==1 else '수도권 복수';basis='cnstrtsiteRgnNm 수도권 명시';geo_review=False
    elif local: region=oprov;basis='수도권 지자체 수요기관 명칭; '+('현장 비수도권/전국' if other else '현장 수도권 미확인');geo_review=True
    else: region='범위 밖/미확인';basis='수도권 현장/지자체 수요기관 근거 없음';geo_review=True
    scope_status='수도권·비수도권 혼합현장' if caps and other else '수도권 현장 명시' if caps else '수도권기관·비수도권/전국현장' if local and other else '수도권기관·현장 미확인' if local else '범위 밖/미확인'
    return dict(in_scope=bool(caps or local),region=region,site_regions=caps,geo_basis=basis,geo_review=geo_review,geography_scope_status=scope_status,
                buyer_type=typ,parent_local_government=parent,agency_province=oprov,agency_mapping_status=mapping,
                agency_mapping_basis=obasis,buyer_segment='수도권 지자체 수요기관' if local else '수도권 현장 기타기관',
                responsible_department='미확인')

def classify(title, main=''):
    t=clean(title);main=clean(main); compact=re.sub(r'\s+','',t)
    lane=bool(re.search(r'차선(?:재)?도색|차선도장|노면표시|노면표지|차로도색|노면색깔유도선|차선.*(?:제거|보수|재도색)',compact))
    traffic=bool(re.search(r'교통안전시설|교통시설|교통신호|신호등|신호기|교통표지|도로표지|안전표지|도로반사경|시선유도|방호울타리|중앙분리대|가드레일|보행자안전|횡단보도조명|무단횡단방지',compact))
    if re.search(r'표지판',compact) and re.search(r'도로|교통|보행|횡단',compact):traffic=True
    park=bool(re.search(r'공원|녹지|가로수|가로화단|수목|조경|예초|전정|풀베기|제초|잔디|도시숲|산책로|수형조절',compact))
    road=bool(re.search(r'도로|보도|차도|포장|인도정비|아스콘|덧씌우기|보행환경|자전거도로|노면',compact))
    near=bool(re.search(r'시설물|안전시설|울타리|보행|제방|임도|숲가꾸기|사방|하천',compact))
    maint=bool(re.search(r'보수|유지|정비|재도색|제거|보식|예초|제초|전정|풀베기|수형조절|병해충|잔디깎|교체|청소|보강|복구|녹지관리|수목관리|가로수관리|공원관리(?!사무)',compact))
    new=bool(re.search(r'신설|확장|조성|개설|신규|신축|확충|증축|설치|도로건설',compact))
    work='혼합' if maint and new else '유지보수' if maint else '신설·확장' if new else '미확인'
    annual=bool(re.search(r'연간\s*단가|연중\s*단가',t))
    annual_install_ambiguous=annual and new and not maint and not re.search(r'신설|확장|조성|개설|신규|신축|확충|증축|도로건설',compact)
    if annual_install_ambiguous:work='미확인'
    recurring=bool(re.search(r'상시|연중|연간|연례|긴급보수|유지관리',compact))
    frequency='연간단가 후보' if annual else '상시·반복 후보' if recurring else '일회성 명시' if re.search(r'일회성|단발성',compact) else '반복성 미확인(일회성 여부 미확인)'
    facilities=[k for k,v in zip(CORE,[lane,traffic,park]) if v]
    paving=bool(re.search(r'포장|아스콘|덧씌우기',compact))
    if len(facilities)>1 or (facilities and paving and maint): primary='혼합(핵심·확장 또는 복수핵심)'
    elif facilities:primary=facilities[0]
    elif road:primary='확장후보: 도로·보도 일반보수'
    elif near:primary='인접분야: 시설·하천·산림 등'
    else:primary='대상 근거 없음'
    tags=facilities+(['도로·보도 일반보수'] if road else [])
    core=bool(facilities)
    expansion=road and not core
    possible=core or expansion
    if not possible:status=EXCLUDE;why='핵심/도로·보도 시설어 근거 부족; 미검출은 수요 없음의 증거가 아님'
    elif work=='신설·확장':status=EXCLUDE;why='제목에서 신설·확장 작업만 관측'
    elif work in ('미확인','혼합') or primary.startswith('혼합'):status=REVIEW;why='작업유형 또는 시설별 범위/금액 분리가 필요'
    else:status=CLEAR;why='제목의 대상시설 및 유지보수 작업어 동시 관측'
    if not possible and re.search(r'조경|도장|교통',main):why+='; 주공종 보조어 있어 놓친 후보 검토'
    if annual_install_ambiguous:why+='; 연간단가 설치가 반복교체/보수인지 신설인지 미확인'
    shape=('장기계속 총액 후보' if re.search(r'장기계속|총괄',t) else '연차금액 후보' if re.search(r'\d+\s*차(?:분|년도)',t) else '연간단가(한도/단가 미확인)' if annual else '단가 후보' if '단가' in t else '연간한도 후보' if re.search(r'연간한도|한도액',compact) else '총액 후보' if '총액' in t else '미확인')
    return dict(primary_class=primary,facility_tags=tags,work_type=work,annual_unit_candidate=annual,maintenance_frequency=frequency,
                recurring_maintenance_candidate=recurring,ordinary_repair_candidate=maint and not annual,
                classification_status=status,classification_reason=why,classification_evidence_field='bidNtceNm (제목)',
                classification_evidence_scope='제목 기반 후보; 첨부·전체 과업범위 미검증',core_or_expansion='핵심' if core and not primary.startswith('혼합') else '혼합 별도검토' if primary.startswith('혼합') else '확장후보' if expansion else '제외/인접',
                thematic_candidate=possible and status!=EXCLUDE,adjacent=near or expansion,
                title_business_years=title_years(t),amount_shape=shape,amount_shape_status='미확인(제목 태그이며 추정가격의 총액성 증거 아님)',rule_version=RULE)

def connect(path):
    c=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');return c

def snapshot():
    p=Path((ROOT/'.local/data_profile/snapshot_path.txt').read_text(encoding='utf-8').strip())
    if not p.is_file():raise RuntimeError('검증된 기존 사본 없음: 일관된 새 백업을 별도로 준비해야 함')
    wal=Path(str(p)+'-wal')
    if wal.exists() and wal.stat().st_size:raise RuntimeError('사본에 WAL 존재: immutable 읽기 대신 일관된 새 백업 필요')
    expected=json.loads((ROOT/'.local/data_profile/aggregates.json').read_text(encoding='utf-8'))
    signature={'path':str(p),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
    marker=WORK/'snapshot_verified.json'
    old=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    if any(old.get(k)!=v for k,v in signature.items()) or old.get('sha256')!=expected['snapshot_sha256']:
        h=hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=expected['snapshot_sha256']:raise RuntimeError('기존 사본 해시 불일치; 분석 중단')
        signature.update(sha256=h.hexdigest(),verified_utc=dt.datetime.now(dt.timezone.utc).isoformat(),profile_basis_utc=expected['started_utc'])
        marker.write_text(json.dumps(signature,indent=2),encoding='utf-8')
    else:signature=old
    return p,signature

def extract(c, db):
    """One source-table pass; retain only approved scalar/URL fields in local cache."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='extraction_complete'").fetchone():return
    db.executescript('''DROP TABLE IF EXISTS base; DROP TABLE IF EXISTS conflicts;
 CREATE TABLE base(no TEXT,ord TEXT,dt TEXT,year TEXT,prev TEXT,kind TEXT,reyn TEXT,
 org TEXT,orgname TEXT,title TEXT,site TEXT,main TEXT,price INTEGER,budget INTEGER,vat INTEGER,extra TEXT,
 PRIMARY KEY(no,ord)); CREATE TABLE conflicts(no TEXT,ord TEXT,id INTEGER,old_sha TEXT,new_sha TEXT,response_id INTEGER,detected TEXT,safe_json TEXT);''')
    sql='''SELECT bid_ntce_no,bid_ntce_ord,bid_ntce_dt,bef_bid_ntce_no,ntce_kind_nm,re_ntce_yn,
 dminstt_cd,dminstt_nm,bid_ntce_nm,cnstrtsite_rgn_nm,main_cnstty_nm,presmpt_prce,bdgt_amt,vat,
 ntce_instt_cd,ntce_instt_nm,first_response_id,last_response_id,first_seen_utc,last_seen_utc,item_sha256,quality_flag,item_json
 FROM bf_notice_revision'''
    batch=[];total=0
    for r in c.execute(sql):
        x=json.loads(r['item_json']); links=[safe_url(x.get('ntceSpecDocUrl'+str(i))) for i in range(1,11)]
        extra={k:r[k] for k in ['ntce_instt_cd','ntce_instt_nm','first_response_id','last_response_id','first_seen_utc','last_seen_utc','item_sha256','quality_flag']}
        extra.update(govsplyAmt=money(x.get('govsplyAmt'))[0],govsplyAmt_status=money(x.get('govsplyAmt'))[1],
                     presmpt_status=money(x.get('presmptPrce'))[1],budget_status=money(x.get('bdgtAmt'))[1],vat_status=money(x.get('VAT'))[1],
                     notice_url=safe_url(x.get('bidNtceDtlUrl')) or safe_url(x.get('bidNtceUrl')),
                     attachment_urls=[u for u in links if u],cntrct_method=clean(x.get('cntrctCnclsMthdNm')),bid_method=clean(x.get('bidMethdNm')))
        batch.append((r['bid_ntce_no'],r['bid_ntce_ord'],r['bid_ntce_dt'],(r['bid_ntce_dt'] or '')[:4],r['bef_bid_ntce_no'],r['ntce_kind_nm'],r['re_ntce_yn'],r['dminstt_cd'],r['dminstt_nm'],r['bid_ntce_nm'],r['cnstrtsite_rgn_nm'],r['main_cnstty_nm'],r['presmpt_prce'],r['bdgt_amt'],r['vat'],json.dumps(extra,ensure_ascii=False)))
        if len(batch)>=2000:
            db.executemany('INSERT INTO base VALUES ('+','.join('?'*16)+')',batch);db.commit();total+=len(batch);batch=[]
            if total%50000==0:print('Streamed notice revisions:',total,flush=True)
    db.executemany('INSERT INTO base VALUES ('+','.join('?'*16)+')',batch)
    # Preserve existing conflict history through a safe, separate row representation.
    for r in c.execute("SELECT * FROM bf_record_conflict WHERE table_name='bf_notice_revision'"):
        x=json.loads(r['old_item_json']);no=x.get('bidNtceNo');ord_=x.get('bidNtceOrd')
        safe={k:x.get(k) for k in ['bidNtceNo','bidNtceOrd','bidNtceNm','bidNtceDt','ntceKindNm','reNtceYn','befBidBbancNo','dminsttCd','dminsttNm','ntceInsttCd','ntceInsttNm','cnstrtsiteRgnNm','mainCnsttyNm','presmptPrce','bdgtAmt','VAT','govsplyAmt']}
        safe['notice_url']=safe_url(x.get('bidNtceDtlUrl'));safe['attachment_urls']=[u for i in range(1,11) if (u:=safe_url(x.get('ntceSpecDocUrl'+str(i))))]
        db.execute('INSERT INTO conflicts VALUES (?,?,?,?,?,?,?,?)',(no,ord_,r['id'],r['old_sha256'],r['new_sha256'],r['response_id'],r['detected_at_utc'],json.dumps(safe,ensure_ascii=False)))
    db.executescript('CREATE INDEX base_prev ON base(prev); CREATE TABLE extraction_complete(n INTEGER); INSERT INTO extraction_complete SELECT COUNT(*) FROM base;');db.commit()
    print('Source extraction complete.',flush=True)

def build_nodes(db):
    db.executescript('DROP TABLE IF EXISTS nodes; CREATE TABLE nodes(no TEXT PRIMARY KEY,ord TEXT,dt TEXT,year TEXT,org TEXT,prev TEXT,reyn TEXT,kind TEXT,title_years TEXT,selection TEXT,reason TEXT,cancel_history INTEGER,nrev INTEGER);')
    conflict_keys={(r[0],r[1]) for r in db.execute('SELECT no,ord FROM conflicts')}
    group=[];old=None;batch=[]
    def emit(rows):
        valid=all(re.fullmatch(r'\d+',r['ord'] or '') and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',r['dt'] or '') for r in rows)
        ordered=sorted(rows,key=lambda r:int(r['ord'])) if valid else rows
        monotonic=valid and all(a['dt']<=b['dt'] for a,b in zip(ordered,ordered[1:]))
        selected=max(rows,key=lambda r:(r['dt'] or '',int(r['ord']) if (r['ord'] or '').isdigit() else -1))
        conflict=(selected['no'],selected['ord']) in conflict_keys
        year_boundary=len({r['year'] for r in rows})>1
        previous_ambiguity=len({clean(r['prev']) for r in rows if clean(r['prev'])})>1
        status='분석용 대표 확인' if monotonic and not conflict and not year_boundary and not previous_ambiguity and selected['kind'] in ('등록공고','변경공고','재공고','취소공고') else '대표 선정 미확인'
        reason='차수 숫자/게시시각 순서 일치; 같은 공고연도의 마지막 관측 버전 선택(법적 유효 최신 확정 아님)' if status=='분석용 대표 확인' else '차수·시각 불일치/결측, 차수의 연도 경계, 이전번호 모호, 미확인 상태 또는 대표 동일키 본문 충돌; 잠정행만 제시'
        cancel=any('취소' in (r['kind'] or '') for r in rows)
        return (selected['no'],selected['ord'],selected['dt'],selected['year'],selected['org'],selected['prev'],selected['reyn'],selected['kind'],json.dumps(title_years(selected['title'] or ''),ensure_ascii=False),status,reason,int(cancel),len(rows))
    for r in db.execute('SELECT no,ord,dt,year,org,prev,reyn,kind,title FROM base ORDER BY no,ord'):
        if old is not None and r['no']!=old:
            batch.append(emit(group));group=[]
            if len(batch)>=2000:db.executemany('INSERT INTO nodes VALUES ('+','.join('?'*13)+')',batch);batch=[]
        old=r['no'];group.append(r)
    if group:batch.append(emit(group))
    db.executemany('INSERT INTO nodes VALUES ('+','.join('?'*13)+')',batch);db.commit()

def lineage(db):
    """Only explicit, nonbranching, acyclic, same-year/agency chronological chains."""
    edges={};issues=collections.defaultdict(set);linked=set();target_ord={}
    for r in db.execute("SELECT * FROM nodes WHERE COALESCE(prev,'')!='' OR reyn='Y'"):
        no=r['no'];prev=clean(r['prev'])
        if not prev:issues[no].add('재공고 이전번호 누락');continue
        parent=db.execute('SELECT * FROM nodes WHERE no=?',(prev,)).fetchone();explicit_ord=''
        if parent is None and '-' in prev:
            p,o=prev.rsplit('-',1)
            if db.execute('SELECT 1 FROM base WHERE no=? AND ord=?',(p,o)).fetchone():
                parent=db.execute('SELECT * FROM nodes WHERE no=?',(p,)).fetchone();explicit_ord=o
        if parent is None:issues[no].add('이전 공고 연결 누락/형식 미확인');continue
        pn=parent['no'];edges[no]=pn;linked.update([no,pn]);target_ord[no]=explicit_ord
        errors=[]
        if no==pn:errors.append('자기 순환')
        if not r['org'] or r['org']!=parent['org']:errors.append('수요기관 불일치/결측')
        if r['year']!=parent['year']:errors.append('공고연도 경계 연결; 연도 이동/병합 보류')
        if (r['dt'] or '')<(parent['dt'] or ''):errors.append('전후 시각 역전')
        if r['selection']!='분석용 대표 확인' or parent['selection']!='분석용 대표 확인':errors.append('대표 버전 미확인')
        a,b=set(json.loads(r['title_years'])),set(json.loads(parent['title_years']))
        if a and b and a!=b:errors.append('제목상 사업연도 불일치')
        if errors:
            issues[no].update(errors);issues[pn].update(errors)
    children=collections.defaultdict(list)
    for child,parent in edges.items():children[parent].append(child)
    for p,cs in children.items():
        if len(cs)>1:
            for n in [p,*cs]:issues[n].add('동일 이전 공고의 복수 후속 연결')
    adjacency=collections.defaultdict(set)
    for a,b in edges.items():adjacency[a].add(b);adjacency[b].add(a)
    mapping={};visited=set();link_report=collections.Counter()
    for start in sorted(linked):
        if start in visited:continue
        stack=[start];component=set()
        while stack:
            n=stack.pop()
            if n in component:continue
            component.add(n);stack.extend(adjacency[n]-component)
        visited.update(component)
        roots=[n for n in component if n not in edges];leaves=[n for n in component if not children[n]]
        reasons=set().union(*(issues[n] for n in component))
        if len(roots)!=1 or len(leaves)!=1:reasons.add('순환/분기 또는 종단 모호')
        if reasons:
            why='; '.join(sorted(reasons))
            for n in component:mapping[n]=('',n,'재공고 연결 미확인',why)
            link_report['unresolved_components']+=1
        else:
            root,leaf=roots[0],leaves[0]
            for n in component:mapping[n]=('OBS:'+root,leaf,'명시적 재공고 연결','이전번호+동일 수요기관/공고연도+시각순서+단일 비순환 연결')
            link_report['merged_components']+=1
    for n,reasons in issues.items():
        if n not in mapping:mapping[n]=('',n,'재공고 연결 미확인','; '.join(sorted(reasons)))
    db.executescript('DROP TABLE IF EXISTS links; CREATE TABLE links(no TEXT PRIMARY KEY,op_id TEXT,terminal TEXT,status TEXT,reason TEXT);')
    db.executemany('INSERT INTO links VALUES (?,?,?,?,?)',[(k,*v) for k,v in mapping.items()]);db.commit()
    return dict(link_report)

def enrich(c,no,ord_):
    licenses=[];license_sources=[]
    for r in c.execute('SELECT license_code,lcns_lmt_nm,indstryty_mfrc_fld_list,quality_flag,response_id FROM bf_license_limit WHERE bid_ntce_no=? AND bid_ntce_ord=?',(no,ord_)):
        licenses.append(dict(r));license_sources.append(r['response_id'])
    regions=[];region_sources=[]
    for r in c.execute('SELECT prtcpt_psbl_rgn_nm,response_id FROM bf_allowed_region WHERE bid_ntce_no=? AND bid_ntce_ord=?',(no,ord_)):
        if r[0]:regions.append(r[0])
        region_sources.append(r[1])
    openings=[dict(r) for r in c.execute('SELECT bid_clsfc_no,rbid_no,progrs_div_cd_nm,response_id FROM bf_opening_unit WHERE bid_ntce_no=? AND bid_ntce_ord=?',(no,ord_))]
    return dict(license_evidence=licenses,allowed_regions=sorted(set(regions)),license_source_response_ids=sorted(set(x for x in license_sources if x is not None)),region_source_response_ids=sorted(set(x for x in region_sources if x is not None)),opening_units=openings,
                auxiliary_join_basis='bid_ntce_no + bid_ntce_ord 정확 일치; 집합/배열 보존',
                license_fetch_state='행 있음(AND/OR 판정 미실시)' if licenses else '미수집/무자료 구분 미확인',region_fetch_state='행 있음' if regions else '미수집/무제한/무자료 구분 미확인')

def row_payload(r):
    x=json.loads(r['extra']);g=geography(r['site'],r['orgname']);cl=classify(r['title'],r['main'])
    for value_key,status_key in [('price','presmpt_status'),('budget','budget_status'),('vat','vat_status')]:
        if x[status_key]=='유효 양수' and (r[value_key] is None or r[value_key]<=0):x[status_key]='이상값'
    if g['geo_review'] and cl['classification_status']==CLEAR:cl['classification_status']=REVIEW;cl['classification_reason']+='; 수도권 현장 범위 확인 필요'
    return dict(observation_id=r['no']+'|'+r['ord']+'|CURRENT',record_kind='공고 차수 현재 관측',bid_ntce_no=r['no'],bid_ntce_ord=r['ord'],previous_notice_no=r['prev'] or '',title=r['title'],notice_datetime=r['dt'],notice_year=r['year'],
                dminstt_cd=r['org'],dminstt_nm=r['orgname'],ntce_instt_cd=x['ntce_instt_cd'],ntce_instt_nm=x['ntce_instt_nm'],site_region_original=r['site'],main_construction_type=r['main'],
                presmpt_prce=r['price'],bdgt_amt=r['budget'],vat=r['vat'],govsplyAmt=x['govsplyAmt'],presmpt_status=x['presmpt_status'],budget_status=x['budget_status'],vat_status=x['vat_status'],govsplyAmt_status=x['govsplyAmt_status'],
                notice_kind=r['kind'],renotice_yn=r['reyn'],notice_url=x['notice_url'],attachment_urls=x['attachment_urls'],source_response_id=x['last_response_id'],first_response_id=x['first_response_id'],item_sha256=x['item_sha256'],quality_flag=x['quality_flag'] or '',
                first_seen_utc=x['first_seen_utc'],last_seen_utc=x['last_seen_utc'],contract_method=x['cntrct_method'],bid_method=x['bid_method'],**g,**cl)

def analyze(db,c):
    aux_fields=('license_evidence','allowed_regions','license_source_response_ids','region_source_response_ids','opening_units','auxiliary_join_basis','license_fetch_state','region_fetch_state')
    db.execute('CREATE TABLE IF NOT EXISTS auxiliary_cache(no TEXT,ord TEXT,payload TEXT,PRIMARY KEY(no,ord))')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exported'").fetchone():
        batch=[]
        for row in db.execute("SELECT no,ord,payload FROM exported WHERE record_kind='공고 차수 현재 관측'"):
            old=json.loads(row['payload'])
            if all(k in old for k in aux_fields):batch.append((row['no'],row['ord'],json.dumps({k:old[k] for k in aux_fields},ensure_ascii=False)))
            if len(batch)>=500:db.executemany('INSERT OR IGNORE INTO auxiliary_cache VALUES (?,?,?)',batch);batch=[]
        db.executemany('INSERT OR IGNORE INTO auxiliary_cache VALUES (?,?,?)',batch)
    db.executescript('DROP TABLE IF EXISTS classified; CREATE TABLE classified(no TEXT,ord TEXT,in_scope INTEGER,thematic INTEGER,status TEXT,primary_class TEXT,year TEXT,region TEXT,annual INTEGER,adjacent INTEGER,price INTEGER,payload TEXT,PRIMARY KEY(no,ord));')
    batch=[];funnel=collections.Counter();year_counts=collections.Counter()
    for r in db.execute('SELECT * FROM base'):
        funnel['source_revisions']+=1;year_counts[r['year']]+=1
        g=geography(r['site'],r['orgname'])
        if not g['in_scope']:continue
        p=row_payload(r);funnel['geography_or_buyer_scope']+=1
        if p['thematic_candidate']:funnel['thematic_candidate_revisions']+=1
        batch.append((r['no'],r['ord'],1,int(p['thematic_candidate']),p['classification_status'],p['primary_class'],r['year'],p['region'],int(p['annual_unit_candidate']),int(p['adjacent']),r['price'],json.dumps(p,ensure_ascii=False)))
        if len(batch)>=1000:db.executemany('INSERT INTO classified VALUES ('+','.join('?'*12)+')',batch);batch=[]
    db.executemany('INSERT INTO classified VALUES ('+','.join('?'*12)+')',batch)
    db.executescript('CREATE INDEX classified_thematic ON classified(thematic,no); CREATE INDEX classified_status ON classified(status,primary_class,region,year,annual); DROP TABLE IF EXISTS candidate_families; CREATE TABLE candidate_families AS SELECT DISTINCT no FROM classified WHERE thematic=1; CREATE UNIQUE INDEX cf_no ON candidate_families(no);')
    # Include related renotice family history as well, even if an earlier title differs.
    db.execute('INSERT OR IGNORE INTO candidate_families SELECT l2.no FROM links l1 JOIN candidate_families f ON f.no=l1.no JOIN links l2 ON l2.op_id=l1.op_id WHERE l1.op_id!=""')
    db.executescript('DROP TABLE IF EXISTS exported; CREATE TABLE exported(obs TEXT PRIMARY KEY,no TEXT,ord TEXT,record_kind TEXT,year TEXT,primary_class TEXT,terminal_row INTEGER,aggregate_eligible INTEGER,payload TEXT);')
    sql='''SELECT b.*,n.ord selected_ord,n.selection,n.reason,n.cancel_history,n.nrev,
 COALESCE(l.op_id,'OBS:'||b.no) op_id,COALESCE(l.terminal,b.no) terminal,
 COALESCE(l.status,'独立') link_status,COALESCE(l.reason,'동일 공고번호 내 차수만 정리; 별도 재공고 연결 관측 없음') link_reason
 FROM base b JOIN candidate_families f ON f.no=b.no JOIN nodes n ON n.no=b.no LEFT JOIN links l ON l.no=b.no ORDER BY b.no,b.ord'''
    batch=[]
    for r in db.execute(sql):
        p=row_payload(r);is_selected=r['ord']==r['selected_ord'];terminal=is_selected and r['terminal']==r['no']
        known=r['selection']=='분석용 대표 확인' and bool(r['op_id'])
        canceled='취소' in (r['kind'] or '')
        aggregate=bool(terminal and known and not canceled and p['thematic_candidate'] and p['in_scope'])
        p.update(analysis_opportunity_id=r['op_id'] if known else '',provisional_family_id='FAMILY:'+r['no'],is_family_representative=is_selected,is_opportunity_representative=bool(terminal and known),is_provisional_terminal=terminal,
                 representative_status=r['selection'],representative_reason=r['reason'],renotice_link_status='독립 관측 공고군' if r['link_status']=='独立' else r['link_status'],renotice_link_reason=r['link_reason'],
                 family_revision_count=r['nrev'],cancellation_assessment='선택 공고 취소 관측' if canceled and is_selected else '과거 취소·후속 비취소 관측' if r['cancel_history'] and is_selected else '이력/비취소 관측',
                 inclusion_status='분석용 대표 후보' if aggregate else '취소 대표 제외' if terminal and canceled else '대표/연결 미확인 검토' if terminal and not known else '선정 대표의 범위/분류 제외' if terminal else '차수/재공고 이력(중복 합산 제외)',
                 aggregate_eligible=aggregate,reference_aggregate_eligible=aggregate,confirmed_market_eligible=False,amount_kind='공고상 추정가격(원; 부가가치세·조달수수료 제외)',verified_business_amount_eligible=False,
                 amount_usability='총액성·계약액 미검증 추정가격 참고집계만 가능' if aggregate and p['presmpt_status']=='유효 양수' else '참고금액 합계 제외; 결측/이상/이력/중복/취소 상태 참조',
                 repeated_program_tag=re.sub(r'\s+','',re.sub(r'(?<!\d)(?:19|20)\d{2}\s*(?:년도|년)|\([^)]*(?:재공고|변경)[^)]*\)','',p['title'] or '')),
                 repeated_program_basis='기관코드+연도어 제거 제목 동일 문자열 탐색용; 동일 발주기회 병합에는 사용하지 않음')
        cached=db.execute('SELECT payload FROM auxiliary_cache WHERE no=? AND ord=?',(r['no'],r['ord'])).fetchone()
        if cached:p.update(json.loads(cached[0]))
        else:
            aux=enrich(c,r['no'],r['ord']);p.update(aux)
            db.execute('INSERT INTO auxiliary_cache VALUES (?,?,?)',(r['no'],r['ord'],json.dumps(aux,ensure_ascii=False)))
        batch.append((p['observation_id'],r['no'],r['ord'],p['record_kind'],r['year'],p['primary_class'],int(terminal),int(aggregate),json.dumps(p,ensure_ascii=False)))
        if len(batch)>=500:
            db.executemany('INSERT INTO exported VALUES (?,?,?,?,?,?,?,?,?)',batch);batch=[]
    db.executemany('INSERT INTO exported VALUES (?,?,?,?,?,?,?,?,?)',batch)
    # Same-key previous bodies remain separate, never counted as new opportunities.
    for r in db.execute('SELECT h.* FROM conflicts h JOIN candidate_families f ON f.no=h.no'):
        current=db.execute('SELECT payload FROM exported WHERE no=? AND ord=? AND record_kind=?',(r['no'],r['ord'],'공고 차수 현재 관측')).fetchone()
        if not current:continue
        p=json.loads(current[0]);old=json.loads(r['safe_json']);p.update(observation_id='CONFLICT:'+str(r['id']),record_kind='동일 차수 과거 본문 충돌',title=old.get('bidNtceNm'),notice_datetime=old.get('bidNtceDt'),notice_year=(old.get('bidNtceDt') or '')[:4],notice_kind=old.get('ntceKindNm'),
            dminstt_cd=old.get('dminsttCd'),dminstt_nm=old.get('dminsttNm'),ntce_instt_cd=old.get('ntceInsttCd'),ntce_instt_nm=old.get('ntceInsttNm'),site_region_original=old.get('cnstrtsiteRgnNm'),main_construction_type=old.get('mainCnsttyNm'),previous_notice_no=old.get('befBidBbancNo'),renotice_yn=old.get('reNtceYn'),notice_url=old.get('notice_url'),attachment_urls=old.get('attachment_urls'),
            presmpt_prce=money(old.get('presmptPrce'))[0],bdgt_amt=money(old.get('bdgtAmt'))[0],vat=money(old.get('VAT'))[0],govsplyAmt=money(old.get('govsplyAmt'))[0],
            presmpt_status=money(old.get('presmptPrce'))[1],budget_status=money(old.get('bdgtAmt'))[1],vat_status=money(old.get('VAT'))[1],govsplyAmt_status=money(old.get('govsplyAmt'))[1],first_response_id='',source_response_id='',first_seen_utc='',last_seen_utc='',conflict_id=r['id'],conflict_new_response_id=r['response_id'],conflict_new_item_sha256=r['new_sha'],conflict_detected_utc=r['detected'],item_sha256=r['old_sha'],is_family_representative=False,is_opportunity_representative=False,is_provisional_terminal=False,aggregate_eligible=False,inclusion_status='동일키 과거 본문(합계 제외)',amount_usability='과거 본문 금액 합계 제외',auxiliary_evidence_temporal_scope='현재보관 동일차수 부가정보; 과거본문 시점값 미확인')
        p.update(geography(old.get('cnstrtsiteRgnNm'),old.get('dminsttNm')))
        p.update(classify(p['title'] or '',old.get('mainCnsttyNm') or ''))
        p['reference_aggregate_eligible']=False
        p['repeated_program_tag']=re.sub(r'\s+','',re.sub(r'(?<!\d)(?:19|20)\d{2}\s*(?:년도|년)|\([^)]*(?:재공고|변경)[^)]*\)','',p['title'] or ''))
        if p['geo_review'] and p['classification_status']==CLEAR:p['classification_status']=REVIEW;p['classification_reason']+='; 수도권 현장 범위 확인 필요'
        db.execute('INSERT INTO exported VALUES (?,?,?,?,?,?,?,?,?)',(p['observation_id'],r['no'],r['ord'],p['record_kind'],p['notice_year'],p['primary_class'],0,0,json.dumps(p,ensure_ascii=False)))
    db.commit();return dict(funnel),dict(year_counts)

def annotate_review_priority(db):
    values=[r[0] for r in db.execute("SELECT json_extract(payload,'$.presmpt_prce') FROM exported WHERE aggregate_eligible=1 AND json_extract(payload,'$.presmpt_status')='유효 양수' ORDER BY 1")]
    cutoff=values[math.ceil(.9*len(values))-1] if values else None
    batch=[]
    for r in db.execute('SELECT obs,payload FROM exported'):
        p=json.loads(r['payload']);high=cutoff is not None and p['presmpt_status']=='유효 양수' and p['presmpt_prce']>=cutoff
        mixed=p['work_type']=='혼합' or p['primary_class'].startswith('혼합')
        p.update(high_recorded_amount_candidate=high,high_mixed_priority_review=bool(high and mixed),
                 review_amount_cutoff_krw=cutoff,review_priority_basis='분석용 대표 후보의 양수 추정가격 P90(최근접 순위), 요율/시장/과금 기준이 아님')
        batch.append((json.dumps(p,ensure_ascii=False),r['obs']))
        if len(batch)>=500:db.executemany('UPDATE exported SET payload=? WHERE obs=?',batch);batch=[]
    db.executemany('UPDATE exported SET payload=? WHERE obs=?',batch);db.commit()

def quantiles(values):
    if not values:return dict(sum='',mean='',median='',p25='',p75='',minimum='',maximum='')
    v=sorted(values)
    def at(p):
        x=(len(v)-1)*p;i=int(x);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i)
    return dict(sum=sum(v),mean=sum(v)/len(v),median=at(.5),p25=at(.25),p75=at(.75),minimum=v[0],maximum=v[-1])

def csv_value(v):
    if isinstance(v,(dict,list)):return json.dumps(v,ensure_ascii=False,separators=(',',':'))
    if v is None:return ''
    if isinstance(v,bool):return '1' if v else '0'
    if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+v
    return v

def write_csv(path, rows, fields):
    count=0
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='raise');w.writeheader()
        for r in rows:w.writerow({k:csv_value(r.get(k,'')) for k in fields});count+=1
    return {'rows':count,'bytes':path.stat().st_size}

def export_candidates(db):
    fields=[]
    for r in db.execute('SELECT payload FROM exported'):
        for k in json.loads(r[0]):
            if k not in fields:fields.append(k)
    return write_csv(OUT/'candidate_notices.csv',(json.loads(r[0]) for r in db.execute('SELECT payload FROM exported ORDER BY year,no,ord,record_kind')),fields)

def year_shards(db):
    if (OUT/'candidate_notices.csv').stat().st_size<=25*1024*1024:return {}
    with (OUT/'candidate_notices.csv').open(encoding='utf-8-sig',newline='') as f:fields=next(csv.reader(f))
    files={}
    for (year,) in db.execute('SELECT DISTINCT year FROM exported ORDER BY year'):
        name=f'candidate_notices_{year}.csv'
        files[name]=write_csv(OUT/name,(json.loads(r[0]) for r in db.execute('SELECT payload FROM exported WHERE year=? ORDER BY no,ord,record_kind',(year,))),fields)
    return files

def summaries(db):
    groups={};overall=collections.Counter();distribution=collections.defaultdict(list);org_totals=collections.Counter()
    for r in db.execute("SELECT payload FROM exported WHERE record_kind='공고 차수 현재 관측'"):
        p=json.loads(r[0])
        # Each revision belongs to exactly one institution/year/main-category/shape/status stratum.
        stratum=('취소 대표 제외' if p['is_provisional_terminal'] and '취소' in p['notice_kind'] else
                 '추정가격 참고집계(총액성·계약액 미검증)' if p['aggregate_eligible'] else
                 '선정 대표의 범위/분류 제외(합계 제외)' if p['inclusion_status']=='선정 대표의 범위/분류 제외' else
                 '중복/대표 미확인(합계 제외)' if p['is_provisional_terminal'] else '차수·재공고 이력(합계 제외)')
        key=(p['buyer_segment'],p['region'],p['geography_scope_status'],p['dminstt_cd'] or '미확인',p['dminstt_nm'],p['buyer_type'],p['parent_local_government'],p['agency_mapping_status'],p['notice_year'],p['core_or_expansion'],p['primary_class'],p['work_type'],p['amount_shape'],p['classification_status'],stratum)
        g=groups.setdefault(key,dict(revisions=0,target=0,opportunities=set(),annual=0,ordinary=0,canceled=0,history=0,unknown=0,scope_excluded=0,positive=0,missing=0,zero=0,invalid=0,values=[]))
        g['revisions']+=1;g['target']+=int(p['thematic_candidate'] and p['in_scope']);g['annual']+=int(p['annual_unit_candidate'] and p['aggregate_eligible']);g['ordinary']+=int(p['ordinary_repair_candidate'] and p['aggregate_eligible'])
        g['canceled']+=int('취소' in p['notice_kind']);g['history']+=int(not p['is_provisional_terminal']);g['unknown']+=int(p['inclusion_status']=='대표/연결 미확인 검토');g['scope_excluded']+=int(p['inclusion_status']=='선정 대표의 범위/분류 제외')
        status=p['presmpt_status'];g[{'유효 양수':'positive','NULL':'missing','0':'zero','이상값':'invalid'}[status]]+=1
        if p['aggregate_eligible']:
            g['opportunities'].add(p['analysis_opportunity_id']);overall['representative_candidates']+=1
            overall['annual_representatives']+=int(p['annual_unit_candidate']);overall[p['core_or_expansion']]+=1
            org_totals[(p['buyer_segment'],p['notice_year'],p['dminstt_cd'] or p['dminstt_nm'])]+=1
            if status=='유효 양수':
                g['values'].append(p['presmpt_prce']);overall['positive_reference_amount_rows']+=1
                if p['annual_unit_candidate']:distribution[(p['buyer_segment'],p['notice_year'],p['core_or_expansion'],p['work_type'],p['amount_shape'])].append(p['presmpt_prce'])
    rows=[]
    for key,g in sorted(groups.items(),key=lambda x:tuple(str(v) for v in x[0])):
        r=dict(zip(['buyer_segment','region','geography_scope_status','institution_code','institution_name_original','institution_type','parent_local_government','agency_mapping_status','notice_year','core_or_expansion','primary_class','work_type','amount_shape','classification_status','aggregation_stratum'],key))
        r['scope_or_classification_excluded_terminal_count']=g['scope_excluded']
        r.update(period_label='주 비교연도(달력연도)' if r['notice_year'] in ('2024','2025') else '저장 기간 부분연도',candidate_revision_rows=g['revisions'],target_candidate_revision_rows=g['target'],analysis_opportunity_count=len(g['opportunities']),annual_unit_opportunity_count=g['annual'],ordinary_repair_opportunity_count=g['ordinary'],cancellation_revision_count=g['canceled'],history_excluded_revision_count=g['history'],unresolved_terminal_count=g['unknown'],amount_positive_revision_count=g['positive'],amount_null_revision_count=g['missing'],amount_zero_revision_count=g['zero'],amount_anomalous_revision_count=g['invalid'],reference_amount_sample_n=len(g['values']),reference_amount_kind='公고 기재 추정가격 분포 — 총액성·계약액 미검증'.replace('公','공'),reference_amount_unit='KRW',verified_business_amount_sample_n=0,verified_business_amount_sum='',verified_business_amount_status='미확인; 0원 아님',price_X='미정',rule_version=RULE)
        r.update({'reference_'+k:v for k,v in quantiles(g['values']).items()});rows.append(r)
    fields=list(rows[0]) if rows else ['notice_year','primary_class','analysis_opportunity_count']
    info=write_csv(OUT/'institution_year_summary.csv',rows,fields)
    return rows,info,dict(overall),distribution,org_totals

def review_sample(db):
    # Purposeful coverage sampling: no statistical representativeness or extrapolation.
    selected={};seen_review_titles=set()
    pool=[]
    for r in db.execute('SELECT e.payload FROM exported e JOIN nodes n ON n.no=e.no AND n.ord=e.ord WHERE e.record_kind=?',('공고 차수 현재 관측',)):
        p=json.loads(r[0]);pool.append(p)
    for r in db.execute("SELECT c.payload FROM classified c JOIN nodes n ON n.no=c.no AND n.ord=c.ord WHERE c.status=? AND (c.primary_class!='대상 근거 없음' OR c.adjacent=1)",(EXCLUDE,)):
        p=json.loads(r[0]);p.setdefault('observation_id',p['bid_ntce_no']+'|'+p['bid_ntce_ord']+'|CURRENT');pool.append(p)
    pool.sort(key=lambda p:hashlib.sha256(p['observation_id'].encode()).hexdigest())
    def take(items,n,reason):
        for p in items:
            if len(selected)>=96 or n<=0:break
            review_key=(p.get('dminstt_cd') or p['dminstt_nm'],clean(p['title']))
            # Only diversify human-review slots. Never merge opportunities by title.
            if p['observation_id'] not in selected and review_key not in seen_review_titles:
                selected[p['observation_id']]=(p,reason);seen_review_titles.add(review_key);n-=1
    # Include high-value/mixed/suspected unit amount examples first, not only frequent classes.
    ranked=sorted(pool,key=lambda p:p.get('presmpt_prce') or -1,reverse=True)
    take(ranked,8,'추정가격 상위 사례')
    take((p for p in ranked if p['work_type']=='혼합' or p['primary_class'].startswith('혼합')),8,'고액·혼합 범위 확인')
    take((p for p in pool if p['amount_shape']!='미확인'),8,'단가/한도/장기계속 등 금액성 확인')
    for status in (CLEAR,REVIEW,EXCLUDE):
        for category in (*CORE,'확장후보: 도로·보도 일반보수','인접분야: 시설·하천·산림 등'):
            take((p for p in pool if p['classification_status']==status and p['primary_class']==category),2,'판정·시설분야 균형')
    for year in ('2024','2025','2023','2026'):
        for region in PROVINCES:
            for annual in (True,False):take((p for p in pool if p['notice_year']==year and p['region']==region and p['annual_unit_candidate']==annual),1,'연도·지역·연간단가 균형')
    take((p for p in pool if p['primary_class'].startswith('인접분야')),8,'핵심 키워드 탈락 인접시설: 누락 후보 확인')
    take(pool,96-len(selected),'남은 분류·지역 사례 보충')
    rows=[]
    for index,(p,reason) in enumerate(selected.values(),1):
        q=['실제 과업 범위가 해당 시설의 유지보수인가, 신설·확장 또는 다른 시설 사업인가?', '추정가격은 총액/단가/연간한도/차수액 중 무엇인가? 부가세·관급자재 기준은 무엇인가?', '수요기관의 실제 사업 담당 과와 현장 소재지가 확인되는가?']
        if p.get('annual_unit_candidate'):q.append('연간단가의 실제 발주한도와 개별 작업지시 방식은 무엇인가?')
        if p.get('work_type')=='혼합' or p['primary_class'].startswith('혼합'):q.append('유지보수 부분과 신설/포장/다른 시설 부분의 금액을 분리할 수 있는가?')
        if p['classification_status']==EXCLUDE:q.append('제외 판정을 뒤집을 유지보수 작업이 내역서에 포함되어 있는가?')
        if p.get('renotice_link_status')=='재공고 연결 미확인':q.append('이전 공고와 동일 발주기회인지, 명시적 이전번호·차수·종료 상태를 확인할 수 있는가?')
        rows.append(dict(review_id=f'R{index:03}',selection_reason=reason,review_status='미검토(첨부 열람 없음)',observation_id=p['observation_id'],bid_ntce_no=p['bid_ntce_no'],bid_ntce_ord=p['bid_ntce_ord'],title=p['title'],notice_year=p['notice_year'],title_business_years=p['title_business_years'],region=p['region'],institution=p['dminstt_nm'],institution_code=p['dminstt_cd'],buyer_type=p['buyer_type'],primary_class=p['primary_class'],work_type=p['work_type'],annual_unit_candidate=p['annual_unit_candidate'],classification_status=p['classification_status'],classification_reason=p['classification_reason'],evidence=p['classification_evidence_field'],presmpt_prce=p['presmpt_prce'],bdgt_amt=p['bdgt_amt'],vat=p['vat'],govsplyAmt=p['govsplyAmt'],amount_shape=p['amount_shape'],notice_url=p['notice_url'],attachment_urls=p['attachment_urls'],source_response_id=p['source_response_id'],review_questions=q,rule_version=RULE))
        rows[-1].update(notice_kind=p.get('notice_kind',''),analysis_opportunity_id=p.get('analysis_opportunity_id',''),
                        representative_status=p.get('representative_status','미확인(제외 사례는 합계 미사용)'),
                        renotice_link_status=p.get('renotice_link_status','미확인(제외 사례는 합계 미사용)'),
                        reference_aggregate_eligible=p.get('aggregate_eligible',False),presmpt_status=p.get('presmpt_status','미확인'))
    return rows,write_csv(OUT/'classification_review.csv',rows,list(rows[0]))

def validate(db,summary_rows,review_rows,source_sig):
    checks={};count=db.execute('SELECT COUNT(*) FROM base').fetchone()[0]
    prior=json.loads((ROOT/'.local/data_profile/aggregates.json').read_text(encoding='utf-8'))
    checks['snapshot_matches_prior_profile_rows']=count==prior['schema']['bf_notice_revision']['rows']
    checks['snapshot_notice_numbers_match_prior']=db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]==prior['queries']['notice_keys']['result'][0]['notice_numbers']
    checks['snapshot_size_mtime_unchanged']=Path(source_sig['path']).stat().st_size==source_sig['bytes'] and Path(source_sig['path']).stat().st_mtime_ns==source_sig['mtime_ns']
    checks['no_duplicate_opportunity_representatives']=db.execute("SELECT COUNT(*) FROM (SELECT json_extract(payload,'$.analysis_opportunity_id') k,COUNT(*) n FROM exported WHERE aggregate_eligible=1 GROUP BY k HAVING n>1)").fetchone()[0]==0
    source_n=db.execute("SELECT COUNT(*) FROM exported WHERE record_kind='공고 차수 현재 관측'").fetchone()[0]
    checks['institution_rows_reconcile_candidates']=sum(r['candidate_revision_rows'] for r in summary_rows)==source_n
    elig=db.execute('SELECT COUNT(*) FROM exported WHERE aggregate_eligible=1').fetchone()[0]
    checks['institutions_opportunities_reconcile']=sum(r['analysis_opportunity_count'] for r in summary_rows)==elig
    checks['history_cancel_unknown_scope_and_reference_partition']=sum(r['history_excluded_revision_count']+r['unresolved_terminal_count']+r['scope_or_classification_excluded_terminal_count']+r['analysis_opportunity_count']+(r['candidate_revision_rows'] if r['aggregation_stratum']=='취소 대표 제외' else 0) for r in summary_rows)==source_n
    checks['primary_categories_reconcile']=sum(v for _,v in db.execute('SELECT primary_class,COUNT(*) FROM exported WHERE aggregate_eligible=1 GROUP BY primary_class'))==elig
    amounts=[r[0] for r in db.execute("SELECT json_extract(payload,'$.presmpt_prce') FROM exported WHERE aggregate_eligible=1 AND json_extract(payload,'$.presmpt_status')='유효 양수'")]
    checks['reference_sums_reconcile']=sum(r['reference_sum'] for r in summary_rows if r['reference_sum']!='')==sum(amounts)
    checks['reference_amount_counts_reconcile']=sum(r['reference_amount_sample_n'] for r in summary_rows)==len(amounts)
    checks['canceled_representatives_excluded']=db.execute("SELECT COUNT(*) FROM exported WHERE aggregate_eligible=1 AND json_extract(payload,'$.notice_kind') LIKE '%취소%'").fetchone()[0]==0
    checks['uncertain_lineage_excluded']=db.execute("SELECT COUNT(*) FROM exported WHERE aggregate_eligible=1 AND (json_extract(payload,'$.analysis_opportunity_id')='' OR json_extract(payload,'$.representative_status')!='분석용 대표 확인')").fetchone()[0]==0
    checks['historical_conflicts_not_counted']=db.execute("SELECT COUNT(*) FROM exported WHERE record_kind!='공고 차수 현재 관측' AND aggregate_eligible!=0").fetchone()[0]==0
    checks['review_sample_60_to_100']=60<=len(review_rows)<=100
    checks['review_has_all_statuses']={r['classification_status'] for r in review_rows}=={CLEAR,REVIEW,EXCLUDE}
    checks['review_has_four_years']={r['notice_year'] for r in review_rows}=={'2023','2024','2025','2026'}
    checks['review_has_capital_regions']=set(PROVINCES).issubset({r['region'] for r in review_rows})
    checks['review_has_annual_and_nonannual']={r['annual_unit_candidate'] for r in review_rows}=={True,False}
    checks['review_has_keyword_rejected_adjacent']=any(r['primary_class'].startswith('인접분야') and r['classification_status']==EXCLUDE for r in review_rows)
    checks['verified_business_sums_are_unknown']=all(r['verified_business_amount_sum']=='' for r in summary_rows)
    # Validate CSV as serialized, including identifier strings and restricted field surface.
    file_rows={};unsafe=0
    for name in ('candidate_notices.csv','institution_year_summary.csv','classification_review.csv'):
        with (OUT/name).open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);headers=reader.fieldnames or []
            assert not any(re.search(r'(?i)item_json|request_url|telno|email|ceonm|bizno|servicekey',k) for k in headers)
            n=0
            for row in reader:
                n+=1
                if any(re.search(r'(?i)(?:servicekey|api[_-]?key|access[_-]?token)=',v or '') for v in row.values()):unsafe+=1
                if any((v or '').startswith(('=','+','@','\t','\r')) for v in row.values()):unsafe+=1
            file_rows[name]=n
    checks['safe_export_surface']=unsafe==0
    checks['csv_candidate_row_count']=file_rows['candidate_notices.csv']==db.execute('SELECT COUNT(*) FROM exported').fetchone()[0]
    checks['csv_institution_row_count']=file_rows['institution_year_summary.csv']==len(summary_rows)
    checks['csv_review_row_count']=file_rows['classification_review.csv']==len(review_rows)
    checks['review_diverse_institution_title_pairs']=len({(r['institution_code'] or r['institution'],clean(r['title'])) for r in review_rows})==len(review_rows)
    shard_count=0
    for path in sorted(OUT.glob('candidate_notices_[0-9][0-9][0-9][0-9].csv')):
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            for row in reader:
                assert row['notice_year']==path.stem[-4:]
                shard_count+=1
    checks['year_shards_reconcile']=shard_count in (0,file_rows['candidate_notices.csv'])
    (OUT/'validation_results.json').write_text(json.dumps({'checks':checks,'file_rows':file_rows,'source_rows':count,'aggregate_representatives':elig,'rule_version':RULE},ensure_ascii=False,indent=2),encoding='utf-8')
    return checks

def report(sig,db,funnel,years,links,summary_rows,overall,distribution,org_totals,review_rows,files,checks):
    lines=[]
    def w(s=''):lines.append(s)
    def table(headers,rows):
        w('| '+' | '.join(headers)+' |');w('| '+' | '.join('---' for _ in headers)+' |')
        for r in rows:w('| '+' | '.join(str(x).replace('|',' / ').replace('\n',' ') for x in r)+' |')
        w()
    w('# 시공노트 1차 발주시장 검토 — 저장 공사 공고 기반');w()
    w('## 완료');w()
    w('후보 전건 경량 CSV, 기관·연도·주분류 집계 CSV, 96건 목적선정 검토표, 이 설명서를 생성했다. 결과는 저장된 공사 공고의 후보 분석이다. 계약 체결액·실제 집행액·전체 공공조달 시장·확정 SaaS 시장규모가 아니다. **사업 기준금액과 X%는 미정이며 요율·할인·상한·도입률·예상매출을 계산하지 않았다.** 감리 대가 또는 법정 관리비와 동일시하지 않는다.');w()
    w('### 분석 사본과 재현');w()
    table(['항목','값'],[['사본 경로',sig['path']],['SHA-256',sig['sha256']],['기존 진단 기준 UTC',sig['profile_basis_utc']],['이번 사본 해시 확인 UTC',sig['verified_utc']],['사본 bytes',sig['bytes']],['규칙',RULE],['실행 환경',sys.version.split()[0]+' / SQLite '+sqlite3.sqlite_version]])
    w('기존 고정 사본을 읽기 전용으로 사용했다. 이 사본 이후 원본에 추가된 자료 및 새 마이그레이션·과거 4992 관련성 재판정은 포함하지 않는다. 원본 DB/JSON/수집기/예약작업/다른 프로세스는 변경·중단하지 않았다. 사본을 한 번 스트리밍 해시 검증했고, 재실행은 경로·크기·수정시각·기록된 해시 일치 시 검증표식을 재사용한다. JSON 원본 전수검사·첨부 다운로드·외부 API 호출은 이번 작업에서 0회다.');w()
    w('재실행:');w('```powershell');w('.\\.venv\\Scripts\\python.exe tools/sigongnote_market_stage1.py');w('.\\.venv\\Scripts\\python.exe tools/test_sigongnote_market_stage1.py');w('```');w()
    w('분석 캐시는 `.local/sigongnote_stage1/derived.sqlite3`, 해시 검증표식은 같은 폴더 `snapshot_verified.json`이다. 캐시는 허용 필드만 스트리밍 추출하며 item_json 전체·연락처·업체 인적정보·요청 URL·인증키를 저장하지 않는다. 원본 전체를 메모리에 올리지 않았다. 후보 키별 면허·지역·개찰은 공고번호+차수로 조회해 배열에 보존했으며 공고금액 행을 늘리는 JOIN을 하지 않았다.');w()
    w('### 기간과 건수 대조');w()
    table(['단계','공고 차수/관측 수'],list(funnel.items())+[['후보군 전체 차수 및 연결 이력',db.execute("SELECT COUNT(*) FROM exported WHERE record_kind='공고 차수 현재 관측'").fetchone()[0]],['내보낸 과거 본문 충돌행',db.execute("SELECT COUNT(*) FROM exported WHERE record_kind!='공고 차수 현재 관측'").fetchone()[0]],['분석용 대표 후보(금액 확정 아님)',overall.get('representative_candidates',0)],['양의 추정가격 참고집계 대상',overall.get('positive_reference_amount_rows',0)]])
    w('A에는 제목/지역 조건에 해당한 차수뿐 아니라 해당 공고번호 및 검증된 재공고 연결군의 다른 차수도 이력으로 포함했다. 따라서 이력 포함 출력행 수는 최초 후보 차수보다 클 수 있다. 합계에 쓰는 것은 aggregate_eligible/reference_aggregate_eligible=1인 분석용 대표뿐이며, 이 값도 확정 시장 편입 여부를 뜻하지 않는다. confirmed_market_eligible과 verified_business_amount_eligible은 모두 0이다.');w()
    table(['공고게시연도','원본 공사 차수','기간 구분'],[[y,n,'1/1~12/31 달력연도 비교(실수집 무누락 보증 아님)' if y in ('2024','2025') else '저장기간 부분연도'] for y,n in sorted(years.items())])
    w('정규화 공고의 저장 범위는 기존 사본 기준 2023-09-16~2026-10-06이며 대량 스윕은 2023-09-20부터다. 2024/2025는 각각 공고게시일 1월 1일~12월 31일 조건으로 비교한다. 2025년 초 관측 공고 시작이 1월 5일인 사실을 포함하여 달력연도 지정이 누락 없는 전수 수집을 뜻하지 않는다. 2023/2026은 별도 부분연도이며 3으로 나누거나 연환산하지 않았다. 제목상 YYYY년/년도는 별도 배열 필드이며 제목 연도로 공고를 이동하지 않는다. 제목연도 미관측 시 공고연도로 채우지 않는다.');w()
    w('### 필터·분류 규칙');w()
    w('분모는 bf_notice_revision 전 행이다. bf_notice_state.relevance를 읽거나 필터로 쓰지 않는다. 수도권 현장 명시 OR 수도권 지자체 수요기관 원문 명칭 근거를 후보 탐색 범위로 삼는다. 혼합 현장/비수도권 현장/현장 미확인은 검토 상태로 분리한다. 참가허용지역·낙찰업체 주소로 수도권을 확정하지 않는다.');w()
    w('기관 구분은 당시 공고의 수요기관 원문 명칭과 코드 기준이다. 서울특별시·인천광역시·경기도 및 뒤따르는 시/군/구 접두어를 상위 지자체 후보로 기록하고 원문 근거를 보존한다. 교육기관·공사·공단·재단·병원 등은 우선 분리한다. 코드 앞자리로 지자체를 확정하지 않는다. 공식 기관 마스터/과거 조직 이력과 대조하지 않았으므로 명칭 기반 매핑이며 모호하면 미확인이다. 수요기관 단위 집계에서 원래 기관코드·명칭을 유지해 본청/사업소를 무조건 합치지 않는다. 담당 과는 전건 미확인이다.');w()
    w('시설 태그는 차선도색·노면표시 / 교통시설 / 공원녹지이며 도로·보도는 확장후보다. 주분류는 하나만 부여하고 복수 핵심 또는 포장과 핵심의 결합은 혼합으로 별도 분리한다. 유지·보수·정비·예초·전정·교체 등과 신설·확장·조성·설치 등 작업어를 별도로 검사한다. 둘 다 있으면 혼합, 작업어가 없으면 미확인이다. 연간단가와 반복성은 별도 태그이며 연간단가 미표기를 제외 사유로 쓰지 않는다. 일반보수는 비연간단가 유지보수 후보이지 일회성 확정이 아니다.');w()
    w('이번 판정은 제목 기반 후보 규칙이다. 주공종·면허·기관 정보는 검토 근거로 함께 제공하지만 제목 판정을 공고문 확인 완료로 격상하지 않는다. 검토표의 모든 사례는 아직 미검토이며 내용/첨부문서 확정 검토를 수행한 것으로 표시하지 않았다. 반복사업군 문자열 태그는 기관코드와 함께 탐색하는 보조값이고 중복 제거키로 사용하지 않는다.');w()
    w('설치만 관측되는 비연간단가 공고는 신설·확장 제외 초안이다. 실제 노후 노면표시 재설치·신호기 교체 등이 숨어 있을 가능성이 있으므로 제외 사례도 첨부 검토 전 확정하지 않는다. 하천·사방·일반 시설물 보수 등 인접 제외 사례는 검토표에 남겼으나 이번 핵심 합계에 추가하지 않았다.');w()
    w('### 대표공고·재공고·취소');w()
    w('공고번호 내 차수 이력은 모두 유지한다. 차수가 숫자이고 게시시각이 차수 순서와 모순되지 않을 때 마지막 관측 버전을 분석용 대표로 선택한다. 이는 법적 최신 유효 상태 확정이 아니다. 날짜/차수 모순·결측·대표 동일키 충돌은 대표 선정 미확인으로 두고 합계에서 제외한다. 과거 취소 후 나중 비취소가 있으면 후속 관측 버전을 검토하며 취소 경험만으로 영구 제외하지 않는다. 선택 대표가 취소일 때만 해당 기회를 집계에서 제외한다.');w()
    w('재공고는 명시적 이전번호(또는 정확한 번호-차수), 같은 수요기관·공고게시연도, 시간순서, 제목상 사업연도 모순 없음, 단일 비순환 연결을 모두 만족할 때만 OBS:루트번호 기회로 묶는다. 공고연도 경계·연결누락·순환·분기·대표 모호함은 보류하며 관련된 양쪽 기회의 합계도 제외한다. 제목 유사도만으로 병합하지 않는다. 유찰/재입찰은 개찰 배열에 보존하고 취소/계약 체결로 바꾸지 않는다. 동일키 본문 충돌은 별도 record_kind의 경량 이력행으로 보존하며 중복 합산하지 않는다.');w()
    table(['명시적 연결 처리','연결 성분 수'],list(links.items()))
    w('위 연결 성분 수는 지역·분야 필터 전 전체 저장 공사 공고에서 계산한 이력 진단 수이며 후보 기회 수에 더하지 않는다. 수도권 현장 기타기관 그룹에는 중앙기관·공기업뿐 아니라 기관유형 미확인도 포함되며 기관 CSV의 institution_type에서 구별한다.');w()
    w('analysis_opportunity_id는 위 규칙으로 판정 가능한 관측 발주기회 ID이며 실세계 사업 전체의 고유 식별자나 계약 ID가 아니다. CSV의 is_provisional_terminal은 미확인 경우에도 검토를 위해 선택한 행이고 aggregate_eligible=1과 구별해야 한다.');w()
    w('### 금액 해석');w()
    w('presmpt_prce는 공고상 추정가격(원, 로컬 명세상 VAT·조달수수료 제외)이며 bdgt_amt·vat·govsplyAmt를 별도로 보존한다. 추정가격 결측에 다른 금액을 대입하지 않았다. NULL·0·이상값·유효 양수를 분리한다. 단가/연간단가/연간한도/총액/장기계속/연차 표기는 제목의 금액성 후보이며 해당 추정가격의 범위를 검증한 증거가 아니다. 금액 종류·총액성·중복 처리까지 확정한 사업비 집계는 미확인으로 비워 두었으며 0원으로 표시하지 않았다.');w()
    w('institution_year_summary의 reference_*는 **공고 기재 추정가격 분포 — 총액성·계약액 미검증**이다. 분석용 대표 후보의 양수 추정가격만 쓰되 기관·연도·주분류·분류판정·금액성별로 분리한다. 검토 필요/혼합/단가 의심 공고는 그 자체의 별도 행에 남긴다. 전체를 합친 금액을 연간 사업비나 요율 적용가능액으로 부르지 않는다. 이력·취소·대표/재공고 연결 미확인 그룹은 금액 합계를 비워 둔다. 사분위수는 정렬값 (n-1)p 선형보간이며 합계·분포는 실제 추정가격의 정확한 저장 정수에서 계산한다.');w()
    w('기관 CSV와 아래 연간단가 분포는 work_type도 분리해 유지보수·신설과의 혼합·작업 미확인을 함께 묻지 않았다. 연간단가 중 장기계속/차수금액 표기가 우선 감지된 사례도 금액성별 별도 행이다.');w()
    w('### 기관당 후보 수와 분야별 결과');w()
    counts=collections.Counter()
    for r in summary_rows:counts[(r['buyer_segment'],r['notice_year'],r['core_or_expansion'],r['primary_class'])]+=r['analysis_opportunity_count']
    table(['기관군','공고연도','핵심/확장','주분류','분석용 대표 후보 수'],[[*k,v] for k,v in sorted(counts.items()) if v])
    by=collections.defaultdict(list)
    for (segment,year,org),n in org_totals.items():by[(segment,year)].append(n)
    table(['기관군','공고연도','후보 관측 기관 수','기관당 후보 평균','중앙값','P25','P75'],[[*k,len(v),round(quantiles(v)['mean'],2),quantiles(v)['median'],quantiles(v)['p25'],quantiles(v)['p75']] for k,v in sorted(by.items())])
    w('기관당 통계의 분모는 후보가 관측된 수요기관 코드(코드가 없으면 원문명)이며 수도권 전체 지자체 수가 아니다. 상위 지자체와 개별 사업소를 혼동하지 않는다. 핵심·확장·혼합의 구분은 위 표와 기관 CSV에 유지된다.');w()
    parent_groups=collections.defaultdict(list)
    for year,kind,parent,n in db.execute("""SELECT year,json_extract(payload,'$.core_or_expansion'),json_extract(payload,'$.parent_local_government'),COUNT(*)
      FROM exported WHERE aggregate_eligible=1 AND json_extract(payload,'$.buyer_segment')='수도권 지자체 수요기관'
      AND json_extract(payload,'$.parent_local_government')!='' GROUP BY 1,2,3"""):
        parent_groups[(year,kind)].append(n)
    table(['공고연도','분류군','명칭 기반 상위 지자체 수','지자체당 대표후보 평균','중앙값','P25','P75'],[[*k,len(v),round(quantiles(v)['mean'],2),quantiles(v)['median'],quantiles(v)['p25'],quantiles(v)['p75']] for k,v in sorted(parent_groups.items())])
    w('이 상위 지자체 표는 같은 후보의 재표현이며 기관 CSV의 합계에 더하지 않는다. 매핑은 원문 명칭 근거이고 공식 과거 조직 마스터 미검증이다. 수도권 전체 지자체 수를 분모로 삼지 않았으며 후보 미관측 지자체를 수요 0으로 해석하지 않는다.');w()
    w('### 연간단가 후보의 공고 기재 추정가격 분포 — 총액성·계약액 미검증');w()
    table(['기관군','공고연도','분류군','작업유형','금액성 후보','양수 표본','평균 원','중앙값 원','P25 원','P75 원'],[[*k,len(v),round(quantiles(v)['mean'],2),quantiles(v)['median'],quantiles(v)['p25'],quantiles(v)['p75']] for k,v in sorted(distribution.items())])
    w('이 값은 연간 발주한도·실제 발주총액·개별 작업 단가·최종 집행액 중 무엇인지 미검증이다. 연간단가 제목만으로 연간 사업비 또는 X%의 기준금액을 정하지 않는다.');w()
    w('### 사례 검토와 추가자료 우선순위');w()
    table(['우선순위','대상 목록을 찾는 방법','필요 자료와 이유'],[
        ['1','classification_review.csv의 고액·혼합/단가/한도/장기계속 선정 사유','해당 공고의 첨부 내역서·입찰조건 일부를 먼저 확인: 금액 범위, 시설·작업별 배분, 연간한도'],
        ['2','candidate_notices의 대표 선정 미확인/재공고 연결 미확인/과거 취소·후속 비취소','변경이력·원공고·이전 공고 연결 근거: 중복/유효 상태와 금액 기준 검토'],
        ['3','핵심 분야의 aggregate_eligible=1 및 근거가 명확한 후보','계약번호·계약/변경차수·변경액/최종액·정산액과 공고 복합키 연결: 추정가격과 실제 계약/집행 구분'],
        ['4','공원녹지 후보의 수도권 수요기관 코드 목록','별도 공원녹지 용역 목록/계약: 현재 공사목록 밖 수요를 보완. 면허의 용역 행을 사업 목록으로 대체 금지'],
        ['5','agency_mapping_status 미확인/현장 범위 확인 필요 후보','당시 수요기관 조직 및 담당 과·사업소/상위 지자체 근거, 실제 현장위치']])
    w('위 자료는 확보 대상·이유만 제안하며 이번 작업에서 수집하지 않았다. CSV에 공고/필요 첨부 링크와 응답 ID를 보존했다. 링크가 없으면 미수집/미확인이며 새 URL을 추측해 만들지 않았다.');w()
    status=collections.Counter(r['classification_status'] for r in review_rows)
    table(['검토표 판정','선정 수'],list(status.items()))
    w('검토표는 고액/혼합 우선 + 판정/분야/지역/연도/연간단가를 의도적으로 분산한 96건이다. 통계적 대표 표본이 아니고 비율을 모집단으로 확대하지 않는다. 제외한 인접 분야 사례의 확인 질문도 포함했다. 검토 결과 규칙을 변경할 경우 단건 수동 덮어쓰기 대신 규칙 버전을 올리고 전체 재집계해야 한다.');w()
    w('초기 실제 제목 검토에서 도로건설 표기가 작업 미확인으로 남는 것을 확인해 규칙 1.1에 신설·확장 작업어로 추가하고 전체 모집단을 다시 분류·집계했다. 유지보수 작업어가 함께 있으면 혼합으로 남긴다. 같은 기관·같은 제목은 검토표에서 한 슬롯만 선택해 사례를 다양화했으며 후보 목록/기회/금액의 병합에는 제목을 사용하지 않았다. 첨부 과업 검토는 미수행이다.');w()
    w('후보 전건의 high_mixed_priority_review는 분석용 대표 후보의 양수 추정가격 P90(최근접 순위) 이상이면서 혼합인 사례다. 이 값은 검토 우선순위일 뿐 금액 유효성·법적 기준·과금 문턱이 아니다. maintenance_frequency는 연간단가/상시반복/일회성 명시/반복성 미확인을 구분하며, 연간단가가 아니라는 이유로 일회성으로 확정하지 않는다.');w()
    w('### 결과 파일');w()
    table(['파일','데이터 행 수','바이트'],[[k,v['rows'],v['bytes']] for k,v in files.items()])
    w('CSV는 UTF-8 BOM, 식별번호는 문자열 원문으로 기록했다. Excel의 CSV 자동 열기에서는 앞자리 0이 사라질 수 있으므로 “데이터→텍스트/CSV에서”의 공고차수·기관코드 등 ID 열을 텍스트로 가져온다. 수식 시작 문자는 단일따옴표로 무력화했다. 배열 필드는 JSON 문자열이며 복수면허/지역을 임의 단일값으로 축소하지 않는다. 경량 파일이지만 묶음 ZIP도 제공한다.');w()
    w('candidate_notices.csv가 25 MiB를 넘으면 동일 자료의 연도별 CSV를 추가 제공한다. 전체 파일과 연도별 분할본은 대체본이므로 행 수/금액을 서로 합치면 안 된다. 출력 파일 전체 행 수·정확한 바이트 수는 output_manifest.json에도 기록한다.');w()
    w('## 실패');w()
    failures=[k for k,v in checks.items() if not v]
    w('최종 집계 검사 실패: '+(', '.join(failures) if failures else '없음')+'. 원본에 쓰기/수집을 시도한 실패는 없다. 실행 도중 발생한 보완 이력은 execution_notes.md에 별도로 기록한다.');w()
    w('## 미검증');w()
    w('- 분류는 제목 기반 후보이며 실제 과업 전체, 현장 범위, 담당 과, 공식 기관 계층/법정 유형은 미검증. 기관 명칭 기반 분류 오류 가능성을 검토표로 드러냈다.\n- 모든 공고의 총액성·계약액·집행액·연간한도·최종정산은 미검증. 검증된 사업비/가격안은 산출하지 않았다.\n- 공원녹지 용역은 미수집 범위이며 수요 0이 아니다. 전체 공공조달 모집단 완전성은 미검증.\n- 기존 사본 이후 변경·취소·신규 공고는 미반영. 법적 유효 최신 공고를 보증하지 않는다.\n- 외부 API·첨부 다운로드·실연동 테스트는 사용자 지시로 SKIPPED. 전체 애플리케이션 테스트는 제품코드 변경 없음으로 SKIPPED; 이번 규칙/집계 검사만 실행.')
    w();w('## 다음 조치');w()
    w('검토표의 구체적인 질문에 따라 고액·혼합·연간단가 사례의 과업과 금액 범위를 먼저 확인한다. 확인 근거가 쌓이면 규칙을 갱신하고 같은 사본에서 재집계한다. 계약/용역 추가 확보 범위는 위 우선순위를 바탕으로 별도 결정한다. X와 기준금액 정의는 계속 미정이다.');w()
    w('## 집계 검증');w()
    table(['검사','관측'],[[k,'PASS' if v else 'FAIL'] for k,v in checks.items()])
    w('JSON 결과: validation_results.json. 원본 공고→후보 이력→대표기회→기관/주분류의 건수 및 추정가격 참고합계가 일치하는지 전수 대조했다. 합계의 산술 일치가 금액의 사업비/계약액 의미까지 검증한 것은 아니다.')
    (OUT/'analysis_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--outputs-only',action='store_true',help='같은 규칙의 완료된 파생 캐시에서 출력·집계 검사만 재생성');args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8');WORK.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    p,sig=snapshot();print('Verified existing frozen snapshot; no raw JSON rescan.',flush=True)
    c=connect(p);db=sqlite3.connect(WORK/'derived.sqlite3');db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA cache_size=-32768');db.execute('PRAGMA temp_store=MEMORY')
    if args.outputs_only:
        previous=json.loads((OUT/'output_manifest.json').read_text(encoding='utf-8'))
        if previous['rule_version']!=RULE or previous['snapshot']!=sig or not all(previous['checks'].values()):raise RuntimeError('완료된 동일 규칙/사본 검증 결과 필요')
        if db.execute("SELECT COUNT(*) FROM exported WHERE json_extract(payload,'$.rule_version')!=?",(RULE,)).fetchone()[0]:raise RuntimeError('캐시 규칙 버전 불일치')
        funnel,years,links=previous['funnel'],previous['year_counts'],previous['lineage']
    else:
        extract(c,db);build_nodes(db);links=lineage(db);print('Representative and explicit renotice chains prepared.',flush=True)
        funnel,years=analyze(db,c);annotate_review_priority(db);print('Candidate/history rows prepared.',flush=True)
    if args.outputs_only:
        names=[name for name in previous['files'] if name=='candidate_notices.csv' or re.fullmatch(r'candidate_notices_\d{4}\.csv',name)]
        for name in names:
            if (OUT/name).stat().st_size!=previous['files'][name]['bytes']:raise RuntimeError('기존 후보 출력 크기 불일치')
        files={name:previous['files'][name] for name in names}
    else:files={'candidate_notices.csv':export_candidates(db)}
    summary_rows,info,overall,distribution,org_totals=summaries(db);files['institution_year_summary.csv']=info
    if args.outputs_only:
        review_path=OUT/'classification_review.csv'
        if review_path.stat().st_size!=previous['files'][review_path.name]['bytes']:raise RuntimeError('검토표 크기 불일치')
        with review_path.open(encoding='utf-8-sig',newline='') as f:reviews=list(csv.DictReader(f))
        for row in reviews:row['annual_unit_candidate']=row['annual_unit_candidate']=='1'
        files[review_path.name]=previous['files'][review_path.name]
    else:
        reviews,info=review_sample(db);files['classification_review.csv']=info
    if not args.outputs_only:files.update(year_shards(db))
    notes=OUT/'execution_notes.md'
    if notes.exists():files[notes.name]={'rows':len(notes.read_text(encoding='utf-8').splitlines()),'bytes':notes.stat().st_size}
    checks=validate(db,summary_rows,reviews,sig)
    report(sig,db,funnel,years,links,summary_rows,overall,distribution,org_totals,reviews,files,checks)
    for _ in range(4):
        observed={'rows':len((OUT/'analysis_summary.md').read_text(encoding='utf-8').splitlines()),'bytes':(OUT/'analysis_summary.md').stat().st_size}
        if files.get('analysis_summary.md')==observed:break
        files['analysis_summary.md']=observed
        report(sig,db,funnel,years,links,summary_rows,overall,distribution,org_totals,reviews,files,checks)
    manifest={'snapshot':sig,'rule_version':RULE,'files':files,'funnel':funnel,'year_counts':years,'overall':overall,'lineage':links,'checks':checks,'external_calls':0,'source_writes':0,'finished_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    (OUT/'output_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(OUT/'sigongnote_market_stage1.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in [*files,'output_manifest.json','validation_results.json']:z.write(OUT/name,arcname=name)
    c.close();db.close();print(json.dumps({'files':files,'checks_passed':sum(checks.values()),'checks_failed':sum(not v for v in checks.values()),'representatives':overall.get('representative_candidates',0)},ensure_ascii=False),flush=True)
    if not all(checks.values()):raise RuntimeError('집계 검사 실패: validation_results.json 확인')

if __name__=='__main__':main()
