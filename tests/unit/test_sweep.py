"""합성 응답만 사용한다. 네트워크 차단 하에서 재개/오판 방지 검증."""
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bidloc.backfill.sweep import SweepConfig, SweepStore, SweepRunner, day_windows, extend_job, finalize_relevance
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import Outcome
from bidloc.clients.http import ApiResult
from bidloc.collector_lock import collector_lock
from bidloc.config import ConfigError
from bidloc.repositories.db import open_database


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'synthetic.sqlite3'
    conn = open_database(path, Path(__file__).resolve().parents[2] / 'migrations')
    cfg = SweepConfig('synthetic',date(2026,1,1),date(2026,1,1),2,1,3,3,('LIST',),('4992',),{})
    store = SweepStore(conn)
    store.ensure_job(cfg)
    yield conn, path, cfg, store
    conn.close()


def result(items, total, **kwargs):
    return ApiResult('bid_notice','op',Outcome.SUCCESS,'synthetic',items=items,total_count=total,**kwargs)


def notice(no='SYNTHETIC-A', ord='000', **extra):
    return dict(bidNtceNo=no,bidNtceOrd=ord,**extra)


def runner(setup, responses, max_calls=20):
    conn,path,cfg,store = setup
    budget=OperationBudget(path,max_per_run=max_calls,today=lambda:date(2026,1,1))
    class Client:
        def call(self,svc,op,params):
            budget.reserve(svc,op)
            return responses.pop(0)
    return SweepRunner(client=Client(),budget=budget,store=store,cfg=cfg,job_id=cfg.job_id)


def test_resume_does_not_repeat_committed_page(setup):
    first=runner(setup,[result([notice(),notice('SYNTHETIC-B')],3)],max_calls=1)
    report=first.run()
    assert 'BUDGET_EXHAUSTED_RUN' in str(report.closed)
    assert setup[0].execute('SELECT next_page FROM sw_partition').fetchone()[0]==2
    second=runner(setup,[result([notice('SYNTHETIC-C')],3,page_no=2)])
    assert second.run().job_status=='COMPLETED'
    assert setup[0].execute('SELECT COUNT(*) FROM bf_notice_revision').fetchone()[0]==3


@pytest.mark.parametrize('bad',[result([notice('SYNTHETIC-C')],4),result([],3),
    result([notice(),notice('SYNTHETIC-B')],4),result([notice('SYNTHETIC-C')],3,page_no=1),
    result([{'bidNtceNo':'SYNTHETIC-C'}],3)])
def test_inconsistent_pages_restart_then_fail(setup,bad):
    responses=[]
    for _ in range(4):
        responses.extend([result([notice(),notice('SYNTHETIC-B')],4 if bad.total_count==4 else 3),bad])
    # totalCount 변화 사례는 별도로 아래에서 확인한다.
    r=runner(setup,responses)
    r.run()
    part=setup[0].execute('SELECT * FROM sw_partition').fetchone()
    assert part['status']=='FAILED'
    assert part['restarts']==4


def test_total_count_change_restarts(setup):
    r=runner(setup,[result([notice(),notice('SYNTHETIC-B')],3),result([notice('SYNTHETIC-C')],4)],max_calls=2)
    r.run()
    part=setup[0].execute('SELECT * FROM sw_partition').fetchone()
    assert part['next_page']==1 and part['rows_received']==0 and part['restarts']==1
    assert setup[0].execute('SELECT COUNT(*) FROM bf_notice_revision').fetchone()[0]==2


def test_quota_is_persisted_but_other_service_can_run(setup):
    r=runner(setup,[ApiResult('bid_notice','op',Outcome.QUOTA_DAILY_EXCEEDED,'synthetic',result_code='22')])
    report=r.run()
    assert 'QUOTA_DAILY_EXCEEDED' in str(report.closed)
    assert r.budget.remaining('bid_notice','other')==0
    assert r.budget.remaining('bid_award','other')>0
    assert setup[0].execute('SELECT last_outcome FROM sw_partition').fetchone()[0]=='QUOTA_DAILY_EXCEEDED'


def test_daily_limit_separate_from_run_limit(setup):
    r=runner(setup,[])
    r.budget.default_limit=0
    assert 'BUDGET_EXHAUSTED_DAY' in str(r.run().closed)


def test_windows_and_recollect_same_end(setup):
    conn,path,cfg,store=setup
    windows=day_windows(replace(cfg,end=date(2026,1,2)))
    assert windows[0][1]==windows[1][0]=='202601020000'
    conn.execute("UPDATE sw_partition SET status='DONE',attempts=2,restarts=2")
    conn.execute("UPDATE sw_job SET status='COMPLETED'")
    assert extend_job(conn,cfg,cfg.job_id,cfg.end)['recollect_partitions']==1
    assert tuple(conn.execute('SELECT status,attempts,restarts FROM sw_partition').fetchone())==('PENDING',0,0)
    assert conn.execute('SELECT status FROM sw_job').fetchone()[0]=='ACTIVE'
    with pytest.raises(ValueError):
        extend_job(conn,cfg,cfg.job_id,date(2025,12,31))


def test_finalize_version_quality_and_partial_guard(setup):
    conn,path,cfg,store=setup
    for no in ('SYNTHETIC-HIT','SYNTHETIC-NON','SYNTHETIC-SHIFT','SYNTHETIC-OLD','SYNTHETIC-NONE','SYNTHETIC-CANCEL'):
        store.rows.upsert_notice(notice(no,ntceKindNm='취소공고' if no.endswith('CANCEL') else '등록공고'),None)
    def lic(no,code,**extra):
        store.rows.insert_license_row(notice(no,lmtGrpNo='1',lmtSno='1',lcnsLmtNm='합성/'+code,**extra),None)
    lic('SYNTHETIC-HIT','4992')
    lic('SYNTHETIC-NON','49920',permsnIndstrytyList='[49920]')
    lic('SYNTHETIC-SHIFT','1111',rgstDt='shift')
    lic('SYNTHETIC-OLD','4992')
    store.rows.upsert_notice(notice('SYNTHETIC-OLD','001'),None)
    lic('SYNTHETIC-CANCEL','4992')
    # 용역/물품 면허만 있는 번호는 공사 상태로 만들지 않는다.
    lic('SYNTHETIC-NONCONSTRUCTION','4992')
    counts=finalize_relevance(conn,cfg,license_complete=False)
    assert counts=={'CANCELLED':1,'PENDING':4,'RELEVANT':1}
    counts=finalize_relevance(conn,cfg)
    assert counts=={'CANCELLED':1,'NOT_RELEVANT':1,'RELEVANT':1,'UNKNOWN':3}


def test_amount_overflow_stored_as_missing_with_quality(setup):
    conn,path,cfg,store=setup
    store.rows.upsert_notice(notice(bdgtAmt='12240000012240000011'),None)
    row=conn.execute('SELECT bdgt_amt,quality_flag FROM bf_notice_revision').fetchone()
    assert row[0] is None and 'AMOUNT_OUT_OF_RANGE' in row[1]


def test_same_db_collector_lock_released_after_exception(tmp_path):
    path=tmp_path/'synthetic.sqlite3'
    with pytest.raises(RuntimeError):
        with collector_lock(path):
            with pytest.raises(ConfigError):
                with collector_lock(path):
                    pass
            raise RuntimeError('synthetic interruption')
    with collector_lock(path):
        pass


def test_cli_reuses_saved_job_settings(project, monkeypatch, capsys):
    from bidloc.cli import main
    monkeypatch.setenv('ALLOW_LIVE_API','false')
    monkeypatch.setenv('DATA_GO_KR_SERVICE_KEY','')
    common=['--project-root',str(project),'sweep']
    assert main(common+['init','--begin','2026-01-01','--end','2026-01-02'])==0
    assert main(common+['status'])==0
    assert '2026-01-01~2026-01-02' in capsys.readouterr().out
