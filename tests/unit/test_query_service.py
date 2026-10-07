from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import socket

import pytest
from streamlit.testing.v1 import AppTest

from bidloc.query_service import SearchFilters, csv_bytes, detail, load_snapshot, search
from bidloc.timeutil import KST
from tests.unit.test_analysis import record

_SOCKET_CONNECT = socket.socket.connect


def item(key='SYNTHETIC-A', **changes):
    return record(key, bid_ntce_no=key, bid_ntce_ord='000', bid_ntce_dt='2026-07-01 10:00:00',
                  ntce_instt_nm='합성기관', cntrct_cncls_mthd_nm='제한경쟁', **changes)


def test_search_missing_is_not_zero_and_region_is_not_site():
    ds=[item(),item('SYNTHETIC-NULL',presmpt_prce=None,regions=[]),
        item('SYNTHETIC-ZERO',presmpt_prce=0,regions=['합성도']),
        item('SYNTHETIC-OTHER',regions=['합성도 가상시X'])]
    assert len(search(ds,SearchFilters(hq='합성도 가상시')))==2
    assert [x['key'] for x in search(ds,SearchFilters(max_amount=0))]==['SYNTHETIC-ZERO']
    assert len(search(ds,SearchFilters(query='합성 차선',begin='2026-07-01',end='2026-07-01')))==4
    assert search(ds,SearchFilters(query="' OR 1=1 --"))==[]


def test_search_deadline_scope_and_composite_key():
    ds=[item(),item('SYNTHETIC-MISSING',deadline=None),item('SYNTHETIC-OTHER',license_codes=['49920'])]
    result=search(ds,SearchFilters(status='마감 전'),now=datetime(2026,7,2,tzinfo=KST))
    assert [r['key'] for r in result]==['SYNTHETIC-A']
    assert detail(ds,'SYNTHETIC-A','001') is None
    assert detail(ds,'SYNTHETIC-A','000')==ds[0]
    with pytest.raises(ValueError):
        search(ds,SearchFilters(begin='2026-08-01',end='2026-01-01'))


def test_missing_database_does_not_create_or_synthesize(tmp_path):
    p=tmp_path/'synthetic.sqlite3'
    rows,meta=load_snapshot(str(p),'synthetic')
    assert not p.exists() and rows==[] and meta['collection_status']=='NOT_COLLECTED'


def test_csv_metadata_formula_guard_and_missing():
    result=csv_bytes([{'name':'=evil','amount':None},{'name':'safe','amount':0}],
                     {'data_mode':'synthetic','snapshot_id':'test','applied_filters':{'q':'x'}}).decode('utf-8-sig')
    assert "'=evil" in result and 'synthetic' in result and '조회조건' in result
    assert 'safe,0' in result


def test_disk_cache_invalidates_on_wal_change_and_preserves_snapshot_time(monkeypatch,tmp_path):
    import bidloc.ui.cache as cache
    db=tmp_path/'synthetic.sqlite3'
    db.write_bytes(b'synthetic test stand-in')
    calls=[]
    def load(*args):
        calls.append(args)
        return [item()],{'data_mode':'synthetic','generated_at_kst':str(len(calls))}
    monkeypatch.setattr(cache,'load_snapshot',load)
    first=cache.read_snapshot(str(db),'synthetic')
    assert cache.read_snapshot(str(db),'synthetic')==first and len(calls)==1
    Path(str(db)+'-wal').write_bytes(b'synthetic new revision')
    second=cache.read_snapshot(str(db),'synthetic')
    assert len(calls)==2 and second[1]['generated_at_kst']=='2'


def test_native_app_search_detail_api_and_rerun_without_collection(monkeypatch,tmp_path):
    # Windows asyncio implements its internal wakeup socketpair over loopback.
    # Keep DNS and every external connection blocked by the unit-test fixture.
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1','::1'):
            raise RuntimeError('외부 네트워크는 테스트에서 금지됩니다.')
        return _SOCKET_CONNECT(sock,address)
    monkeypatch.setattr(socket.socket,'connect',loopback_only)
    import bidloc.ui.app as ui
    rows=[item(),item('SYNTHETIC-B',bid_ntce_nm='합성 방수 공사')]
    meta={'data_mode':'synthetic','filters':{'begin':'2026-01-01','end':'2026-10-07'},
          'collection_status':'PARTIAL','snapshot_id':'test','generated_at_kst':'2026-10-08',
          'partitions':[],'quality':{},'notes':[]}
    monkeypatch.setattr(ui,'load_settings',lambda:SimpleNamespace(database_path=tmp_path/'synthetic.sqlite3',data_mode='synthetic'))
    monkeypatch.setattr(ui,'cached_snapshot',lambda *args:(rows,meta))
    at=AppTest.from_file(str(Path(__file__).resolve().parents[2]/'app.py'),default_timeout=30).run()
    assert not at.exception
    assert '2건' in at.subheader[0].value
    at.text_input(key='q').set_value('방수')
    next(b for b in at.button if b.label=='검색').click().run()
    assert not at.exception and '1건' in at.subheader[0].value
    at.button(key='open_SYNTHETIC-B').click().run()
    assert not at.exception and at.title[0].value=='합성 방수 공사'
    next(b for b in at.button if b.label=='← 검색 결과로 돌아가기').click().run()
    assert not at.exception and '1건' in at.subheader[0].value
    at.button(key='nav_API 연동').click().run()
    assert not at.exception and at.title[0].value=='API 연동'
    assert next(b for b in at.button if b.label=='연결 테스트').disabled
    for page in ('지역 비교','단가계약 분석','검토 대기','수집·품질'):
        at.button(key='nav_'+page).click().run()
        assert not at.exception, page
        assert at.title[0].value==page
