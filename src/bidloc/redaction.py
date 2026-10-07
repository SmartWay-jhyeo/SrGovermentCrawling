"""비밀값 보관과 마스킹.

serviceKey는 채팅·로그·예외·요청 URL·원본 파일에 노출되면 안 된다.
- SecretValue: repr/str에서 값을 숨긴다.
- SecretRegistry: 등록된 비밀값과 그 인코딩 변형(원문, 1회/2회 URL 인코딩, 디코딩)을 모두 치환한다.
- 파라미터 패턴(serviceKey=...)도 2차로 치환한다.
"""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import quote, quote_plus, unquote
from typing import BinaryIO

MASK = "***REDACTED***"

_PARAM_PATTERN = re.compile(r"(?i)(service_?key\s*[=:]\s*[\"']?)([^&\s\"'<>]+)")


class SecretValue:
    """값을 숨기는 래퍼. reveal()로만 원문을 얻는다."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("비밀값은 비어 있지 않은 문자열이어야 한다")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:  # 비교는 허용하되 값은 노출하지 않는다
        return isinstance(other, SecretValue) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("SecretValue", self._value))

    def __reduce__(self):  # pickle 직렬화 차단
        raise TypeError("SecretValue는 직렬화할 수 없다")


def secret_variants(raw: str) -> set[str]:
    variants = {raw, quote(raw, safe=""), quote_plus(raw), quote(quote(raw, safe=""), safe="")}
    decoded = unquote(raw)
    if decoded != raw:
        variants.update({decoded, quote_plus(decoded)})
    return {v for v in variants if len(v) >= 4}


class SecretRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._variants: list[str] = []

    def register(self, secret: SecretValue | str) -> None:
        raw = secret.reveal() if isinstance(secret, SecretValue) else secret
        with self._lock:
            merged = set(self._variants) | secret_variants(raw)
            self._variants = sorted(merged, key=len, reverse=True)

    def clear(self) -> None:
        with self._lock:
            self._variants = []

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for variant in self._variants:
            if variant in out:
                out = out.replace(variant, MASK)
        return _PARAM_PATTERN.sub(lambda m: m.group(1) + MASK, out)

    def redact_bytes(self, data: bytes) -> tuple[bytes, bool]:
        """바이트 본문에서 비밀값 변형을 제거한다. (치환된 본문, 치환 발생 여부)"""
        changed = False
        out = data
        for variant in self._variants:
            token = variant.encode("utf-8")
            if token in out:
                out = out.replace(token, MASK.encode("ascii"))
                changed = True
        return out, changed

    def contains_secret(self, text: str) -> bool:
        return any(v in text for v in self._variants)

    def contains_secret_stream(self, handle: BinaryIO, *, chunk_bytes: int = 1024 * 1024) -> bool:
        """큰 DB도 크기 제한 없이 검사한다. 청크 경계에 걸친 키도 탐지한다."""
        if chunk_bytes < 1:
            raise ValueError("chunk_bytes는 1 이상이어야 한다")
        tokens = tuple(v.encode("utf-8") for v in self._variants)
        if not tokens:
            return False
        overlap = max(len(token) for token in tokens) - 1
        tail = b""
        while chunk := handle.read(chunk_bytes):
            data = tail + chunk
            if any(token in data for token in tokens):
                return True
            tail = data[-overlap:] if overlap else b""
        return False


REGISTRY = SecretRegistry()


def redact(text: object) -> str:
    return REGISTRY.redact_text(str(text))


class RedactingFilter(logging.Filter):
    """로그 레코드의 메시지·인자를 마스킹한다. 핸들러에 부착한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # 포맷 실패 시에도 원문 인자를 남기지 않는다
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_info:
            import traceback

            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
