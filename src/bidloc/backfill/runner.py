"""백필 수집 루프.

단계와 호출 오퍼레이션:
  LIST     bid_notice.getBidPblancListInfoCnstwkPPSSrch   기간 파티션 × 전체 페이지 (후보 공고 필터링)
  LICENSE  bid_notice.getBidPblancListInfoLicenseLimit    후보 공고의 최신 차수 → 관련성(목표 면허코드) 판정
  REGION   bid_notice.getBidPblancListInfoPrtcptPsblRgn   관련·미확인 공고만
  OPENING  bid_award.getOpengResultListInfoCnstwk         관련·미확인 + 취소 아님 + 개찰일시 경과 공고만
  AWARD    bid_award.getScsbidListSttusCnstwk             (설정 시)
  ROSTER   bid_award.getOpengResultListInfoOpengCompt     (설정 시, 참가 수 상한 이내 개찰완료 단위)

오퍼레이션마다 일일 한도가 독립이므로, 한 오퍼레이션이 소진돼도 나머지 단계는 계속 진행한다.
진행 위치는 모두 DB(bf_partition, bf_task)에 있고 다음 실행에서 그대로 이어간다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from bidloc.backfill.plan import BackfillConfig
from bidloc.backfill.store import TASK_TYPES, BackfillStore, Job, transaction
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import DATA_OK, SERVICE_FATAL, Outcome
from bidloc.collectors.pagination import PageCollection, collect_all_pages
from bidloc.redaction import redact
from bidloc.timeutil import kst_today

SVC_BID = "bid_notice"
SVC_AWARD = "bid_award"
OP_LICENSE = "getBidPblancListInfoLicenseLimit"
OP_REGION = "getBidPblancListInfoPrtcptPsblRgn"
OP_OPENING = "getOpengResultListInfoCnstwk"
OP_AWARD = "getScsbidListSttusCnstwk"
OP_ROSTER = "getOpengResultListInfoOpengCompt"

BUDGET_OUTCOMES = {Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY}

WORKED, IDLE, BUDGET, STOP = "WORKED", "IDLE", "BUDGET", "STOP"


@dataclass
class RunReport:
    partitions_done: int = 0
    list_pages: int = 0
    notices_seen: int = 0
    tasks_done: dict[str, int] = field(default_factory=dict)
    tasks_failed: dict[str, int] = field(default_factory=dict)
    closed_ops: dict[str, str] = field(default_factory=dict)
    stop_reason: str | None = None
    job_status: str = "ACTIVE"

    def bump(self, bucket: dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1


class BackfillRunner:
    def __init__(self, *, client: Any, budget: OperationBudget, store: BackfillStore, job: Job, cfg: BackfillConfig,
                 today: Callable[[], date] = kst_today) -> None:
        self.client = client
        self.budget = budget
        self.store = store
        self.job = job
        self.cfg = cfg
        self.today = today
        d = cfg.detail
        self.page_size = int(d.get("detail_page_size", 100))
        self.max_pages = int(d.get("detail_max_pages", 20))
        self.max_attempts = int(d.get("max_task_attempts", 3))
        self.report = RunReport()

    # ------------------------------------------------------------------ 루프

    def stages(self) -> list[tuple[str, str, str, Callable[[], str]]]:
        d = self.cfg.detail
        out = [("LIST", self.cfg.list_service, self.cfg.list_operation, self.step_list)]
        if d.get("license", True):
            out.append(("LICENSE", SVC_BID, OP_LICENSE, lambda: self.step_task("LICENSE", SVC_BID, OP_LICENSE, self.do_license)))
        if d.get("region", True):
            out.append(("REGION", SVC_BID, OP_REGION, lambda: self.step_task("REGION", SVC_BID, OP_REGION, self.do_region)))
        if d.get("opening", True):
            out.append(("OPENING", SVC_AWARD, OP_OPENING, lambda: self.step_task("OPENING", SVC_AWARD, OP_OPENING, self.do_opening)))
        if d.get("award", False):
            out.append(("AWARD", SVC_AWARD, OP_AWARD, lambda: self.step_task("AWARD", SVC_AWARD, OP_AWARD, self.do_award)))
        if d.get("roster", False):
            out.append(("ROSTER", SVC_AWARD, OP_ROSTER, lambda: self.step_task("ROSTER", SVC_AWARD, OP_ROSTER, self.do_roster)))
        return out

    def run(self) -> RunReport:
        stages = self.stages()
        closed: set[str] = set()
        while self.report.stop_reason is None:
            progressed = False
            for name, svc, op, fn in stages:
                key = f"{svc}.{op}"
                if name in closed:
                    continue
                if self.budget.remaining(svc, op) <= 0:
                    closed.add(name)
                    self.report.closed_ops[key] = "오늘 한도 소진 또는 실행 상한 도달"
                    continue
                result = fn()
                if result == WORKED:
                    progressed = True
                elif result == BUDGET:
                    closed.add(name)
                    self.report.closed_ops[key] = "호출예산 또는 제공기관 일일 한도"
                elif result == STOP:
                    break
                if self.report.stop_reason:
                    break
            if not progressed:
                break
        self.report.job_status = self.update_job_status()
        return self.report

    # ------------------------------------------------------------------ 공통 결과 처리

    def _fatal_or_budget(self, outcome: Outcome | None, svc: str, op: str) -> str | None:
        if outcome in BUDGET_OUTCOMES:
            return BUDGET
        if outcome == Outcome.QUOTA_DAILY_EXCEEDED:
            self.budget.mark_quota_exhausted(svc, op)
            return BUDGET
        if outcome in SERVICE_FATAL or outcome == Outcome.IP_NOT_ALLOWED:
            self.report.stop_reason = f"{svc}.{op}: {outcome.value} — 인증·권한·IP 문제로 수집 중단"
            return STOP
        return None

    # ------------------------------------------------------------------ LIST

    def step_list(self) -> str:
        part = self.store.next_partition(self.job.job_id)
        if part is None:
            return IDLE
        cfg = self.cfg
        page = int(part["next_page"])
        params = dict(cfg.list_params)
        params.update({"inqryBgnDt": part["window_begin"], "inqryEndDt": part["window_end"],
                       "pageNo": str(page), "numOfRows": str(cfg.num_of_rows)})
        result = self.client.call(cfg.list_service, cfg.list_operation, params)
        special = self._fatal_or_budget(result.outcome, cfg.list_service, cfg.list_operation)
        if special:
            return special
        wb = part["window_begin"]
        if result.outcome not in DATA_OK or (result.total_count is None and result.outcome != Outcome.NO_DATA):
            attempts = int(part["attempts"]) + 1
            with transaction(self.store.conn):
                self.store.update_partition(self.job.job_id, wb, attempts=attempts, last_outcome=result.outcome.value,
                                            last_error=redact(result.basis)[:500],
                                            status="FAILED" if attempts >= self.max_attempts else "IN_PROGRESS")
            return WORKED
        items = list(result.items or [])
        total = 0 if result.outcome == Outcome.NO_DATA else int(result.total_count or 0)
        response_id = result.source_response_ids[-1] if result.source_response_ids else None
        self.report.list_pages += 1
        with transaction(self.store.conn):
            touched = {no for no in (self.store.upsert_notice(i, response_id) for i in items) if no}
            for no in sorted(touched):
                self.plan_notice(no)
            self.report.notices_seen += len(touched)
            rows = (0 if page == 1 else int(part["rows_received"])) + len(items)
            stored_total = total if page == 1 else part["total_count"]
            restarts = int(part["restarts"])
            finished = total == 0 or rows >= total or len(items) < cfg.num_of_rows
            if page > 1 and stored_total != total:
                self._restart_partition(wb, restarts, f"totalCount 변경 {stored_total} -> {total}")
            elif finished and rows != total:
                self._restart_partition(wb, restarts, f"수신 {rows}행 != totalCount {total}")
            elif finished:
                self.store.update_partition(self.job.job_id, wb, status="DONE", total_count=total, rows_received=rows,
                                            next_page=page, last_outcome=result.outcome.value, last_error=None)
                self.report.partitions_done += 1
            else:
                self.store.update_partition(self.job.job_id, wb, status="IN_PROGRESS", total_count=total,
                                            rows_received=rows, next_page=page + 1, last_outcome=result.outcome.value)
        return WORKED

    def _restart_partition(self, window_begin: str, restarts: int, reason: str) -> None:
        restarts += 1
        status = "FAILED" if restarts > self.cfg.max_partition_restarts else "PENDING"
        self.store.update_partition(self.job.job_id, window_begin, status=status, next_page=1, rows_received=0,
                                    total_count=None, restarts=restarts, last_error=reason)

    # ------------------------------------------------------------------ 공고별 작업 계획

    def plan_notice(self, no: str) -> None:
        """트랜잭션 안에서 호출된다."""
        store, job = self.store, self.job.job_id
        revs = store.notice_revisions(no)
        if not revs:
            return
        state = store.state(no)
        if any("취소" in (r["ntce_kind_nm"] or "") for r in revs):
            if state is None or state["relevance"] != "CANCELLED":
                store.set_state(no, relevance="CANCELLED", relevance_basis="공고 차수 중 취소공고 존재")
            store.skip_open_tasks(job, no, TASK_TYPES, "취소공고")
            return
        latest = revs[-1]["bid_ntce_ord"]
        if state is None:
            store.set_state(no, relevance="PENDING")
        if self.cfg.detail.get("license", True):
            store.conn.execute(
                """UPDATE bf_task SET status = 'SKIPPED', reason = '새 공고차수로 대체', updated_at_utc = datetime('now')
                   WHERE job_id = ? AND task_type IN ('LICENSE', 'REGION') AND bid_ntce_no = ? AND bid_ntce_ord <> ?
                   AND status IN ('PENDING', 'DEFERRED', 'FAILED')""",
                (job, no, latest),
            )
            store.enqueue(job, "LICENSE", no, latest)
        else:
            store.set_state(no, relevance="UNKNOWN", relevance_basis="면허제한 조회 비활성")
            self._enqueue_details(no, latest)

    def _enqueue_details(self, no: str, ord_: str) -> None:
        store, job, d = self.store, self.job.job_id, self.cfg.detail
        if d.get("region", True):
            store.enqueue(job, "REGION", no, ord_)
        if d.get("opening", True):
            revs = store.notice_revisions(no)
            openg = (revs[-1]["openg_dt"] or "")[:10] if revs else ""
            if openg:
                not_before = (date.fromisoformat(openg) + timedelta(days=int(d.get("opening_defer_days", 1)))).isoformat()
                if not_before > self.today().isoformat():
                    store.enqueue(job, "OPENING", no, status="DEFERRED", not_before=not_before, reason="개찰일시 이전")
                    return
            store.enqueue(job, "OPENING", no)

    # ------------------------------------------------------------------ 작업 공통

    def step_task(self, task_type: str, svc: str, op: str, handler: Callable[[Any], str]) -> str:
        task = self.store.next_task(self.job.job_id, task_type, self.today().isoformat(), self.max_attempts)
        if task is None:
            return IDLE
        return handler(task)

    def _collect(self, svc: str, op: str, params: dict[str, str], max_pages: int | None = None) -> PageCollection:
        return collect_all_pages(self.client, svc, op, params, num_of_rows=self.page_size,
                                 max_pages=max_pages or self.max_pages)

    def _incomplete(self, task: Any, col: PageCollection, svc: str, op: str) -> str | None:
        if col.complete:
            return None
        special = self._fatal_or_budget(col.final_outcome, svc, op)
        if special:
            return special
        attempts = int(task["attempts"]) + 1
        with transaction(self.store.conn):
            self.store.update_task(task, status="FAILED", attempts=attempts,
                                   last_outcome=col.final_outcome.value if col.final_outcome else None,
                                   last_error=redact("; ".join(i.code + ": " + i.detail for i in col.issues))[:500])
        self.report.bump(self.report.tasks_failed, task["task_type"])
        return WORKED

    def _done(self, task: Any, col: PageCollection | None, reason: str | None = None) -> None:
        self.store.update_task(task, status="DONE", attempts=int(task["attempts"]) + (1 if col else 0),
                               total_count=col.total_count if col else None, rows_received=len(col.items) if col else None,
                               last_outcome=col.final_outcome.value if col and col.final_outcome else None,
                               last_error=None, reason=reason)
        self.report.bump(self.report.tasks_done, task["task_type"])

    def _skip_if_not_needed(self, task: Any, *, require_latest_ord: bool) -> bool:
        no = task["bid_ntce_no"]
        state = self.store.state(no)
        reason = None
        if state is not None and state["relevance"] == "CANCELLED":
            reason = "취소공고"
        elif state is not None and state["relevance"] == "NOT_RELEVANT" and task["task_type"] != "LICENSE":
            reason = "목표 면허 없음"
        elif require_latest_ord:
            revs = self.store.notice_revisions(no)
            if revs and revs[-1]["bid_ntce_ord"] != task["bid_ntce_ord"]:
                reason = "새 공고차수로 대체"
        if reason:
            with transaction(self.store.conn):
                self.store.update_task(task, status="SKIPPED", reason=reason)
            return True
        return False

    @staticmethod
    def _response_id(col: PageCollection) -> int | None:
        return col.source_response_ids[-1] if col.source_response_ids else None

    # ------------------------------------------------------------------ 작업별 처리

    def do_license(self, task: Any) -> str:
        if self._skip_if_not_needed(task, require_latest_ord=True):
            return WORKED
        no, ord_ = task["bid_ntce_no"], task["bid_ntce_ord"]
        conn = self.store.conn
        fetched = conn.execute("SELECT rows_received FROM bf_license_fetch WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                               (no, ord_)).fetchone()
        col = None
        if fetched is None:
            col = self._collect(SVC_BID, OP_LICENSE, {"inqryDiv": "2", "bidNtceNo": no, "bidNtceOrd": ord_})
            early = self._incomplete(task, col, SVC_BID, OP_LICENSE)
            if early:
                return early
        codes = set(self.cfg.target_license_codes)
        with transaction(conn):
            if col is not None:
                parsed = self.store.replace_license_rows(no, ord_, col.items, self._response_id(col))
                conn.execute(
                    "INSERT OR REPLACE INTO bf_license_fetch (bid_ntce_no, bid_ntce_ord, rows_received, response_id, fetched_at_utc) "
                    "VALUES (?, ?, ?, ?, datetime('now'))", (no, ord_, len(col.items), self._response_id(col)))
            else:
                parsed = [{"code": row[0], "permsn": row[1] or "", "quality": row[2]} for row in conn.execute(
                    "SELECT license_code, permsn_indstryty_list, quality_flag FROM bf_license_limit WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                    (no, ord_))]
            matched = [p for p in parsed if p["code"] in codes or any(f"/{c}]" in p["permsn"] for c in codes)]
            flagged = [p for p in parsed if p["quality"]]
            if matched:
                relevance, basis = "RELEVANT", f"면허제한 코드 일치 {sorted({p['code'] for p in matched if p['code']})}"
            elif not parsed:
                relevance, basis = "UNKNOWN", "면허제한 행 없음"
            elif flagged:
                relevance, basis = "UNKNOWN", "목표 코드 없음, 필드 밀림 의심 행 있음"
            else:
                relevance, basis = "NOT_RELEVANT", "면허제한에 목표 코드 없음"
            self.store.set_state(no, relevance=relevance, relevance_basis=basis, license_ord=ord_)
            self._done(task, col, reason=None if col is not None else "기존 면허제한 조회 결과 재사용(호출 없음)")
            if relevance == "RELEVANT" or (relevance == "UNKNOWN" and self.cfg.detail_for_unknown):
                self._enqueue_details(no, ord_)
            else:
                self.store.skip_open_tasks(self.job.job_id, no, ("REGION", "OPENING", "AWARD", "ROSTER"), basis)
        return WORKED

    def do_region(self, task: Any) -> str:
        if self._skip_if_not_needed(task, require_latest_ord=True):
            return WORKED
        no, ord_ = task["bid_ntce_no"], task["bid_ntce_ord"]
        col = self._collect(SVC_BID, OP_REGION, {"inqryDiv": "2", "bidNtceNo": no, "bidNtceOrd": ord_})
        early = self._incomplete(task, col, SVC_BID, OP_REGION)
        if early:
            return early
        with transaction(self.store.conn):
            self.store.replace_region_rows(no, ord_, col.items, self._response_id(col))
            self.store.set_state(no, region_ord=ord_)
            self._done(task, col)
        return WORKED

    def do_opening(self, task: Any) -> str:
        if self._skip_if_not_needed(task, require_latest_ord=False):
            return WORKED
        no = task["bid_ntce_no"]
        col = self._collect(SVC_AWARD, OP_OPENING, {"inqryDiv": "4", "bidNtceNo": no})
        early = self._incomplete(task, col, SVC_AWARD, OP_OPENING)
        if early:
            return early
        d = self.cfg.detail
        today = self.today()
        with transaction(self.store.conn):
            keys = [k for k in (self.store.upsert_opening(i, self._response_id(col)) for i in col.items) if k]
            if not keys:
                revs = self.store.notice_revisions(no)
                openg = (revs[-1]["openg_dt"] or "")[:10] if revs else ""
                recheck_until = (date.fromisoformat(openg) + timedelta(days=int(d.get("opening_empty_recheck_days", 14)))
                                 if openg else None)
                attempts = int(task["attempts"]) + 1
                if recheck_until and today <= recheck_until and attempts < self.max_attempts:
                    self.store.update_task(task, status="DEFERRED", attempts=attempts, total_count=0, rows_received=0,
                                           not_before_kst=min(recheck_until, today + timedelta(days=3)).isoformat(),
                                           last_outcome=col.final_outcome.value if col.final_outcome else None,
                                           reason="개찰결과 0건, 등록 지연 가능성으로 재확인 예정")
                else:
                    self._done(task, col, reason="개찰결과 0건 확인(참여 0이 아니라 개찰기록 없음)")
                return WORKED
            self._done(task, col)
            if d.get("award", False):
                self.store.enqueue(self.job.job_id, "AWARD", no)
            if d.get("roster", False):
                cap = int(d.get("roster_max_participants", 500))
                for key in keys:
                    unit = self.store.conn.execute(
                        """SELECT progrs_div_cd_nm, prtcpt_cnum FROM bf_opening_unit WHERE bid_ntce_no = ? AND bid_ntce_ord = ?
                           AND bid_clsfc_no = ? AND rbid_no = ?""", key).fetchone()
                    if unit["progrs_div_cd_nm"] != "개찰완료":
                        continue
                    if unit["prtcpt_cnum"] is None or unit["prtcpt_cnum"] > cap:
                        self.store.enqueue(self.job.job_id, "ROSTER", *key, status="SKIPPED",
                                           reason=f"참가업체수 {unit['prtcpt_cnum']} — 상한 {cap} 초과 또는 미상")
                    else:
                        self.store.enqueue(self.job.job_id, "ROSTER", *key)
        return WORKED

    def do_award(self, task: Any) -> str:
        if self._skip_if_not_needed(task, require_latest_ord=False):
            return WORKED
        no = task["bid_ntce_no"]
        col = self._collect(SVC_AWARD, OP_AWARD, {"inqryDiv": "4", "bidNtceNo": no})
        early = self._incomplete(task, col, SVC_AWARD, OP_AWARD)
        if early:
            return early
        with transaction(self.store.conn):
            for item in col.items:
                self.store.upsert_award(item, self._response_id(col))
            self._done(task, col)
        return WORKED

    def do_roster(self, task: Any) -> str:
        key = (task["bid_ntce_no"], task["bid_ntce_ord"], task["bid_clsfc_no"], task["rbid_no"])
        cap = int(self.cfg.detail.get("roster_max_participants", 500))
        col = self._collect(SVC_AWARD, OP_ROSTER,
                            {"bidNtceNo": key[0], "bidNtceOrd": key[1], "bidClsfcNo": key[2], "rbidNo": key[3]},
                            max_pages=math.ceil(cap / self.page_size) + 1)
        early = self._incomplete(task, col, SVC_AWARD, OP_ROSTER)
        if early:
            return early
        with transaction(self.store.conn):
            self.store.replace_roster(key, col.items, self._response_id(col))
            self._done(task, col)
        return WORKED

    # ------------------------------------------------------------------ job 상태

    def update_job_status(self) -> str:
        return refresh_job_status(self.store, self.job.job_id, self.max_attempts)


def refresh_job_status(store: BackfillStore, job_id: str, max_attempts: int) -> str:
    conn = store.conn
    open_parts = conn.execute(
        "SELECT COUNT(*) FROM bf_partition WHERE job_id = ? AND status IN ('PENDING', 'IN_PROGRESS')", (job_id,)).fetchone()[0]
    open_tasks = conn.execute(
        """SELECT COUNT(*) FROM bf_task WHERE job_id = ? AND (status IN ('PENDING', 'DEFERRED')
           OR (status = 'FAILED' AND attempts < ?))""", (job_id, max_attempts)).fetchone()[0]
    if open_parts or open_tasks:
        status = "ACTIVE"
    else:
        gaps = conn.execute(
            """SELECT (SELECT COUNT(*) FROM bf_partition WHERE job_id = ? AND status = 'FAILED')
                    + (SELECT COUNT(*) FROM bf_task WHERE job_id = ? AND status = 'FAILED')""", (job_id, job_id)).fetchone()[0]
        status = "COMPLETED_WITH_GAPS" if gaps else "COMPLETED"
    with transaction(conn):
        conn.execute("UPDATE bf_job SET status = ?, updated_at_utc = datetime('now') WHERE job_id = ?", (status, job_id))
    return status
