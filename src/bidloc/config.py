"""환경설정 로딩과 검증.

우선순위: 프로세스 환경변수(비어 있지 않은 값) > 프로젝트 .env > 기본값.
인증키는 DATA_GO_KR_SERVICE_KEY에서만 읽고 SecretValue로 감싼다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from bidloc.redaction import REGISTRY, SecretValue

KEY_ENV = "DATA_GO_KR_SERVICE_KEY"

DEFAULTS: dict[str, str] = {
    "DATA_GO_KR_SERVICE_KEY_FORMAT": "decoded",
    "ALLOW_LIVE_API": "false",
    "LIVE_MAX_CALLS_PER_RUN": "100",
    "LIVE_MAX_CALLS_PER_DAY": "100",
    "BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY": "800",
    "BACKFILL_QUOTA_SCOPE": "service",
    "REQUEST_INTERVAL_SECONDS": "1.0",
    "HTTP_TIMEOUT_SECONDS": "30",
    "RETRY_MAX_ATTEMPTS": "3",
    "MAX_RESPONSE_BYTES": str(20 * 1024 * 1024),
    "DATA_MODE": "real",
    "DATABASE_PATH": ".local/real/bidloc.sqlite3",
    "RAW_RESPONSE_DIR": ".local/real/raw",
    "EXPORT_DIR": ".local/real/exports",
    "REPORT_DIR": ".local/real/reports",
    "LOG_DIR": ".local/logs",
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "8501",
}

KNOWN_KEYS = frozenset({KEY_ENV, *DEFAULTS.keys()})
_ENCODED_KEY_RE = re.compile(r"^[A-Za-z0-9._~%-]+$")
_PERCENT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ConfigError(ValueError):
    """설정값이 잘못되었을 때. 메시지에 비밀값을 넣지 않는다."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    env_file: Path
    env_file_exists: bool
    service_key: SecretValue | None
    service_key_format: str
    allow_live_api: bool
    live_max_calls_per_run: int
    live_max_calls_per_day: int
    request_interval_seconds: float
    http_timeout_seconds: float
    retry_max_attempts: int
    max_response_bytes: int
    data_mode: str
    database_path: Path
    raw_response_dir: Path
    export_dir: Path
    report_dir: Path
    log_dir: Path
    app_host: str
    app_port: int
    backfill_max_calls_per_service_per_day: int = 800
    backfill_quota_scope: str = "service"
    value_sources: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    unknown_env_file_keys: tuple[str, ...] = ()

    @property
    def service_key_present(self) -> bool:
        return self.service_key is not None

    def safe_summary(self) -> list[tuple[str, str, str]]:
        """(이름, 표시값, 출처). 인증키는 설정 여부만 표시한다."""
        src = self.value_sources
        return [
            (KEY_ENV, "설정됨" if self.service_key else "미설정", src.get(KEY_ENV, "-")),
            ("DATA_GO_KR_SERVICE_KEY_FORMAT", self.service_key_format, src.get("DATA_GO_KR_SERVICE_KEY_FORMAT", "default")),
            ("ALLOW_LIVE_API", str(self.allow_live_api).lower(), src.get("ALLOW_LIVE_API", "default")),
            ("LIVE_MAX_CALLS_PER_RUN", str(self.live_max_calls_per_run), src.get("LIVE_MAX_CALLS_PER_RUN", "default")),
            ("LIVE_MAX_CALLS_PER_DAY", str(self.live_max_calls_per_day), src.get("LIVE_MAX_CALLS_PER_DAY", "default")),
            ("BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY", str(self.backfill_max_calls_per_service_per_day), src.get("BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY", "default")),
            ("BACKFILL_QUOTA_SCOPE", self.backfill_quota_scope, src.get("BACKFILL_QUOTA_SCOPE", "default")),
            ("REQUEST_INTERVAL_SECONDS", str(self.request_interval_seconds), src.get("REQUEST_INTERVAL_SECONDS", "default")),
            ("HTTP_TIMEOUT_SECONDS", str(self.http_timeout_seconds), src.get("HTTP_TIMEOUT_SECONDS", "default")),
            ("RETRY_MAX_ATTEMPTS", str(self.retry_max_attempts), src.get("RETRY_MAX_ATTEMPTS", "default")),
            ("DATA_MODE", self.data_mode, src.get("DATA_MODE", "default")),
            ("DATABASE_PATH", self._rel(self.database_path), src.get("DATABASE_PATH", "default")),
            ("RAW_RESPONSE_DIR", self._rel(self.raw_response_dir), src.get("RAW_RESPONSE_DIR", "default")),
            ("REPORT_DIR", self._rel(self.report_dir), src.get("REPORT_DIR", "default")),
            ("EXPORT_DIR", self._rel(self.export_dir), src.get("EXPORT_DIR", "default")),
            ("LOG_DIR", self._rel(self.log_dir), src.get("LOG_DIR", "default")),
            ("APP_HOST", self.app_host, src.get("APP_HOST", "default")),
        ]

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path)


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigError(f"{name}는 true 또는 false여야 한다")


def _parse_int(name: str, raw: str, *, minimum: int, maximum: int) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}는 정수여야 한다") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}는 {minimum} 이상 {maximum} 이하여야 한다")
    return value


def _parse_float(name: str, raw: str, *, minimum: float, maximum: float) -> float:
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}는 숫자여야 한다") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}는 {minimum} 이상 {maximum} 이하여야 한다")
    return value


def _resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw.strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def load_settings(
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    *,
    register_secret: bool = True,
) -> Settings:
    root = (project_root or Path.cwd()).resolve()
    env_path = (env_file or root / ".env").resolve()
    env_exists = env_path.is_file()
    file_values = {k: (v or "") for k, v in dotenv_values(env_path).items()} if env_exists else {}
    process = os.environ if environ is None else environ

    sources: dict[str, str] = {}
    warnings: list[str] = []

    def get(name: str) -> str:
        proc_value = process.get(name)
        if proc_value is not None and proc_value.strip() != "":
            sources[name] = "env"
            return proc_value
        if name in file_values and file_values[name].strip() != "":
            sources[name] = ".env"
            return file_values[name]
        sources[name] = "default"
        return DEFAULTS.get(name, "")

    key_format = get("DATA_GO_KR_SERVICE_KEY_FORMAT").strip().lower()
    if key_format not in {"decoded", "encoded"}:
        raise ConfigError("DATA_GO_KR_SERVICE_KEY_FORMAT은 decoded 또는 encoded여야 한다")

    raw_key = get(KEY_ENV).strip()
    service_key: SecretValue | None = None
    if raw_key:
        if any(ch.isspace() for ch in raw_key):
            raise ConfigError(f"{KEY_ENV}에 공백 문자가 포함되어 있다 (값은 표시하지 않음)")
        if key_format == "encoded" and not _ENCODED_KEY_RE.match(raw_key):
            raise ConfigError("encoded 형식 키에는 URL 인코딩된 문자만 허용된다 (값은 표시하지 않음)")
        if key_format == "decoded" and _PERCENT_RE.search(raw_key):
            warnings.append(
                "인증키에 %XX 시퀀스가 있다. Encoding 키를 넣었다면 "
                "DATA_GO_KR_SERVICE_KEY_FORMAT=encoded로 지정해야 이중 인코딩을 막는다."
            )
        service_key = SecretValue(raw_key)
        if register_secret:
            REGISTRY.register(service_key)

    allow_live = _parse_bool("ALLOW_LIVE_API", get("ALLOW_LIVE_API"))
    max_run = _parse_int("LIVE_MAX_CALLS_PER_RUN", get("LIVE_MAX_CALLS_PER_RUN"), minimum=0, maximum=100_000)
    max_day = _parse_int("LIVE_MAX_CALLS_PER_DAY", get("LIVE_MAX_CALLS_PER_DAY"), minimum=0, maximum=1_000_000)
    backfill_per_service = _parse_int("BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY", get("BACKFILL_MAX_CALLS_PER_SERVICE_PER_DAY"), minimum=0, maximum=1_000_000)
    quota_scope = get("BACKFILL_QUOTA_SCOPE").strip().lower()
    if quota_scope not in {"service", "operation"}:
        raise ConfigError("BACKFILL_QUOTA_SCOPE는 service 또는 operation이어야 한다")
    if quota_scope == "operation":
        warnings.append("BACKFILL_QUOTA_SCOPE=operation: 오퍼레이션별 독립 할당은 실측 근거가 있을 때만 쓴다")
    interval = _parse_float("REQUEST_INTERVAL_SECONDS", get("REQUEST_INTERVAL_SECONDS"), minimum=0.0, maximum=3600.0)
    timeout = _parse_float("HTTP_TIMEOUT_SECONDS", get("HTTP_TIMEOUT_SECONDS"), minimum=1.0, maximum=300.0)
    retries = _parse_int("RETRY_MAX_ATTEMPTS", get("RETRY_MAX_ATTEMPTS"), minimum=1, maximum=10)
    max_bytes = _parse_int("MAX_RESPONSE_BYTES", get("MAX_RESPONSE_BYTES"), minimum=1024, maximum=512 * 1024 * 1024)
    if interval < 0.2:
        warnings.append(
            "REQUEST_INTERVAL_SECONDS가 0.2초 미만이다. 제공기관 초당 제한과 별개로 내부 안전값을 낮춘 상태다."
        )
    if max_run > max_day:
        warnings.append("LIVE_MAX_CALLS_PER_RUN이 LIVE_MAX_CALLS_PER_DAY보다 크다. 일일 예산에서 먼저 중단된다.")

    data_mode = get("DATA_MODE").strip().lower()
    if data_mode not in {"real", "demo"}:
        raise ConfigError("DATA_MODE는 real 또는 demo여야 한다")

    def mode_default(name: str) -> str:
        value = get(name)
        if sources[name] == "default" and data_mode == "demo":
            value = value.replace(".local/real/", ".local/demo/")
        return value

    paths = {
        name: _resolve_path(root, mode_default(name))
        for name in ("DATABASE_PATH", "RAW_RESPONSE_DIR", "EXPORT_DIR", "REPORT_DIR")
    }
    log_dir = _resolve_path(root, get("LOG_DIR"))
    for name, path in paths.items():
        parts = {p.lower() for p in path.parts}
        if data_mode == "real" and "demo" in parts:
            raise ConfigError(f"DATA_MODE=real인데 {name}가 demo 경로를 가리킨다")
        if data_mode == "demo" and "real" in parts:
            raise ConfigError(f"DATA_MODE=demo인데 {name}가 real 경로를 가리킨다")

    host = get("APP_HOST").strip()
    if host not in _LOOPBACK:
        warnings.append("APP_HOST가 루프백 주소가 아니다. 공개 바인딩은 별도 승인 없이 사용하지 않는다.")
    port = _parse_int("APP_PORT", get("APP_PORT"), minimum=1, maximum=65535)

    unknown = tuple(sorted(k for k in file_values if k not in KNOWN_KEYS))

    return Settings(
        project_root=root,
        env_file=env_path,
        env_file_exists=env_exists,
        service_key=service_key,
        service_key_format=key_format,
        allow_live_api=allow_live,
        live_max_calls_per_run=max_run,
        live_max_calls_per_day=max_day,
        request_interval_seconds=interval,
        http_timeout_seconds=timeout,
        retry_max_attempts=retries,
        max_response_bytes=max_bytes,
        data_mode=data_mode,
        database_path=paths["DATABASE_PATH"],
        raw_response_dir=paths["RAW_RESPONSE_DIR"],
        export_dir=paths["EXPORT_DIR"],
        report_dir=paths["REPORT_DIR"],
        log_dir=log_dir,
        app_host=host,
        app_port=port,
        backfill_max_calls_per_service_per_day=backfill_per_service,
        backfill_quota_scope=quota_scope,
        value_sources=dict(sources),
        warnings=tuple(warnings),
        unknown_env_file_keys=unknown,
    )
