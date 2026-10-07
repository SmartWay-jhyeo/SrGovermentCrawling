"""안전한 data.go.kr HTTP 클라이언트.

보장하는 것:
- 실제 호출은 CLI --live와 ALLOW_LIVE_API=true, 인증키, DATA_MODE=real이 모두 있어야 생성된다(LiveGateDecision).
- 카탈로그에 문서화된 https 호스트·오퍼레이션·요청 파라미터만 사용한다.
- Decoding 키는 정확히 한 번만 URL 인코딩한다. encoded 형식 키는 다시 인코딩하지 않는다.
- TLS 검증을 끄지 않는다. 리다이렉트를 따라가지 않는다(키가 다른 호스트로 전달되는 것 방지).
- 모든 HTTP 시도(재시도 포함)는 전송 전에 실행·일일 예산을 예약한다.
- 재시도는 일시 오류(초당 제한·게이트웨이/기관 오류·timeout·네트워크)만, 제한된 지수 백오프와 jitter로 한다.
  인증 오류·파라미터 오류·일일 한도 초과는 재시도하지 않는다. Retry-After를 존중하되 상한을 넘으면 중단한다.
- 로그·예외·저장 메타데이터에는 마스킹된 URL만 남긴다.
"""

from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit

import httpx

from bidloc import __version__
from bidloc.catalog import ALLOWED_HOSTS, Catalog, OperationSpec
from bidloc.clients.budget import BudgetExhausted, CallBudget
from bidloc.clients.envelope import ItemDict, ParsedBody, parse_body
from bidloc.clients.errors import DATA_OK, RETRYABLE, Classification, Outcome, classify_response
from bidloc.config import Settings
from bidloc.redaction import MASK, REGISTRY, SecretValue, redact
from bidloc.repositories.raw_store import ResponseRecorder
from bidloc.timeutil import now_utc

USER_AGENT = f"bid-location-lab/{__version__} (internal P0 verification)"
MAX_RETRY_AFTER_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0


class LiveCallBlocked(RuntimeError):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("실제 API 호출이 허용되지 않았다: " + "; ".join(reasons))
        self.reasons = reasons


class ParameterError(ValueError):
    pass


class _TooLarge(Exception):
    pass


@dataclass(frozen=True)
class LiveGateDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_live_gate(settings: Settings, cli_live: bool) -> LiveGateDecision:
    reasons: list[str] = []
    if not cli_live:
        reasons.append("CLI --live 옵션이 지정되지 않았다")
    if not settings.allow_live_api:
        reasons.append("ALLOW_LIVE_API=true가 아니다")
    if settings.service_key is None:
        reasons.append("DATA_GO_KR_SERVICE_KEY가 설정되지 않았다")
    if settings.data_mode != "real":
        reasons.append("DATA_MODE=real이 아니면 실제 API를 호출하지 않는다")
    return LiveGateDecision(allowed=not reasons, reasons=tuple(reasons))


@dataclass
class ApiResult:
    service_id: str
    operation: str
    outcome: Outcome
    basis: str
    http_status: int | None = None
    result_code: str | None = None
    result_msg: str | None = None
    items: list[ItemDict] | None = None
    total_count: int | None = None
    page_no: int | None = None
    num_of_rows: int | None = None
    envelope_shape: str | None = None
    response_format: str | None = None
    attempts: int = 0
    source_response_ids: list[int] = field(default_factory=list)
    error_detail: str | None = None

    @property
    def data_ok(self) -> bool:
        return self.outcome in DATA_OK


def validate_params(op: OperationSpec, params: Mapping[str, str]) -> None:
    documented_lower = {name.lower(): name for name in op.params}
    for name, value in params.items():
        if name.lower() == op.auth_param.lower() or name.lower() == "servicekey":
            raise ParameterError("인증키는 params로 전달하지 않는다")
        if name not in op.params:
            if name.lower() in documented_lower:
                raise ParameterError(
                    f"파라미터 대소문자가 문서와 다르다: {name} (카탈로그: {documented_lower[name.lower()]})"
                )
            raise ParameterError(f"카탈로그(공식 문서)에 없는 요청 파라미터: {op.service_id}.{op.name}.{name}")
        if not isinstance(value, str):
            raise ParameterError(f"파라미터 값은 문자열이어야 한다: {name}")
        if any(ch in value for ch in "\r\n\x00"):
            raise ParameterError(f"파라미터 값에 제어문자가 있다: {name}")


def encode_service_key(key: SecretValue, key_format: str) -> str:
    raw = key.reveal()
    if key_format == "decoded":
        return quote(raw, safe="")
    if key_format == "encoded":
        return raw
    raise ParameterError("알 수 없는 키 형식")


def build_request_url(op: OperationSpec, key: SecretValue, key_format: str,
                      params: Mapping[str, str]) -> tuple[str, str, dict[str, str]]:
    parts = urlsplit(op.url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ParameterError("허용되지 않은 호출 URL")
    encoded_params = [(quote(name, safe=""), quote(value, safe="")) for name, value in params.items()]
    query = "&".join([f"{quote(op.auth_param, safe='')}={encode_service_key(key, key_format)}"]
                     + [f"{n}={v}" for n, v in encoded_params])
    redacted_query = "&".join([f"{quote(op.auth_param, safe='')}={MASK}"] + [f"{n}={v}" for n, v in encoded_params])
    return f"{op.url}?{query}", redact(f"{op.url}?{redacted_query}"), {k: redact(v) for k, v in params.items()}


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (parsed - current).total_seconds())


def _charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("charset="):
            return piece.split("=", 1)[1].strip().strip('"') or None
    return None


class DataGoKrClient:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: Catalog,
        gate: LiveGateDecision,
        budget: CallBudget,
        recorder: ResponseRecorder | None,
        run_id: str | None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        if not gate.allowed:
            raise LiveCallBlocked(gate.reasons)
        if settings.service_key is None:  # gate가 막지만 방어적으로 재확인
            raise LiveCallBlocked(("DATA_GO_KR_SERVICE_KEY가 설정되지 않았다",))
        self._settings = settings
        self._catalog = catalog
        self._budget = budget
        self._recorder = recorder
        self._run_id = run_id
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._last_started: float | None = None
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            follow_redirects=False,
            verify=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.9, */*;q=0.1"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DataGoKrClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def budget(self) -> CallBudget:
        return self._budget

    def _throttle(self) -> None:
        if self._last_started is None:
            return
        wait = self._settings.request_interval_seconds - (self._monotonic() - self._last_started)
        if wait > 0:
            self._sleep(wait)

    def _backoff(self, attempt: int, retry_after: float | None) -> float | None:
        delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))) + self._jitter() * 0.5
        if retry_after is not None:
            if retry_after > MAX_RETRY_AFTER_SECONDS:
                return None
            delay = max(delay, retry_after)
        return delay

    def _read_limited(self, response: httpx.Response) -> bytes:
        limit = self._settings.max_response_bytes
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise _TooLarge(f"content-length {declared} > {limit}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise _TooLarge(f"body > {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def call(self, service_id: str, operation: str, params: Mapping[str, str], *,
             response_type: str | None = "json") -> ApiResult:
        op = self._catalog.operation(service_id, operation)
        request_params = dict(params)
        if response_type and "type" in op.params:
            request_params.setdefault("type", response_type)
        validate_params(op, request_params)
        assert self._settings.service_key is not None
        url, url_redacted, params_redacted = build_request_url(
            op, self._settings.service_key, self._settings.service_key_format, request_params
        )
        result = ApiResult(service_id=service_id, operation=operation, outcome=Outcome.BLOCKED_BY_POLICY,
                           basis="not started")
        max_attempts = self._settings.retry_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                self._budget.reserve(service_id=service_id, operation=operation)
            except BudgetExhausted as exc:
                result.outcome = Outcome.BUDGET_EXHAUSTED_RUN if exc.scope == "run" else Outcome.BUDGET_EXHAUSTED_DAY
                result.basis = str(exc)
                return result
            self._throttle()
            requested_at = now_utc()
            started = self._monotonic()
            self._last_started = started
            http_status: int | None = None
            content_type: str | None = None
            body: bytes | None = None
            parsed: ParsedBody | None = None
            retry_after: float | None = None
            error_detail: str | None = None
            try:
                with self._http.stream("GET", url) as response:
                    http_status = response.status_code
                    content_type = response.headers.get("content-type")
                    retry_after = parse_retry_after(response.headers.get("retry-after"))
                    body = self._read_limited(response)
                # 응답에 되돌아온 키는 파싱·오류 문자열 절단·후속 정규화 전에 제거한다.
                safe_body, _ = REGISTRY.redact_bytes(body)
                parsed = parse_body(safe_body, encoding_hint=_charset(content_type))
                classification = classify_response(http_status, parsed)
                if classification.outcome not in DATA_OK:
                    snippet = parsed.text_snippet or parsed.result_msg or ""
                    error_detail = f"{classification.basis}; {snippet}"[:1000]
            except _TooLarge as exc:
                classification = Classification(Outcome.RESPONSE_TOO_LARGE, str(exc))
                body = None
                error_detail = str(exc)
            except httpx.TimeoutException as exc:
                classification = Classification(Outcome.TIMEOUT, type(exc).__name__)
                error_detail = redact(f"{type(exc).__name__}: {exc}")
            except httpx.TransportError as exc:
                classification = Classification(Outcome.NETWORK_ERROR, type(exc).__name__)
                error_detail = redact(f"{type(exc).__name__}: {exc}")
            elapsed_ms = int((self._monotonic() - started) * 1000)

            if self._recorder is not None:
                row_id = self._recorder.record(
                    run_id=self._run_id, service_id=service_id, operation=operation,
                    params_redacted=params_redacted, url_redacted=url_redacted, attempt_no=attempt,
                    requested_at=requested_at, elapsed_ms=elapsed_ms, http_status=http_status,
                    content_type=content_type, body=body, parsed=parsed, outcome=classification.outcome.value,
                    classification_basis=classification.basis, error_detail=error_detail,
                )
                result.source_response_ids.append(row_id)

            result.attempts = attempt
            result.outcome = classification.outcome
            result.basis = redact(classification.basis)
            result.http_status = http_status
            result.error_detail = redact(error_detail) if error_detail else None
            if parsed is not None:
                result.result_code = parsed.result_code
                result.result_msg = redact(parsed.result_msg) if parsed.result_msg else None
                result.total_count = parsed.total_count
                result.page_no = parsed.page_no
                result.num_of_rows = parsed.num_of_rows
                result.envelope_shape = parsed.shape
                result.response_format = parsed.fmt
            result.items = (parsed.items or []) if (parsed is not None and classification.outcome in DATA_OK) else None

            if classification.outcome in RETRYABLE and attempt < max_attempts:
                delay = self._backoff(attempt, retry_after)
                if delay is None:
                    result.basis += f"; Retry-After {retry_after}s가 상한 {MAX_RETRY_AFTER_SECONDS}s를 넘어 재시도하지 않음"
                    return result
                self._sleep(delay)
                continue
            return result
        return result
