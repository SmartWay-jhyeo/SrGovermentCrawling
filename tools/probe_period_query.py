"""면허제한·참가가능지역의 기간 조회(inqryDiv=1) 실측 프로브.

목적: 공고마다 1회씩 부르는 현재 방식 대신 등록일시 기간으로 한 번에 받는 방식이
호출을 줄이는지 판단할 근거를 만든다. 문서에는 있으나 실응답으로 확인한 적이 없다.

측정
1. 하루 구간의 totalCount(면허제한·참가가능지역)
2. numOfRows 100/500/999에서 실제 반환 행 수 — 페이지 크기 상한 확인
3. 하루 구간 totalCount × 수집일수 ÷ 실제 페이지 크기 = 예상 호출 수

호출 예산: 서비스 일일 한도에 합산되고 --max-calls(기본 20)로 제한한다.
실제 호출은 ALLOW_LIVE_API=true와 --live를 함께 요구한다. 키는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bidloc.catalog import default_catalog_path, load_catalog  # noqa: E402
from bidloc.clients.budget import OperationBudget  # noqa: E402
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate  # noqa: E402
from bidloc.config import load_settings  # noqa: E402
from bidloc.repositories.db import default_migrations_dir, open_database  # noqa: E402
from bidloc.repositories.raw_store import ResponseRecorder  # noqa: E402
from bidloc.repositories.runs import RunRepository, new_run_id  # noqa: E402
from bidloc.timeutil import now_kst  # noqa: E402

PROBES = {
    "면허제한": ("bid_notice", "getBidPblancListInfoLicenseLimit", {"inqryDiv": "1"}),
    "참가가능지역": ("bid_notice", "getBidPblancListInfoPrtcptPsblRgn", {"inqryDiv": "1"}),
    "공사공고목록(필터없음)": ("bid_notice", "getBidPblancListInfoCnstwkPPSSrch", {"inqryDiv": "1"}),
    "개찰결과(개찰일시)": ("bid_award", "getOpengResultListInfoCnstwk", {"inqryDiv": "3"}),
}
COLLECT_DAYS = 1096  # 3년 수집 범위 일수


def main() -> int:
    ap = argparse.ArgumentParser(description="기간 조회 실측 프로브")
    ap.add_argument("--live", action="store_true", help="실제 호출(ALLOW_LIVE_API=true도 필요)")
    ap.add_argument("--max-calls", type=int, default=20, help="이번 실행 호출 상한(기본 20)")
    ap.add_argument("--day", default="20250610", help="측정할 하루(YYYYMMDD, 기본 2025-06-10)")
    ap.add_argument("--page-sizes", default="100,500,999", help="시험할 numOfRows 목록")
    ap.add_argument("--probes", default="면허제한,참가가능지역", help="측정할 항목(쉼표): " + ", ".join(PROBES))
    args = ap.parse_args()

    settings = load_settings(ROOT)
    gate = evaluate_live_gate(settings, args.live)
    if not gate.allowed:
        print("[BLOCKED] 실제 호출 조건 미충족 — 네트워크 호출 없음")
        for reason in gate.reasons:
            print(f"  - {reason}")
        return 2

    conn = open_database(settings.database_path, default_migrations_dir(ROOT))
    budget = OperationBudget(settings.database_path, default_limit=settings.backfill_max_calls_per_service_per_day,
                            scope=settings.backfill_quota_scope, max_per_run=args.max_calls)
    catalog = load_catalog(default_catalog_path(ROOT))
    runs = RunRepository(conn)
    run_id = new_run_id("probe")
    runs.start(run_id=run_id, command="probe-period-query", data_mode=settings.data_mode, live=True, status="RUNNING",
               max_calls_run=args.max_calls, catalog_sha256=catalog.sha256, notes={"day": args.day})
    client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode), run_id=run_id)

    begin, end = f"{args.day}0000", f"{args.day}2359"
    sizes = [int(s) for s in args.page_sizes.split(",") if s.strip()]
    findings: dict[str, dict] = {}
    stop = None
    try:
        for label in [x.strip() for x in args.probes.split(",") if x.strip()]:
            svc, op, extra = PROBES[label]
            entry: dict = {"service": svc, "operation": op, "day": args.day, "pages": {}}
            findings[label] = entry
            for size in sizes:
                res = client.call(svc, op, {**extra, "inqryBgnDt": begin, "inqryEndDt": end,
                                            "pageNo": "1", "numOfRows": str(size)})
                got = len(res.items or [])
                entry["pages"][str(size)] = {"outcome": res.outcome.value, "result_code": res.result_code,
                                             "total_count": res.total_count, "returned_rows": got,
                                             "num_of_rows_echo": res.num_of_rows}
                print(f"[{label}] numOfRows={size}: {res.outcome.value} rc={res.result_code} "
                      f"totalCount={res.total_count} 반환행={got}")
                if not res.data_ok and res.outcome.value in ("BUDGET_EXHAUSTED_RUN", "BUDGET_EXHAUSTED_DAY",
                                                             "QUOTA_DAILY_EXCEEDED"):
                    stop = f"{label}: {res.outcome.value}"
                    break
                if res.total_count is None:
                    entry["note"] = "totalCount 없음 — 기간 조회 미지원 가능성"
                    break
            if stop:
                break
    finally:
        client.close()

    # 판단: 하루 행수 × 수집일수 ÷ 최대 페이지 크기
    verdict = {}
    for label, entry in findings.items():
        ok_pages = {int(k): v for k, v in entry["pages"].items() if v["total_count"] is not None}
        if not ok_pages:
            verdict[label] = {"결론": "기간 조회 근거 없음(totalCount 미확인)"}
            continue
        total_day = max(v["total_count"] for v in ok_pages.values())
        max_rows = max(v["returned_rows"] for v in ok_pages.values())
        effective_page = max_rows or 100
        est_rows = total_day * COLLECT_DAYS
        est_calls = -(-est_rows // effective_page) if effective_page else None
        verdict[label] = {"하루 행수": total_day, "실제 최대 페이지 크기": max_rows,
                          "3년 예상 행수": est_rows, "3년 예상 호출": est_calls,
                          "현재 방식 호출(공고당 1회, 추정)": 38000,
                          "이득": (est_calls is not None and est_calls < 38000)}
    report = {"run_id": run_id, "generated_at_kst": now_kst().isoformat(timespec="seconds"),
              "measured_day": args.day, "collect_days": COLLECT_DAYS, "calls_used": budget.run_used,
              "calls_by_api": budget.run_used_by_op, "stop_reason": stop, "findings": findings, "verdict": verdict}
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"probe-period-query-{now_kst().strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    runs.finish(run_id, status="PARTIAL" if stop else "COMPLETED", calls_attempted=budget.run_used, stop_reason=stop,
                notes={"verdict": verdict})
    conn.close()

    print(f"\n호출 {budget.run_used}회 {budget.run_used_by_op}")
    for label, v in verdict.items():
        print(f"- {label}: {v}")
    print(f"상세(Git 제외): {path}")
    print("주의: 하루 1개 구간 측정이라 날짜별 편차는 반영되지 않는다. 전환 판단은 여러 날 측정 후에 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
