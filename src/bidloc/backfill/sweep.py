"""기간 스윕 수집기.

공고마다 상세 API를 1회씩 부르는 대신, 각 API의 기간 조회로 하루치를 한 번에 받는다.
2026-09-20 실측(tools/probe_period_query.py): 하루 행수 목록 884, 면허제한 2,668, 참가가능지역 1,634,
개찰결과 753. numOfRows는 999까지 실제로 반환됐다. 3년 기준 입찰공고정보서비스 약 6,200회,
낙찰정보서비스 약 1,100회로, 공고당 1회 방식(약 76,000회)보다 훨씬 적다.

설계
- 단계별(LIST/LICENSE/REGION/OPENING)로 하루 단위 파티션을 만들고, 페이지 커서를 저장해 이어받는다.
- 목록은 업종 필터 없이 받는다. 면허제한도 전 공고를 받으므로 4992 해당 여부를 표본이 아니라 직접 판정한다.
- 행은 기존 bf_* 테이블에 누적한다. 같은 키의 행은 덮어쓰되 이전 값이 다르면 bf_record_conflict에 남긴다.
- 관련성(RELEVANT)은 면허제한 행에 목표 코드가 보이면 그 자리에서 표시하고,
  '없음'(NOT_RELEVANT) 판정은 면허 단계가 끝난 뒤 sweep-finalize에서 한 번에 한다(부분 수집 중 오판 방지).

한계
- 기간 조회 기준 필드는 API마다 다르다(목록: 공고게시일시, 면허·지역: 등록일시, 개찰: 개찰일시).
  범위 경계 밖에 등록된 행은 그 단계 범위에 안 들어올 수 있다. 경계는 하루 겹침으로 완화한다.
- 하루 행수는 날짜마다 다르다. 위 추정치는 2025-06-10 한 날 기준이다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from bidloc.backfill.plan import BackfillConfig
from bidloc.backfill.store import BackfillStore, transaction
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import DATA_OK, SERVICE_FATAL, Outcome
from bidloc.redaction import redact
from bidloc.timeutil import KST, format_inqry_datetime, now_utc, to_iso_utc

SVC_BID, SVC_AWARD = "bid_notice", "bid_award"
WORKED, IDLE, BUDGET, STOP = "WORKED", "IDLE", "BUDGET", "STOP"

#: 단계 정의 — (단계, 서비스, 오퍼레이션, 고정 파라미터, 기간 기준 설명)
STAGES: list[tuple[str, str, str, dict[str, str], str]] = [
    ("LIST", SVC_BID, "getBidPblancListInfoCnstwkPPSSrch", {"inqryDiv": "1"}, "공고게시일시"),
    ("LICENSE", SVC_BID, "getBidPblancListInfoLicenseLimit", {"inqryDiv": "1"}, "등록일시"),
    ("REGION", SVC_BID, "getBidPblancListInfoPrtcptPsblRgn", {"inqryDiv": "1"}, "등록일시"),
    ("OPENING", SVC_AWARD, "getOpengResultListInfoCnstwk", {"inqryDiv": "3"}, "개찰일시"),
]


def _now() -> str:
    return to_iso_utc(now_utc())


@dataclass(frozen=True)
class SweepConfig:
    job_name: str
    begin: date
    end: date
    num_of_rows: int
    window_days: int
    max_attempts: int
    max_restarts: int
    stages: tuple[str, ...]
    target_codes: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def job_id(self) -> str:
        return f"{self.job_name}:{self.begin}:{self.end}"

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def stage_specs(self) -> list[tuple[str, str, str, dict[str, str]]]:
        return [(n, svc, op, params) for n, svc, op, params, _ in STAGES if n in self.stages]


def load_sweep_config(cfg: BackfillConfig, today: date | None = None) -> SweepConfig:
    raw = dict(cfg.raw.get("sweep") or {})
    begin, end = cfg.range_for_new_job(today)
    stages = tuple(str(s) for s in (raw.get("stages") or [n for n, *_ in STAGES]))
    unknown = set(stages) - {n for n, *_ in STAGES}
    if unknown:
        raise ValueError(f"알 수 없는 sweep 단계: {sorted(unknown)}")
    return SweepConfig(
        job_name=str(raw.get("job_name") or f"{cfg.job_name}-sweep"),
        begin=begin, end=end,
        num_of_rows=int(raw.get("num_of_rows", 999)),
        window_days=int(raw.get("window_days", 1)),
        max_attempts=int(raw.get("max_attempts", 3)),
        max_restarts=int(raw.get("max_restarts", 3)),
        stages=stages,
        target_codes=cfg.target_license_codes,
        raw={"sweep": raw, "range": [begin.isoformat(), end.isoformat()]},
    )


def day_windows(cfg: SweepConfig) -> list[tuple[str, str]]:
    """하루(또는 window_days) 단위 구간. 끝은 다음 구간 시작과 1분 겹쳐 경계 누락을 막는다."""
    out = []
    cur = cfg.begin
    while cur <= cfg.end:
        stop = min(cur + timedelta(days=cfg.window_days), cfg.end + timedelta(days=1))
        begin_dt = datetime(cur.year, cur.month, cur.day, tzinfo=KST)
        end_dt = datetime(stop.year, stop.month, stop.day, tzinfo=KST)
        out.append((format_inqry_datetime(begin_dt), format_inqry_datetime(end_dt)))
        cur = stop
    return out


class SweepStore:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.rows = BackfillStore(conn)

    def ensure_job(self, cfg: SweepConfig) -> str:
        with transaction(self.conn):
            self.conn.execute(
                """INSERT OR IGNORE INTO sw_job (job_id, job_name, range_begin_kst, range_end_kst, config_json,
                   config_sha256, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)""",
                (cfg.job_id, cfg.job_name, cfg.begin.isoformat(), cfg.end.isoformat(),
                 json.dumps(cfg.raw, ensure_ascii=False, sort_keys=True), cfg.sha256(), _now(), _now()))
            for stage, *_ in cfg.stage_specs():
                for wb, we in day_windows(cfg):
                    self.conn.execute(
                        """INSERT OR IGNORE INTO sw_partition (job_id, stage, window_begin, window_end, status,
                           updated_at_utc) VALUES (?, ?, ?, ?, 'PENDING', ?)""", (cfg.job_id, stage, wb, we, _now()))
        return cfg.job_id

    def next_partition(self, job_id: str, stage: str, max_attempts: int):
        return self.conn.execute(
            """SELECT * FROM sw_partition WHERE job_id = ? AND stage = ?
               AND (status IN ('PENDING', 'IN_PROGRESS') OR (status = 'FAILED' AND attempts < ?))
               ORDER BY window_begin LIMIT 1""", (job_id, stage, max_attempts)).fetchone()

    def update_partition(self, job_id: str, stage: str, window_begin: str, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE sw_partition SET {sets}, updated_at_utc = ? WHERE job_id = ? AND stage = ? "
                          f"AND window_begin = ?", (*fields.values(), _now(), job_id, stage, window_begin))

    def progress(self, job_id: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for stage, status, count in self.conn.execute(
                "SELECT stage, status, COUNT(*) FROM sw_partition WHERE job_id = ? GROUP BY stage, status", (job_id,)):
            out.setdefault(stage, {})[status] = count
        return out

    def rows_received(self, job_id: str) -> dict[str, int]:
        return {stage: total or 0 for stage, total in self.conn.execute(
            "SELECT stage, SUM(rows_received) FROM sw_partition WHERE job_id = ? GROUP BY stage", (job_id,))}


@dataclass
class SweepReport:
    pages: dict[str, int]
    rows: dict[str, int]
    partitions_done: dict[str, int]
    closed: dict[str, str]
    stop_reason: str | None = None
    job_status: str = "ACTIVE"


class SweepRunner:
    def __init__(self, *, client: Any, budget: OperationBudget, store: SweepStore, cfg: SweepConfig,
                 job_id: str, on_page: Callable[[str, int], None] | None = None) -> None:
        self.client, self.budget, self.store, self.cfg, self.job_id = client, budget, store, cfg, job_id
        self.report = SweepReport(pages={}, rows={}, partitions_done={}, closed={})
        self.on_page = on_page

    # ------------------------------------------------------------------ 루프

    def run(self) -> SweepReport:
        """서비스마다 자기 단계 목록을 순서대로(목록 → 면허 → 지역) 처리한다.

        서비스끼리는 일일 한도가 따로라 번갈아 진행한다. 한 서비스가 한도에 닿아도 다른 서비스는 계속한다.
        """
        by_service: dict[str, list[tuple[str, str, str, dict[str, str]]]] = {}
        for stage, svc, op, params in self.cfg.stage_specs():
            by_service.setdefault(svc, []).append((stage, svc, op, params))
        closed: set[str] = set()
        while self.report.stop_reason is None:
            progressed = False
            for svc, specs in by_service.items():
                for stage, _svc, op, params in specs:
                    if stage in closed:
                        continue
                    if self.store.next_partition(self.job_id, stage, self.cfg.max_attempts) is None:
                        closed.add(stage)
                        continue
                    if self.budget.remaining(svc, op) <= 0:
                        closed.add(stage)
                        self.report.closed[f"{svc}.{op}"] = self.budget.exhaustion_reason(svc, op)
                        continue
                    result = self.step(stage, svc, op, params)
                    if result == WORKED:
                        progressed = True
                    elif result == BUDGET:
                        closed.add(stage)
                        self.report.closed[f"{svc}.{op}"] = self.budget.exhaustion_reason(svc, op)
                        continue  # 같은 서비스의 다음 단계도 같은 예산이라 곧 닫힌다
                    elif result == IDLE:
                        closed.add(stage)  # 이 단계는 남은 파티션이 없다 — 다음 단계로 넘어간다
                        continue
                    break  # 이 서비스는 이번 차례 끝. 다음 서비스로
                if self.report.stop_reason:
                    break
            if not progressed:
                break
        self.report.job_status = self.refresh_status()
        return self.report

    def _bump(self, bucket: dict[str, int], key: str, n: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + n

    # ------------------------------------------------------------------ 한 페이지

    def step(self, stage: str, svc: str, op: str, extra: dict[str, str]) -> str:
        part = self.store.next_partition(self.job_id, stage, self.cfg.max_attempts)
        if part is None:
            return IDLE
        page = int(part["next_page"])
        params = {**extra, "inqryBgnDt": part["window_begin"], "inqryEndDt": part["window_end"],
                  "pageNo": str(page), "numOfRows": str(self.cfg.num_of_rows)}
        result = self.client.call(svc, op, params)
        if result.outcome not in DATA_OK:
            with transaction(self.store.conn):
                self.store.update_partition(self.job_id, stage, part["window_begin"],
                                            last_outcome=result.outcome.value, last_error=redact(result.basis)[:500])
        if result.outcome in (Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY):
            return BUDGET
        if result.outcome == Outcome.QUOTA_DAILY_EXCEEDED:
            self.budget.mark_quota_exhausted(svc, op)
            return BUDGET
        if result.outcome in SERVICE_FATAL or result.outcome == Outcome.IP_NOT_ALLOWED:
            self.report.stop_reason = f"{svc}.{op}: {result.outcome.value} — 인증·권한·IP 문제로 수집 중단"
            return STOP

        wb = part["window_begin"]
        if result.outcome not in DATA_OK or (result.total_count is None and result.outcome != Outcome.NO_DATA):
            attempts = int(part["attempts"]) + 1
            with transaction(self.store.conn):
                self.store.update_partition(self.job_id, stage, wb, attempts=attempts,
                                            last_outcome=result.outcome.value, last_error=redact(result.basis)[:500],
                                            status="FAILED" if attempts >= self.cfg.max_attempts else "IN_PROGRESS")
            return WORKED

        items = list(result.items or [])
        total = 0 if result.outcome == Outcome.NO_DATA else int(result.total_count or 0)
        response_id = result.source_response_ids[-1] if result.source_response_ids else None
        self._bump(self.report.pages, stage)
        with transaction(self.store.conn):
            rows = (0 if page == 1 else int(part["rows_received"])) + len(items)
            stored_total = total if page == 1 else part["total_count"]
            finished = total == 0 or rows >= total or len(items) < self.cfg.num_of_rows
            fingerprint = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            repeated = items and self.store.conn.execute(
                "SELECT 1 FROM sw_page WHERE job_id=? AND stage=? AND window_begin=? AND item_hash=? AND page_no != ?",
                (self.job_id, stage, wb, fingerprint, page)).fetchone()
            keys = {"LIST": ("bidNtceNo", "bidNtceOrd"),
                    "LICENSE": ("bidNtceNo", "bidNtceOrd", "lmtGrpNo", "lmtSno"),
                    "REGION": ("bidNtceNo", "bidNtceOrd", "lmtSno"),
                    "OPENING": ("bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo")}[stage]
            reason = None
            if any(any(item.get(k) in (None, "") for k in keys) for item in items):
                reason = "복합키 필드 누락 (원본 보존, 페이지 확정 불가)"
            elif result.page_no is not None and int(result.page_no) != page:
                reason = "요청 pageNo와 응답 pageNo 불일치"
            elif len(items) > self.cfg.num_of_rows:
                reason = "응답 행 수가 요청 numOfRows 초과"
            elif repeated:
                reason = "이전 페이지와 동일한 응답 반복"
            elif page > 1 and stored_total != total:
                reason = f"totalCount 변경 {stored_total} -> {total}"
            elif finished and rows != total:
                reason = f"수신 {rows}행 != totalCount {total}"
            if reason:
                self._restart(stage, wb, int(part["restarts"]), reason)
                return WORKED
            self.store_rows(stage, items, response_id)
            self.store.conn.execute("INSERT OR REPLACE INTO sw_page VALUES (?, ?, ?, ?, ?, ?)",
                                    (self.job_id, stage, wb, page, fingerprint, response_id))
            self._bump(self.report.rows, stage, len(items))
            if finished:
                self.store.update_partition(self.job_id, stage, wb, status="DONE", rows_received=rows,
                                            total_count=total, next_page=page, last_outcome=result.outcome.value,
                                            last_error=None)
                self._bump(self.report.partitions_done, stage)
            else:
                self.store.update_partition(self.job_id, stage, wb, status="IN_PROGRESS", rows_received=rows,
                                            total_count=stored_total, next_page=page + 1,
                                            last_outcome=result.outcome.value)
        if self.on_page:
            self.on_page(stage, len(items))
        return WORKED

    def _restart(self, stage: str, wb: str, restarts: int, reason: str) -> None:
        """페이지 도중 총건수가 흔들리면 그 구간을 1페이지부터 다시 받는다(최대 max_restarts)."""
        self.store.conn.execute("DELETE FROM sw_page WHERE job_id=? AND stage=? AND window_begin=?",
                                (self.job_id, stage, wb))
        if restarts + 1 > self.cfg.max_restarts:
            self.store.update_partition(self.job_id, stage, wb, status="FAILED", last_error=f"재시작 한도 초과: {reason}",
                                        attempts=self.cfg.max_attempts, restarts=restarts + 1)
            return
        self.store.update_partition(self.job_id, stage, wb, status="IN_PROGRESS", next_page=1, rows_received=0,
                                    total_count=None, restarts=restarts + 1, last_error=reason[:500])

    # ------------------------------------------------------------------ 저장

    def store_rows(self, stage: str, items: list[dict], response_id: int | None) -> None:
        store = self.store.rows
        if stage == "LIST":
            for item in items:
                no = store.upsert_notice(item, response_id)
                if no and "취소" in (item.get("ntceKindNm") or ""):
                    store.set_state(no, relevance="CANCELLED", relevance_basis="목록 ntceKindNm 취소")
        elif stage == "LICENSE":
            for index, item in enumerate(items):
                hit = store.insert_license_row(item, response_id, index)
                if not hit:
                    continue
                no, ord_, code, permsn = hit
                if set(self.cfg.target_codes) & ({code} | set(re.findall(r"\d+", permsn))):
                    state = store.state(no)
                    if state is None or state["relevance"] != "CANCELLED":
                        store.set_state(no, relevance="RELEVANT", license_ord=ord_,
                                        relevance_basis=f"면허제한에 {','.join(self.cfg.target_codes)} 있음(기간 스윕)")
        elif stage == "REGION":
            for index, item in enumerate(items):
                store.insert_region_row(item, response_id, index)
                # region_ord는 행마다 갱신하지 않고 sweep-finalize에서 한 번에 채운다(행 수가 많다).
        elif stage == "OPENING":
            for item in items:
                store.upsert_opening(item, response_id)

    # ------------------------------------------------------------------ 상태

    def refresh_status(self) -> str:
        rows = self.store.conn.execute(
            "SELECT status, COUNT(*) FROM sw_partition WHERE job_id = ? GROUP BY status", (self.job_id,)).fetchall()
        counts = {r[0]: r[1] for r in rows}
        status = "COMPLETED" if set(counts) <= {"DONE"} and counts.get("DONE") else "ACTIVE"
        with transaction(self.store.conn):
            self.store.conn.execute("UPDATE sw_job SET status = ?, updated_at_utc = ? WHERE job_id = ?",
                                    (status, _now(), self.job_id))
        return status


def _target_notices(conn, codes: tuple[str, ...]) -> set[str]:
    """목표 면허코드를 가진 공고 집합. 코드 컬럼은 정확히 비교하고, 허용업종목록은 숫자 토큰으로 끊어서 비교한다."""
    place = ",".join("?" * len(codes))
    hit = {r[0] for r in conn.execute(
        f"SELECT DISTINCT bid_ntce_no FROM bf_license_limit WHERE license_code IN ({place})", codes)}
    like = " OR ".join(["permsn_indstryty_list LIKE ?"] * len(codes))
    for no, permsn in conn.execute(
            f"SELECT bid_ntce_no, permsn_indstryty_list FROM bf_license_limit WHERE {like}",
            [f"%{c}%" for c in codes]):
        tokens = set(re.findall(r"\d+", permsn or ""))
        if tokens & set(codes):   # '49920' 같은 부분일치는 제외한다
            hit.add(no)
    return hit


def extend_job(conn, cfg: SweepConfig, job_id: str, new_end: date, *, recollect_last: int = 2) -> dict[str, int]:
    """수집 범위 끝을 미뤄서 새 날짜 파티션을 만든다(API 호출 없음).

    3개년 수집이 끝난 뒤 매일 새 공고를 이어받는 용도다.
    마지막 recollect_last일치는 하루가 끝나기 전에 받았을 수 있어 PENDING으로 되돌려 다시 받는다
    (행은 키로 덮어써지므로 중복이 생기지 않는다).
    """
    row = conn.execute("SELECT range_begin_kst, range_end_kst FROM sw_job WHERE job_id = ?", (job_id,)).fetchone()
    old_end = date.fromisoformat(row["range_end_kst"])
    if new_end < old_end or recollect_last < 0:
        raise ValueError("범위를 축소하거나 음수 recollect_last를 지정할 수 없다")
    added = 0
    with transaction(conn):
        if new_end > old_end:
            span = SweepConfig(**{**cfg.__dict__, "begin": old_end + timedelta(days=1), "end": new_end})
            for stage, *_ in cfg.stage_specs():
                for wb, we in day_windows(span):
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO sw_partition (job_id, stage, window_begin, window_end, status,
                           updated_at_utc) VALUES (?, ?, ?, ?, 'PENDING', ?)""", (job_id, stage, wb, we, _now()))
                    added += cur.rowcount
            conn.execute("UPDATE sw_job SET range_end_kst = ?, status = 'ACTIVE', updated_at_utc = ? WHERE job_id = ?",
                         (new_end.isoformat(), _now(), job_id))
        reset = 0
        if recollect_last > 0:
            cutoff = (new_end - timedelta(days=recollect_last - 1)).strftime("%Y%m%d")
            cur = conn.execute(
                """UPDATE sw_partition SET status = 'PENDING', next_page = 1, rows_received = 0, total_count = NULL,
                   attempts=0, restarts=0, last_outcome=NULL, last_error=NULL,
                   updated_at_utc = ? WHERE job_id = ? AND status = 'DONE' AND substr(window_begin, 1, 8) >= ?""",
                (_now(), job_id, cutoff))
            reset = cur.rowcount
            conn.execute("DELETE FROM sw_page WHERE job_id=? AND substr(window_begin,1,8)>=?", (job_id, cutoff))
        if added or reset:
            conn.execute("UPDATE sw_job SET status='ACTIVE', updated_at_utc=? WHERE job_id=?", (_now(), job_id))
    return {"added_partitions": added, "recollect_partitions": reset,
            "range_end": max(new_end, old_end).isoformat()}


def finalize_relevance(conn, cfg: SweepConfig, *, license_complete: bool = True) -> dict[str, int]:
    from bidloc.backfill.relevance import finalize
    return finalize(conn, cfg.target_codes, license_complete=license_complete)
