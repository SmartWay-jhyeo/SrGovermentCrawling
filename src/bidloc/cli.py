"""명령행 인터페이스.

구현된 명령(P0): doctor, init-db, catalog-check, verify-api
제안 단계(P1 이후, 아직 없음): plan, collect, analyze, export
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

from bidloc import __version__

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2
EXIT_PARTIAL = 3

log = logging.getLogger("bidloc.cli")


def _prepare_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _root(args: argparse.Namespace) -> Path:
    return Path(args.project_root).resolve() if args.project_root else Path.cwd().resolve()


def cmd_doctor(args: argparse.Namespace) -> int:
    from bidloc.doctor import run_doctor

    checks, settings = run_doctor(_root(args), network=args.network)
    width = max(len(c.name) for c in checks)
    print(f"bid-location-lab doctor (v{__version__})")
    for check in checks:
        print(f"[{check.status:<7}] {check.name:<{width}}  {check.detail}")
    fails = [c for c in checks if c.status == "FAIL"]
    blocked = [c for c in checks if c.status == "BLOCKED"]
    print()
    print(f"요약: FAIL {len(fails)}, BLOCKED {len(blocked)}, WARN {sum(c.status == 'WARN' for c in checks)}")
    if fails:
        return EXIT_FAIL
    if args.require_live and blocked:
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_init_db(args: argparse.Namespace) -> int:
    from bidloc.config import load_settings
    from bidloc.repositories.db import apply_migrations, connect, default_migrations_dir, migration_status

    settings = load_settings(_root(args))
    conn = connect(settings.database_path)
    try:
        applied = apply_migrations(conn, default_migrations_dir(settings.project_root))
        status = migration_status(conn, default_migrations_dir(settings.project_root))
    finally:
        conn.close()
    print(f"DB: {settings._rel(settings.database_path)}")
    print(f"이번에 적용: {applied or '없음'}")
    print(f"적용 완료: {status.applied}")
    return EXIT_OK if status.ok else EXIT_FAIL


def cmd_catalog_check(args: argparse.Namespace) -> int:
    from bidloc.catalog import default_catalog_path, load_catalog

    catalog = load_catalog(default_catalog_path(_root(args)))
    print(f"카탈로그: {catalog.path.name} sha256={catalog.sha256[:16]}…")
    for sid, svc in catalog.services.items():
        in_scope = sum(1 for op in svc.operations.values() if op.in_p0_scope)
        print(f"- {sid}: {svc.base_url} (오퍼레이션 {len(svc.operations)}개, P0 대상 {in_scope}개, 서비스 상태 {svc.status})")
    print(f"오퍼레이션 상태 집계: {catalog.status_counts()}")
    live = catalog.data.get("live_verification") or {}
    print(f"실연동 상태: {live.get('status')} — {live.get('reason', '')}")
    return EXIT_OK


def cmd_verify_api(args: argparse.Namespace) -> int:
    from bidloc.catalog import default_catalog_path, load_catalog
    from bidloc.clients.budget import CallBudget
    from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
    from bidloc.collectors.verify import VerifyRunner, final_run_status, load_verify_plan, write_reports
    from bidloc.config import load_settings
    from bidloc.logging_setup import configure_logging
    from bidloc.repositories.db import default_migrations_dir, open_database
    from bidloc.repositories.raw_store import ResponseRecorder
    from bidloc.repositories.runs import RunRepository, new_run_id

    root = _root(args)
    settings = load_settings(root)
    configure_logging(settings.log_dir)
    catalog = load_catalog(default_catalog_path(root))
    plan_path = Path(args.plan) if args.plan else root / "config" / "verify_samples.yaml"
    plan = load_verify_plan(plan_path)
    if args.fixed_only:
        from dataclasses import replace

        plan = replace(plan, windows=())
    low, note = plan.estimate_calls()
    gate = evaluate_live_gate(settings, args.live)
    max_calls = settings.live_max_calls_per_run if args.max_calls is None else min(args.max_calls, settings.live_max_calls_per_run)

    print(f"verify-api 계획: 고정 표본 {len(plan.fixed_notices)}건, 탐색 창 {len(plan.windows)}개, "
          f"예상 최소 호출 {low}회(추정), 실행 예산 {max_calls}회, 일일 예산 {settings.live_max_calls_per_day}회")
    print(f"  참고: {note}")

    conn = open_database(settings.database_path, default_migrations_dir(root))
    runs = RunRepository(conn)
    run_id = new_run_id("verify")
    try:
        if not args.live:
            runs.start(run_id=run_id, command="verify-api", data_mode=settings.data_mode, live=False, status="SKIPPED",
                       max_calls_run=0, catalog_sha256=catalog.sha256, notes={"reason": "dry-run (--live 없음)"})
            runs.finish(run_id, status="SKIPPED", calls_attempted=0, stop_reason="dry-run: --live 미지정, 네트워크 호출 없음")
            print(f"[SKIPPED] {run_id}: --live 미지정. 네트워크 호출 없이 계획만 표시했다.")
            if not gate.allowed:
                print("  실제 실행 전 준비 필요: " + "; ".join(r for r in gate.reasons if "--live" not in r))
            return EXIT_OK
        if not gate.allowed:
            runs.start(run_id=run_id, command="verify-api", data_mode=settings.data_mode, live=False, status="BLOCKED",
                       max_calls_run=0, catalog_sha256=catalog.sha256, notes={"reasons": list(gate.reasons)})
            runs.finish(run_id, status="BLOCKED", calls_attempted=0, stop_reason="; ".join(gate.reasons))
            print(f"[BLOCKED] {run_id}: 실제 호출 조건 미충족 — 네트워크 호출 없음")
            for reason in gate.reasons:
                print(f"  - {reason}")
            return EXIT_BLOCKED

        budget = CallBudget(settings.database_path, max_per_run=max_calls, max_per_day=settings.live_max_calls_per_day)
        _, day_remaining = budget.remaining()
        if day_remaining <= 0 or max_calls <= 0:
            runs.start(run_id=run_id, command="verify-api", data_mode=settings.data_mode, live=True, status="BLOCKED",
                       max_calls_run=max_calls, catalog_sha256=catalog.sha256, notes={"reason": "budget"})
            runs.finish(run_id, status="BLOCKED", calls_attempted=0, stop_reason="내부 호출예산 0 (실행 또는 일일)")
            print(f"[BLOCKED] {run_id}: 내부 호출예산이 남아 있지 않다 (실행 {max_calls}, 일일 잔여 {day_remaining})")
            return EXIT_BLOCKED

        reuse = {}
        if args.resume:
            previous = runs.get(args.resume)
            if previous is None:
                print(f"[FAIL] 이어받을 실행이 없다: {args.resume}")
                return EXIT_FAIL
            reuse = runs.done_steps(args.resume)
        runs.start(run_id=run_id, command="verify-api", data_mode=settings.data_mode, live=True, status="RUNNING",
                   max_calls_run=max_calls, catalog_sha256=catalog.sha256,
                   notes={"plan": plan_path.name, "fixed_only": bool(args.fixed_only)}, resumed_from_run_id=args.resume)
        recorder = ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode)
        client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget, recorder=recorder,
                                run_id=run_id)
        from bidloc.clients.envelope import parse_body

        def read_raw_items(source_response_id: int):
            row = conn.execute("SELECT raw_path FROM source_response WHERE id = ?", (source_response_id,)).fetchone()
            if row is None or not row["raw_path"]:
                return None
            return parse_body(recorder.read_raw(row["raw_path"])).items

        runner = VerifyRunner(client=client, runs=runs, conn=conn, run_id=run_id, plan=plan, reuse=reuse,
                              reuse_from_run_id=args.resume, raw_items_reader=read_raw_items)
        try:
            summary = runner.run()
        except KeyboardInterrupt:
            runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="사용자 중단")
            print(f"[ABORTED] {run_id}: 사용자 중단. 완료 단계는 --resume {run_id}로 재사용할 수 있다.")
            return EXIT_FAIL
        finally:
            client.close()
        status = final_run_status(runner)
        runs.finish(run_id, status=status, calls_attempted=budget.run_used, stop_reason=runner.stop_reason,
                    notes={"step_status_counts": summary["step_status_counts"],
                           "any_core_link_confirmed": summary["any_core_link_confirmed"]})
        md_path, json_path = write_reports(settings.report_dir, summary)
        print(f"[{status}] {run_id}: HTTP 시도 {budget.run_used}회, 단계 {summary['step_status_counts']}")
        print(f"  핵심 연결 확인 표본 존재: {summary['any_core_link_confirmed']}; 참가업체수 비교: {summary['count_comparisons']}")
        if runner.stop_reason:
            print(f"  중단 사유: {runner.stop_reason} — 이어받기: python -m bidloc verify-api --live --resume {run_id}")
        if runner.blocked_services:
            print(f"  차단된 서비스: {runner.blocked_services}")
        print(f"  로컬 보고서(Git 제외): {settings._rel(md_path)}, {settings._rel(json_path)}")
        return {"COMPLETED": EXIT_OK, "PARTIAL": EXIT_PARTIAL, "BLOCKED": EXIT_BLOCKED}.get(status, EXIT_FAIL)
    finally:
        conn.close()


def _backfill_ops(cfg) -> list[tuple[str, str, str]]:
    from bidloc.backfill import runner as r

    d = cfg.detail
    ops = [("LIST", cfg.list_service, cfg.list_operation)]
    for flag, name, svc, op in (("license", "LICENSE", r.SVC_BID, r.OP_LICENSE), ("region", "REGION", r.SVC_BID, r.OP_REGION),
                                ("opening", "OPENING", r.SVC_AWARD, r.OP_OPENING), ("award", "AWARD", r.SVC_AWARD, r.OP_AWARD),
                                ("roster", "ROSTER", r.SVC_AWARD, r.OP_ROSTER)):
        if d.get(flag, flag in ("license", "region", "opening")):
            ops.append((name, svc, op))
    return ops


def _sweep(args, settings, conn, root, cfg) -> int:
    """기간 스윕 수집(sweep-init/run/status/finalize). 공고당 상세 호출 대신 날짜 구간으로 받는다."""
    import json
    from bidloc.backfill.sweep import STAGES, SweepRunner, SweepStore, extend_job, finalize_relevance, load_sweep_config
    from bidloc.catalog import default_catalog_path, load_catalog
    from bidloc.clients.budget import OperationBudget
    from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
    from bidloc.repositories.raw_store import ResponseRecorder
    from bidloc.repositories.runs import RunRepository, new_run_id
    from bidloc.timeutil import kst_today
    from dataclasses import replace
    from datetime import date

    settings = replace(settings, retry_max_attempts=min(settings.retry_max_attempts, 3),
                       request_interval_seconds=max(settings.request_interval_seconds, 1.0))
    scfg = getattr(args, "sweep_config", None) or load_sweep_config(cfg)
    store = SweepStore(conn)
    existing = conn.execute("SELECT job_id, status FROM sw_job WHERE job_name = ? ORDER BY created_at_utc DESC LIMIT 1",
                            (scfg.job_name,)).fetchone()
    if existing is None:
        if args.action not in ("sweep-init", "sweep-run"):
            print(f"[FAIL] sweep job '{scfg.job_name}'이 없다. 먼저 python -m bidloc backfill sweep-init")
            return EXIT_FAIL
        job_id = store.ensure_job(scfg)
        total = conn.execute("SELECT COUNT(*) FROM sw_partition WHERE job_id = ?", (job_id,)).fetchone()[0]
        print(f"[생성] sweep job {job_id}: 범위 {scfg.begin}~{scfg.end} (KST, 이후 고정), "
              f"단계 {'/'.join(scfg.stages)}, 파티션 {total}개({scfg.window_days}일 단위)")
    else:
        job_id = existing["job_id"]
        saved = conn.execute("SELECT * FROM sw_job WHERE job_id=?", (job_id,)).fetchone()
        saved_config = json.loads(saved["config_json"])
        saved_sweep = saved_config.get("sweep", saved_config)
        scfg = replace(scfg, begin=date.fromisoformat(saved["range_begin_kst"]),
                       end=date.fromisoformat(saved["range_end_kst"]),
                       num_of_rows=int(saved_sweep.get("num_of_rows", scfg.num_of_rows)))

    budget = OperationBudget(settings.database_path, default_limit=min(800, settings.backfill_max_calls_per_service_per_day),
                             limits={k: min(800, v, settings.backfill_max_calls_per_service_per_day) for k, v in cfg.limits.items()}, scope="service",
                             max_per_run=args.max_calls if args.action == "sweep-run" else None)

    if args.action == "sweep-init":
        print(f"sweep job {job_id}. 수집: python -m bidloc backfill sweep-run --live")
        return EXIT_OK

    if args.action == "sweep-extend":
        from datetime import date as _date
        from datetime import timedelta as _td

        new_end = _date.fromisoformat(args.to) if args.to else kst_today() - _td(days=1)
        result = extend_job(conn, scfg, job_id, new_end, recollect_last=args.recollect_last)
        print(f"범위 끝 {result['range_end']}까지 확장: 새 파티션 {result['added_partitions']}개, "
              f"다시 받을 파티션 {result['recollect_partitions']}개(최근 {args.recollect_last}일)")
        print("수집: python -m bidloc backfill sweep-run --live")
        return EXIT_OK

    if args.action == "sweep-finalize":
        left = conn.execute("SELECT COUNT(*) FROM sw_partition WHERE job_id = ? AND stage = 'LICENSE' AND status != 'DONE'",
                            (job_id,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM sw_partition WHERE job_id=? AND stage='LICENSE'", (job_id,)).fetchone()[0]
        counts = finalize_relevance(conn, scfg, license_complete=left == 0 and total > 0)
        print(f"관련성 확정(네트워크 없음): {counts}")
        if left:
            print(f"  면허 단계 미완료 파티션 {left}개 — RELEVANT만 표시했다. 'NOT_RELEVANT·UNKNOWN' 판정은 미룬다.")
        else:
            print("  UNKNOWN은 최신 차수 면허 미수집·필드 밀림 의심 등이다. 참가불가로 보지 않는다.")
        return EXIT_OK

    if args.action == "sweep-status":
        progress = store.progress(job_id)
        rows = store.rows_received(job_id)
        job_row = conn.execute("SELECT status, range_begin_kst, range_end_kst FROM sw_job WHERE job_id = ?",
                               (job_id,)).fetchone()
        # 범위는 job 생성 시점에 고정된 값을 쓴다(설정의 years_back은 실행일마다 달라진다).
        print(f"sweep job {job_id}  범위 {job_row['range_begin_kst']}~{job_row['range_end_kst']}  상태 {job_row['status']}")
        today = kst_today().isoformat()
        by_service: dict[str, list[str]] = {}
        for stage, svc, op, _params, basis in STAGES:
            if stage not in scfg.stages:
                continue
            by_service.setdefault(svc, []).append(stage)
            st = progress.get(stage, {})
            left = st.get("PENDING", 0) + st.get("IN_PROGRESS", 0) + st.get("FAILED", 0)
            done = st.get("DONE", 0)
            avg = (rows.get(stage, 0) / done) if done else None
            est_calls = None if avg is None else round(left * max(1.0, avg / scfg.num_of_rows))
            print(f"- {stage:<8} ({basis} 기준) 파티션 {st}, 수신 행 {rows.get(stage, 0):,}"
                  + (f", 완료 파티션 평균 {avg:,.0f}행 → 남은 호출 추정 {est_calls:,}" if avg is not None else ""))
        for svc, stages in by_service.items():
            used, exhausted = budget.used_today(svc, "")
            limit = budget.limit_for(svc, "")
            print(f"- 서비스 {svc}: 오늘(KST {today}) {used}/{limit}"
                  + (" (제공기관 한도 초과 표시)" if exhausted else "") + f", 단계 {'/'.join(stages)}")
        rel = dict(conn.execute("""SELECT s.relevance, COUNT(*) FROM bf_notice_state s
            WHERE EXISTS (SELECT 1 FROM bf_notice_revision n WHERE n.bid_ntce_no=s.bid_ntce_no)
            GROUP BY s.relevance""").fetchall())
        print(f"공고 관련성(공사 목록, 현재까지): {rel}")
        print("주의: 면허 단계가 끝나기 전 NOT_RELEVANT 판정은 하지 않는다. 완료 후 sweep-finalize로 확정한다.")
        return EXIT_OK

    gate = evaluate_live_gate(settings, args.live)
    if not gate.allowed:
        print("[BLOCKED] 실제 호출 조건 미충족 — 네트워크 호출 없음")
        for reason in gate.reasons:
            print(f"  - {reason}")
        return EXIT_BLOCKED
    catalog = load_catalog(default_catalog_path(root))
    runs = RunRepository(conn)
    run_id = new_run_id("sweep")
    runs.start(run_id=run_id, command="backfill sweep-run", data_mode=settings.data_mode, live=True, status="RUNNING",
               max_calls_run=args.max_calls or 0, catalog_sha256=catalog.sha256, notes={"job_id": job_id})
    client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode), run_id=run_id)
    runner = SweepRunner(client=client, budget=budget, store=store, cfg=scfg, job_id=job_id)
    try:
        report = runner.run()
    except KeyboardInterrupt:
        runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="사용자 중단")
        return EXIT_FAIL
    except BaseException:
        runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="수집 프로세스 오류")
        raise
    finally:
        client.close()
    status = "COMPLETED" if report.job_status == "COMPLETED" and not report.stop_reason else "PARTIAL"
    runs.finish(run_id, status=status, calls_attempted=budget.run_used, stop_reason=report.stop_reason,
                notes={"pages": report.pages, "rows": report.rows, "partitions_done": report.partitions_done,
                       "closed": report.closed, "calls_by_api": budget.run_used_by_op})
    print(f"[{status}] {run_id}: HTTP 시도 {budget.run_used}회, sweep job 상태 {report.job_status}")
    print(f"  API별 호출: {budget.run_used_by_op}")
    print(f"  단계별 페이지 {report.pages}, 수신 행 {report.rows}, 완료 파티션 {report.partitions_done}")
    if report.closed:
        print(f"  멈춘 API: {report.closed}")
    if report.stop_reason:
        print(f"  중단: {report.stop_reason}")
    if report.job_status == "COMPLETED":
        print("  수집 완료. python -m bidloc backfill sweep-finalize 로 관련성을 확정한다.")
    else:
        print("  다음 실행: python -m bidloc backfill sweep-run --live (마지막 커서부터 자동 재개)")
    return EXIT_FAIL if report.stop_reason else EXIT_OK


def _recall(args, settings, conn, budget, root) -> int:
    import json as _json

    from bidloc.backfill.recall import RecallRunner, ensure_study, load_study_config, recall_report
    from bidloc.catalog import default_catalog_path, load_catalog
    from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
    from bidloc.repositories.raw_store import ResponseRecorder
    from bidloc.repositories.runs import RunRepository, new_run_id
    from bidloc.timeutil import now_kst

    scfg = load_study_config(Path(args.study_config) if args.study_config else root / "config" / "recall_study.yaml")
    ensure_study(conn, scfg)
    if args.action == "recall-report":
        report = recall_report(conn, scfg)
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        path = settings.report_dir / f"recall-{scfg.name}-{now_kst().strftime('%Y%m%dT%H%M%S')}.json"
        path.write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"study {report['study_id']}")
        print(f"진행: 완료 시간창 {report['progress']['windows_fully_done']}, 정답 판정 {report['progress']['truth']}")
        print(f"비교 대상 전체 공사공고 {report['universe_notices']}건 중 면허 판정 {report['evaluated_notices']}건, "
              f"4992 양성 {report['positives_any']}건(목표 {report['target_min_positives']}건 이상: {report['sufficient_sample']})")
        for kind, f in report["filters"].items():
            r = f["recall_any"]
            print(f"- {kind} {f['params']}: recall {r[0]} (95% {r[1]}~{r[2]}), 찾음 {f['true_positive']}, 놓침 {f['missed']}, "
                  f"필터 결과 {f['hits_total']}건, precision {f['precision'][0]}")
        print(f"상세(Git 제외): {settings._rel(path)}")
        return EXIT_OK
    gate = evaluate_live_gate(settings, args.live)
    if not gate.allowed:
        print("[BLOCKED] 실제 호출 조건 미충족 — 네트워크 호출 없음")
        for reason in gate.reasons:
            print(f"  - {reason}")
        return EXIT_BLOCKED
    runs = RunRepository(conn)
    catalog = load_catalog(default_catalog_path(root))
    run_id = new_run_id("recall")
    runs.start(run_id=run_id, command="recall-check", data_mode=settings.data_mode, live=True, status="RUNNING",
               max_calls_run=args.max_calls or 0, catalog_sha256=catalog.sha256, notes={"study_id": scfg.study_id})
    client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode), run_id=run_id)
    runner = RecallRunner(client=client, budget=budget, conn=conn, cfg=scfg)
    try:
        runner.run()
    except KeyboardInterrupt:
        runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="사용자 중단")
        return EXIT_FAIL
    finally:
        client.close()
    runs.finish(run_id, status="PARTIAL" if runner.stop_reason else "COMPLETED", calls_attempted=budget.run_used,
                stop_reason=runner.stop_reason, notes={"study_id": scfg.study_id, "calls_by_api": budget.run_used_by_op,
                                                       "truth_called": runner.truth_called, "truth_reused": runner.truth_reused})
    print(f"[{'PARTIAL' if runner.stop_reason else 'COMPLETED'}] {run_id}: HTTP 시도 {budget.run_used}회 {budget.run_used_by_op}")
    print(f"  목록 페이지 {runner.list_pages}, 면허 판정 호출 {runner.truth_called}, 재사용 {runner.truth_reused}")
    if runner.stop_reason:
        print(f"  중단: {runner.stop_reason} — 같은 명령을 다시 실행하면 이어서 측정")
    return EXIT_OK if not runner.stop_reason else EXIT_PARTIAL


def cmd_backfill(args: argparse.Namespace) -> int:
    import math

    from bidloc.backfill.plan import load_backfill_config
    from bidloc.backfill.runner import BackfillRunner, refresh_job_status
    from bidloc.backfill.stats import compute_stats, write_stats
    from bidloc.backfill.store import BackfillStore
    from bidloc.catalog import default_catalog_path, load_catalog
    from bidloc.clients.budget import OperationBudget
    from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
    from bidloc.config import load_settings
    from bidloc.logging_setup import configure_logging
    from bidloc.repositories.db import default_migrations_dir, open_database
    from bidloc.repositories.raw_store import ResponseRecorder
    from bidloc.repositories.runs import RunRepository, new_run_id
    from bidloc.timeutil import kst_today

    root = _root(args)
    settings = load_settings(root)
    configure_logging(settings.log_dir)
    cfg_path = Path(args.config) if args.config else root / "config" / "backfill.yaml"
    cfg = load_backfill_config(cfg_path)
    conn = open_database(settings.database_path, default_migrations_dir(root))
    try:
        if args.action.startswith("sweep-"):
            from bidloc.collector_lock import collector_lock
            with collector_lock(settings.database_path):
                return _sweep(args, settings, conn, root, cfg)
        store = BackfillStore(conn)
        job = store.find_active_job(cfg.job_name)
        if job is None and args.action in ("init", "run"):
            begin, end = cfg.range_for_new_job()
            job = store.create_job(cfg, begin, end)
            parts = conn.execute("SELECT COUNT(*) FROM bf_partition WHERE job_id = ?", (job.job_id,)).fetchone()[0]
            print(f"[생성] job {job.job_id}: 범위 {begin}~{end} (KST, 이후 고정), 목록 파티션 {parts}개({cfg.window_days}일 단위)")
        if job is None:
            print(f"[FAIL] job '{cfg.job_name}'이 없다. 먼저 python -m bidloc backfill init")
            return EXIT_FAIL
        stored_sha = conn.execute("SELECT config_sha256 FROM bf_job WHERE job_id = ?", (job.job_id,)).fetchone()[0]
        if stored_sha != cfg.sha256():
            print("[WARN] backfill.yaml이 job 생성 시점과 다르다. 수집 범위는 job에 고정된 값을 계속 쓴다.")
        budget = OperationBudget(settings.database_path, default_limit=settings.backfill_max_calls_per_service_per_day,
                                 limits=cfg.limits, scope=settings.backfill_quota_scope,
                                 max_per_run=args.max_calls if args.action in ("run", "recall-check") else None)
        max_attempts = int(cfg.detail.get("max_task_attempts", 3))

        if args.action in ("recall-check", "recall-report"):
            return _recall(args, settings, conn, budget, root)

        if args.action == "init":
            print(f"job {job.job_id} 상태 {job.status}. 수집: python -m bidloc backfill run --live")
            return EXIT_OK

        if args.action == "status":
            status = refresh_job_status(store, job.job_id, max_attempts)
            print(f"job {job.job_id}  범위 {job.range_begin}~{job.range_end}  상태 {status}")
            parts = dict(conn.execute("SELECT status, COUNT(*) FROM bf_partition WHERE job_id = ? GROUP BY status", (job.job_id,)).fetchall())
            done_parts = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(rows_received), 0), COALESCE(SUM((rows_received + ? - 1) / ?), 0) FROM bf_partition "
                "WHERE job_id = ? AND status = 'DONE'", (cfg.num_of_rows, cfg.num_of_rows, job.job_id)).fetchone()
            print(f"목록 파티션: {parts} / 완료 파티션 수신 행 {done_parts[1]}")
            today = kst_today().isoformat()
            print(f"한도 정책: {settings.backfill_quota_scope} 단위 합산, 서비스당 하루 {settings.backfill_max_calls_per_service_per_day}회")
            services: dict[str, list] = {}
            for name, svc, op in _backfill_ops(cfg):
                services.setdefault(svc, []).append((name, op))
            eta_days = {}
            for svc, stages in services.items():
                used, exhausted = budget.used_today(svc, stages[0][1])
                limit = budget.limit_for(svc, stages[0][1])
                print(f"- 서비스 {svc}: 오늘(KST {today}) {used}/{limit}{' (제공기관 한도 초과 표시)' if exhausted else ''}")
                remaining_units = 0.0
                for name, op in stages:
                    if name == "LIST":
                        pending = parts.get("PENDING", 0) + parts.get("IN_PROGRESS", 0)
                        avg_pages = (done_parts[2] / done_parts[0]) if done_parts[0] else None
                        est = pending * avg_pages if avg_pages is not None else None
                        remaining_units += est or 0
                        print(f"    {name:<8} {op}: 남은 파티션 {pending}, 완료 파티션 평균 {avg_pages if avg_pages is None else round(avg_pages, 2)}페이지 → 남은 목록 호출 추정 {est if est is None else round(est)}")
                    else:
                        by_status = dict(conn.execute("SELECT status, COUNT(*) FROM bf_task WHERE job_id = ? AND task_type = ? GROUP BY status",
                                                      (job.job_id, name)).fetchall())
                        open_n = conn.execute(
                            """SELECT COUNT(*) FROM bf_task WHERE job_id = ? AND task_type = ? AND (status IN ('PENDING', 'DEFERRED')
                               OR (status = 'FAILED' AND attempts < ?))""", (job.job_id, name, max_attempts)).fetchone()[0]
                        remaining_units += open_n
                        print(f"    {name:<8} {op}: 작업 {by_status}, 남은 작업 {open_n}")
                eta_days[svc] = math.ceil(remaining_units / limit) if limit else None
                print(f"    → 현재 큐·추정 목록 기준 이 서비스 최소 {eta_days[svc]}일(목록이 남아 있으면 상세 작업이 더 늘어난다)")
            if done_parts[0] and parts.get("PENDING", 0):
                cand = conn.execute("SELECT COUNT(DISTINCT bid_ntce_no) FROM bf_task WHERE job_id = ? AND task_type = 'LICENSE'", (job.job_id,)).fetchone()[0]
                total_parts = sum(parts.values())
                per_part = cand / (done_parts[0] + parts.get("IN_PROGRESS", 0))
                projected_license = per_part * total_parts
                print(f"전체 규모 추정(완료 파티션 {done_parts[0]}개 외삽, 확정 아님): 후보 공고 약 {round(projected_license)}건 → "
                      f"입찰공고정보서비스(목록+면허+지역) 호출 약 {round(projected_license * 2 + done_parts[2] / done_parts[0] * total_parts)}회 이하, "
                      f"하루 {settings.backfill_max_calls_per_service_per_day}회로 약 "
                      f"{math.ceil((projected_license * 2 + done_parts[2] / done_parts[0] * total_parts) / settings.backfill_max_calls_per_service_per_day)}일")
            conflicts = conn.execute("SELECT COUNT(*) FROM bf_record_conflict").fetchone()[0]
            shifted = conn.execute("SELECT COUNT(*) FROM bf_license_limit WHERE quality_flag IS NOT NULL").fetchone()[0]
            print(f"데이터 품질: 같은 키 내용 변경 {conflicts}건, 면허제한 필드 밀림 의심 {shifted}행")
            from bidloc.backfill.recall import recall_gate as _rg

            g = _rg(conn, dict(cfg.list_params), cfg.target_license_codes, min_positives=cfg.recall_min_positives,
                    min_recall_lower95=cfg.recall_min_lower95)
            print(f"후보 필터 {g['filter_params']} recall 검증: {'통과' if g['passed'] else '미통과'} — 양성 {g['positives']}건, "
                  f"찾음 {g['found']}, recall {g['recall']}, 기준 {g['criteria']}")
            if not g["passed"]:
                print("주의: recall이 입증되기 전 수집 결과는 '필터 기반 수집 집합'이며 전체 모집단이 아니다.")
            return EXIT_OK

        if args.action == "stats":
            status = refresh_job_status(store, job.job_id, max_attempts)
            stats = compute_stats(conn, job)
            from bidloc.backfill.recall import recall_gate as _rg2

            stats["candidate_filter"] = _rg2(conn, dict(cfg.list_params), cfg.target_license_codes,
                                             min_positives=cfg.recall_min_positives, min_recall_lower95=cfg.recall_min_lower95)
            stats["population_label"] = ("필터 기반 수집 집합(recall 검증 통과, 표본 추정치 포함)" if stats["candidate_filter"]["passed"]
                                         else "필터 기반 수집 집합(recall 미입증 — 전체 모집단 아님)")
            json_path, csv_path = write_stats(settings.report_dir, stats, cfg.job_name)
            if status != "COMPLETED":
                print(f"[주의] job 상태 {status}: 수집이 끝나지 않았거나 누락 구간이 있다. 아래 값은 현재까지 누적된 부분 결과다.")
            print(f"집합 구분: {stats['population_label']}")
            print(f"범위 내 후보 공고 {stats['candidate_notices_in_range']}건, 제외 {stats['excluded_notices']}")
            for row in stats["rows"]:
                if row["allowed_region"] == "전국 고유 공고(지역 중복 제거)":
                    print(f"  {row['year']}: 관련 {row['relevant_notices']}건, 검토필요 {row['review_needed_notices']}건, "
                          f"개찰연결 {row['opening_linked_notices']}/{row['opening_checked_notices']}, "
                          f"참가업체수 중앙값 {row['prtcpt_cnum_median']} (n={row['prtcpt_cnum_n']})")
            print(f"지역별 전체 표(Git 제외): {settings._rel(csv_path)}, {settings._rel(json_path)}")
            return EXIT_OK

        from bidloc.backfill.recall import recall_gate

        rgate = recall_gate(conn, cfg.list_params | {}, cfg.target_license_codes, min_positives=cfg.recall_min_positives,
                            min_recall_lower95=cfg.recall_min_lower95) if "indstrytyCd" in cfg.list_params or "indstrytyNm" in cfg.list_params else None

        # run
        if rgate is not None and not rgate["passed"] and not args.allow_unvalidated_filter:
            print("[BLOCKED] 후보 필터 recall이 아직 입증되지 않아 백필 전체 실행을 막았다(네트워크 호출 없음).")
            print(f"  필터 {rgate['filter_params']}: 양성 {rgate['positives']}건, 찾음 {rgate['found']}, recall {rgate['recall']}")
            for reason in rgate["reasons"]:
                print(f"  - {reason}")
            print("  먼저 python -m bidloc backfill recall-check --live 로 표본을 늘린다. 미검증 상태로 수집하려면 --allow-unvalidated-filter")
            return EXIT_BLOCKED
        gate = evaluate_live_gate(settings, args.live)
        if not gate.allowed:
            print("[BLOCKED] 실제 호출 조건 미충족 — 네트워크 호출 없음")
            for reason in gate.reasons:
                print(f"  - {reason}")
            return EXIT_BLOCKED
        runs = RunRepository(conn)
        catalog = load_catalog(default_catalog_path(root))
        run_id = new_run_id("backfill")
        runs.start(run_id=run_id, command="backfill-run", data_mode=settings.data_mode, live=True, status="RUNNING",
                   max_calls_run=args.max_calls or 0, catalog_sha256=catalog.sha256,
                   notes={"job_id": job.job_id, "daily_limit": settings.backfill_max_calls_per_service_per_day,
                          "quota_scope": settings.backfill_quota_scope,
                          "limits": cfg.limits, "max_calls_run": args.max_calls})
        recorder = ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode)
        client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget, recorder=recorder, run_id=run_id)
        runner = BackfillRunner(client=client, budget=budget, store=store, job=job, cfg=cfg)
        try:
            report = runner.run()
        except KeyboardInterrupt:
            runs.finish(run_id, status="ABORTED", calls_attempted=budget.run_used, stop_reason="사용자 중단")
            print(f"[ABORTED] {run_id}: 사용자 중단. 완료된 단계는 저장됐고 다음 실행에서 이어간다.")
            return EXIT_FAIL
        finally:
            client.close()
        run_status = "COMPLETED" if report.job_status.startswith("COMPLETED") else ("FAILED" if report.stop_reason else "PARTIAL")
        runs.finish(run_id, status=run_status, calls_attempted=budget.run_used, stop_reason=report.stop_reason,
                    notes={"job_id": job.job_id, "calls_by_api": budget.run_used_by_op, "tasks_done": report.tasks_done,
                           "tasks_failed": report.tasks_failed, "partitions_done": report.partitions_done,
                           "closed_ops": report.closed_ops})
        print(f"[{run_status}] {run_id}: HTTP 시도 {budget.run_used}회, job 상태 {report.job_status}")
        print(f"  API별 호출: {budget.run_used_by_op}")
        print(f"  목록: 페이지 {report.list_pages}, 완료 파티션 {report.partitions_done}, 갱신 공고 {report.notices_seen}")
        print(f"  상세 완료 {report.tasks_done}, 실패 {report.tasks_failed}")
        if report.closed_ops:
            print(f"  멈춘 API: {report.closed_ops}")
        if report.stop_reason:
            print(f"  중단 사유: {report.stop_reason}")
        print("  다음 실행: python -m bidloc backfill run --live (마지막 커서부터 자동 재개), 진행 확인: python -m bidloc backfill status")
        return {"COMPLETED": EXIT_OK, "PARTIAL": EXIT_PARTIAL}.get(run_status, EXIT_FAIL)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m bidloc", description="면허 입지 분석기 — P0 API 계약·연결 검증 도구")
    parser.add_argument("--version", action="version", version=f"bid-location-lab {__version__}")
    parser.add_argument("--project-root", help="프로젝트 루트(기본: 현재 디렉터리)")
    parser.add_argument("--debug-traceback", action="store_true", help="오류 시 마스킹된 traceback 출력")
    sub = parser.add_subparsers(dest="command", required=True)

    from bidloc.probe import command as probe_command

    p_probe = sub.add_parser("probe", help="P1: 네 API 하루 구간·페이지 크기 검증(최대 20회)")
    p_probe.add_argument("--live", action="store_true")
    p_probe.add_argument("--day", help="YYYYMMDD (기본 어제 KST)")
    p_probe.add_argument("--page-sizes", default="100,999")
    p_probe.add_argument("--max-calls", type=int, default=10)
    p_probe.set_defaults(func=probe_command)

    from bidloc.sweep_cli import command as sweep_command
    p_sweep = sub.add_parser("sweep", help="기간 스윕 수집·재개·확정 (collector.yaml)")
    p_sweep.add_argument("action", choices=["init", "run", "status", "finalize", "extend"])
    p_sweep.add_argument("--live", action="store_true")
    p_sweep.add_argument("--config")
    p_sweep.add_argument("--job-name", default="sweep-3y")
    p_sweep.add_argument("--begin", help="새 job의 시작일 YYYY-MM-DD")
    p_sweep.add_argument("--end", help="새 job의 끝일 YYYY-MM-DD")
    p_sweep.add_argument("--max-calls", type=int)
    p_sweep.add_argument("--to")
    p_sweep.add_argument("--recollect-last", type=int, default=2)
    p_sweep.set_defaults(func=sweep_command)

    from bidloc.analysis_cli import add_arguments
    p_analyze = sub.add_parser("analyze", help="오프라인 입지·단가계약 분석 및 CSV/JSON")
    p_analyze.add_argument("analysis", choices=["location", "unitprice"])
    add_arguments(p_analyze)
    p_shortlist = sub.add_parser("shortlist", help="마감 전 지역·면허 기준 후보 공고")
    add_arguments(p_shortlist, hq_required=True)
    p_shortlist.set_defaults(analysis="shortlist")

    p_doctor = sub.add_parser("doctor", help="설정·DB·카탈로그·연결 준비 상태 점검")
    p_doctor.add_argument("--network", action="store_true", help="DNS·TLS 핸드셰이크 점검(HTTP 요청·인증키 전송 없음)")
    p_doctor.add_argument("--require-live", action="store_true", help="실연동 준비가 안 되면 종료코드 2")
    p_doctor.set_defaults(func=cmd_doctor)

    p_init = sub.add_parser("init-db", help="SQLite DB 생성 및 마이그레이션 적용")
    p_init.set_defaults(func=cmd_init_db)

    p_cat = sub.add_parser("catalog-check", help="config/api_catalog.yaml 형식·상태 점검")
    p_cat.set_defaults(func=cmd_catalog_check)

    p_verify = sub.add_parser("verify-api", help="소량 실호출로 API 연결 검증 (--live 없으면 계획만 표시)")
    p_verify.add_argument("--live", action="store_true", help="실제 호출. ALLOW_LIVE_API=true와 인증키도 필요")
    p_verify.add_argument("--max-calls", type=int, help="이번 실행 호출 상한(설정값보다 낮출 때만 적용)")
    p_verify.add_argument("--plan", help="표본 계획 YAML 경로(기본 config/verify_samples.yaml)")
    p_verify.add_argument("--fixed-only", action="store_true", help="탐색 조회 없이 고정 표본만 검증")
    p_verify.add_argument("--resume", help="이전 실행 ID. DONE 단계 결과를 재사용한다")
    p_verify.set_defaults(func=cmd_verify_api)

    p_back = sub.add_parser("backfill", help="최근 3개년 완전 수집(재개 가능)")
    p_back.add_argument("action", choices=["init", "run", "status", "stats", "recall-check", "recall-report",
                                           "sweep-init", "sweep-run", "sweep-status", "sweep-finalize",
                                           "sweep-extend"],
                        help="init: job·파티션 생성 / run: 수집(재개) / status: 진행·서비스별 사용량 / stats: 누적 통계 / "
                             "recall-check: 후보 필터 recall 측정(재개) / recall-report: 측정 결과")
    p_back.add_argument("--study-config", help="recall 측정 설정(기본 config/recall_study.yaml)")
    p_back.add_argument("--to", help="sweep-extend: 이 날짜(YYYY-MM-DD, KST)까지 범위를 늘린다. 기본 어제")
    p_back.add_argument("--recollect-last", type=int, default=1,
                        help="sweep-extend: 최근 N일치를 다시 받는다(하루 끝나기 전 받은 분 보완, 기본 1)")
    p_back.add_argument("--allow-unvalidated-filter", action="store_true",
                        help="recall 미입증 상태에서도 run 허용(결과는 필터 기반 집합으로만 표시)")
    p_back.add_argument("--config", help="백필 설정 YAML(기본 config/backfill.yaml)")
    p_back.add_argument("--live", action="store_true", help="run에서 실제 호출. ALLOW_LIVE_API=true와 인증키도 필요")
    p_back.add_argument("--max-calls", type=int, help="이번 실행 전체 호출 상한(선택). 서비스별 일일 한도는 별도로 항상 적용")
    p_back.set_defaults(func=cmd_backfill)
    return parser


def main(argv: list[str] | None = None) -> int:
    _prepare_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "max_calls", None) is not None and args.max_calls < 0:
        parser.error("--max-calls는 0 이상")
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("중단됨", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:  # 모든 예외 메시지를 마스킹해 출력한다
        from bidloc.redaction import redact

        print(f"[FAIL] {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        if getattr(args, "debug_traceback", False):
            print(redact("".join(traceback.format_exc())), file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
