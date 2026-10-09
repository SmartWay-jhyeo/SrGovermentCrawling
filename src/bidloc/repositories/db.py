"""SQLite 연결과 마이그레이션.

- 외래키 ON, WAL, busy_timeout을 연결마다 설정한다.
- migrations/NNNN_name.sql을 버전 순서로 한 번씩 적용하고 체크섬을 기록한다.
- 이미 적용된 파일의 체크섬이 바뀌면 오류로 처리한다(적용 이력 변조 방지).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bidloc.timeutil import now_utc, to_iso_utc

_VERSION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationFile:
    version: str
    path: Path
    sha256: str
    # CRLF 그대로 해시한 값. Windows 체크아웃(CRLF)에서 적용한 DB를 리눅스(LF)에서 열 때
    # 같은 내용을 '변경됨'으로 보지 않기 위해 둔다. 2026-10-09 NAS 이전 때 0004가 이걸로 막혔다.
    legacy_sha256: str


@dataclass(frozen=True)
class MigrationStatus:
    applied: list[str]
    pending: list[str]
    checksum_mismatch: list[str]
    unknown_applied: list[str]

    @property
    def ok(self) -> bool:
        return not self.pending and not self.checksum_mismatch and not self.unknown_applied


def default_migrations_dir(project_root: Path | None = None) -> Path:
    if project_root is not None:
        candidate = Path(project_root) / "migrations"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parents[3] / "migrations"


def connect(db_path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    db_path = Path(db_path)
    if readonly:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True,
                               timeout=30, isolation_level=None)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def list_migration_files(migrations_dir: Path) -> list[MigrationFile]:
    files: list[MigrationFile] = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        match = _VERSION_RE.match(path.name)
        if not match:
            raise MigrationError(f"마이그레이션 파일 이름 형식 오류: {path.name}")
        # 줄 끝(CRLF/LF)은 내용이 아니다. 체크섬은 LF로 정규화한 바이트로 만든다.
        normalized = path.read_bytes().replace(b"\r\n", b"\n")
        files.append(MigrationFile(
            version=path.stem, path=path,
            sha256=hashlib.sha256(normalized).hexdigest(),
            legacy_sha256=hashlib.sha256(normalized.replace(b"\n", b"\r\n")).hexdigest(),
        ))
    versions = [f.version[:4] for f in files]
    if len(versions) != len(set(versions)):
        raise MigrationError("같은 번호의 마이그레이션이 두 개 이상 있다")
    return files


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version         TEXT PRIMARY KEY,
            checksum_sha256 TEXT NOT NULL,
            applied_at_utc  TEXT NOT NULL
        )
        """
    )


def migration_status(conn: sqlite3.Connection, migrations_dir: Path) -> MigrationStatus:
    files = {f.version: f for f in list_migration_files(migrations_dir)}
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    rows = ({r["version"]: r["checksum_sha256"] for r in conn.execute("SELECT version, checksum_sha256 FROM schema_migrations")}
            if exists else {})
    applied = sorted(v for v in rows if v in files)
    pending = sorted(v for v in files if v not in rows)
    mismatch = sorted(v for v in rows if v in files and rows[v] not in (files[v].sha256, files[v].legacy_sha256))
    unknown = sorted(v for v in rows if v not in files)
    return MigrationStatus(applied=applied, pending=pending, checksum_mismatch=mismatch, unknown_applied=unknown)


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[str]:
    status = migration_status(conn, migrations_dir)
    if status.checksum_mismatch:
        raise MigrationError("적용된 마이그레이션 파일이 변경되었다: " + ", ".join(status.checksum_mismatch))
    if status.unknown_applied:
        raise MigrationError("알 수 없는 마이그레이션이 적용되어 있다: " + ", ".join(status.unknown_applied))
    _ensure_migrations_table(conn)
    applied_now: list[str] = []
    files = {f.version: f for f in list_migration_files(migrations_dir)}
    # CRLF 파일로 적용됐던 기록은 정규화 체크섬으로 바꿔 둔다(내용 동일, 한 번만 일어난다).
    for version in status.applied:
        mfile = files[version]
        conn.execute("UPDATE schema_migrations SET checksum_sha256 = ? WHERE version = ? AND checksum_sha256 = ?",
                     (mfile.sha256, version, mfile.legacy_sha256))
    for version in status.pending:
        mfile = files[version]
        sql = mfile.path.read_text(encoding="utf-8")
        if not re.fullmatch(r"[0-9a-f]{64}", mfile.sha256):
            raise MigrationError("체크섬 형식 오류")
        applied_at = to_iso_utc(now_utc())
        script = (
            "BEGIN IMMEDIATE;\n"
            + sql
            + "\n;\nINSERT INTO schema_migrations (version, checksum_sha256, applied_at_utc) VALUES ("
            + f"'{version}', '{mfile.sha256}', '{applied_at}');\nCOMMIT;\n"
        )
        try:
            conn.executescript(script)
        except Exception as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"마이그레이션 {version} 적용 실패: {exc}") from exc
        applied_now.append(version)
    return applied_now


def open_database(db_path: Path, migrations_dir: Path, *, migrate: bool = True) -> sqlite3.Connection:
    conn = connect(db_path)
    try:
        if migrate:
            apply_migrations(conn, migrations_dir)
    except Exception:
        conn.close()
        raise
    return conn
