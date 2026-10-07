"""SEC-01: 예외·로그·요청 URL·저장 파일에 키가 남지 않는다."""

from __future__ import annotations

import logging
import pickle
from urllib.parse import quote, quote_plus

import pytest

from bidloc.logging_setup import configure_logging
from bidloc.redaction import MASK, REGISTRY, RedactingFilter, SecretValue, redact
from tests.conftest import FAKE_KEY


def test_secret_value_hides_itself():
    secret = SecretValue(FAKE_KEY)
    assert FAKE_KEY not in repr(secret) and FAKE_KEY not in str(secret) and FAKE_KEY not in f"{secret}"
    with pytest.raises(TypeError):
        pickle.dumps(secret)


@pytest.mark.parametrize("variant", [FAKE_KEY, quote(FAKE_KEY, safe=""), quote_plus(FAKE_KEY),
                                     quote(quote(FAKE_KEY, safe=""), safe="")])
def test_all_encoding_variants_are_redacted(variant: str):
    REGISTRY.register(SecretValue(FAKE_KEY))
    text = f"error at https://apis.data.go.kr/x?foo=1&serviceKey={variant}&bar=2 body={variant}"
    out = redact(text)
    assert variant not in out
    assert MASK in out


def test_param_pattern_redacts_unregistered_values():
    out = redact("GET /api?ServiceKey=UNREGISTERED_VALUE_123&pageNo=1")
    assert "UNREGISTERED_VALUE_123" not in out


def test_redact_bytes_reports_change():
    REGISTRY.register(FAKE_KEY)
    body = f"<echo>{quote(FAKE_KEY, safe='')}</echo>".encode()
    redacted, changed = REGISTRY.redact_bytes(body)
    assert changed and quote(FAKE_KEY, safe="").encode() not in redacted


def test_logging_filter_and_httpx_logger(tmp_path):
    REGISTRY.register(FAKE_KEY)
    configure_logging(tmp_path / "logs")
    logger = logging.getLogger("bidloc.test")
    logger.warning("calling %s", f"https://apis.data.go.kr/a?serviceKey={quote(FAKE_KEY, safe='')}")
    try:
        raise RuntimeError(f"boom {FAKE_KEY}")
    except RuntimeError:
        logger.exception("failed")
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpx").info("HTTP Request: GET https://apis.data.go.kr/a?serviceKey=%s", FAKE_KEY)
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = (tmp_path / "logs" / "bidloc.log").read_text(encoding="utf-8")
    assert FAKE_KEY not in content and quote(FAKE_KEY, safe="") not in content
    assert "boom" in content


def test_filter_handles_record_args():
    REGISTRY.register(FAKE_KEY)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key=%s", (FAKE_KEY,), None)
    RedactingFilter().filter(record)
    assert FAKE_KEY not in record.getMessage()


@pytest.mark.parametrize("chunk_bytes", [1, 7, 1024])
def test_stream_scan_detects_secrets_across_chunk_boundaries(chunk_bytes):
    import io

    REGISTRY.register(FAKE_KEY)
    body = b"padding" + quote(FAKE_KEY, safe="").encode() + b"tail"
    assert REGISTRY.contains_secret_stream(io.BytesIO(body), chunk_bytes=chunk_bytes)
    assert not REGISTRY.contains_secret_stream(io.BytesIO(b"safe synthetic content"), chunk_bytes=chunk_bytes)


def test_cached_exception_and_stack_info_are_redacted():
    REGISTRY.register(FAKE_KEY)
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "error", (), None)
    record.exc_text = f"cached error {FAKE_KEY}"
    record.stack_info = f"stack {FAKE_KEY}"
    RedactingFilter().filter(record)
    assert FAKE_KEY not in logging.Formatter().format(record)
