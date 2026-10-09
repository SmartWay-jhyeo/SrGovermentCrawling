"""Synthetic provider pages only; no network or real notices."""
from datetime import date
from types import SimpleNamespace
import sqlite3

from bidloc.clients.envelope import parse_body
from bidloc.clients.errors import Outcome, classify_response
from bidloc.provider_collect import collect_provider, init_store

DAY = date(2026, 10, 1)


def test_lh_declared_euckr_direct_items_and_empty_response():
    xml = '<?xml version="1.0" encoding="EUC-KR"?><response><header><resultCode>00</resultCode></header><body><item><bidNum>SYNTHETIC</bidNum><bidnmKor>합성 방수</bidnmKor></item><totalCount>1</totalCount><pageNo>1</pageNo><numOfRows>5</numOfRows></body></response>'
    parsed = parse_body(xml.encode("euc-kr"))
    assert parsed.items[0]["bidnmKor"] == "합성 방수"
    assert classify_response(200, parsed).outcome == Outcome.SUCCESS
    empty = parse_body(b'<response><header><resultCode>00</resultCode></header><body><totalCount>0</totalCount></body></response>')
    assert classify_response(200, empty).outcome == Outcome.SUCCESS_EMPTY
    malicious = parse_body(b'<!DOCTYPE response [<!ENTITY x "payload">]><response><body>&x;</body></response>')
    assert classify_response(200, malicious).outcome == Outcome.MALFORMED_RESPONSE


def response(page, total=3, items=None, error=None):
    return SimpleNamespace(items=items, total_count=total, page_no=page, source_response_ids=[page],
        data_ok=error is None, outcome=error or Outcome.SUCCESS)


def run(store, responses):
    calls = []
    def call(*a, **kw):
        calls.append(a[2]["pageNo"])
        return responses.pop(0)
    job = collect_provider(SimpleNamespace(call=call), SimpleNamespace(mark_quota_exhausted=lambda *a: None),
                           store, "lh", DAY, DAY, 2, "synthetic-contract")
    return job, calls


def test_resume_after_budget_stop_no_duplicates_and_complete_rerun_no_calls():
    store = sqlite3.connect(":memory:"); init_store(store)
    job, calls = run(store, [response(1, items=[{"id": "a"}, {"id": "b"}]),
                             response(2, error=Outcome.BUDGET_EXHAUSTED_RUN)])
    assert job["status"] == "BLOCKED" and job["next_page"] == 2 and job["received"] == 2
    job, calls = run(store, [response(2, items=[{"id": "c"}])])
    assert calls == ["2"] and job["status"] == "COMPLETE_RANGE"
    assert store.execute("SELECT count(*) FROM notice_observation").fetchone()[0] == 3
    job, calls = run(store, [])
    assert calls == [] and job["received"] == 3


def test_repeated_page_and_changed_count_never_mark_complete():
    for second, reason in [(response(2, total=4, items=[{"id": "a"}, {"id": "b"}]), "repeated page"),
                           (response(2, total=5, items=[{"id": "c"}, {"id": "d"}]), "totalCount changed")]:
        store = sqlite3.connect(":memory:"); init_store(store)
        job, calls = run(store, [response(1, total=4, items=[{"id": "a"}, {"id": "b"}]), second])
        assert job["status"] == "REVIEW_REQUIRED" and reason in job["reason"]
        assert job["received"] == 2


def test_failed_first_page_is_unknown_not_zero_total():
    store = sqlite3.connect(":memory:"); init_store(store)
    job, calls = run(store, [response(1, total=None, error=Outcome.AUTH_KEY_INVALID)])
    assert calls == ["1"] and job["total_count"] is None and job["status"] == "BLOCKED"


def test_short_page_and_overlap_cannot_hide_missing_data():
    for responses in [[response(1, total=3, items=[{"id": "a"}])],
                      [response(1, total=3, items=[{"id": "a"}, {"id": "b"}]),
                       response(2, total=3, items=[{"id": "b"}])]]:
        store = sqlite3.connect(":memory:"); init_store(store)
        job, calls = run(store, responses)
        assert job["status"] == "REVIEW_REQUIRED"


def test_daily_windows_reread_recent_days_and_last_month_for_month_only_api():
    from bidloc.provider_collect import daily_windows
    early = daily_windows(date(2026, 10, 2), ["kapt", "kwater", "d2b"], 3)
    assert early == [("kapt", date(2026, 9, 30), date(2026, 10, 2)),
                     ("kwater", date(2026, 9, 1), date(2026, 9, 30)), ("kwater", date(2026, 10, 1), date(2026, 10, 2)),
                     ("d2b", date(2026, 9, 30), date(2026, 10, 2))]
    assert daily_windows(date(2026, 10, 15), ["kwater"], 3) == [("kwater", date(2026, 10, 1), date(2026, 10, 15))]


def test_new_collection_day_rereads_a_completed_request():
    store = sqlite3.connect(":memory:"); init_store(store)
    calls = []
    def call(*a, **kw):
        calls.append(a[2]["pageNo"])
        return response(1, total=1, items=[{"id": f"row-{len(calls)}"}])
    client, budget = SimpleNamespace(call=call), SimpleNamespace(mark_quota_exhausted=lambda *a: None)
    first = collect_provider(client, budget, store, "kwater", DAY, DAY, 2, "synthetic-contract", "2026-10-01")
    again = collect_provider(client, budget, store, "kwater", DAY, DAY, 2, "synthetic-contract", "2026-10-01")
    later = collect_provider(client, budget, store, "kwater", DAY, DAY, 2, "synthetic-contract", "2026-10-02")
    assert first["job_id"] == again["job_id"] != later["job_id"]
    assert calls == ["1", "1"] and later["snapshot"] == "2026-10-02"


D2B_ROW = {"pblancYear": "2026", "pblancSeCode": "B", "pblancNo": "SYN0001", "pblancOdr": "1",
           "cntrwkNo": "2026-00001", "orntCode": "SYN", "busiDivs": "공사", "pblancSe": "긴급공고"}


def test_d2b_details_only_for_open_construction_rows_once_and_stop_on_budget():
    from bidloc.provider_collect import collect_details
    store = sqlite3.connect(":memory:"); init_store(store)
    rows = [D2B_ROW, dict(D2B_ROW, pblancNo="SYN0002", pblancOdr="2", pblancSe="취소공고"),
            dict(D2B_ROW, pblancNo="SYN0003", busiDivs="용역"), dict(D2B_ROW, pblancNo="SYN0004", cntrwkNo=""),
            dict(D2B_ROW, pblancNo="SYN0005"), dict(D2B_ROW, pblancNo="SYN0006")]
    job = collect_provider(SimpleNamespace(call=lambda *a, **k: response(1, total=6, items=rows)),
                           SimpleNamespace(mark_quota_exhausted=lambda *a: None), store, "d2b", DAY, DAY, 50, "synthetic")
    outcomes = [Outcome.SUCCESS, Outcome.BUDGET_EXHAUSTED_DAY]
    calls = []
    def call(service, operation, params, **kw):
        calls.append(params["pblancNo"])
        outcome = outcomes.pop(0)
        ok = outcome == Outcome.SUCCESS
        return SimpleNamespace(data_ok=ok, outcome=outcome, items=[{"lc": "합성"}] if ok else None,
                               source_response_ids=[90] if ok else [])
    summary = collect_details(SimpleNamespace(call=call), store, "d2b", job["job_id"], 10)
    # Cancelled, service and incomplete-key rows are never requested; a budget stop defers the rest.
    assert calls == ["SYN0001", "SYN0005"]
    assert summary == {"fetched": 1, "already_stored": 0, "deferred": 1, "failed": 1,
                       "stop_reason": "BUDGET_EXHAUSTED_DAY"}
    outcomes[:] = [Outcome.SUCCESS, Outcome.SUCCESS]
    calls.clear()
    summary = collect_details(SimpleNamespace(call=call), store, "d2b", job["job_id"], 10)
    assert calls == ["SYN0005", "SYN0006"] and summary["already_stored"] == 1
    assert store.execute("SELECT count(*) FROM notice_detail").fetchone()[0] == 3


def test_daily_cli_runs_every_provider_and_d2b_details(project, monkeypatch):
    import json
    from datetime import datetime
    import bidloc.provider_collect as module
    from bidloc.config import load_settings
    from bidloc.timeutil import KST
    settings = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-PROVIDER",
                                              "ALLOW_LIVE_API": "true"})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    monkeypatch.setattr(module, "now_kst", lambda: datetime(2026, 10, 15, 0, 12, tzinfo=KST))
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, service, operation, params, **kwargs):
            calls.append((service, operation))
            if operation == "getFcltyCmpetBidPblancDetail":
                return SimpleNamespace(data_ok=True, outcome=Outcome.SUCCESS, items=[{"lc": "합성"}], source_response_ids=[7])
            return response(1, total=1, items=[dict(D2B_ROW)] if service == "d2b" else [{"id": service}])
    monkeypatch.setattr(module, "DataGoKrClient", Client)
    assert module.main(["--live", "--daily", "--max-calls", "20"]) == 0
    assert [s for s, _ in calls] == ["kapt", "lh", "kwater", "d2b", "d2b"]
    report = json.loads(next((settings.database_path.parent / "providers").glob("provider-collect-*.json")).read_text(encoding="utf-8"))
    assert report["snapshot"] == "2026-10-15" and report["details"]["d2b"]["fetched"] == 1
    import pytest
    with pytest.raises(SystemExit):
        module.main(["--live", "--daily", "--begin", "2026-10-01"])


def test_cli_finishes_run_and_report_for_empty_success(project, monkeypatch):
    import json
    import bidloc.provider_collect as module
    from bidloc.config import load_settings
    settings = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-PROVIDER",
                                              "ALLOW_LIVE_API": "true"})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, *args, **kwargs): return response(1, total=0, items=[])
    monkeypatch.setattr(module, "DataGoKrClient", Client)
    assert module.main(["--live", "--providers", "lh", "--begin", DAY.isoformat(), "--end", DAY.isoformat()]) == 0
    report = json.loads(next((settings.database_path.parent / "providers").glob("provider-collect-*.json")).read_text(encoding="utf-8"))
    conn = sqlite3.connect(settings.database_path)
    assert conn.execute("SELECT status FROM api_run WHERE run_id=?", (report["run_id"],)).fetchone()[0] == "COMPLETED"
