"""마이그레이션, 원본 저장, 실행 기록."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bidloc.clients.envelope import parse_body
from bidloc.redaction import MASK, REGISTRY
from bidloc.repositories.db import MigrationError, apply_migrations, connect, migration_status, open_database
from bidloc.repositories.raw_store import RawStoreError, ResponseRecorder
from bidloc.repositories.runs import RunRepository
from tests.conftest import FAKE_KEY

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migrations_apply_once_and_enable_foreign_keys(tmp_path: Path):
    db = tmp_path / "m.sqlite3"
    conn = connect(db)
    assert apply_migrations(conn, REPO_ROOT / "migrations") == [
        "0001_p0_core", "0002_backfill", "0003_recall_study",
        "0004_notice_quality", "0005_sweep", "0006_stats_indexes", "0007_sweep_page", "0008_analysis_indexes",
    ]
    assert apply_migrations(conn, REPO_ROOT / "migrations") == []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"api_run", "request_budget_daily", "source_response", "verify_step", "verify_notice_revision",
            "verify_opening_unit", "schema_migrations", "bf_notice_revision", "sw_partition"} <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO verify_step (run_id, step_key, status) VALUES ('missing-run', 'x', 'DONE')")
    conn.close()


def test_checksum_mismatch_detected(tmp_path: Path):
    mig = tmp_path / "migrations"
    shutil.copytree(REPO_ROOT / "migrations", mig)
    conn = connect(tmp_path / "c.sqlite3")
    apply_migrations(conn, mig)
    (mig / "0001_p0_core.sql").write_text((mig / "0001_p0_core.sql").read_text(encoding="utf-8") + "\n-- tampered\n",
                                          encoding="utf-8")
    assert migration_status(conn, mig).checksum_mismatch == ["0001_p0_core"]
    with pytest.raises(MigrationError):
        apply_migrations(conn, mig)
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path):
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "0001_bad.sql").write_text("CREATE TABLE ok_table (id INTEGER);\nTHIS IS NOT SQL;", encoding="utf-8")
    conn = connect(tmp_path / "b.sqlite3")
    with pytest.raises(MigrationError):
        apply_migrations(conn, mig)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables
    assert migration_status(conn, mig).pending == ["0001_bad"]
    conn.close()


def test_check_constraints_reject_negative_counts(tmp_path: Path):
    conn = open_database(tmp_path / "x.sqlite3", REPO_ROOT / "migrations")
    RunRepository(conn).start(run_id="r1", command="t", data_mode="real", live=False, status="SKIPPED", max_calls_run=0,
                              catalog_sha256=None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO verify_opening_unit (run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, "
                     "official_prtcpt_cnum, official_prtcpt_cnum_status, roster_status, link_status) "
                     "VALUES ('r1','R99BK1','000','0','000', -1, 'OBSERVED', 'NOT_QUERIED', 'NOT_LINKED')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO api_run (run_id, command, data_mode, live, status, started_at_utc, max_calls_run, code_version) "
                     "VALUES ('r2','t','prod',0,'SKIPPED','x',0,'v')")
    conn.close()


def test_raw_store_redacts_and_stays_inside_dir(tmp_path: Path):
    REGISTRY.register(FAKE_KEY)
    conn = open_database(tmp_path / "r.sqlite3", REPO_ROOT / "migrations")
    recorder = ResponseRecorder(conn, tmp_path / "raw", "real")
    body = f'{{"response":{{"header":{{"resultCode":"00","resultMsg":"{FAKE_KEY}"}}}}}}'.encode()
    stored = recorder.store_body(service_id="bid_notice", operation="getBidPblancListInfoCnstwk",
                                 requested_at=datetime(2099, 1, 1, tzinfo=timezone.utc), attempt_no=1, body=body, fmt="json")
    path = tmp_path / "raw" / stored.relative_path
    assert path.is_file() and stored.redaction_applied
    assert FAKE_KEY.encode() not in path.read_bytes() and MASK.encode() in path.read_bytes()
    assert stored.relative_path.startswith("2099-01-01/bid_notice/getBidPblancListInfoCnstwk/")
    with pytest.raises(RawStoreError):
        recorder.store_body(service_id="../evil", operation="x", requested_at=datetime.now(timezone.utc), attempt_no=1,
                            body=b"x", fmt="json")
    conn.close()


def test_recorder_refuses_unredacted_url(tmp_path: Path):
    REGISTRY.register(FAKE_KEY)
    conn = open_database(tmp_path / "u.sqlite3", REPO_ROOT / "migrations")
    recorder = ResponseRecorder(conn, tmp_path / "raw", "real")
    with pytest.raises(RawStoreError):
        recorder.record(run_id=None, service_id="bid_notice", operation="op", params_redacted={},
                        url_redacted=f"https://apis.data.go.kr/x?serviceKey={FAKE_KEY}", attempt_no=1,
                        requested_at=datetime.now(timezone.utc), elapsed_ms=1, http_status=200, content_type=None,
                        body=None, parsed=parse_body(b"{}"), outcome="SUCCESS", classification_basis=None, error_detail=None)
    conn.close()


def test_run_repository_steps_and_reuse(tmp_path: Path):
    conn = open_database(tmp_path / "s.sqlite3", REPO_ROOT / "migrations")
    runs = RunRepository(conn)
    runs.start(run_id="r1", command="verify-api", data_mode="real", live=True, status="RUNNING", max_calls_run=10,
               catalog_sha256="abc")
    runs.record_step(run_id="r1", step_key="a", status="DONE", summary={"x": 1})
    runs.record_step(run_id="r1", step_key="b", status="NOT_RUN_QUOTA", summary={})
    runs.record_step(run_id="r1", step_key="a", status="DONE_EMPTY", summary={"x": 2})
    runs.finish("r1", status="PARTIAL", calls_attempted=3, stop_reason="quota")
    assert runs.get("r1")["status"] == "PARTIAL"
    assert runs.done_steps("r1") == {"a": {"x": 2}}
    with pytest.raises(ValueError):
        runs.record_step(run_id="r1", step_key="c", status="OK")
    conn.close()


def test_unknown_applied_migration_blocks_further_migration(tmp_path):
    conn = open_database(tmp_path / "unknown.sqlite3", REPO_ROOT / "migrations")
    try:
        conn.execute("INSERT INTO schema_migrations VALUES ('9999_unknown', 'synthetic-checksum', '2099-01-01')")
        with pytest.raises(MigrationError, match="알 수 없는"):
            apply_migrations(conn, REPO_ROOT / "migrations")
    finally:
        conn.close()
