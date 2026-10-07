"""합성 공고로 검증. 과거 대화의 실제 지역 순위·숫자를 정답으로 쓰지 않는다."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from bidloc.analysis import (AnalysisRepository,Filters,business_year,first_openings,location,municipality,
                            parse_datetime,partial_year,quantiles,region_matches,shortlist,unit_category,unitprice)
from bidloc.backfill.store import BackfillStore
from bidloc.exports import safe_cell,safe_url,write_csv
from bidloc.report import build
from bidloc.repositories.db import open_database
from bidloc.timeutil import KST


@pytest.mark.parametrize('hq,region,expected',[
 ('경기도 남양주시 진접읍','경기도',True),('경기도 남양주시 진접읍','경기도 남양주시',True),
 ('경기도 남양주시','경기도 수원시',False),('경기도 용인시 수지구','경기도 용인시 처인구',False),
 ('경기도 용인시 처인구','경기도 용인시 처인구',True),('경기도 수원시','',False),('경기도X 수원시','경기도',False)])
def test_region_token_prefix(hq,region,expected):
    assert region_matches(hq,region)==expected


@pytest.mark.parametrize('title,expected',[
 ('도로 노면 재포장 단가공사',None),('노면표시 정비 단가공사','차선도색·노면표시'),
 ('노면 표시 정비','차선도색·노면표시'),('보호구역 도색 단가','차선도색·노면표시'),
 ('교통안전시설 유지보수 단가','도로·교통시설물 보수'),('상수도 시설물 정비 단가','상하수도 시설물 보수'),
 ('시설물 보수 단가','기타 시설물 보수')])
def test_unit_categories(title,expected):
    assert unit_category(title)==expected


def test_year_and_agency_grouping():
    assert business_year('합성 2027년 차선 단가','2026-12-01 09:00:00')==2027
    assert business_year('합성 차선 단가','2026-12-01 09:00:00')==2026
    assert business_year('합성',None) is None
    assert municipality('3999999','합성도 가상시 도로과')=='합성도 가상시'
    assert municipality('6999999','합성도 도로사업소')=='합성도 (광역)'
    for code in ('7999999','B999999','399','1999999'):
        assert municipality(code,'합성 기관') is None
    assert partial_year(2027,'2023-09-20','2026-10-06') is True
    assert partial_year(2024,'2023-09-20','2026-10-06') is False


def record(key='SYNTHETIC-000',**changes):
    return {'key':key,'relevance':'RELEVANT','license_codes':['4992'],'licenses':[],
            'regions':['합성도 가상시'],'region_revision_mismatch':False,'cnstrtsite_rgn_nm':'별도현장도',
            'presmpt_prce':100,'bdgt_amt':120,'year':2026,'bid_ntce_nm':'합성 차선 단가',
            'dminstt_cd':'3999999','dminstt_nm':'합성도 가상시 도로과',
            'deadline':'2026-08-01 12:00:00','openings':[],**changes}


def test_only_first_round_and_split_units_are_counted():
    d=record(openings=[{'rbid_no':'000','prtcpt_cnum':10},{'rbid_no':'000','prtcpt_cnum':None},
                       {'rbid_no':'001','prtcpt_cnum':1000},{'rbid_no':'000','prtcpt_cnum':20}])
    r=location([d])['rows'][0]
    assert r['local_competition_median']==15 and r['local_competition_n']==2
    assert r['total_notices']==1 and r['opening_link_rate']==1
    d['openings']=[{'rbid_no':'001','prtcpt_cnum':9}]
    assert first_openings(d)==[]


def test_regions_missing_fallback_and_no_national_sum():
    records=[record(regions=['합성도 가상시','합성도 다른시'])]
    result=location(records)
    assert result['national_distinct_notices']==1
    assert sum(r['total_notices'] for r in result['rows'])==2
    result=location([record(regions=[])])
    assert '참가 자격 지역이 아님' in result['basis']
    assert result['rows'][0]['local_notices'] is None
    assert result['region_unspecified']==1
    assert location([])['region_coverage'] is None
    result=location([record(regions=['합성도 가상시','합성도  가상시 '])])
    assert len(result['rows'])==1 and result['rows'][0]['total_notices']==1


def test_null_budget_does_not_become_zero_annual():
    rows=[record(),record('SYNTHETIC-B',bdgt_amt=None)]
    result=unitprice(rows,'2026-03-01','2026-09-30')
    group=result['rows'][0]
    assert group['notice_budget']['median']==120
    assert group['annual_budget']['median'] is None
    assert group['annual_incomplete_municipalities']==1 and group['missing_budget']==1
    assert result['annual'][0]['known_budget_sum']==120
    assert group['partial_year'] is True


def test_shortlist_separates_unknown_and_stale_regions():
    ds=[record(),record('SYNTHETIC-NO',regions=[]),record('SYNTHETIC-STALE',region_revision_mismatch=True),
        record('SYNTHETIC-OTHER',regions=['합성도 다른시']),record('SYNTHETIC-CLOSED',deadline='2026-06-01 00:00:00')]
    result=shortlist(ds,'합성도 가상시',now=datetime(2026,7,1,tzinfo=KST))
    assert len(result['allowed'])==len(result['unspecified'])==len(result['revision_review'])==1


@pytest.mark.parametrize('value',['=1+2','+SUM(A1)','-2','@evil',' \t=HYPERLINK("x")'])
def test_csv_injection(value):
    assert safe_cell(value).startswith("'")


def test_exports_and_urls(tmp_path):
    write_csv(tmp_path/'synthetic.csv',[{'name':'=synthetic','amount':None}])
    assert (tmp_path/'synthetic.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert safe_url('javascript:alert(1)') is None
    assert safe_url('https://g2b.go.kr.evil.test') is None
    assert safe_url('https://www.g2b.go.kr/example')


@pytest.fixture
def conn(tmp_path):
    c=open_database(tmp_path/'synthetic.sqlite3',Path(__file__).resolve().parents[2]/'migrations')
    yield c
    c.close()


def test_repository_uses_revision_key_and_removes_cancel_and_reannouncement(conn):
    store=BackfillStore(conn)
    def add(no,ord='000',**extra):
        store.upsert_notice(dict(bidNtceNo=no,bidNtceOrd=ord,bidNtceNm='합성 단가 차선',bidNtceDt='2026-01-02 09:00:00',**extra),None)
        store.set_state(no,relevance='RELEVANT',region_ord='000')
    add('SYNTHETIC-A');add('SYNTHETIC-A','001')
    add('SYNTHETIC-CANCEL',ntceKindNm='취소공고');add('SYNTHETIC-CANCEL','001')
    add('SYNTHETIC-OLD');add('SYNTHETIC-NEW',reNtceYn='Y',befBidBbancNo='SYNTHETIC-OLD-000')
    for no,ord in [('SYNTHETIC-A','000'),('SYNTHETIC-NEW','000')]:
        store.insert_license_row(dict(bidNtceNo=no,bidNtceOrd=ord,lmtGrpNo='1',lmtSno='1',lcnsLmtNm='합성/4992'),None)
        store.upsert_opening(dict(bidNtceNo=no,bidNtceOrd=ord,bidClsfcNo='001',rbidNo='000',prtcptCnum='99'),None)
    ds=AnalysisRepository(conn).records()
    assert {d['key'] for d in ds}=={'SYNTHETIC-A-001','SYNTHETIC-NEW-000'}
    current=next(d for d in ds if d['bid_ntce_no']=='SYNTHETIC-A')
    assert current['license_codes']==[] and current['openings']==[]


def test_empty_report_is_single_file_and_offline(conn,tmp_path):
    result=build(conn,SimpleNamespace(report_dir=tmp_path/'synthetic'),hq='합성도 가상시')
    html=Path(result['path']).read_text(encoding='utf-8')
    assert result['records']==0
    assert '부분 결과' in html and '참가 자격 지역이 아님' in html
    assert '<script src=' not in html and '<link ' not in html
    assert 'connect-src \'none\'' in html
    assert (tmp_path/'synthetic/location.csv').exists()
