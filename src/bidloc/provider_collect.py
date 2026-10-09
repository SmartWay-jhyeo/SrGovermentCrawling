"""Bounded provider collection into an isolated, provenance-preserving staging database.

Completeness describes only the requested API range, not nationwide procurement coverage.
No provider rows are inserted into the existing eligibility/analysis tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from bidloc.catalog import load_catalog
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import RUN_FATAL, SERVICE_FATAL
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
from bidloc.config import PROVIDER_KEY_ENV, load_settings
from bidloc.provider_probe import DETAIL_KEYS, DETAIL_OPERATIONS, OPERATIONS, query_params, settings_for_provider
from bidloc.redaction import redact
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.raw_store import ResponseRecorder
from bidloc.repositories.runs import RunRepository, new_run_id
from bidloc.timeutil import now_kst

PROVIDERS = ("kapt", "lh", "kwater", "d2b")


def dump(value):
    return redact(json.dumps(value, ensure_ascii=False, sort_keys=True))


def digest(value):
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


def init_store(conn):
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS collection_job (
        job_id TEXT PRIMARY KEY, provider TEXT NOT NULL, params_json TEXT NOT NULL,
        next_page INTEGER NOT NULL DEFAULT 1, total_count INTEGER, received INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'PENDING', reason TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS collection_page (
        job_id TEXT NOT NULL, page_no INTEGER NOT NULL, source_response_id INTEGER NOT NULL,
        fingerprint TEXT NOT NULL, item_count INTEGER NOT NULL,
        PRIMARY KEY(job_id,page_no), UNIQUE(job_id,fingerprint)
    );
    CREATE TABLE IF NOT EXISTS notice_observation (
        job_id TEXT NOT NULL, page_no INTEGER NOT NULL, row_no INTEGER NOT NULL,
        provider TEXT NOT NULL, source_response_id INTEGER NOT NULL, row_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY(job_id,page_no,row_no), UNIQUE(job_id,row_hash)
    );
    CREATE TABLE IF NOT EXISTS notice_detail (
        provider TEXT NOT NULL, detail_key TEXT NOT NULL, list_job_id TEXT NOT NULL,
        source_response_id INTEGER NOT NULL, payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
        PRIMARY KEY(provider,detail_key)
    );
    """)
    # Daily runs re-read the same request on a new day; the KST collection day is kept for review.
    if "snapshot" not in {r[1] for r in conn.execute("PRAGMA table_info(collection_job)")}:
        conn.execute("ALTER TABLE collection_job ADD COLUMN snapshot TEXT")
        conn.commit()


def collect_provider(client, budget, store, provider, begin, end, rows, contract_sha, snapshot=None):
    base = query_params(provider, begin, end)
    base["numOfRows"] = str(rows)
    # A changed parser/contract or request creates an independent snapshot. A collection day does too,
    # so a later day re-reads a range (e.g. K-water's month) instead of reusing a completed job.
    identity = {"provider": provider, "params": base, "contract": contract_sha, "version": 1}
    if snapshot:
        identity["snapshot"] = snapshot
    job_id = digest(identity)
    store.execute("INSERT OR IGNORE INTO collection_job(job_id,provider,params_json,updated_at,snapshot) VALUES(?,?,?,?,?)",
                  (job_id, provider, dump(base), now_kst().isoformat(), snapshot))
    store.commit()
    while True:
        job = dict(store.execute("SELECT * FROM collection_job WHERE job_id=?", (job_id,)).fetchone())
        if job["status"] in {"COMPLETE_RANGE", "REVIEW_REQUIRED"}:
            return job
        page = job["next_page"]
        params = {**base, "pageNo": str(page)}
        result = client.call(provider, OPERATIONS[provider], params, response_type=None)
        reason = None
        if result.outcome.value == "QUOTA_DAILY_EXCEEDED":
            budget.mark_quota_exhausted(provider, OPERATIONS[provider])
        if not result.data_ok:
            status, reason = "BLOCKED", result.outcome.value
        else:
            status = "REVIEW_REQUIRED"
            items = result.items if result.items is not None else ([] if result.total_count == 0 else None)
            if items is None or result.total_count is None:
                reason = "missing items or totalCount"
            elif job["total_count"] is not None and result.total_count != job["total_count"]:
                reason = "totalCount changed; start a new range snapshot after review"
            elif result.page_no is not None and result.page_no != page:
                reason = "returned page number mismatch"
            elif job["received"] + len(items) > result.total_count:
                reason = "received rows exceed totalCount"
            elif len(items) < rows and job["received"] + len(items) < result.total_count:
                reason = "short page before totalCount; page-size contract needs review"
            elif len(items) > rows:
                reason = "returned page exceeds requested size"
            elif not result.source_response_ids:
                reason = "missing raw provenance"
            else:
                fingerprint = digest(items)
                hashes = [digest(item) for item in items]
                if store.execute("SELECT 1 FROM collection_page WHERE job_id=? AND fingerprint=?", (job_id, fingerprint)).fetchone():
                    reason = "repeated page"
                elif len(set(hashes)) != len(hashes) or any(store.execute(
                        "SELECT 1 FROM notice_observation WHERE job_id=? AND row_hash=?", (job_id, h)).fetchone() for h in hashes):
                    reason = "duplicate payload across rows/pages; completeness uncertain"
                else:
                    received = job["received"] + len(items)
                    status = "COMPLETE_RANGE" if received == result.total_count else "RUNNING"
                    sid = result.source_response_ids[-1]
                    with store:
                        store.execute("INSERT INTO collection_page VALUES(?,?,?,?,?)", (job_id, page, sid, fingerprint, len(items)))
                        store.executemany("INSERT INTO notice_observation VALUES(?,?,?,?,?,?,?)",
                            [(job_id, page, i, provider, sid, hashes[i], dump(item)) for i, item in enumerate(items)])
                        store.execute("UPDATE collection_job SET next_page=?,total_count=?,received=?,status=?,reason=NULL,updated_at=? WHERE job_id=?",
                                      (page+1, result.total_count, received, status, now_kst().isoformat(), job_id))
                    continue
        with store:
            store.execute("UPDATE collection_job SET status=?,reason=?,updated_at=? WHERE job_id=?",
                          (status, reason, now_kst().isoformat(), job_id))
        return dict(store.execute("SELECT * FROM collection_job WHERE job_id=?", (job_id,)).fetchone())


def detail_targets(store, provider, job_id):
    """Construction rows of a list job that still need a detail lookup, keyed by every documented key."""
    keys = DETAIL_KEYS[provider]
    for (payload,) in store.execute("SELECT payload_json FROM notice_observation WHERE job_id=? ORDER BY page_no,row_no",
                                    (job_id,)):
        item = json.loads(payload)
        # Cancelled versions carry no new conditions; services and goods are outside the construction scope.
        if str(item.get("busiDivs") or "").strip() != "공사" or str(item.get("pblancSe") or "").strip() == "취소공고":
            continue
        params = {k: str(item.get(k) or "").strip() for k in keys}
        if all(params.values()):
            yield params


def collect_details(client, store, provider, job_id, limit):
    summary = {"fetched": 0, "already_stored": 0, "deferred": 0, "failed": 0, "stop_reason": None}
    for params in detail_targets(store, provider, job_id):
        detail_key = dump(params)
        if store.execute("SELECT 1 FROM notice_detail WHERE provider=? AND detail_key=?", (provider, detail_key)).fetchone():
            summary["already_stored"] += 1
            continue
        if summary["stop_reason"] or summary["fetched"] + summary["failed"] >= limit:
            summary["deferred"] += 1
            continue
        result = client.call(provider, DETAIL_OPERATIONS[provider], {"pageNo": "1", "numOfRows": "1", **params},
                             response_type=None)
        if not result.data_ok or not result.items or not result.source_response_ids:
            # A failed or empty lookup stays missing; the next run retries it.
            summary["failed"] += 1
            if result.outcome in RUN_FATAL or result.outcome in SERVICE_FATAL:
                summary["stop_reason"] = result.outcome.value
            continue
        with store:
            store.execute("INSERT INTO notice_detail VALUES(?,?,?,?,?,?)",
                          (provider, detail_key, job_id, result.source_response_ids[-1], dump(result.items[0]),
                           now_kst().isoformat()))
        summary["fetched"] += 1
    return summary


def daily_windows(today, providers, lookback_days):
    """Recent re-read windows; K-water only supports whole-month search, so month starts also re-read last month."""
    windows = []
    for p in providers:
        if p == "kwater":
            if today.day <= lookback_days:
                last_month_end = today.replace(day=1) - timedelta(days=1)
                windows.append((p, last_month_end.replace(day=1), last_month_end))
            windows.append((p, today.replace(day=1), today))
        else:
            windows.append((p, today - timedelta(days=lookback_days - 1), today))
    return windows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--providers", help="comma list from kapt,lh,kwater,d2b (default kapt,lh,kwater; all with --daily)")
    parser.add_argument("--begin", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--daily", action="store_true", help="re-read the recent window as today's snapshot")
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--rows", type=int, choices=[50, 100, 200], default=100)
    parser.add_argument("--max-calls", type=int, default=16)
    parser.add_argument("--max-details", type=int, default=10, help="d2b construction detail lookups per run")
    args = parser.parse_args(argv)
    providers = (args.providers or (",".join(PROVIDERS) if args.daily else "kapt,lh,kwater")).split(",")
    if len(set(providers)) != len(providers) or any(p not in PROVIDERS for p in providers):
        parser.error("providers must be unique " + ",".join(PROVIDERS) + " identifiers")
    if not 1 <= args.max_calls <= 40:
        parser.error("max-calls must be 1..40; existing daily limits still apply")
    if args.daily and (args.begin or args.end):
        parser.error("--daily computes its own window; do not combine it with --begin/--end")
    if not 1 <= args.lookback_days <= 7:
        parser.error("lookback-days must be 1..7")
    if not 0 <= args.max_details <= 30:
        parser.error("max-details must be 0..30")
    root = Path(__file__).resolve().parents[2]
    settings = settings_for_provider(load_settings(root), "kapt")
    settings = replace(settings, retry_max_attempts=1)
    gate = evaluate_live_gate(settings, args.live)
    if not gate.allowed:
        print(dump({"status": "BLOCKED", "http_calls": 0,
                    "reasons": [r.replace("DATA_GO_KR_SERVICE_KEY", PROVIDER_KEY_ENV) for r in gate.reasons]}))
        return 2
    today = now_kst().date()
    if args.daily:
        windows, snapshot = daily_windows(today, providers, args.lookback_days), today.isoformat()
    else:
        end = args.end or today
        begin = args.begin or max(end.replace(day=1), end-timedelta(days=7))
        windows, snapshot = [(p, begin, end) for p in providers], None
    for p, b, e in windows:
        query_params(p, b, e)
    catalog = load_catalog(root / "config/provider_api_catalog.yaml")
    conn = open_database(settings.database_path, default_migrations_dir(root))
    folder = settings.database_path.parent / "providers"
    folder.mkdir(parents=True, exist_ok=True)
    store = sqlite3.connect(folder / "notices.sqlite3")
    init_store(store)
    budget = OperationBudget(settings.database_path, default_limit=min(20, settings.live_max_calls_per_day),
                             max_per_run=min(args.max_calls, settings.live_max_calls_per_run), scope="service")
    run_id = new_run_id("provider-collect")
    runs = RunRepository(conn)
    runs.start(run_id=run_id, command="provider-collect", data_mode="real", live=True, status="RUNNING",
               max_calls_run=budget.max_per_run, catalog_sha256=catalog.sha256)
    report = {"run_id": run_id, "providers": {}, "jobs": [], "details": {}, "snapshot": snapshot,
              "staging_database": str(folder / "notices.sqlite3"),
              "scope": "API request range only; construction/site/eligibility and revision semantics need review"}
    status = "PARTIAL"
    try:
        with DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, "real"), run_id=run_id) as client:
            for p, b, e in windows:
                job = collect_provider(client, budget, store, p, b, e, args.rows, catalog.sha256, snapshot)
                report["providers"][p] = job
                report["jobs"].append(job)
                print(p, b.isoformat(), e.isoformat(), job["status"], job["received"], "/", job["total_count"], flush=True)
                if p in DETAIL_OPERATIONS and job["received"]:
                    details = collect_details(client, store, p, job["job_id"], args.max_details)
                    report["details"][p] = details
                    print(p, "details", dump(details), flush=True)
        if all(j["status"] == "COMPLETE_RANGE" for j in report["jobs"]):
            status = "COMPLETED"
    except Exception as exc:
        report["error"] = redact(f"{type(exc).__name__}: {exc}")
        status = "FAILED"
    finally:
        report.update(status=status, http_calls=budget.run_used)
        path = folder / f"{run_id}.json"
        path.write_text(dump(report), encoding="utf-8")
        runs.finish(run_id, status=status, calls_attempted=budget.run_used,
                    stop_reason="API range pages saved; see staging collection status", notes={"report": str(path)})
        store.close()
        conn.close()
    print(dump({"report": str(path), "status": status, "http_calls": budget.run_used}))
    return 0 if status == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
