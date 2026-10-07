"""P1 프로브를 합성 응답으로 확인한다. 네트워크 없음."""

import pytest
from bidloc.clients.errors import Outcome
from bidloc.clients.http import ApiResult
from bidloc.config import ConfigError
from bidloc.probe import inspect_result, probe_requests, run_probe


def test_probe_requests_cover_four_operations_without_license_filter():
    requests = list(probe_requests("20990203", "100,999"))
    assert len(requests) == 8
    assert {r[0] for r in requests} == {"LIST", "LICENSE", "REGION", "OPENING"}
    for _, _, _, _, params in requests:
        assert params["inqryBgnDt"] == "209902030000"
        assert params["inqryEndDt"] == "209902032359"
        assert not {"indstrytyCd", "indstrytyNm"} & params.keys()


@pytest.mark.parametrize("day,sizes", [("20990230", "100"), ("20990203", "1000"),
                                       ("20990203", "100,100"), ("20990203", "0")])
def test_probe_invalid_request_rejected(day, sizes):
    with pytest.raises(ConfigError):
        list(probe_requests(day, sizes))


def test_empty_response_does_not_claim_fields_verified():
    result = ApiResult("bid_notice", "op", Outcome.SUCCESS_EMPTY, "synthetic", result_code="00", items=[], total_count=0)
    finding = inspect_result("LIST", 999, result)
    assert finding["field_verification"] == "UNVERIFIED" and not finding["returned_999_rows"]


@pytest.mark.parametrize("outcome", [Outcome.AUTH_KEY_INVALID, Outcome.IP_NOT_ALLOWED, Outcome.QUOTA_DAILY_EXCEEDED])
def test_probe_stops_after_fatal_response_and_persists_daily_quota(outcome):
    class Fake:
        calls = 0
        exhausted = []
        def call(self, *args):
            self.calls += 1
            return ApiResult("bid_notice", "op", outcome, "synthetic")
        def mark_quota_exhausted(self, svc, op):
            self.exhausted.append(svc)
    fake = Fake()
    findings, stop = run_probe(fake, list(probe_requests("20990203", "100,999")), fake)
    assert len(findings) == fake.calls == 1 and stop == outcome.value
    assert bool(fake.exhausted) == (outcome == Outcome.QUOTA_DAILY_EXCEEDED)
