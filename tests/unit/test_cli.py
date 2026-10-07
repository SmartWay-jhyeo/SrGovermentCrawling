"""API-01: 키 없음·허용 없음 상태에서 CLI가 네트워크 없이 준비항목만 안내한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from bidloc.cli import EXIT_BLOCKED, EXIT_OK, main
from tests.conftest import FAKE_KEY


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("DATA_GO_KR_SERVICE_KEY", "ALLOW_LIVE_API", "DATA_MODE", "DATABASE_PATH", "RAW_RESPONSE_DIR",
                 "REPORT_DIR", "LOG_DIR", "EXPORT_DIR", "LIVE_MAX_CALLS_PER_RUN", "LIVE_MAX_CALLS_PER_DAY"):
        monkeypatch.delenv(name, raising=False)


def test_doctor_without_key_reports_blocked_and_exits_ok(project: Path, clean_env, capsys):
    code = main(["--project-root", str(project), "doctor"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "[BLOCKED] service-key" in out and "[BLOCKED] live-gate" in out
    assert "[SKIPPED] network" in out
    assert main(["--project-root", str(project), "doctor", "--require-live"]) == EXIT_BLOCKED


def test_doctor_with_key_never_prints_it(project: Path, clean_env, capsys):
    (project / ".env").write_text(f"DATA_GO_KR_SERVICE_KEY={FAKE_KEY}\nALLOW_LIVE_API=true\n", encoding="utf-8")
    code = main(["--project-root", str(project), "doctor"])
    out = capsys.readouterr().out
    assert code == EXIT_OK and FAKE_KEY not in out
    assert "[PASS   ] service-key" in out and "[PASS   ] live-gate" in out


def test_init_db_and_catalog_check(project: Path, clean_env, capsys):
    assert main(["--project-root", str(project), "init-db"]) == EXIT_OK
    assert (project / ".local" / "real" / "bidloc.sqlite3").is_file()
    assert main(["--project-root", str(project), "catalog-check"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "bid_notice" in out and "실연동 상태" in out


def test_verify_api_without_live_is_skipped_dry_run(project: Path, clean_env, capsys):
    code = main(["--project-root", str(project), "verify-api"])
    out = capsys.readouterr().out
    assert code == EXIT_OK and "[SKIPPED]" in out


def test_verify_api_live_without_allow_is_blocked(project: Path, clean_env, capsys):
    (project / ".env").write_text(f"DATA_GO_KR_SERVICE_KEY={FAKE_KEY}\nALLOW_LIVE_API=false\n", encoding="utf-8")
    code = main(["--project-root", str(project), "verify-api", "--live"])
    out = capsys.readouterr().out
    assert code == EXIT_BLOCKED and "ALLOW_LIVE_API" in out and FAKE_KEY not in out


def test_verify_api_live_without_key_is_blocked(project: Path, clean_env, capsys):
    (project / ".env").write_text("ALLOW_LIVE_API=true\n", encoding="utf-8")
    code = main(["--project-root", str(project), "verify-api", "--live"])
    out = capsys.readouterr().out
    assert code == EXIT_BLOCKED and "DATA_GO_KR_SERVICE_KEY" in out


def test_blocked_runs_are_recorded(project: Path, clean_env):
    import sqlite3

    main(["--project-root", str(project), "verify-api", "--live"])
    conn = sqlite3.connect(project / ".local" / "real" / "bidloc.sqlite3")
    rows = conn.execute("SELECT status, live, calls_attempted FROM api_run").fetchall()
    conn.close()
    assert rows == [("BLOCKED", 0, 0)]


def test_unexpected_error_output_is_redacted(project: Path, clean_env, capsys, monkeypatch):
    (project / ".env").write_text(f"DATA_GO_KR_SERVICE_KEY={FAKE_KEY}\n", encoding="utf-8")

    def boom(args):
        from bidloc.config import load_settings

        load_settings(project)
        raise RuntimeError(f"unexpected failure with key {FAKE_KEY}")

    import bidloc.cli as cli

    monkeypatch.setattr(cli, "cmd_catalog_check", boom)
    code = main(["--project-root", str(project), "--debug-traceback", "catalog-check"])
    captured = capsys.readouterr()
    assert code == 1 and FAKE_KEY not in captured.err and FAKE_KEY not in captured.out
