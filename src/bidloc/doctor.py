"""doctor: 설정·저장소·카탈로그·연결 준비 상태 점검.

기본 점검은 네트워크를 쓰지 않는다. --network를 주면 apis.data.go.kr에 대해 DNS 조회와 TLS 핸드셰이크(인증서 검증)만 한다.
이때 HTTP 요청이나 인증키 전송은 하지 않으므로 API 호출·쿼터 사용이 아니다. 인증된 호출 확인은 verify-api --live의 몫이다.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import socket
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bidloc.catalog import CatalogError, default_catalog_path, load_catalog
from bidloc.config import ConfigError, Settings, load_settings
from bidloc.redaction import SecretRegistry, redact
from bidloc.repositories.db import MigrationError, connect, default_migrations_dir, migration_status

STATUSES = ("PASS", "WARN", "FAIL", "BLOCKED", "SKIPPED", "INFO")
API_HOST = "apis.data.go.kr"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "미설치"


def check_python() -> list[Check]:
    info = sys.version_info
    version = f"{info.major}.{info.minor}.{info.micro}"
    if (info.major, info.minor) < (3, 11):
        return [Check("python", "FAIL", f"Python {version}: 3.11 이상 필요")]
    if (info.major, info.minor) != (3, 11):
        return [Check("python", "WARN", f"Python {version}: CODEX_PROMPT 기준 버전(3.11)과 다르다")]
    return [Check("python", "PASS", f"Python {version}")]


def check_dependencies() -> list[Check]:
    wanted = {"httpx": "0.28.1", "PyYAML": "6.0.3", "defusedxml": "0.7.1", "python-dotenv": "1.2.3"}
    out = []
    for dist, pinned in wanted.items():
        installed = _version(dist)
        if installed == "미설치":
            out.append(Check(f"dependency:{dist}", "FAIL", "미설치 (pip install -e .)"))
        elif installed != pinned:
            out.append(Check(f"dependency:{dist}", "WARN", f"설치 {installed}, 고정 버전 {pinned}"))
        else:
            out.append(Check(f"dependency:{dist}", "PASS", installed))
    return out


def check_settings(project_root: Path) -> tuple[Settings | None, list[Check]]:
    checks: list[Check] = []
    env_file = project_root / ".env"
    if env_file.is_file():
        checks.append(Check(".env", "PASS", "존재 (내용 비표시)"))
    else:
        checks.append(Check(".env", "WARN", ".env 없음. .env.example을 복사해 로컬 편집기로 작성한다"))
    try:
        settings = load_settings(project_root)
    except ConfigError as exc:
        checks.append(Check("settings", "FAIL", redact(str(exc))))
        return None, checks
    checks.append(Check("settings", "PASS", "설정값 형식 검증 통과"))
    for warning in settings.warnings:
        checks.append(Check("settings:warning", "WARN", warning))
    if settings.unknown_env_file_keys:
        checks.append(Check("settings:unknown-keys", "WARN", ".env에 알 수 없는 키: " + ", ".join(settings.unknown_env_file_keys)))
    for name, value, source in settings.safe_summary():
        checks.append(Check(f"config:{name}", "INFO", f"{value} (출처: {source})"))
    return settings, checks


def check_live_readiness(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    if settings.service_key is None:
        checks.append(Check("service-key", "BLOCKED", "DATA_GO_KR_SERVICE_KEY 미설정 — 실연동 검증 불가(단위테스트·명세 정리는 가능)"))
    else:
        checks.append(Check("service-key", "PASS", f"설정됨 (값 비표시, 형식 {settings.service_key_format})"))
    reasons = []
    if not settings.allow_live_api:
        reasons.append("ALLOW_LIVE_API=true 아님")
    if settings.service_key is None:
        reasons.append("인증키 없음")
    if settings.data_mode != "real":
        reasons.append("DATA_MODE=real 아님")
    if reasons:
        checks.append(Check("live-gate", "BLOCKED", "실제 호출 조건 미충족: " + ", ".join(reasons) + " (추가로 CLI --live 필요)"))
    else:
        checks.append(Check("live-gate", "PASS", "환경 조건 충족. 실제 호출에는 CLI --live가 추가로 필요"))
    checks.append(Check("service-approval", "INFO",
                         "활용신청 승인 여부는 인증된 호출 응답으로만 확인된다 (verify-api --live)"))
    return checks


def check_database(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    db_path = settings.database_path
    migrations_dir = default_migrations_dir(settings.project_root)
    if not db_path.exists():
        checks.append(Check("database", "WARN", f"DB 미생성: {settings._rel(db_path)} (python -m bidloc init-db)"))
        return checks
    try:
        conn = connect(db_path, readonly=True)
        try:
            status = migration_status(conn, migrations_dir)
            from bidloc.timeutil import kst_today
            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='request_budget_daily'").fetchone()
            used = None
            if row:
                r = conn.execute("SELECT calls_reserved FROM request_budget_daily WHERE budget_day_kst = ?",
                                 (kst_today().isoformat(),)).fetchone()
                used = int(r[0]) if r else 0
        finally:
            conn.close()
    except (MigrationError, Exception) as exc:  # noqa: BLE001 - 점검 결과로 보고
        checks.append(Check("database", "FAIL", redact(f"{type(exc).__name__}: {exc}")))
        return checks
    if status.checksum_mismatch or status.unknown_applied:
        checks.append(Check("migrations", "FAIL", f"체크섬 불일치 {status.checksum_mismatch}, 알 수 없는 적용 {status.unknown_applied}"))
    elif status.pending:
        checks.append(Check("migrations", "WARN", f"미적용 {status.pending} (python -m bidloc init-db)"))
    else:
        checks.append(Check("migrations", "PASS", f"적용 {status.applied}"))
    if used is not None:
        checks.append(Check("budget:today", "INFO",
                            f"오늘(KST) 내부 예약 호출 {used}/{settings.live_max_calls_per_day} — 내부 안전값이며 제공기관 쿼터가 아님"))
    return checks


def check_catalog(project_root: Path) -> list[Check]:
    path = default_catalog_path(project_root)
    if not path.is_file():
        return [Check("api-catalog", "FAIL", f"없음: {path}")]
    try:
        catalog = load_catalog(path)
    except (CatalogError, OSError, ValueError) as exc:
        return [Check("api-catalog", "FAIL", redact(str(exc)))]
    counts = catalog.status_counts()
    live = (catalog.data.get("live_verification") or {}).get("status", "미기재")
    out = [Check("api-catalog", "PASS", f"서비스 {len(catalog.services)}개, 오퍼레이션 상태 {counts}")]
    out.append(Check("api-catalog:live", "INFO" if live == "LIVE_VERIFIED" else "WARN", f"카탈로그 실연동 상태: {live}"))
    return out


def check_collector_config(project_root: Path) -> list[Check]:
    from bidloc.collector_config import load_collector_config

    try:
        config = load_collector_config(project_root / "config" / "collector.yaml")
    except (ConfigError, OSError) as exc:
        return [Check("collector-config", "FAIL", redact(str(exc)))]
    return [Check("collector-config", "PASS",
                  f"{config.years_back}년, {config.window_days}일 창, {config.num_of_rows}행/페이지, "
                  f"단계 {config.stages}, 서비스별 내부 한도 {config.daily_limits} (실연동 검증 아님)")]


def check_verify_plan(project_root: Path) -> list[Check]:
    from bidloc.collectors.verify import VerifyConfigError, load_verify_plan

    path = project_root / "config" / "verify_samples.yaml"
    try:
        plan = load_verify_plan(path)
    except (VerifyConfigError, OSError) as exc:
        return [Check("verify-plan", "FAIL", redact(str(exc)))]
    low, _ = plan.estimate_calls()
    return [Check("verify-plan", "PASS",
                  f"고정 표본 {len(plan.fixed_notices)}건, 탐색 창 {len(plan.windows)}개, 예상 최소 호출 {low}회(추정)")]


def check_git_hygiene(settings: Settings) -> list[Check]:
    root = settings.project_root
    gitignore = root / ".gitignore"
    checks: list[Check] = []
    if not gitignore.is_file():
        return [Check("gitignore", "FAIL", ".gitignore 없음")]
    rules = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
    missing = [rule for rule in (".env", ".local/") if rule not in rules]
    if missing:
        checks.append(Check("gitignore", "FAIL", f"필수 제외 규칙 없음: {missing}"))
    else:
        checks.append(Check("gitignore", "PASS", ".env, .local/ 제외 규칙 확인"))
    local = (root / ".local").resolve()
    for label, path in (("DATABASE_PATH", settings.database_path), ("RAW_RESPONSE_DIR", settings.raw_response_dir),
                        ("REPORT_DIR", settings.report_dir), ("EXPORT_DIR", settings.export_dir),
                        ("LOG_DIR", settings.log_dir)):
        if local not in path.parents and path != local:
            checks.append(Check(f"path:{label}", "WARN", f".local 밖 경로 — Git 제외 여부를 직접 확인해야 한다: {path}"))
    if not (root / ".git").exists():
        checks.append(Check("git", "INFO", "Git 저장소가 아니다. .gitignore 규칙만 확인했다"))
    return checks


def check_disk_space(settings: Settings, *, recommended_bytes: int = 20 * 1024**3) -> list[Check]:
    """미생성 저장 경로도 가장 가까운 부모에서 점검한다. 디렉터리를 만들지 않는다."""
    checks: list[Check] = []
    seen: set[int] = set()
    for target in (settings.database_path.parent, settings.raw_response_dir, settings.report_dir,
                   settings.export_dir, settings.log_dir):
        try:
            existing = target
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            device = existing.stat().st_dev
            if device in seen:
                continue
            seen.add(device)
            free = shutil.disk_usage(existing).free
        except OSError as exc:
            checks.append(Check("disk-space", "FAIL", f"저장 볼륨 점검 실패: {type(exc).__name__}"))
            continue
        status = "PASS" if free >= recommended_bytes else "WARN"
        checks.append(Check("disk-space", status,
                            f"{existing.anchor} 여유 {free / 1024**3:.2f} GiB, "
                            f"내부 권장 여유 {recommended_bytes / 1024**3:.0f} GiB "
                            "(3년 원본·DB용 참고값, 실제 필요량 보장 아님)"))
    return checks


def check_secret_leak(settings: Settings, *, chunk_bytes: int = 1024 * 1024) -> list[Check]:
    if settings.service_key is None:
        return [Check("secret-scan", "SKIPPED", "인증키가 없어 저장 파일 노출 검사를 생략")]
    registry = SecretRegistry()
    registry.register(settings.service_key)
    roots = [settings.project_root, settings.database_path, settings.raw_response_dir,
             settings.report_dir, settings.export_dir, settings.log_dir]
    # 키 주입 파일·도구 캐시는 의도적으로 제외한다. src와 루트 문서, 큰 DB는 검사한다.
    excluded_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    scanned = 0
    hits: list[str] = []
    incomplete: list[str] = []
    seen: set[Path] = set()

    def visit(path: Path) -> None:
        nonlocal scanned
        if path in seen:
            return
        seen.add(path)
        if path.name.startswith(".env") and path.name != ".env.example":
            return
        if path == settings.env_file:
            return
        if path.is_symlink():
            incomplete.append("symbolic-link")
            return
        try:
            with path.open("rb") as handle:
                found = registry.contains_secret_stream(handle, chunk_bytes=chunk_bytes)
            scanned += 1
            if found:
                hits.append(settings._rel(path))
        except OSError as exc:
            incomplete.append(type(exc).__name__)

    for base in roots:
        if not base.exists():
            continue
        if base.is_file():
            visit(base)
            continue
        if base.is_symlink():
            incomplete.append("symbolic-link")
            continue
        for directory, dirs, files in os.walk(base, followlinks=False,
                                              onerror=lambda exc: incomplete.append(type(exc).__name__)):
            allowed = []
            for name in dirs:
                if name in excluded_dirs:
                    continue
                if (Path(directory) / name).is_symlink():
                    incomplete.append("symbolic-link")
                else:
                    allowed.append(name)
            dirs[:] = allowed
            for name in files:
                visit(Path(directory) / name)
    checks = []
    if hits:
        checks.append(Check("secret-scan", "FAIL", redact(f"인증키가 포함된 파일 {len(hits)}개: {hits[:5]}")))
    else:
        checks.append(Check("secret-scan", "WARN" if incomplete else "PASS",
                            f"{scanned}개 파일에서 현재 인증키 변형 미검출 "
                            "(DB 크기 제한 없음, .env 계열·도구 캐시 제외)"))
    if incomplete:
        checks.append(Check("secret-scan:incomplete", "WARN", f"미검사 {len(incomplete)}건: {sorted(set(incomplete))}"))
    return checks


def check_tls_offline() -> list[Check]:
    try:
        import certifi

        bundle = Path(certifi.where())
        ctx = ssl.create_default_context(cafile=str(bundle))
        ok = bundle.is_file() and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    except Exception as exc:  # noqa: BLE001
        return [Check("tls:local", "FAIL", f"TLS 컨텍스트 생성 실패: {type(exc).__name__}")]
    return [Check("tls:local", "PASS" if ok else "FAIL", "인증서 검증 필수(CERT_REQUIRED, hostname 확인) 컨텍스트 생성")]


def check_network(timeout: float = 10.0,
                  resolver: Callable[..., list] = socket.getaddrinfo,
                  connector: Callable[..., socket.socket] = socket.create_connection) -> list[Check]:
    checks: list[Check] = []
    try:
        infos = resolver(API_HOST, 443, type=socket.SOCK_STREAM)
        addresses = sorted({info[4][0] for info in infos})
        checks.append(Check("network:dns", "PASS", f"{API_HOST} → {len(addresses)}개 주소"))
    except OSError as exc:
        return [Check("network:dns", "FAIL", f"{API_HOST} DNS 실패: {type(exc).__name__}")]
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
        with connector((API_HOST, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=API_HOST) as tls:
                cert = tls.getpeercert() or {}
                version = tls.version()
        not_after = cert.get("notAfter", "?")
        checks.append(Check("network:tls", "PASS",
                            f"TLS 핸드셰이크·인증서 검증 성공 ({version}, 만료 {not_after}). HTTP 요청·인증키 전송 없음"))
    except ssl.SSLCertVerificationError as exc:
        checks.append(Check("network:tls", "FAIL", f"인증서 검증 실패: {exc.verify_message}"))
    except OSError as exc:
        checks.append(Check("network:tls", "FAIL", f"TLS 연결 실패: {type(exc).__name__}"))
    return checks


def run_doctor(project_root: Path, *, network: bool = False) -> tuple[list[Check], Settings | None]:
    checks: list[Check] = []
    checks += check_python()
    checks += check_dependencies()
    settings, setting_checks = check_settings(project_root)
    checks += setting_checks
    if settings is not None:
        checks += check_live_readiness(settings)
        checks += check_database(settings)
        checks += check_disk_space(settings)
        checks += check_git_hygiene(settings)
        checks += check_secret_leak(settings)
    checks += check_catalog(project_root)
    checks += check_collector_config(project_root)
    checks += check_verify_plan(project_root)
    checks += check_tls_offline()
    if network:
        checks += check_network()
    else:
        checks.append(Check("network", "SKIPPED", "--network 미지정 (DNS·TLS 핸드셰이크 점검 생략)"))
    return checks, settings
