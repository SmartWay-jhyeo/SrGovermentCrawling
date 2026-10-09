"""Commercial review proposals, independent of preserved stage-one classifications."""
import json
import re

RULE = 'sigongnote-stage2-2.0'
LANE, TRAFFIC, GREEN = '차선도색·노면표시', '교통시설 유지보수', '공원녹지 유지보수'


def truth(value):
    return value is True or str(value) == '1'


def array(value):
    if isinstance(value, list): return value
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError): return []


def integer(value):
    if value is None or str(value).strip() == '': return None
    try: return int(value)
    except (ValueError, TypeError): return None


def commercial(row):
    """Never rewrites work_type, facility_tags, monetary fields or notice years."""
    title = re.sub(r'\s+', ' ', row.get('title') or '').strip()
    masked, place_count = re.subn(r'[가-힣A-Za-z0-9]*공원역', '[역명]', title)
    t = re.sub(r'\s+', '', masked)
    lane = bool(re.search(r'차선(?:재)?도색|차선도장|노면표[시지]|차로도색|노면색깔유도선|차선.*(?:제거|보수|재도색)', t))
    traffic = bool(re.search(r'교통안전시설|교통시설|교통신호|신호등|신호기|교통표지|도로표지|안전표지|도로반사경|시선유도|방호울타리|중앙분리대|가드레일|보행자안전|횡단보도조명|무단횡단방지', t))
    if '표지판' in t and re.search(r'도로|교통|보행|횡단', t): traffic = True
    green = bool(re.search(r'공원|녹지|가로수|가로화단|수목|조경|예초|전정|풀베기|제초|잔디|도시숲|산책로|수형조절', t))
    vegetation_only = bool('중앙분리대' in t and green and re.search(r'수목|가로화단|전정|예초|조형수', t)
                           and not re.search(r'교통안전시설|신호|방호울타리|차선|표지|중앙분리대(?:교체|보수|설치)', t))
    if vegetation_only: traffic = False
    tags = [name for name, found in ((LANE, lane), (TRAFFIC, traffic), (GREEN, green)) if found]
    road = bool(re.search(r'도로|보도|차도|포장|인도정비|아스콘|덧씌우기|보행환경|자전거도로|노면', t))
    explicit_paving = bool(re.search(r'재포장|덧씌우기|아스콘.*(?:보수|포장)|포장(?:보수|정비|공사)', t))
    building = bool(re.search(r'신축|건립|재건축|개축', t) or ('복합화' in t and re.search(r'건축|센터|청사', t)))
    development = bool(re.search(r'도로건설|도시개발|택지개발|산업단지조성|도로확장|도로개설|일반화도로개량', t))
    explicit_new = bool(re.search(r'신설|신규|조성|확장|개설|확충|증축', t))
    legacy_work = row.get('work_type', '미확인')
    new_only = explicit_new and legacy_work not in ('유지보수', '혼합')
    install = '설치' in t
    paint = bool(tags and re.search(r'도색|도장', t))
    annual = truth(row.get('annual_unit_candidate'))
    distributed = bool(re.search(r'상시|연중|연간|관내|전역|분산|긴급보수|유지관리', t))
    intensive = bool(re.search(r'전면|대규모|집중개보수|재해복구|수해복구|재난복구|리모델링', t))
    reasons, flags = [], []
    if place_count: flags.append('공원역 지명 마스킹'); reasons.append('공원역은 역명 근거로 별도 처리; 실제 공원 작업어만 재탐색')
    if vegetation_only: flags.append('중앙분리대 수목 맥락'); reasons.append('중앙분리대는 수목관리 위치 후보이며 교통시설 작업 범위는 미확인')
    if '재난대비' in t: flags.append('재난대비 표현'); reasons.append('재난대비만으로 유지보수/재해복구 작업유형을 확정하지 않음')
    if paint: flags.append('도색/도장 맥락'); reasons.append('도색 작업어를 상업 검토 근거로 사용; 기존 작업유형은 보존')
    if install: flags.append('설치 맥락'); reasons.append('설치가 신설인지 기존시설 재표시/교체인지 과업 확인 필요')
    if building or development or (new_only and (tags or road)):
        tier, scope, group = '별도사업모델', '별도사업모델', '별도사업모델: 건축·개발·신설확장'
        reasons.append('건립/신축/개발/명시적 신설확장 맥락; 기존시설 유지관리 대상 합계에서 분리')
        if green and building: flags.append('건축사업 부속 조경 가능성')
    elif not tags and not road:
        tier, scope, group = '범위 밖/근거 부족', '범위 밖', '범위 밖/핵심시설 근거 부족'
        reasons.append('지명 맥락 제거 후 핵심/도로 시설 근거 없음; 미검출은 수요 없음의 증거가 아님')
    else:
        if tags and explicit_paving:
            scope, group = '핵심·인접 복합', '핵심·도로포장 복합 검토'
        elif len(tags) > 1:
            scope, group = '핵심', '통합 핵심: 복수 핵심시설'
            reasons.append('복수 핵심시설을 단일 대표공고·단일 금액으로 집계; 시설 태그 합산 금지')
        elif tags: scope, group = '핵심', tags[0]
        else: scope, group = '확장후보', '확장후보: 도로·보도 일반보수'
        supports_maintenance = legacy_work == '유지보수' or (paint and not install)
        if tags and (annual or distributed) and supports_maintenance and legacy_work != '혼합' and not intensive and not explicit_paving and not install:
            tier = '우선검토'
            reasons.append('연간/상시/분산 표현과 대상시설 유지·도색 작업어; 실제 작업지시 흐름은 미검증')
        else:
            tier = '개별검토'
            reasons.append('설치·혼합·집중개보수 또는 반복/관리 흐름 불명; 실제 업무흐름 개별 확인')
        if truth(row.get('geo_review')) and tier == '우선검토':
            tier = '개별검토'; reasons.append('수도권 현장 범위 확인 필요')
    years = array(row.get('title_business_years'))
    year_bucket = str(years[0]) if len(years) == 1 else '미기재' if not years else '복수연도 미확인'
    split_terms = re.findall(r'\d+\s*(?:차(?:분|년도)?|공구|구역)|상반기|하반기|반기|[A-Z]구역|북부권|남부권|동부권|서부권', title)
    price, budget, vat = (integer(row.get(k)) for k in ('presmpt_prce','bdgt_amt','vat'))
    residual = budget-price-vat if None not in (price,budget,vat) else ''
    return dict(s2_rule_version=RULE, s2_commercial_tier=tier, s2_commercial_scope=scope,
                s2_exclusive_group=group, s2_facility_query_tags=tags,
                s2_commercial_candidate=tier in ('우선검토','개별검토'),
                s2_commercial_reason='; '.join(reasons), s2_context_flags=flags,
                s2_evidence_level='제목·공고 메타데이터 기반 제안; 실제 과업 미검증',
                s2_title_year_candidate_bucket=year_bucket, s2_year_candidate_basis='제목상 사업연도 후보; 실제 사업/집행연도 아님',
                s2_term_or_zone_markers=split_terms, s2_verified_business_start='', s2_verified_business_end='',
                s2_verified_management_start='', s2_verified_management_end='', s2_verified_business_year='',
                s2_verified_management_year='', s2_service_subscription_count='',
                s2_period_status='미확인; 차수/공구/반기를 1년 이용권으로 계산하지 않음',
                s2_budget_price_vat_residual=residual,
                s2_residual_status='단순 필드 차액 참고; 관급/기간/수량의 원인 미확인' if residual!='' else '금액 필드 결측으로 비교 불가',
                s2_small_annual_amount_review=bool(annual and price is not None and 0<price<1000000),
                s2_small_amount_rule='연간단가 제목·양수 추정가격 100만원 미만 검토표시; 단위 판정/보정/삭제 기준 아님',
                s2_amount_verification_status='미검증', s2_billing_basis_candidate_amount='',
                s2_billing_basis_definition='', s2_price_X='미정', s2_document_review_status='미검토')
