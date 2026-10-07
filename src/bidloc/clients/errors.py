"""호출 결과 분류.

근거:
- 공공데이터포털 서비스 페이지의 '오픈API 에러코드 안내' 표(확인일 2026-09-16)
- 조달청 OpenAPI 참고자료 docx의 'OPEN API 에러코드별 조치방안' 표

두 표는 같은 번호에 다른 의미를 붙인 경우가 있다(예: 10, 20). 메시지 토큰이 있으면 토큰을 우선하고,
번호만 있으면 보수적으로(재시도하지 않는 쪽으로) 분류한다. 실제 응답 형식은 LIVE 검증 대상이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bidloc.clients.envelope import ParsedBody


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"                      # resultCode 00, 1건 이상
    SUCCESS_EMPTY = "SUCCESS_EMPTY"          # resultCode 00, 0건 (조회조건상 결과 없음)
    NO_DATA = "NO_DATA"                      # resultCode 03 (문서: 데이터 없음 에러)
    AUTH_KEY_MISSING = "AUTH_KEY_MISSING"
    AUTH_KEY_INVALID = "AUTH_KEY_INVALID"
    AUTH_KEY_EXPIRED = "AUTH_KEY_EXPIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    IP_NOT_ALLOWED = "IP_NOT_ALLOWED"
    QUOTA_DAILY_EXCEEDED = "QUOTA_DAILY_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    SERVICE_NOT_FOUND = "SERVICE_NOT_FOUND"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UNKNOWN_API_ERROR = "UNKNOWN_API_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    REDIRECT_BLOCKED = "REDIRECT_BLOCKED"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    BUDGET_EXHAUSTED_RUN = "BUDGET_EXHAUSTED_RUN"
    BUDGET_EXHAUSTED_DAY = "BUDGET_EXHAUSTED_DAY"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"


DATA_OK = frozenset({Outcome.SUCCESS, Outcome.SUCCESS_EMPTY, Outcome.NO_DATA})
RETRYABLE = frozenset({Outcome.RATE_LIMITED, Outcome.UPSTREAM_ERROR, Outcome.TIMEOUT, Outcome.NETWORK_ERROR})
# 해당 서비스(활용신청 단위)의 나머지 호출을 중단해야 하는 결과
SERVICE_FATAL = frozenset({
    Outcome.AUTH_KEY_MISSING, Outcome.AUTH_KEY_INVALID, Outcome.AUTH_KEY_EXPIRED,
    Outcome.ACCESS_DENIED, Outcome.SERVICE_NOT_FOUND,
})
# 실행 전체를 중단하고 이어받기 상태를 남겨야 하는 결과
RUN_FATAL = frozenset({
    Outcome.QUOTA_DAILY_EXCEEDED, Outcome.IP_NOT_ALLOWED,
    Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY,
})

MESSAGE_TOKENS: dict[str, Outcome] = {
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR": Outcome.RATE_LIMITED,
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": Outcome.QUOTA_DAILY_EXCEEDED,
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": Outcome.AUTH_KEY_INVALID,
    "SERVICE_KEY_IS_NULL": Outcome.AUTH_KEY_MISSING,
    "DEADLINE_HAS_EXPIRED_ERROR": Outcome.AUTH_KEY_EXPIRED,
    "SERVICE_ACCESS_DENIED_ERROR": Outcome.ACCESS_DENIED,
    "PERMISSION_DENIED": Outcome.ACCESS_DENIED,
    "BLACKLIST_IP_ACCESS_ERROR": Outcome.IP_NOT_ALLOWED,
    "UNREGISTERED_IP_ERROR": Outcome.IP_NOT_ALLOWED,
    "INVALID_REQUEST_PARAMETER_ERROR": Outcome.INVALID_PARAMETER,
    "NO_OPENAPI_SERVICE_ERROR": Outcome.SERVICE_NOT_FOUND,
    "SERVICETIMEOUT_ERROR": Outcome.UPSTREAM_ERROR,
    "APPLICATION_ERROR": Outcome.UPSTREAM_ERROR,
    "HTTP_ERROR": Outcome.UPSTREAM_ERROR,
    "NODATA_ERROR": Outcome.NO_DATA,
}

CODE_MAP: dict[str, Outcome] = {
    "01": Outcome.UPSTREAM_ERROR,
    "02": Outcome.UPSTREAM_ERROR,
    "03": Outcome.NO_DATA,
    "04": Outcome.UPSTREAM_ERROR,
    "05": Outcome.UPSTREAM_ERROR,
    "06": Outcome.INVALID_PARAMETER,
    "07": Outcome.INVALID_PARAMETER,
    "08": Outcome.INVALID_PARAMETER,
    "10": Outcome.INVALID_PARAMETER,  # 포털: INVALID_REQUEST_PARAMETER / 참고자료: ServiceKey 파라미터 없음
    "11": Outcome.INVALID_PARAMETER,
    "12": Outcome.SERVICE_NOT_FOUND,
    "20": Outcome.ACCESS_DENIED,      # 포털: SERVICE_KEY_IS_NULL / PERMISSION_DENIED / SERVICE_ACCESS_DENIED
    "22": Outcome.QUOTA_DAILY_EXCEEDED,
    "23": Outcome.RATE_LIMITED,
    "29": Outcome.IP_NOT_ALLOWED,
    "30": Outcome.AUTH_KEY_INVALID,
    "31": Outcome.AUTH_KEY_EXPIRED,
    "32": Outcome.IP_NOT_ALLOWED,
}

TEXT_HINTS: list[tuple[str, Outcome]] = [
    ("unauthorized", Outcome.AUTH_KEY_INVALID),
    ("forbidden", Outcome.ACCESS_DENIED),
    ("rate limit", Outcome.RATE_LIMITED),
    ("too many requests", Outcome.RATE_LIMITED),
    ("api not found", Outcome.SERVICE_NOT_FOUND),
]


@dataclass(frozen=True)
class Classification:
    outcome: Outcome
    basis: str  # 분류 근거(메시지 토큰/코드/HTTP 상태)


def _token_match(*texts: str | None) -> tuple[Outcome, str] | None:
    for text in texts:
        if not text:
            continue
        upper = text.upper()
        for token, outcome in MESSAGE_TOKENS.items():
            if token in upper:
                return outcome, f"message token {token}"
    return None


def classify_response(http_status: int, parsed: ParsedBody) -> Classification:
    if 300 <= http_status < 400:
        return Classification(Outcome.REDIRECT_BLOCKED, f"HTTP {http_status} redirect (not followed)")

    if parsed.kind in ("standard", "flat_error", "gateway_error"):
        token = _token_match(parsed.auth_msg, parsed.result_msg, parsed.err_msg)
        code = parsed.result_code
        if code == "00" and parsed.kind == "standard" and 200 <= http_status < 300:
            if parsed.problems:
                return Classification(Outcome.MALFORMED_RESPONSE, "resultCode 00 but envelope problems: " + "; ".join(parsed.problems))
            if parsed.items is None:
                if parsed.total_count == 0:
                    return Classification(Outcome.SUCCESS_EMPTY, "resultCode 00, items absent, totalCount 0")
                return Classification(Outcome.MALFORMED_RESPONSE, "resultCode 00 but items absent")
            if len(parsed.items) == 0:
                return Classification(Outcome.SUCCESS_EMPTY, "resultCode 00, 0 items")
            return Classification(Outcome.SUCCESS, "resultCode 00")
        if token is not None:
            return Classification(token[0], token[1])
        if code is not None and code in CODE_MAP:
            return Classification(CODE_MAP[code], f"result code {code}")
        if code is not None and code != "00":
            return Classification(Outcome.UNKNOWN_API_ERROR, f"unmapped result code {code}")
        if code is None and parsed.kind == "standard" and 200 <= http_status < 300:
            return Classification(Outcome.MALFORMED_RESPONSE, "standard envelope without resultCode")

    if parsed.kind == "unknown" and parsed.text_snippet:
        token = _token_match(parsed.text_snippet)
        if token is not None:
            return Classification(token[0], token[1])
        lower = parsed.text_snippet.lower()
        for hint, outcome in TEXT_HINTS:
            if hint in lower and not 200 <= http_status < 300:
                return Classification(outcome, f"text hint '{hint}' with HTTP {http_status}")

    if http_status == 401:
        return Classification(Outcome.AUTH_KEY_INVALID, "HTTP 401")
    if http_status == 403:
        return Classification(Outcome.ACCESS_DENIED, "HTTP 403")
    if http_status == 404:
        return Classification(Outcome.SERVICE_NOT_FOUND, "HTTP 404")
    if http_status == 429:
        return Classification(Outcome.RATE_LIMITED, "HTTP 429")
    if 500 <= http_status < 600:
        return Classification(Outcome.UPSTREAM_ERROR, f"HTTP {http_status}")
    if not 200 <= http_status < 300:
        return Classification(Outcome.HTTP_ERROR, f"HTTP {http_status}")
    return Classification(Outcome.MALFORMED_RESPONSE, f"HTTP {http_status} with unrecognized body ({parsed.shape})")
