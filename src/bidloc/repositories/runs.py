"""실행(api_run)과 검증 단계(verify_step) 기록."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from bidloc import __version__
from bidloc.redaction import redact
from bidloc.timeutil import now_kst, now_utc, to_iso_utc

RUN_STATUSES = {"RUNNING", "COMPLETED", "PARTIAL", "BLOCKED", "SKIPPED", "FAILED", "ABORTED"}
STEP_STATUSES = {"DONE", "DONE_EMPTY", "FAILED", "BLOCKED", "SKIPPED", "NOT_RUN_BUDGET", "NOT_RUN_QUOTA",
                 "NOT_RUN_SERVICE_BLOCKED"}


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{now_kst().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _dumps(value: Any) -> str:
    return redact(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


class RunRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def start(self, *, run_id: str, command: str, data_mode: str, live: bool, status: str, max_calls_run: int,
              catalog_sha256: str | None, notes: dict[str, Any] | None = None,
              resumed_from_run_id: str | None = None) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(status)
        self._conn.execute(
            """
            INSERT INTO api_run (run_id, command, data_mode, live, status, started_at_utc, max_calls_run,
                                 calls_attempted, stop_reason, resumed_from_run_id, code_version, catalog_sha256, notes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?)
            """,
            (run_id, command, data_mode, 1 if live else 0, status, to_iso_utc(now_utc()), max_calls_run,
             resumed_from_run_id, __version__, catalog_sha256, _dumps(notes or {})),
        )

    def finish(self, run_id: str, *, status: str, calls_attempted: int, stop_reason: str | None,
               notes: dict[str, Any] | None = None) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(status)
        self._conn.execute(
            """
            UPDATE api_run SET status = ?, finished_at_utc = ?, calls_attempted = ?, stop_reason = ?,
                   notes_json = COALESCE(?, notes_json)
            WHERE run_id = ?
            """,
            (status, to_iso_utc(now_utc()), calls_attempted, redact(stop_reason) if stop_reason else None,
             _dumps(notes) if notes is not None else None, run_id),
        )

    def get(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM api_run WHERE run_id = ?", (run_id,)).fetchone()

    def record_step(self, *, run_id: str, step_key: str, status: str, sample_key: str | None = None,
                    service_id: str | None = None, operation: str | None = None, calls_used: int = 0,
                    summary: dict[str, Any] | None = None) -> None:
        if status not in STEP_STATUSES:
            raise ValueError(status)
        now = to_iso_utc(now_utc())
        self._conn.execute(
            """
            INSERT INTO verify_step (run_id, step_key, sample_key, service_id, operation, status, calls_used,
                                     summary_json, started_at_utc, finished_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, step_key) DO UPDATE SET
                status = excluded.status, calls_used = excluded.calls_used,
                summary_json = excluded.summary_json, finished_at_utc = excluded.finished_at_utc
            """,
            (run_id, step_key, sample_key, service_id, operation, status, calls_used,
             _dumps(summary or {}), now, now),
        )

    def steps(self, run_id: str) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM verify_step WHERE run_id = ? ORDER BY id", (run_id,)))

    def done_steps(self, run_id: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in self._conn.execute(
            "SELECT step_key, summary_json FROM verify_step WHERE run_id = ? AND status IN ('DONE', 'DONE_EMPTY')",
            (run_id,),
        ):
            out[row["step_key"]] = json.loads(row["summary_json"] or "{}")
        return out
