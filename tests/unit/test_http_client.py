"""API-01/02/04/05, SEC-01: 실호출 게이트, 키 인코딩, 재시도·예산, 리다이렉트, 저장 마스킹."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from bidloc.clients.budget import CallBudget
from bidloc.clients.errors import Outcome
from bidloc.clients.http import (
    DataGoKrClient,
    LiveCallBlocked,
    ParameterError,
    evaluate_live_gate,
    parse_retry_after,
)
from bidloc.redaction import MASK
from tests.conftest import FAKE_KEY, make_settings
from tests.unit.helpers import build_client, gateway_xml, std_json

SVC, OP = "bid_notice", "getBidPblancListInfoCnstwk"
PARAMS = {"inqryDiv": "2", "bidNtceNo": "R99BK99990001", "pageNo": "1", "numOfRows": "10"}


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=std_json({"item": [{"bidNtceNo": "R99BK99990001", "bidNtceOrd": "000"}]}, 1))


@pytest.mark.parametrize("overrides,cli_live,reason", [
    ({"ALLOW_LIVE_API": "true", "DATA_GO_KR_SERVICE_KEY": FAKE_KEY}, False, "--live"),
    ({"ALLOW_LIVE_API": "false", "DATA_GO_KR_SERVICE_KEY": FAKE_KEY}, True, "ALLOW_LIVE_API"),
    ({"ALLOW_LIVE_API": "true", "DATA_GO_KR_SERVICE_KEY": ""}, True, "DATA_GO_KR_SERVICE_KEY"),
    ({"ALLOW_LIVE_API": "true", "DATA_GO_KR_SERVICE_KEY": FAKE_KEY, "DATA_MODE": "demo"}, True, "DATA_MODE"),
])
def test_live_gate_blocks_and_never_touches_transport(project: Path, catalog, overrides, cli_live, reason):
    settings = make_settings(project, **overrides)
    gate = evaluate_live_gate(settings, cli_live)
    assert not gate.allowed and any(reason in r for r in gate.reasons)
    called = []
    with pytest.raises(LiveCallBlocked):
        DataGoKrClient(settings=settings, catalog=catalog, gate=gate,
                       budget=CallBudget(project / "x.sqlite3", max_per_run=1, max_per_day=1), recorder=None,
                       run_id=None, transport=httpx.MockTransport(lambda r: called.append(r) or httpx.Response(200)))
    assert called == []


def test_decoded_key_is_encoded_exactly_once(project: Path, catalog):
    b = build_client(project, catalog, ok_handler)
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.SUCCESS
    request = b.requests[0]
    raw_query = request.url.query.decode("ascii")
    assert f"serviceKey={quote(FAKE_KEY, safe='')}" in raw_query
    assert "%25" not in raw_query  # 이중 인코딩 없음
    assert request.url.params["serviceKey"] == FAKE_KEY
    assert request.url.scheme == "https" and request.url.host == "apis.data.go.kr"
    assert request.url.path == "/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk"
    assert request.url.params["type"] == "json"


def test_encoded_key_is_not_reencoded(project: Path, catalog):
    encoded = quote(FAKE_KEY, safe="")
    b = build_client(project, catalog, ok_handler, key=encoded, key_format="encoded")
    b.client.call(SVC, OP, PARAMS)
    raw_query = b.requests[0].url.query.decode("ascii")
    assert f"serviceKey={encoded}" in raw_query and "%252B" not in raw_query
    assert b.requests[0].url.params["serviceKey"] == FAKE_KEY


def test_korean_param_values_are_percent_encoded(project: Path, catalog):
    b = build_client(project, catalog, ok_handler)
    b.client.call(SVC, "getBidPblancListInfoCnstwkPPSSrch",
                  {"inqryDiv": "1", "inqryBgnDt": "209901010000", "inqryEndDt": "209901012359", "indstrytyNm": "도장",
                   "pageNo": "1", "numOfRows": "10"})
    assert b.requests[0].url.params["indstrytyNm"] == "도장"


@pytest.mark.parametrize("params,match", [
    ({"inqryDiv": "2", "notDocumented": "x"}, "에 없는 요청 파라미터"),
    ({"inqrydiv": "2"}, "대소문자"),
    ({"serviceKey": "x"}, "인증키"),
    ({"inqryDiv": "2\r\nX: y"}, "제어문자"),
])
def test_undocumented_or_unsafe_params_rejected_before_network(project: Path, catalog, params, match):
    b = build_client(project, catalog, ok_handler)
    with pytest.raises(ParameterError, match=match):
        b.client.call(SVC, OP, params)
    assert b.requests == [] and b.budget.run_used == 0


def test_unknown_operation_rejected(project: Path, catalog):
    from bidloc.catalog import CatalogError

    b = build_client(project, catalog, ok_handler)
    with pytest.raises(CatalogError):
        b.client.call(SVC, "getInventedOperation", {})


def test_retry_on_upstream_error_counts_budget_and_backs_off(project: Path, catalog):
    responses = iter([httpx.Response(500, content=b"err"), httpx.Response(200, content=std_json({"item": []}, 0))])
    b = build_client(project, catalog, lambda r: next(responses))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.SUCCESS_EMPTY and result.attempts == 2
    assert b.budget.run_used == 2 and b.budget.day_used() == 2
    assert b.clock.sleeps and b.clock.sleeps[0] >= 1.0
    rows = b.conn.execute("SELECT attempt_no, outcome FROM source_response ORDER BY id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "UPSTREAM_ERROR"), (2, "SUCCESS_EMPTY")]


def test_http_200_gateway_auth_error_is_not_retried_or_stored_as_success(project: Path, catalog):
    b = build_client(project, catalog, lambda r: httpx.Response(200, content=gateway_xml("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR")))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.AUTH_KEY_INVALID and result.attempts == 1 and result.items is None
    assert b.conn.execute("SELECT outcome, item_count FROM source_response").fetchone()[0] == "AUTH_KEY_INVALID"


def test_daily_quota_error_stops_without_retry(project: Path, catalog):
    b = build_client(project, catalog,
                     lambda r: httpx.Response(200, content=gateway_xml("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.QUOTA_DAILY_EXCEEDED and result.attempts == 1 and b.clock.sleeps == []


def test_429_respects_retry_after(project: Path, catalog):
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}, content=b"Too Many Requests"),
                      httpx.Response(200, content=std_json({"item": [{"bidNtceNo": "R99BK99990001"}]}, 1))])
    b = build_client(project, catalog, lambda r: next(responses))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.SUCCESS and result.attempts == 2
    assert b.clock.sleeps[0] >= 7.0


def test_retry_after_too_long_stops(project: Path, catalog):
    b = build_client(project, catalog, lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.RATE_LIMITED and result.attempts == 1 and "Retry-After" in result.basis


def test_per_second_limit_retries_are_bounded(project: Path, catalog):
    b = build_client(project, catalog,
                     lambda r: httpx.Response(200, content=gateway_xml("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR")),
                     retries=3)
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.RATE_LIMITED and result.attempts == 3 and b.budget.run_used == 3
    assert len(b.clock.sleeps) == 2


def test_timeout_retries_count_in_budget_and_stop_at_run_budget(project: Path, catalog):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    b = build_client(project, catalog, timeout, retries=5, max_run=2)
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.BUDGET_EXHAUSTED_RUN
    assert result.attempts == 2 and b.budget.run_used == 2 and len(b.requests) == 2


def test_daily_budget_blocks_before_sending(project: Path, catalog):
    b = build_client(project, catalog, ok_handler, max_day=1)
    assert b.client.call(SVC, OP, PARAMS).outcome == Outcome.SUCCESS
    second = b.client.call(SVC, OP, PARAMS)
    assert second.outcome == Outcome.BUDGET_EXHAUSTED_DAY and len(b.requests) == 1


def test_redirect_is_not_followed(project: Path, catalog):
    b = build_client(project, catalog, lambda r: httpx.Response(302, headers={"Location": "https://evil.example/steal"}))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.REDIRECT_BLOCKED and len(b.requests) == 1


def test_response_size_limit(project: Path, catalog):
    b = build_client(project, catalog, lambda r: httpx.Response(200, content=b"<" + b"x" * 5000), MAX_RESPONSE_BYTES="2048")
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.RESPONSE_TOO_LARGE


def test_throttle_enforces_interval(project: Path, catalog):
    b = build_client(project, catalog, ok_handler, interval="1.5")
    b.client.call(SVC, OP, PARAMS)
    b.client.call(SVC, OP, PARAMS)
    assert b.clock.sleeps == [1.5]


def test_saved_metadata_and_raw_body_never_contain_key(project: Path, catalog):
    echo = f'{{"response":{{"header":{{"resultCode":"00","resultMsg":"echo {quote(FAKE_KEY, safe="")} {FAKE_KEY}"}},' \
           f'"body":{{"items":{{"item":[{{"bidNtceNo":"R99BK99990001"}}]}},"totalCount":"1"}}}}}}'
    b = build_client(project, catalog, lambda r: httpx.Response(200, content=echo.encode()))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.SUCCESS
    row = b.conn.execute("SELECT request_url_redacted, request_params_redacted_json, raw_path, redaction_applied, result_msg "
                         "FROM source_response").fetchone()
    assert MASK in row[0] and FAKE_KEY not in row[0] and quote(FAKE_KEY, safe="") not in row[0]
    assert row[3] == 1 and FAKE_KEY not in (row[4] or "")
    for path in project.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert FAKE_KEY.encode() not in data, path
            assert quote(FAKE_KEY, safe="").encode() not in data, path


def test_network_error_detail_is_redacted(project: Path, catalog):
    def fail(request):
        raise httpx.ConnectError(f"cannot connect {request.url}", request=request)

    b = build_client(project, catalog, fail, retries=1)
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.NETWORK_ERROR
    assert FAKE_KEY not in (result.error_detail or "") and quote(FAKE_KEY, safe="") not in (result.error_detail or "")


def test_parse_retry_after_http_date():
    from datetime import datetime, timezone

    now = datetime(2026, 9, 16, 0, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Wed, 16 Sep 2026 00:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("garbage") is None


@pytest.mark.parametrize("allow,cli_live", [(False, False), (False, True), (True, False), (True, True)])
def test_live_gate_requires_both_switches(project, allow, cli_live):
    settings = make_settings(project, DATA_GO_KR_SERVICE_KEY=FAKE_KEY,
                             ALLOW_LIVE_API=str(allow).lower())
    assert evaluate_live_gate(settings, cli_live).allowed is (allow and cli_live)


def test_echoed_key_is_removed_from_items_before_downstream_storage(project, catalog):
    body = std_json([{"bidNtceNo": "SYNTHETIC-NOTICE", "echo": FAKE_KEY}], 1)
    b = build_client(project, catalog, lambda r: httpx.Response(200, content=body))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.data_ok and result.items[0]["echo"] == MASK
    assert FAKE_KEY not in repr(result)


def test_echoed_key_in_error_code_and_truncated_snippet_is_removed(project, catalog):
    body = std_json([], 0, code=FAKE_KEY)
    b = build_client(project, catalog, lambda r: httpx.Response(200, content=body))
    result = b.client.call(SVC, OP, PARAMS)
    assert not result.data_ok and FAKE_KEY not in repr(result)
    assert FAKE_KEY not in str([tuple(row) for row in b.conn.execute("SELECT * FROM source_response")])
    b.client.close()
    b.conn.close()

    truncated = b"x" * 290 + FAKE_KEY.encode()
    b = build_client(project, catalog, lambda r: httpx.Response(400, content=truncated))
    result = b.client.call(SVC, OP, PARAMS)
    assert FAKE_KEY[:10] not in (result.error_detail or "")


def test_empty_http_response_is_recorded_with_body_hash(project, catalog):
    import hashlib

    b = build_client(project, catalog, lambda r: httpx.Response(200, content=b""))
    result = b.client.call(SVC, OP, PARAMS)
    assert result.outcome == Outcome.MALFORMED_RESPONSE
    row = b.conn.execute("SELECT raw_path, body_sha256, body_bytes FROM source_response").fetchone()
    assert row[0] and row[1] == hashlib.sha256(b"").hexdigest() and row[2] == 0
    assert (b.settings.raw_response_dir / row[0]).read_bytes() == b""
