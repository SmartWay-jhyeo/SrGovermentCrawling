"""카탈로그 계약 검증(추측 금지·상태 구분)과 기간 분할(API-09)."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

import pytest

from bidloc.catalog import ALLOWED_STATUSES, validate_catalog_data
from bidloc.collectors.windows import dedupe_by_key, split_windows
from bidloc.timeutil import KST

OPENING_KEY = ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"]


def test_catalog_hosts_statuses_and_evidence(catalog):
    for svc in catalog.services.values():
        parts = urlsplit(svc.base_url)
        assert parts.scheme == "https" and parts.hostname == "apis.data.go.kr"
        for op in svc.operations.values():
            assert op.status in ALLOWED_STATUSES
            if op.status == "LIVE_VERIFIED":
                assert op.raw.get("live_evidence")
            assert op.auth_param.lower() == "servicekey"
            assert all(name.lower() != "servicekey" for name in op.params)


def test_catalog_contains_core_construction_operations(catalog):
    expected = {
        "bid_notice": ["getBidPblancListInfoCnstwk", "getBidPblancListInfoLicenseLimit", "getBidPblancListInfoPrtcptPsblRgn",
                       "getBidPblancListInfoCnstwkBsisAmount", "getBidPblancListInfoChgHstryCnstwk"],
        "bid_award": ["getOpengResultListInfoCnstwk", "getOpengResultListInfoOpengCompt", "getScsbidListSttusCnstwk",
                      "getOpengResultListInfoRebid", "getOpengResultListInfoFailing"],
        "industry_law": ["getIndstrytyBaseLawrgltInfoList"],
    }
    for sid, ops in expected.items():
        for name in ops:
            op = catalog.operation(sid, name)
            assert op.in_p0_scope and op.status in {"DOCUMENTED", "LIVE_VERIFIED"}
    for name in ("getOpengResultListInfoCnstwk", "getOpengResultListInfoOpengCompt", "getScsbidListSttusCnstwk"):
        op = catalog.operation("bid_award", name)
        assert op.raw["record_key"]["fields"][:4] == OPENING_KEY
        assert op.raw["record_key"]["status"] == "UNVERIFIED"
    roster = catalog.operation("bid_award", "getOpengResultListInfoOpengCompt")
    assert set(OPENING_KEY) <= set(roster.params)


def test_catalog_does_not_invent_industry_code_or_live_status(catalog):
    codes = catalog.data["industry_codes"]
    evidence_runs = catalog.data["live_verification"]["evidence_runs"]
    # 업종코드는 실응답 근거가 있을 때만 값이 있다
    assert (codes["code"] is None and codes["status"] == "UNVERIFIED") or (codes["status"] == "LIVE_VERIFIED" and evidence_runs)
    assert catalog.status_counts().get("LIVE_VERIFIED", 0) == 0 or evidence_runs
    run_ids = {r["run_id"] for r in evidence_runs}
    for svc in catalog.services.values():
        for op in svc.operations.values():
            if op.status == "LIVE_VERIFIED":
                assert set(op.raw["live_evidence"]["runs"]) <= run_ids and op.raw["live_observations"]


def test_catalog_records_count_and_region_semantics(catalog):
    counts = catalog.data["count_definitions"]
    assert counts["prtcptCnum"]["meaning_status"] in {"UNVERIFIED", "LIVE_VERIFIED"}
    assert "0이 아니다" in counts["zero_policy"]
    region = catalog.data["region_semantics"]
    assert "prtcptPsblRgnNm" in region["allowed_region"] and "cnstrtsiteRgnNm" in region["construction_site"]


def test_validate_catalog_rejects_bad_entries():
    good_op = {"status": "DOCUMENTED", "auth_param": "serviceKey", "request_params": [{"name": "pageNo"}]}
    base = {"catalog_schema_version": 1, "services": {"svc": {"base_url": "https://apis.data.go.kr/x", "status": "DOCUMENTED",
                                                              "operations": {"getX": good_op}}}}
    assert validate_catalog_data(base) == []
    bad_scheme = {**base, "services": {"svc": {**base["services"]["svc"], "base_url": "http://apis.data.go.kr/x"}}}
    assert any("https" in e for e in validate_catalog_data(bad_scheme))
    bad_host = {**base, "services": {"svc": {**base["services"]["svc"], "base_url": "https://evil.example/x"}}}
    assert any("호스트" in e for e in validate_catalog_data(bad_host))
    live_no_evidence = {**base, "services": {"svc": {**base["services"]["svc"],
                                                     "operations": {"getX": {**good_op, "status": "LIVE_VERIFIED"}}}}}
    assert any("live_evidence" in e for e in validate_catalog_data(live_no_evidence))


def test_split_windows_covers_range_without_gaps():
    begin = datetime(2025, 1, 1, 0, 0, tzinfo=KST)
    end = datetime(2025, 3, 15, 23, 59, tzinfo=KST)
    windows = split_windows(begin, end, max_span=timedelta(days=31))
    assert windows[0].inqry_bgn_dt == "202501010000" and windows[-1].inqry_end_dt == "202503152359"
    for prev, nxt in zip(windows, windows[1:]):
        assert nxt.begin - prev.end == timedelta(minutes=1)
        assert prev.end - prev.begin <= timedelta(days=31)


def test_split_windows_with_overlap_and_validation():
    begin = datetime(2025, 1, 1, tzinfo=KST)
    windows = split_windows(begin, begin + timedelta(hours=5), max_span=timedelta(hours=2), overlap=timedelta(minutes=10))
    assert windows[1].begin < windows[0].end
    with pytest.raises(ValueError):
        split_windows(datetime(2025, 1, 1), datetime(2025, 1, 2), max_span=timedelta(days=1))
    with pytest.raises(ValueError):
        split_windows(begin, begin, max_span=timedelta(hours=1), overlap=timedelta(hours=1))


def test_dedupe_keeps_conflicts():
    items = [
        {"bidNtceNo": "R99BK1", "bidNtceOrd": "000", "v": "a"},
        {"bidNtceNo": "R99BK1", "bidNtceOrd": "000", "v": "a"},   # 경계 겹침으로 같은 행 중복
        {"bidNtceNo": "R99BK1", "bidNtceOrd": "000", "v": "b"},   # 같은 키, 다른 내용 → 충돌 보존
        {"bidNtceNo": "R99BK1", "bidNtceOrd": "001", "v": "a"},
        {"bidNtceOrd": "000"},
    ]
    result = dedupe_by_key(items, lambda i: (i["bidNtceNo"], i["bidNtceOrd"]) if "bidNtceNo" in i else None)
    assert result.exact_duplicates == 1 and result.conflicting_keys == [("R99BK1", "000")]
    assert len(result.unique) == 4 and result.keyless == 1
