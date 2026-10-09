"""Small, explicitly gated provider API checks; never ingests notices into analysis tables."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bidloc.catalog import load_catalog
from bidloc.clients.budget import OperationBudget
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
from bidloc.config import PROVIDER_KEY_ENV, Settings, load_settings
from bidloc.redaction import redact
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.raw_store import ResponseRecorder
from bidloc.repositories.runs import RunRepository, new_run_id
from bidloc.timeutil import now_kst

OPERATIONS = {
    "bid_notice": "getBidPblancListInfoCnstwkPPSSrch",
    # Same G2B service and key; registration-date lists used to see which registration types they return.
    "bid_notice_reg": "getBidPblancListInfoCnstwk",
    "bid_notice_etc": "getBidPblancListInfoEtc",
    "kapt": "getPblAncDeSearchV3",
    "lh": "getOpenBidInfo",
    "kwater": "cntrwkList",
    "d2b": "getFcltyCmpetBidPblancList",
    "pps_openstd": "getDataSetOpnStdBidPblancInfo",
}
G2B_PROVIDERS = frozenset({"bid_notice", "bid_notice_reg", "bid_notice_etc"})
DATE_FIELDS = {"bid_notice": "bidNtceDt", "bid_notice_reg": "rgstDt", "bid_notice_etc": "rgstDt",
               "kapt": "bidRegDate", "lh": "tndrbidRegDt", "kwater": "tndrPblancDe", "d2b": "pblancDate",
               "pps_openstd": "bidNtceDate"}
# Categorical fields that separate source system, notice version and business type.
VALUE_COUNT_FIELDS = {**{p: ("rgstTyNm", "ntceKindNm") for p in G2B_PROVIDERS},
                      "d2b": ("busiDivs", "pblancSe", "cntrctMth"),
                      "pps_openstd": ("ppsNtceYn", "bsnsDivNm", "bidNtceSttusNm")}
G2B_OWN_REGISTRATION = "조달청 또는 나라장터 자체 공고건"
# Detail lookups need the full composite key from a list row; a notice number alone is not enough.
DETAIL_OPERATIONS = {"d2b": "getFcltyCmpetBidPblancDetail"}
DETAIL_KEYS = {"d2b": ("pblancYear", "pblancSeCode", "pblancNo", "pblancOdr", "cntrwkNo", "orntCode")}


def service_id(provider: str) -> str:
    return "bid_notice" if provider in G2B_PROVIDERS else provider


def settings_for_provider(settings: Settings, provider: str) -> Settings:
    if provider in G2B_PROVIDERS:
        return settings
    # The user approved D2B and the PPS open-standard service on the same account as kapt/lh/kwater.
    if provider not in {"kapt", "lh", "kwater", "d2b", "pps_openstd"}:
        raise ValueError("unsupported provider")
    # Missing credentials must not fall back to the unrelated G2B credential.
    return replace(settings, service_key=settings.provider_service_key,
                   service_key_format=settings.provider_service_key_format)


def query_params(provider: str, begin: date, end: date, page: int = 1, rows: int = 5) -> dict[str, str]:
    if begin > end:
        raise ValueError("begin must not exceed end")
    params = {"pageNo": str(page), "numOfRows": str(rows)}
    if provider in G2B_PROVIDERS:
        # inqryDiv=1 is 공고게시일시 for PPSSrch and 등록일시 for the plain list operation.
        params.update(inqryDiv="1", inqryBgnDt=begin.strftime("%Y%m%d") + "0000",
                      inqryEndDt=end.strftime("%Y%m%d") + "2359", type="json")
    elif provider == "d2b":
        # Swagger names 공고일자 bounds without a format; YYYYMMDD is a probe hypothesis.
        params.update(anmtDateBegin=begin.strftime("%Y%m%d"), anmtDateEnd=end.strftime("%Y%m%d"))
    elif provider == "pps_openstd":
        # Documented as YYYYMMDDHHMM with at most a one-month range; no business-type filter.
        if end - begin > timedelta(days=30):
            raise ValueError("pps_openstd probe range must stay within one month")
        params.update(bidNtceBgnDt=begin.strftime("%Y%m%d") + "0000",
                      bidNtceEndDt=end.strftime("%Y%m%d") + "2359", type="json")
    elif provider == "kapt":
        # YYYYMMDD is a probe hypothesis until accepted with dated rows.
        params.update(startDate=begin.strftime("%Y%m%d"), endDate=end.strftime("%Y%m%d"))
    elif provider == "lh":
        params.update(tndrbidRegDtStart=begin.strftime("%Y%m%d"), tndrbidRegDtEnd=end.strftime("%Y%m%d"))
    elif provider == "kwater":
        # This API only documents month-based search, not a day-range filter.
        if (begin.year, begin.month) != (end.year, end.month):
            raise ValueError("kwater probe must stay within one calendar month")
        params.update(searchDt=end.strftime("%Y%m"), _type="json")
    else:
        raise ValueError("unsupported provider")
    return params


def inspect_result(provider: str, result: Any, begin: date, end: date) -> dict[str, Any]:
    items = result.items
    dates = []
    for item in items or []:
        value = str(item.get(DATE_FIELDS[provider]) or "")
        digits = "".join(c for c in value if c.isascii() and c.isdigit())
        try:
            dates.append(date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])))
        except ValueError:
            continue
    recent = sum(begin <= d <= end for d in dates)
    state = "BLOCKED" if not result.data_ok else "LIVE_EMPTY" if not items else "LIVE_SAMPLE_RECEIVED"
    value_counts = {name: dict(Counter(str(item[name]) for item in items or [] if name in item))
                    for name in VALUE_COUNT_FIELDS.get(provider, ())}
    if provider in G2B_PROVIDERS:
        # Agencies of rows not registered as G2B's own notices (e.g. linked self-procurement systems).
        value_counts["ntceInsttNm_non_own_registration"] = dict(Counter(
            str(item.get("ntceInsttNm")) for item in items or []
            if "rgstTyNm" in item and item["rgstTyNm"] != G2B_OWN_REGISTRATION))
    elif provider == "pps_openstd":
        # Rows explicitly flagged as not also posted on G2B.
        value_counts["ntceInsttNm_not_pps_notice"] = dict(Counter(
            str(item.get("ntceInsttNm")) for item in items or [] if item.get("ppsNtceYn") == "N"))
    return {
        "status": state,
        "outcome": result.outcome.value,
        "http_status": result.http_status,
        "result_code": result.result_code,
        "result_message": result.result_msg,
        "attempts": result.attempts,
        "source_response_ids": result.source_response_ids,
        "total_count": result.total_count,
        "sample_rows": None if items is None else len(items),
        "observed_fields": sorted({k for item in items or [] for k in item}),
        "date_field": DATE_FIELDS[provider],
        "parseable_date_rows": len(dates),
        "recent_sample_rows": recent if items else None,
        "sample_latest_date": max(dates).isoformat() if dates else None,
        "freshness": "RECENT_SAMPLE_OBSERVED" if recent else "UNVERIFIED",
        "sample_sha256": hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if items else None,
        "value_counts": value_counts if items else None,
        "full_collection": "NOT_ATTEMPTED",
        "site_region_eligibility_and_revision_semantics": "UNVERIFIED",
    }


def probe_detail(client: Any, provider: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    keys = DETAIL_KEYS[provider]
    candidates = [item for item in items if all(str(item.get(k) or "").strip() for k in keys)]
    chosen = next((item for item in candidates if item.get("busiDivs") == "공사"), candidates[0] if candidates else None)
    if chosen is None:
        return {"status": "SKIPPED", "reason": "no list row carries every detail key"}
    params = {"pageNo": "1", "numOfRows": "1", **{k: str(chosen[k]).strip() for k in keys}}
    result = client.call(service_id(provider), DETAIL_OPERATIONS[provider], params, response_type=None)
    item = (result.items or [None])[0]
    return {
        "status": "BLOCKED" if not result.data_ok else "LIVE_EMPTY" if not item else "LIVE_DETAIL_RECEIVED",
        "outcome": result.outcome.value,
        "http_status": result.http_status,
        "result_code": result.result_code,
        "source_response_ids": result.source_response_ids,
        "request_params": params,
        "chosen_busiDivs": chosen.get("busiDivs"),
        "observed_fields": sorted(item) if item else [],
        # Field names only; empty strings are kept apart from filled values, never coerced.
        "non_empty_fields": sorted(k for k, v in (item or {}).items() if str(v or "").strip()),
    }


def probe_provider(client: Any, budget: Any, provider: str, begin: date, end: date, rows: int = 5,
                   detail: bool = False) -> dict[str, Any]:
    pages = []
    first_items: list[dict[str, Any]] = []
    for page in (1, 2):
        params = query_params(provider, begin, end, page, rows)
        result = client.call(service_id(provider), OPERATIONS[provider], params, response_type=None)
        observation = inspect_result(provider, result, begin, end)
        observation["request_params"] = params
        pages.append(observation)
        if page == 1:
            first_items = list(result.items or [])
        if result.outcome.value == "QUOTA_DAILY_EXCEEDED":
            budget.mark_quota_exhausted(service_id(provider), OPERATIONS[provider])
        if not result.data_ok or not result.items or result.total_count is None or result.total_count <= rows:
            break
    repeated = len(pages) == 2 and bool(pages[0]["sample_sha256"]) and pages[0]["sample_sha256"] == pages[1]["sample_sha256"]
    status = pages[0]["status"]
    if repeated or (len(pages) > 1 and pages[-1]["status"] == "BLOCKED"):
        status = "REVIEW_REQUIRED"
    observation = {"pages": pages, "repeated_page": repeated, "status": status}
    if detail and provider in DETAIL_OPERATIONS and status == "LIVE_SAMPLE_RECEIVED":
        observation["detail"] = probe_detail(client, provider, first_items)
    return observation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--providers", default="kapt,lh,kwater")
    parser.add_argument("--begin", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--rows", type=int, default=5, help="numOfRows per page; at most two pages per provider")
    parser.add_argument("--detail", action="store_true",
                        help="d2b only: one detail call for the first construction row of page 1")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(root)
    providers = args.providers.split(",")
    if not providers or any(p not in OPERATIONS for p in providers) or len(set(providers)) != len(providers):
        parser.error("providers must be unique " + ",".join(OPERATIONS) + " identifiers")
    if not 1 <= args.max_calls <= 12:
        parser.error("max-calls must be between 1 and 12")
    if not 1 <= args.rows <= 999:
        parser.error("rows must be between 1 and 999")
    gates = {p: evaluate_live_gate(settings_for_provider(settings, p), args.live) for p in providers}
    blocked = {p: [reason.replace("DATA_GO_KR_SERVICE_KEY", PROVIDER_KEY_ENV) if p not in G2B_PROVIDERS else reason
                   for reason in gate.reasons] for p, gate in gates.items() if not gate.allowed}
    if blocked:
        print(json.dumps({"status": "BLOCKED", "providers": blocked, "http_calls": 0}, ensure_ascii=False))
        return 2
    end = args.end or now_kst().date()
    begin = args.begin or max(end.replace(day=1), end - timedelta(days=7))
    for provider in providers:
        query_params(provider, begin, end)
    catalog = load_catalog(root / "config/provider_api_catalog.yaml")
    if G2B_PROVIDERS & set(providers):
        existing = load_catalog(root / "config/api_catalog.yaml")
        catalog.services["bid_notice"] = existing.services["bid_notice"]
        catalog.sha256 = hashlib.sha256((catalog.sha256 + existing.sha256).encode()).hexdigest()
    # One attempt only: no repeated denied requests; later runs use persisted daily usage.
    settings = replace(settings, retry_max_attempts=1)
    budget = OperationBudget(settings.database_path, default_limit=min(20, settings.live_max_calls_per_day),
                             limits={"bid_notice": settings.backfill_max_calls_per_service_per_day},
                             max_per_run=min(args.max_calls, settings.live_max_calls_per_run), scope="service")
    conn = open_database(settings.database_path, default_migrations_dir(root))
    runs = RunRepository(conn)
    run_id = new_run_id("provider-probe")
    report: dict[str, Any] = {"run_id": run_id, "checked_at_kst": now_kst().isoformat(),
        "begin": begin.isoformat(), "end": end.isoformat(), "rows": args.rows, "catalog_sha256": catalog.sha256,
        "providers": {}, "kepco": {"status": "SKIPPED", "reason": "KEPCO adapter not implemented; separate portal credential and endpoint verification required"}}
    runs.start(run_id=run_id, command="provider-probe", data_mode="real", live=True,
               status="RUNNING", max_calls_run=budget.max_per_run, catalog_sha256=catalog.sha256)
    status = "PARTIAL"
    try:
        for provider in providers:
            with DataGoKrClient(settings=settings_for_provider(settings, provider), catalog=catalog,
                                gate=gates[provider], budget=budget,
                                recorder=ResponseRecorder(conn, settings.raw_response_dir, "real"), run_id=run_id) as client:
                report["providers"][provider] = probe_provider(client, budget, provider, begin, end, args.rows,
                                                               args.detail)
                print(provider, report["providers"][provider]["status"], flush=True)
    except Exception as exc:
        # Never print an unredacted network exception or traceback.
        report["execution_error"] = redact(f"{type(exc).__name__}: {exc}")
        status = "FAILED"
    finally:
        report["http_calls"] = budget.run_used
        report["calls_by_api"] = budget.run_used_by_op
        report["status"] = status
        folder = settings.report_dir / "provider-probe"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{run_id}.json"
        path.write_text(redact(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
        runs.finish(run_id, status=status, calls_attempted=budget.run_used,
                    stop_reason="Sample-only validation; see provider statuses", notes={"report": str(path)})
        conn.close()
    print(json.dumps({"report": str(path), "http_calls": budget.run_used, "status": status}, ensure_ascii=False))
    return 1 if status == "FAILED" or any(v["status"] in {"BLOCKED", "REVIEW_REQUIRED"} for v in report["providers"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
