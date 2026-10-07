"""네트워크 없는 분석. 버전·분할·재입찰 키를 유지하고 관측치만 출력한다."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import re
from statistics import mean, median

from bidloc.timeutil import KST, now_kst


NOTES = [
    '지역·면허 기준의 후보 분석이다. 적격심사·실적·시평액·본점 이전 기준일·복수면허 AND/OR는 UNKNOWN이며 공고문 확인이 필요하다.',
    '기존 법인 면허 추가 / 신규 법인 / 본점 이전을 구분해 선택하되, 프로필별 자격 판정은 미검증이다.',
    '참가업체수는 최초 개찰단위의 명부 전체 수다. 하한선 미달·취소신청을 포함하며 유효 경쟁자 수가 아니다.',
    '배정예산·추정가격은 공고상 금액이다. 실제 집행액·계약액·낙찰금액 또는 기대매출이 아니다. 단가 투찰금액을 연간 예산에 더하지 않는다.',
    '복수 허용지역 공고는 각 지역에 포함된다. 지역 행을 합산해 전국 합계를 만들지 않는다.',
    '현재 주소·면허로 과거 참가 가능 여부를 확정하지 않는다. 당시 자격과 증빙은 별도 확인한다.',
    '최신 공고 차수와 정확히 연결한 면허·개찰을 사용한다. 지역은 해당 차수 이하 최신 지역 차수를 표시한다. 차수가 다르면 확인 필요다.',
    '기간 조회의 기준일이 API마다 다르므로 경계 밖에 등록된 면허·지역·개찰은 연결되지 않을 수 있다.',
    'UNKNOWN·미수집·오류·미개찰을 0으로 바꾸지 않는다. PENDING은 아직 판정 전이며 해당 없음이 아니다.',
]


@dataclass(frozen=True)
class Filters:
    begin: str | None = None
    end: str | None = None
    license: str = '4992'
    contract: str | None = None
    min_amount: int | None = None
    max_amount: int | None = None
    profile: str = '기존 법인 면허 추가'


def tokens(value):
    return tuple(str(value or '').split())


def region_matches(hq, region):
    h, r = tokens(hq), tokens(region)
    return bool(r and len(h) >= len(r) and h[:len(r)] == r)


def parse_datetime(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=KST) if dt.tzinfo is None else dt.astimezone(KST)
    except (TypeError, ValueError):
        return None


def business_year(title, published):
    hit = re.search(r'(?<!\d)((?:19|20)\d{2})\s*년', title or '')
    if hit:
        return int(hit[1])
    dt = parse_datetime(published)
    return dt.year if dt else None


def unit_category(title):
    title = re.sub(r'\s+', '', title or '')
    if any(x in title for x in ('차선', '노면표시', '노면도색')) or (
            '도색' in title and any(x in title for x in ('주차', '주정차', '과속방지턱', '구획선', '보호구역', '횡단보도'))):
        return '차선도색·노면표시'
    if any(x in title for x in ('보수', '유지', '정비')):
        if any(x in title for x in ('도로시설물', '교통시설', '교통안전시설', '대중교통시설')):
            return '도로·교통시설물 보수'
        if '시설물' in title:
            return '상하수도 시설물 보수' if any(x in title for x in ('상수도','하수','상하수도','정수','배수')) else '기타 시설물 보수'
    return None


def municipality(code, name):
    if not re.fullmatch(r'[3456]\d{6}', code or '') or not tokens(name):
        return None
    return tokens(name)[0] + ' (광역)' if code[0] == '6' else ' '.join(tokens(name)[:2])


def quantiles(values):
    values = sorted(v for v in values if v is not None)
    def percentile(p):
        pos = (len(values)-1)*p
        i = int(pos)
        return values[i] + (values[min(i+1,len(values)-1)]-values[i])*(pos-i)
    return {'n':len(values),'median':median(values) if values else None,
            'p25':percentile(.25) if values else None,'p75':percentile(.75) if values else None,
            'mean':mean(values) if values else None}


def partial_year(year, begin=None, end=None):
    return year is None or bool((begin and begin>f'{year:04d}-01-01') or (end and end<f'{year:04d}-12-31'))


class AnalysisRepository:
    def __init__(self, conn, filters=None, job_name='sweep-3y'):
        self.conn, self.filters = conn, filters or Filters()
        job = conn.execute('SELECT * FROM sw_job WHERE job_name=? ORDER BY created_at_utc DESC LIMIT 1',(job_name,)).fetchone()
        self.job = dict(job) if job else None
        self.begin = self.filters.begin or (job['range_begin_kst'] if job else None)
        self.end = self.filters.end or (job['range_end_kst'] if job else None)

    def metadata(self, *, include_relevance=True):
        c = self.conn
        parts = [dict(r) for r in c.execute('''SELECT stage,status,COUNT(*) AS partitions,SUM(rows_received) AS rows_received
            FROM sw_partition WHERE job_id=? GROUP BY stage,status''',(self.job['job_id'] if self.job else '',))]
        complete = bool(parts) and all(r['status']=='DONE' for r in parts) and {r['stage'] for r in parts}=={'LIST','LICENSE','REGION','OPENING'}
        return {'generated_at_kst':now_kst().isoformat(),'data_mode':'real','job':self.job,'partitions':parts,
                'collection_status':'DONE' if complete else 'PARTIAL / 부분 결과',
                'filters':{**asdict(self.filters),'begin':self.begin,'end':self.end},'notes':NOTES,
                'relevance':dict(c.execute('''SELECT s.relevance,COUNT(*) FROM bf_notice_state s NOT INDEXED
                    WHERE EXISTS(SELECT 1 FROM bf_notice_revision n WHERE n.bid_ntce_no=s.bid_ntce_no) GROUP BY s.relevance''')) if include_relevance else None,
                'relevance_status':'COMPUTED' if include_relevance else 'NOT_REQUESTED',
                'quality':{
                    'license_field_shift':c.execute("SELECT COUNT(*) FROM bf_license_limit WHERE quality_flag LIKE 'FIELD_SHIFT_SUSPECTED%'").fetchone()[0],
                    'amount_out_of_range':c.execute("SELECT COUNT(*) FROM bf_notice_revision WHERE quality_flag LIKE 'AMOUNT_OUT_OF_RANGE%'").fetchone()[0],
                    'conflicts':c.execute('SELECT COUNT(*) FROM bf_record_conflict').fetchone()[0]},
                'last_response_at_utc':c.execute('SELECT MAX(requested_at_utc) FROM source_response').fetchone()[0]}

    def records(self):
        c, f = self.conn, self.filters
        c.execute('DROP TABLE IF EXISTS temp._analysis_latest')
        c.execute('''CREATE TEMP TABLE _analysis_latest AS SELECT p.*,n.bid_ntce_nm AS title FROM
            (SELECT bid_ntce_no,MAX(bid_ntce_ord) AS ord,
            MAX(CASE WHEN ntce_kind_nm LIKE '%취소%' THEN 1 ELSE 0 END) AS cancelled
            FROM bf_notice_revision GROUP BY bid_ntce_no) p
            JOIN bf_notice_revision n INDEXED BY ix_bf_notice_analysis
            ON n.bid_ntce_no=p.bid_ntce_no AND n.bid_ntce_ord=p.ord''')
        c.execute('CREATE UNIQUE INDEX temp.idx_analysis_latest ON _analysis_latest(bid_ntce_no,ord)')
        known = {r[0] for r in c.execute('SELECT bid_ntce_no FROM _analysis_latest')}
        excluded = set()
        for (previous,) in c.execute("SELECT DISTINCT bef_bid_ntce_no FROM bf_notice_revision WHERE re_ntce_yn='Y' AND bef_bid_ntce_no IS NOT NULL"):
            previous = previous.strip()
            if previous in known:
                excluded.add(previous)
            else:
                base = re.sub(r'-\d{2,3}$','',previous)
                if base in known:
                    excluded.add(base)
        c.execute("""DELETE FROM _analysis_latest WHERE cancelled=1 OR
            (COALESCE(title,'') NOT LIKE '%단가%' AND bid_ntce_no NOT IN
                (SELECT bid_ntce_no FROM bf_notice_state WHERE relevance='RELEVANT'))""")
        conditions = ['l.cancelled=0']
        params = []
        for cond, value in [('n.bid_ntce_dt>=?',self.begin),('n.bid_ntce_dt<?',self.end+' 24:00:00' if self.end else None),
                            ('n.cntrct_cncls_mthd_nm=?',f.contract),('n.presmpt_prce>=?',f.min_amount),('n.presmpt_prce<=?',f.max_amount)]:
            if value is not None:
                conditions.append(cond); params.append(value)
        # item_json를 전체 출력하지 않는다. 분석에 필요한 공고 필드만 추출한다.
        # CROSS JOIN keeps the small candidate set outermost. Otherwise SQLite
        # chooses the date index and reads every large raw notice in the period.
        rows = c.execute('''SELECT n.bid_ntce_no,n.bid_ntce_ord,n.bid_ntce_nm,n.bid_ntce_dt,n.openg_dt,
            n.cntrct_cncls_mthd_nm,n.ntce_instt_nm,n.dminstt_cd,n.dminstt_nm,n.cnstrtsite_rgn_nm,
            n.bdgt_amt,n.presmpt_prce,n.quality_flag,n.last_response_id,n.last_seen_utc,
            json_extract(n.item_json,'$.bidClseDt') AS deadline,
            json_extract(n.item_json,'$.bidNtceDtlUrl') AS url,
            s.relevance,s.license_ord,s.region_ord
            FROM _analysis_latest l CROSS JOIN bf_notice_revision n
            LEFT JOIN bf_notice_state s ON s.bid_ntce_no=n.bid_ntce_no
            WHERE n.bid_ntce_no=l.bid_ntce_no AND n.bid_ntce_ord=l.ord AND '''+' AND '.join(conditions),params)
        records = {}
        for r in rows:
            d = dict(r)
            if d['bid_ntce_no'] in excluded:
                continue
            d.update(key=d['bid_ntce_no']+'-'+d['bid_ntce_ord'],year=business_year(d['bid_ntce_nm'],d['bid_ntce_dt']),
                     licenses=[],regions=[],openings=[],license_codes=[],region_sources=[])
            records[(d['bid_ntce_no'],d['bid_ntce_ord'])] = d
        c.execute('DROP TABLE IF EXISTS temp._analysis_keys')
        c.execute('CREATE TEMP TABLE _analysis_keys(no TEXT,ord TEXT,region_ord TEXT,PRIMARY KEY(no,ord))')
        c.executemany('INSERT INTO _analysis_keys VALUES (?,?,?)',((no,ord_,d['region_ord']) for (no,ord_),d in records.items()))
        for row in c.execute('''SELECT l.bid_ntce_no,l.bid_ntce_ord,l.lmt_grp_no,l.lmt_sno,l.lcns_lmt_nm,
            l.license_code,l.permsn_indstryty_list,l.indstryty_mfrc_fld_list,l.quality_flag,l.response_id
            FROM _analysis_keys k CROSS JOIN bf_license_limit l
            WHERE l.bid_ntce_no=k.no AND l.bid_ntce_ord=k.ord'''):
            d = records[(row['bid_ntce_no'],row['bid_ntce_ord'])]
            d['licenses'].append({k:row[k] for k in ('lmt_grp_no','lmt_sno','lcns_lmt_nm','license_code','permsn_indstryty_list','indstryty_mfrc_fld_list','quality_flag','response_id')})
            d['license_codes'].extend([row['license_code']] if row['license_code'] else [])
            d['license_codes'].extend(re.findall(r'\d+',row['permsn_indstryty_list'] or ''))
        for row in c.execute('''SELECT k.no,k.ord,r.prtcpt_psbl_rgn_nm,r.response_id FROM _analysis_keys k
            CROSS JOIN bf_allowed_region r WHERE r.bid_ntce_no=k.no AND r.bid_ntce_ord=k.region_ord'''):
            d = records[(row['no'],row['ord'])]
            name = ' '.join(tokens(row['prtcpt_psbl_rgn_nm']))
            if name:
                d['regions'].append(name)
                d['region_sources'].append(row['response_id'])
        for row in c.execute('''SELECT o.bid_ntce_no,o.bid_ntce_ord,o.bid_clsfc_no,o.rbid_no,o.prtcpt_cnum,o.openg_dt,o.progrs_div_cd_nm,o.response_id
            FROM _analysis_keys k CROSS JOIN bf_opening_unit o
            WHERE o.bid_ntce_no=k.no AND o.bid_ntce_ord=k.ord'''):
            records[(row['bid_ntce_no'],row['bid_ntce_ord'])]['openings'].append(dict(row))
        for d in records.values():
            d['regions'] = sorted(set(d['regions']))
            d['license_codes'] = sorted(set(d['license_codes']))
            d['region_revision_mismatch'] = bool(d['region_ord'] and d['region_ord'] != d['bid_ntce_ord'])
        return list(records.values())


def relevant_records(records, license='4992'):
    return [d for d in records if d['relevance']=='RELEVANT' and license in d['license_codes']]


def first_openings(d):
    return [o for o in d['openings'] if re.fullmatch(r'0+',o['rbid_no'] or '')]


def location(records, license='4992', hq=None):
    data = relevant_records(records,license)
    coverage = sum(bool(d['regions']) for d in data)/len(data) if data else None
    basis = '참가가능지역' if hq or (coverage is not None and coverage>=.8) else '공사현장지역 (참가 자격 지역이 아님)'
    cities = sorted({' '.join(tokens(r)) for d in data for r in d['regions'] if len(tokens(r))>=2})
    if hq:
        cities = [hq]
    rows = []
    if basis.startswith('공사현장') and not hq:
        buckets = defaultdict(list)
        for d in data:
            buckets[d['cnstrtsite_rgn_nm'] or '현장지역 미수집'].append(d)
        for city, group in sorted(buckets.items()):
            rows.append(_location_row(city,[],[],group,basis))
    else:
        for city in cities:
            local, province = [], []
            for d in data:
                matches = [r for r in d['regions'] if region_matches(city,r)]
                if any(len(tokens(r))>=2 for r in matches):
                    local.append(d)
                elif matches:
                    province.append(d)
            rows.append(_location_row(city,local,province,local+province,'참가가능지역'))
    return {'basis':basis,'region_coverage':coverage,'national_distinct_notices':len(data),
            'region_unspecified':sum(not d['regions'] for d in data),'rows':rows}


def _location_row(city,local,province,group,basis):
    def counts(items):
        return [o['prtcpt_cnum'] for d in items for o in first_openings(d) if o['prtcpt_cnum'] is not None]
    local_c, province_c = counts(local),counts(province)
    opened = sum(bool(first_openings(d)) for d in group)
    return {'region':city,'basis':basis,'local_notices':len(local) if basis=='참가가능지역' else None,
            'province_notices':len(province) if basis=='참가가능지역' else None,'total_notices':len(group),
            'local_competition_median':median(local_c) if local_c else None,'local_competition_n':len(local_c),
            'province_competition_median':median(province_c) if province_c else None,'province_competition_n':len(province_c),
            'estimated_price_median':quantiles(d['presmpt_prce'] for d in group)['median'],
            'opening_linked_notices':opened,'opening_link_rate':opened/len(group) if group else None,
            'years':dict(sorted(Counter(str(d['year']) for d in group).items())),
            'notice_keys':[d['key'] for d in group]}


def unitprice(records, begin=None, end=None):
    groups = defaultdict(list)
    details = []
    agency_samples = {}
    for d in records:
        if '단가' not in (d['bid_ntce_nm'] or ''):
            continue
        muni, category = municipality(d['dminstt_cd'],d['dminstt_nm']),unit_category(d['bid_ntce_nm'])
        if not muni or not category:
            continue
        row = {**d,'municipality':muni,'category':category}
        groups[(category,d['year'])].append(row)
        details.append(row)
        agency_samples.setdefault(d['dminstt_cd'],{'code':d['dminstt_cd'],'name':d['dminstt_nm'],'group':muni,'notice_key':d['key']})
    rows = []
    annual = []
    for (cat,year),items in sorted(groups.items(),key=lambda kv:(kv[0][0],str(kv[0][1]))):
        by_muni=defaultdict(list)
        for d in items:
            by_muni[d['municipality']].append(d)
        annual_values=[]
        for muni,ds in sorted(by_muni.items()):
            known=[d['bdgt_amt'] for d in ds if d['bdgt_amt'] is not None]
            total=sum(known) if known else None
            # 결측이 있는 지자체는 완전한 연간 합계의 통계에서 제외한다.
            if len(known)==len(ds):
                annual_values.append(total)
            annual.append({'category':cat,'year':year,'municipality':muni,'known_budget_sum':total,
                           'notices':len(ds),'missing_budget':len(ds)-len(known),'notice_keys':[d['key'] for d in ds]})
        required=Counter(code for d in items for code in d['license_codes'])
        rows.append({'category':cat,'year':year,'partial_year':partial_year(year,begin,end),
                     'notices':len(items),'municipalities':len(by_muni),'notice_budget':quantiles(d['bdgt_amt'] for d in items),
                     'annual_budget':quantiles(annual_values),'annual_incomplete_municipalities':len(by_muni)-len(annual_values),
                     'estimated_price':quantiles(d['presmpt_prce'] for d in items),'missing_budget':sum(d['bdgt_amt'] is None for d in items),
                     'missing_estimated_price':sum(d['presmpt_prce'] is None for d in items),
                     'license_4992_ratio':required['4992']/len(items),'missing_license':sum(not d['licenses'] for d in items),
                     'license_distribution':dict(required),'notice_keys':[d['key'] for d in items]})
    return {'rows':rows,'annual':annual,'details':details,'agency_samples':list(agency_samples.values()),
            'notes':['지자체 코드는 관측 규칙이며 모든 기관 유형을 보증하지 않는다. 기관명 표본과 원공고를 함께 확인한다.',
                     '평균은 대형 발주에 민감하므로 중앙값을 대표값으로 제시한다. 연간 합계는 해당 연도 1건 이상 발주한 기관만 센다.',
                     '지자체가 권역·회차별로 나눠 발주하므로 공고 1건과 연간 합계는 다르다. 예산 결측이 있는 기관은 완전한 연간 합계 통계에서 제외한다.',
                     '허용업종과 요구 면허가 함께 관측된 코드 분포다. 복수면허/허용업종으로 비율 합이 100%를 넘을 수 있으며 AND/OR 판정이 아니다.']}


def shortlist(records, hq, license='4992', now=None):
    now=now or now_kst()
    allowed, unspecified, review=[],[],[]
    for d in relevant_records(records,license):
        deadline=parse_datetime(d['deadline'])
        if deadline is None or deadline<=now:
            continue
        if not d['regions']:
            unspecified.append(d)
        elif any(region_matches(hq,r) for r in d['regions']):
            (review if d['region_revision_mismatch'] or any(l['quality_flag'] for l in d['licenses']) else allowed).append(d)
    for rows in (allowed,unspecified,review):
        rows.sort(key=lambda d:(parse_datetime(d['deadline']),d['key']))
    return {'hq':hq,'as_of_kst':now.isoformat(),'allowed':allowed,'unspecified':unspecified,'revision_review':review,
            'eligibility':'지역·면허 기준 후보 / 전체 참가자격 UNKNOWN'}
