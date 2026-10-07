"""호출예산.

- 실행 예산: 한 프로세스 실행 안에서 실제 HTTP 시도 수(재시도 포함)를 센다.
- 일일 예산: SQLite request_budget_daily 테이블에 KST 날짜별로 누적한다.
  BEGIN IMMEDIATE로 예약하므로 프로세스 재시작·동시 실행에서도 한도를 넘지 않는다.
주의: 이 한도는 내부 안전값이며 제공기관 승인 쿼터가 아니다. 예약은 전송 전에 하며, 전송 실패도 사용량에 포함한다.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from bidloc.timeutil import kst_today, now_utc, to_iso_utc


class BudgetExhausted(Exception):
    def __init__(self, scope: str, used: int, limit: int) -> None:
        super().__init__(f"{scope} 호출예산 소진 ({used}/{limit})")
        self.scope = scope
        self.used = used
        self.limit = limit


@dataclass(frozen=True)
class Reservation:
    budget_day_kst: str
    run_used: int
    day_used: int


class CallBudget:
    def __init__(
        self,
        db_path: Path,
        *,
        max_per_run: int,
        max_per_day: int,
        today: Callable[[], date] = kst_today,
    ) -> None:
        if max_per_run < 0 or max_per_day < 0:
            raise ValueError("예산은 0 이상이어야 한다")
        self._db_path = Path(db_path)
        self.max_per_run = max_per_run
        self.max_per_day = max_per_day
        self._today = today
        self._lock = threading.Lock()
        self.run_used = 0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def day_used(self, day: date | None = None) -> int:
        key = (day or self._today()).isoformat()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT calls_reserved FROM request_budget_daily WHERE budget_day_kst = ?", (key,)
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def remaining(self) -> tuple[int, int]:
        return max(0, self.max_per_run - self.run_used), max(0, self.max_per_day - self.day_used())

    def reserve(self, service_id: str | None = None, operation: str | None = None) -> Reservation:
        """전체 호출 1회 예약. service_id·operation은 오퍼레이션별 예산과 같은 인터페이스를 위해 받기만 한다."""
        with self._lock:
            if self.run_used >= self.max_per_run:
                raise BudgetExhausted("run", self.run_used, self.max_per_run)
            key = self._today().isoformat()
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT calls_reserved FROM request_budget_daily WHERE budget_day_kst = ?", (key,)
                    ).fetchone()
                    used = int(row[0]) if row else 0
                    if used >= self.max_per_day:
                        conn.execute("ROLLBACK")
                        raise BudgetExhausted("day", used, self.max_per_day)
                    conn.execute(
                        """
                        INSERT INTO request_budget_daily (budget_day_kst, calls_reserved, daily_limit_last_seen, updated_at_utc)
                        VALUES (?, 1, ?, ?)
                        ON CONFLICT(budget_day_kst) DO UPDATE SET
                            calls_reserved = calls_reserved + 1,
                            daily_limit_last_seen = excluded.daily_limit_last_seen,
                            updated_at_utc = excluded.updated_at_utc
                        """,
                        (key, self.max_per_day, to_iso_utc(now_utc())),
                    )
                    conn.execute("COMMIT")
                except BudgetExhausted:
                    raise
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()
            self.run_used += 1
            return Reservation(budget_day_kst=key, run_used=self.run_used, day_used=used + 1)


def kst_day_utc_bounds(day: date) -> tuple[str, str]:
    """KST 하루를 source_response.requested_at_utc 문자열 비교용 UTC 경계로 바꾼다."""
    from datetime import datetime, timedelta, timezone

    from bidloc.timeutil import KST

    start = datetime(day.year, day.month, day.day, tzinfo=KST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


class OperationBudget:
    """일일 호출예산(기본: OpenAPI 서비스 단위 합산).

    포털 표기는 서비스 단위("신청 가능 트래픽 개발계정 1,000")다. 참고자료 오류코드 22 설명에 "서비스 상세기능별
    일일 트래픽"이라는 문구가 있지만 게이트웨이가 오퍼레이션별로 독립 할당한다는 실측 증거는 없다.
    그래서 기본 scope="service"로 같은 서비스의 모든 오퍼레이션 호출을 하나의 예산으로 합산한다.
    scope="operation"은 오퍼레이션별 독립 한도가 실제로 확인된 경우에만 쓴다.

    사용량 = max(source_response에 기록된 그날 실제 시도 수, 이 테이블의 예약 카운터).
    - source_response를 세므로 verify-api·backfill·recall 등 모든 명령의 호출이 합산된다.
    - 예약 카운터는 BEGIN IMMEDIATE로 올려 동시 실행에서도 한도를 넘지 않게 한다.
    - 재시도·전송 실패도 source_response에 시도로 남으므로 사용량이다.
    """

    SERVICE_ROW = "*"

    def __init__(
        self,
        db_path: Path,
        *,
        default_limit: int = 800,
        limits: dict[str, int] | None = None,
        max_per_run: int | None = None,
        scope: str = "service",
        today: Callable[[], date] = kst_today,
    ) -> None:
        if default_limit < 0 or (max_per_run is not None and max_per_run < 0):
            raise ValueError("예산은 0 이상이어야 한다")
        if scope not in ("service", "operation"):
            raise ValueError("scope는 service 또는 operation")
        self._db_path = Path(db_path)
        self.default_limit = default_limit
        self.limits = dict(limits or {})
        self.max_per_run = max_per_run
        self.scope = scope
        self._today = today
        self._lock = threading.Lock()
        self.run_used = 0
        self.run_used_by_op: dict[str, int] = {}

    def _row_key(self, operation: str) -> str:
        return self.SERVICE_ROW if self.scope == "service" else operation

    def limit_for(self, service_id: str, operation: str) -> int:
        if self.scope == "service":
            return int(self.limits.get(service_id, self.default_limit))
        return int(self.limits.get(f"{service_id}.{operation}", self.limits.get(operation, self.default_limit)))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _usage(self, conn: sqlite3.Connection, day: date, service_id: str, operation: str) -> tuple[int, str | None]:
        key = day.isoformat()
        row_op = self._row_key(operation)
        start, end = kst_day_utc_bounds(day)
        if self.scope == "service":
            recorded = conn.execute(
                "SELECT COUNT(*) FROM source_response WHERE service_id = ? AND requested_at_utc >= ? AND requested_at_utc < ?",
                (service_id, start, end)).fetchone()[0]
        else:
            recorded = conn.execute(
                "SELECT COUNT(*) FROM source_response WHERE service_id = ? AND operation = ? "
                "AND requested_at_utc >= ? AND requested_at_utc < ?",
                (service_id, operation, start, end)).fetchone()[0]
        row = conn.execute(
            "SELECT calls_reserved, quota_exhausted_at_utc FROM api_daily_usage "
            "WHERE budget_day_kst = ? AND service_id = ? AND operation = ?",
            (key, service_id, row_op)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO api_daily_usage (budget_day_kst, service_id, operation, calls_reserved, seeded_calls, updated_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, service_id, row_op, int(recorded), int(recorded), to_iso_utc(now_utc())))
            return int(recorded), None
        return max(int(recorded), int(row[0])), row[1]

    def used_today(self, service_id: str, operation: str) -> tuple[int, bool]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            used, exhausted = self._usage(conn, self._today(), service_id, operation)
            conn.execute("COMMIT")
            return used, exhausted is not None
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def remaining(self, service_id: str, operation: str) -> int:
        used, exhausted = self.used_today(service_id, operation)
        if exhausted:
            return 0
        left = max(0, self.limit_for(service_id, operation) - used)
        if self.max_per_run is not None:
            left = min(left, max(0, self.max_per_run - self.run_used))
        return left

    def exhaustion_reason(self, service_id: str, operation: str) -> str:
        if self.max_per_run is not None and self.run_used >= self.max_per_run:
            return "실행 상한 도달 (BUDGET_EXHAUSTED_RUN)"
        _, exhausted = self.used_today(service_id, operation)
        if exhausted:
            return "제공기관 일일 한도 초과 (QUOTA_DAILY_EXCEEDED)"
        return "오늘 서비스 한도 소진 (BUDGET_EXHAUSTED_DAY)"

    def reserve(self, service_id: str | None = None, operation: str | None = None) -> Reservation:
        if not service_id or not operation:
            raise ValueError("OperationBudget.reserve에는 service_id와 operation이 필요하다")
        with self._lock:
            if self.max_per_run is not None and self.run_used >= self.max_per_run:
                raise BudgetExhausted("run", self.run_used, self.max_per_run)
            day = self._today()
            limit = self.limit_for(service_id, operation)
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    used, exhausted = self._usage(conn, day, service_id, operation)
                    if exhausted is not None or used >= limit:
                        conn.execute("COMMIT")
                        raise BudgetExhausted("day", used, limit)
                    conn.execute(
                        "UPDATE api_daily_usage SET calls_reserved = ?, updated_at_utc = ? "
                        "WHERE budget_day_kst = ? AND service_id = ? AND operation = ?",
                        (used + 1, to_iso_utc(now_utc()), day.isoformat(), service_id, self._row_key(operation)))
                    conn.execute("COMMIT")
                except BudgetExhausted:
                    raise
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()
            self.run_used += 1
            op_key = f"{service_id}.{operation}"
            self.run_used_by_op[op_key] = self.run_used_by_op.get(op_key, 0) + 1
            return Reservation(budget_day_kst=day.isoformat(), run_used=self.run_used, day_used=used + 1)

    def mark_quota_exhausted(self, service_id: str, operation: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._usage(conn, self._today(), service_id, operation)
            conn.execute(
                "UPDATE api_daily_usage SET quota_exhausted_at_utc = ?, updated_at_utc = ? "
                "WHERE budget_day_kst = ? AND service_id = ? AND operation = ?",
                (to_iso_utc(now_utc()), to_iso_utc(now_utc()), self._today().isoformat(), service_id,
                 self._row_key(operation)))
            conn.execute("COMMIT")
        finally:
            conn.close()
