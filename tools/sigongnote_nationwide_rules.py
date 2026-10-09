"""Offline national title/agency rules. Name inference is never an agency registry."""
import re
from sigongnote_market_stage1 import classify, title_years
from sigongnote_stage2_rules import commercial

RULE = 'sigongnote-nationwide-4.1'
OBSERVED_COMBINED_NAME='전남광주통합특별시'
OBSERVED_COMBINED_REGION='전남·광주 통합명칭(과거 귀속 미확인)'
PROVINCES = {
 '서울': ('서울특별시','서울'), '부산': ('부산광역시','부산'),
 '대구': ('대구광역시','대구'), '인천': ('인천광역시','인천'),
 '광주': ('광주광역시',), '대전': ('대전광역시','대전'),
 '울산': ('울산광역시','울산'), '세종': ('세종특별자치시','세종'),
 '경기': ('경기도','경기'), '강원': ('강원특별자치도','강원도','강원'),
 '충북': ('충청북도','충북'), '충남': ('충청남도','충남'),
 '전북': ('전북특별자치도','전라북도','전북'), '전남': ('전라남도','전남'),
 '경북': ('경상북도','경북'), '경남': ('경상남도','경남'),
 '제주': ('제주특별자치도','제주도','제주')}
METRO = {'서울','부산','대구','인천','광주','대전','울산'}
CORE = {'차선도색·노면표시','교통시설','공원녹지','복수 핵심시설 통합'}

def province(text, prefix=False):
    found=[]
    boundary=r'(?=\s|$|[,/;·]|교육청)' if prefix else r'(?=\s|$|[,/;·])'
    if re.search((r'^' if prefix else r'(?<![가-힣])')+OBSERVED_COMBINED_NAME+boundary,text or ''):
        found.append((OBSERVED_COMBINED_REGION,OBSERVED_COMBINED_NAME))
    for display, aliases in PROVINCES.items():
        for alias in aliases:
            pattern = (r'^' if prefix else r'(?<![가-힣])')+re.escape(alias)+boundary
            if re.search(pattern,text or ''):
                found.append((display,alias));break
    return found

def organization(name):
    s=re.sub(r'\s+',' ',name or '').strip(); ps=province(s,True)
    p,original=ps[0] if len(ps)==1 else ('미확인','')
    typ,parent,level,confidence='기관유형 미확인','','미확인','미확인'
    if '한국도로공사' in s: typ='한국도로공사(별도사업모델)'
    elif re.search(r'교육청|교육지원청|학교|대학교|교육연구|교육원',s):typ='교육기관 후보'
    elif re.search(r'한국\S*(?:공사|공단)|공사(?:\s|$)|공단|도시공사|시설관리공단|교통공사',s):typ='공기업·공단 후보'
    elif re.search(r'재단|병원|의료원|협회|법인|주식회사|농협|아파트|공동주택|관리단',s):typ='기타기관 후보'
    elif re.search(r'^(?:국토교통부|환경부|국방부|해양수산부|문화체육관광부|법무부|농림|조달청|경찰청|산림청|육군|해군|공군|국가|대통령|고용노동부|행정안전부)|경찰서|지방국토관리청|국토관리사무소|지방우정청',s):typ='중앙기관 후보'
    elif ps:
        rest=s[len(original):].strip();parts=rest.split();first=parts[0] if parts else ''
        if p==OBSERVED_COMBINED_REGION and re.fullmatch(r'[가-힣]+(?:시|군|구)',first):
            typ='지자체 수요기관';parent=original+' '+first;level='기초 '+first[-1]
        elif p==OBSERVED_COMBINED_REGION and first in ('무안청사','광주청사'):
            typ='지자체 수요기관';parent=original;level='광역'
        elif p in METRO and re.fullmatch(r'[가-힣]+(?:구|군)',first):
            typ='지자체 수요기관';parent=original+' '+first;level='기초 '+first[-1]
        elif p not in METRO|{'세종','제주'} and re.fullmatch(r'[가-힣]+(?:시|군)',first):
            typ='지자체 수요기관';parent=original+' '+first;level='기초 '+first[-1]
        elif p=='제주' and first in ('제주시','서귀포시'):
            typ='지자체 수요기관';parent=original;level='행정시(기초지자체로 세지 않음)'
        elif not rest or re.match(r'본청|의회|건설|도로|도시|공원|녹지|산림|환경|상수도|하수도|수도|물|한강|푸른|북부|남부|동부|서부|중부|경제자유|소방|안전|교통|종합|사업|농업|인재|자치|보건|미래|체육|문화|해양|수산|팔당|광교|농수산|농촌|농림|동물|축산|산업|농업기술|산림환경|세계유산',rest):
            typ='지자체 수요기관';parent=original;level='광역'
        if parent:
            confidence='중간(원문명칭 명시; 공식 계층 미대조)'
            suffix=s[len(parent):].strip()
            if suffix:
                level+=' 일반구·소속기관' if re.match(r'[가-힣]+구(?:\s|$)',suffix) else ' 소속기관'
            else:level+=' 본청'
    return dict(buyer_region=p,buyer_region_original=original,buyer_type=typ,
      parent_local_government=parent,agency_level=level,mapping_confidence=confidence,
      mapping_basis='수요기관 원문 명칭: '+s+'; 원문 코드 유지, 공식 조직마스터/주소 미대조',
      buyer_region_basis='저장 응답의 통합 명칭; 공고 당시 조직/전남·광주 귀속 미확인, 소급배분 안 함' if p==OBSERVED_COMBINED_REGION else '수요기관 명칭상 관할 지역 후보; 실제 소재지 주소 미검증' if ps else '소재지 미확인')

def site_region(text):
    ps=province(text);regions=sorted({p for p,_ in ps})
    if '전국' in (text or ''):return '전국/배분 미확인'
    return regions[0] if len(regions)==1 else '복수지역/배분 미확인' if regions else '미확인'

def classify_national(title,main=''):
    old=classify(title,main)
    s=commercial(dict(title=title,**old))
    t=re.sub(r'\s+','',title or '')
    changes=[]; work=old['work_type']
    # Explicit management terms absent from the original maintenance pattern.
    added_green=bool(re.search(r'잔디관리|조경관리|병해충방제|수목방제|수목생육|지피식물관리',t))
    if added_green and not re.search(r'신축|건립|신설|조성|확장|개설',t):
        work='유지보수';changes.append('잔디관리·조경관리·병해충 방제 등 관리 표현 보완(제목 기반)')
        tags=s['s2_facility_query_tags']
        if '공원녹지 유지보수' not in tags:tags.append('공원녹지 유지보수')
        s['s2_commercial_scope']='핵심';s['s2_commercial_candidate']=True
        s['s2_exclusive_group']='통합 핵심: 복수 핵심시설' if len(tags)>1 else '공원녹지 유지보수'
        s['s2_commercial_tier']='우선검토' if re.search(r'연간|연중|상시|관내|전역|관리',t) else '개별검토'
    group=s['s2_exclusive_group']
    group={'교통시설 유지보수':'교통시설','공원녹지 유지보수':'공원녹지','통합 핵심: 복수 핵심시설':'복수 핵심시설 통합'}.get(group,group)
    # A public enterprise model remains distinct even when its road tasks match.
    annual=old['annual_unit_candidate']; unit='단가' in t
    freq='연간단가' if annual else '단가계약 후보' if unit else '단기 집중정비 후보' if re.search(r'집중|일제정비|대규모|재해복구|수해복구',t) else '비연간 유지관리 후보' if work=='유지보수' else '반복·기간 미확인'
    return dict(category=group,commercial_tier=s['s2_commercial_tier'],
      candidate=int(s['s2_commercial_candidate']),facility_tags=s['s2_facility_query_tags'],
      work_type=work,legacy_work_type=old['work_type'],legacy_category=old['primary_class'],
      frequency=freq,annual_unit=int(annual),amount_shape=old['amount_shape'],
      title_business_years=title_years(title or ''),classification_reason='; '.join([s['s2_commercial_reason'],*changes]),
      rule_change='; '.join(changes+s['s2_context_flags']),
      evidence_level='제목 기반 후보; 실제 과업 미검증',
      relevant=int(bool(s['s2_facility_query_tags']) or group.startswith(('확장후보','핵심·','별도사업모델')) or old['primary_class'] not in ('대상 근거 없음','인접분야: 시설·하천·산림 등')),
      legacy_thematic=int(old['thematic_candidate']))
