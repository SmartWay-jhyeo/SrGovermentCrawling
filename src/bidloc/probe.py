"""P1: 네 오퍼레이션의 하루 구간/페이지 크기 확인. 원문 값은 보고서에 복사하지 않는다."""

from dataclasses import replace
from datetime import datetime, timedelta
import json

from bidloc.backfill.sweep import STAGES
from bidloc.catalog import default_catalog_path, load_catalog
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import SERVICE_FATAL, Outcome
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
from bidloc.config import ConfigError, load_settings
from bidloc.logging_setup import configure_logging
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.raw_store import ResponseRecorder
from bidloc.repositories.runs import RunRepository, new_run_id
from bidloc.timeutil import now_kst

KEY_FIELDS = {
    "LIST": ("bidNtceNo", "bidNtceOrd"),
    "LICENSE": ("bidNtceNo", "bidNtceOrd", "lmtGrpNo", "lmtSno"),
    "REGION": ("bidNtceNo", "bidNtceOrd", "lmtSno"),
    "OPENING": ("bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"),
}


def probe_requests(day: str, page_sizes: str):
    try:
        parsed = datetime.strptime(day, "%Y%m%d")
        if parsed.strftime("%Y%m%d") != day:
            raise ValueError
        sizes = tuple(int(x) for x in page_sizes.split(","))
        if not sizes or len(set(sizes)) != len(sizes) or any(x < 1 or x > 999 for x in sizes):
            raise ValueError
    except ValueError:
        raise ConfigError("probe: day는 YYYYMMDD, page-sizes는 중복 없는 1~999 정수 목록이어야 한다") from None
    for stage, service, operation, extra, _ in STAGES:
        for size in sizes:
            yield stage, service, operation, size, {
                **extra, "inqryBgnDt": day + "0000", "inqryEndDt": day + "2359",
                "pageNo": "1", "numOfRows": str(size),
            }


def inspect_result(stage, size, result):
    items = result.items or []
    keys = KEY_FIELDS[stage]
    missing_rows = sum(any(item.get(key) in (None, "") for key in keys) for item in items)
    return {
        "stage": stage, "requested_rows": size, "outcome": result.outcome.value,
        "result_code": result.result_code, "http_status": result.http_status,
        "total_count": result.total_count, "returned_rows": len(items),
        "num_of_rows_echo": result.num_of_rows, "envelope_shape": result.envelope_shape,
        "response_format": result.response_format, "attempts": result.attempts,
        "fields_observed": sorted({key for item in items for key in item}),
        "key_fields": keys, "missing_key_rows": missing_rows,
        "field_verification": "LIVE_VERIFIED" if items and missing_rows == 0 else "UNVERIFIED",
        "returned_999_rows": len(items) == 999,
        "source_response_ids": result.source_response_ids,
    }


def run_probe(client, requests, budget):
    findings = []
    stop = None
    for stage, svc, op, size, params in requests:
        result = client.call(svc, op, params)
        findings.append(inspect_result(stage, size, result))
        if result.outcome == Outcome.QUOTA_DAILY_EXCEEDED:
            budget.mark_quota_exhausted(svc, op)
        if (result.outcome in SERVICE_FATAL or result.outcome in {
            Outcome.IP_NOT_ALLOWED, Outcome.QUOTA_DAILY_EXCEEDED,
            Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY,
        }):
            stop = result.outcome.value
            break
    return findings, stop


def command(args):
    from pathlib import Path

    root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    if not 0 <= args.max_calls <= 20:
        raise ConfigError("P1 probe --max-calls는 0~20이어야 한다")
    day = args.day or (now_kst().date() - timedelta(days=1)).strftime("%Y%m%d")
    requests = list(probe_requests(day, args.page_sizes))
    settings = load_settings(root)
    gate = evaluate_live_gate(settings, args.live)
    if not gate.allowed:
        print("[BLOCKED] " + "; ".join(gate.reasons))
        return 2
    configure_logging(settings.log_dir)
    settings = replace(settings, retry_max_attempts=min(settings.retry_max_attempts, 3),
                       request_interval_seconds=max(settings.request_interval_seconds, 1.0))
    conn = open_database(settings.database_path, default_migrations_dir(root))
    catalog = load_catalog(default_catalog_path(root))
    budget = OperationBudget(settings.database_path,
                             default_limit=min(800, settings.backfill_max_calls_per_service_per_day),
                             scope="service", max_per_run=args.max_calls)
    runs = RunRepository(conn)
    run_id = new_run_id("probe")
    runs.start(run_id=run_id, command="probe", data_mode="real", live=True, status="RUNNING",
               max_calls_run=args.max_calls, catalog_sha256=catalog.sha256, notes={"day": day})
    try:
        with DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, "real"), run_id=run_id) as client:
            findings, stop = run_probe(client, requests, budget)
        passed = len(findings) == len(requests) and all(
            f["result_code"] == "00" and f["outcome"] in ("SUCCESS", "SUCCESS_EMPTY")
            and f["missing_key_rows"] == 0 for f in findings)
        status = "COMPLETED" if passed else ("PARTIAL" if stop else "FAILED")
        report = {"run_id": run_id, "day": day, "status": status, "stop_reason": stop,
                  "generated_at_kst": now_kst().isoformat(), "calls_used": budget.run_used,
                  "calls_by_api": budget.run_used_by_op, "findings": findings}
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        path = settings.report_dir / f"{run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        runs.finish(run_id, status=status, calls_attempted=budget.run_used, stop_reason=stop,
                    notes={"report": path.name})
        for f in findings:
            print(f"{f['stage']} rows={f['requested_rows']}: {f['outcome']}, "
                  f"rc={f['result_code']}, total={f['total_count']}, returned={f['returned_rows']}, "
                  f"missing_keys={f['missing_key_rows']}")
        print(f"[{status}] {run_id}, HTTP 시도 {budget.run_used}회, 보고서 {settings._rel(path)}")
        return 0 if passed else 3 if stop else 1
    except BaseException:
        runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="프로브 중단")
        raise
    finally:
        conn.close()
