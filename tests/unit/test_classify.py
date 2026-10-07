"""API-02(권한 거부), API-03(HTTP 200 내부 오류), API-05(429·초당·일일 제한) 분류."""

from __future__ import annotations

import json

import pytest

from bidloc.clients.envelope import parse_body
from bidloc.clients.errors import DATA_OK, RETRYABLE, RUN_FATAL, SERVICE_FATAL, Outcome, classify_response


def std(code: str, msg: str = "", items=None, total: str = "0") -> bytes:
    body = {"items": items if items is not None else "", "totalCount": total, "numOfRows": "10", "pageNo": "1"}
    return json.dumps({"response": {"header": {"resultCode": code, "resultMsg": msg}, "body": body}}).encode()


def gw(code: str, auth_msg: str) -> bytes:
    return (f"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>{auth_msg}"
            f"</returnAuthMsg><returnReasonCode>{code}</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>").encode()


def classify(status: int, body: bytes) -> Outcome:
    return classify_response(status, parse_body(body)).outcome


def test_success_empty_and_no_data_are_distinct():
    assert classify(200, std("00", items={"item": [{"a": "1"}]}, total="1")) == Outcome.SUCCESS
    assert classify(200, std("00", items="", total="0")) == Outcome.SUCCESS_EMPTY
    assert classify(200, std("03", "NODATA_ERROR")) == Outcome.NO_DATA
    assert {Outcome.SUCCESS, Outcome.SUCCESS_EMPTY, Outcome.NO_DATA} == set(DATA_OK)


def test_http_200_xml_gateway_error_when_json_requested_is_not_success():
    outcome = classify(200, gw("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))
    assert outcome == Outcome.AUTH_KEY_INVALID
    assert outcome not in DATA_OK and outcome in SERVICE_FATAL and outcome not in RETRYABLE


@pytest.mark.parametrize("code,msg,expected", [
    ("20", "SERVICE_ACCESS_DENIED_ERROR", Outcome.ACCESS_DENIED),
    ("20", "PERMISSION_DENIED", Outcome.ACCESS_DENIED),
    ("20", "SERVICE_KEY_IS_NULL", Outcome.AUTH_KEY_MISSING),
    ("31", "DEADLINE_HAS_EXPIRED_ERROR", Outcome.AUTH_KEY_EXPIRED),
    ("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", Outcome.QUOTA_DAILY_EXCEEDED),
    ("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR", Outcome.RATE_LIMITED),
    ("29", "BLACKLIST_IP_ACCESS_ERROR", Outcome.IP_NOT_ALLOWED),
    ("12", "NO_OPENAPI_SERVICE_ERROR", Outcome.SERVICE_NOT_FOUND),
    ("05", "SERVICETIMEOUT_ERROR", Outcome.UPSTREAM_ERROR),
    ("10", "INVALID_REQUEST_PARAMETER_ERROR", Outcome.INVALID_PARAMETER),
])
def test_gateway_tokens(code, msg, expected):
    assert classify(200, gw(code, msg)) == expected


def test_quota_is_run_fatal_and_not_retryable():
    assert Outcome.QUOTA_DAILY_EXCEEDED in RUN_FATAL and Outcome.QUOTA_DAILY_EXCEEDED not in RETRYABLE
    assert Outcome.RATE_LIMITED in RETRYABLE


@pytest.mark.parametrize("code,expected", [("06", Outcome.INVALID_PARAMETER), ("07", Outcome.INVALID_PARAMETER),
                                           ("08", Outcome.INVALID_PARAMETER), ("01", Outcome.UPSTREAM_ERROR),
                                           ("30", Outcome.AUTH_KEY_INVALID), ("99", Outcome.UNKNOWN_API_ERROR)])
def test_standard_envelope_error_codes_without_tokens(code, expected):
    assert classify(200, std(code, "message")) == expected


def test_http_status_fallbacks():
    assert classify(429, b"Too Many Requests") == Outcome.RATE_LIMITED
    assert classify(401, b"") == Outcome.AUTH_KEY_INVALID
    assert classify(403, b"<html>blocked</html>") == Outcome.ACCESS_DENIED
    assert classify(503, b"") == Outcome.UPSTREAM_ERROR
    assert classify(302, b"") == Outcome.REDIRECT_BLOCKED
    assert classify(418, b"") == Outcome.HTTP_ERROR
    assert classify(200, b"<html>maintenance</html>") == Outcome.MALFORMED_RESPONSE
    assert classify(200, b"plain text") == Outcome.MALFORMED_RESPONSE


def test_success_code_with_envelope_problem_is_malformed():
    body = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"items": {"x": 1}, "totalCount": "1"}}}).encode()
    assert classify(200, body) == Outcome.MALFORMED_RESPONSE


def test_success_code_with_non_2xx_is_not_success():
    assert classify(500, std("00", items={"item": [{"a": "1"}]}, total="1")) == Outcome.UPSTREAM_ERROR
