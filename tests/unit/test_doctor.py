"""P0 점검의 실패·미검사 구분. 모든 파일·키는 임시 합성 데이터다."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from bidloc.doctor import check_database, check_disk_space, check_secret_leak
from bidloc.repositories.db import connect
from tests.conftest import FAKE_KEY, make_settings


@pytest.mark.parametrize("free,status", [(25 * 1024**3, "PASS"), (1024, "WARN")])
def test_disk_space_checks_uncreated_storage_without_creating_it(project, monkeypatch, free, status):
    settings = make_settings(project)
    calls = []

    def usage(path):
        calls.append(path)
        assert path.exists()
        return SimpleNamespace(free=free)

    monkeypatch.setattr("bidloc.doctor.shutil.disk_usage", usage)
    checks = check_disk_space(settings)
    assert len(calls) == 1 and checks[0].status == status
    assert not settings.raw_response_dir.exists()


def test_disk_inspection_failure_is_not_a_pass(project, monkeypatch):
    def denied(path):
        raise OSError("synthetic disk error")

    monkeypatch.setattr("bidloc.doctor.shutil.disk_usage", denied)
    assert check_disk_space(make_settings(project))[0].status == "FAIL"


@pytest.mark.parametrize("location", ["src/example.py", "README.md", ".local/real/big.sqlite3"])
def test_secret_scan_covers_source_root_and_large_files(project, location):
    settings = make_settings(project, DATA_GO_KR_SERVICE_KEY=FAKE_KEY)
    path = project / location
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        # 크기가 이전 50 MiB 상한을 넘고, 키가 청크 경계에 걸린 합성 파일.
        offset = 51 * 1024**2 - 5 if path.suffix == ".sqlite3" else 1019
        handle.seek(offset)
        handle.write(quote(FAKE_KEY, safe="").encode())
    checks = check_secret_leak(settings, chunk_bytes=1024 * 1024)
    assert checks[0].status == "FAIL"
    assert FAKE_KEY not in str(checks) and quote(FAKE_KEY, safe="") not in str(checks)


def test_secret_scan_excludes_env_but_includes_configured_external_log(project, tmp_path):
    log_dir = tmp_path / "separate-logs"
    log_dir.mkdir()
    settings = make_settings(project, DATA_GO_KR_SERVICE_KEY=FAKE_KEY, LOG_DIR=str(log_dir))
    (project / ".env").write_text(f"DATA_GO_KR_SERVICE_KEY={FAKE_KEY}", encoding="utf-8")
    assert check_secret_leak(settings)[0].status == "PASS"
    (log_dir / "leak.log").write_text(FAKE_KEY, encoding="utf-8")
    assert check_secret_leak(settings)[0].status == "FAIL"


def test_unreadable_file_prevents_clean_secret_scan_claim(project, monkeypatch):
    settings = make_settings(project, DATA_GO_KR_SERVICE_KEY=FAKE_KEY)
    unreadable = project / "locked.bin"
    unreadable.write_bytes(b"synthetic")
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("synthetic lock")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    checks = check_secret_leak(settings)
    assert checks[0].status == "WARN"
    assert any(check.name == "secret-scan:incomplete" for check in checks)


def test_doctor_inspects_unmigrated_database_without_writing_schema(project):
    settings = make_settings(project)
    conn = connect(settings.database_path)
    conn.execute("CREATE TABLE user_data (value TEXT)")
    conn.execute("INSERT INTO user_data VALUES ('preserve')")
    conn.close()
    checks = check_database(settings)
    assert any(check.name == "migrations" and check.status == "WARN" for check in checks)
    conn = connect(settings.database_path, readonly=True)
    try:
        assert conn.execute("SELECT value FROM user_data").fetchone()[0] == "preserve"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone() is None
    finally:
        conn.close()
