"""Synthetic records only; the API is exercised in-process without external sockets or real data."""
from datetime import datetime
import socket

import pytest
from starlette.testclient import TestClient

from bidloc.api import Snapshots, create_app, main
from bidloc.recommend import ProfileError, recommend, resolve_license, resolve_region
from bidloc.timeutil import KST
from tests.unit.test_analysis import record

_SOCKET_CONNECT = socket.socket.connect
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=KST)
FUTURE = "2099-01-01 10:00:00"


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    # The Windows asyncio loop behind TestClient uses a loopback socketpair; external hosts stay blocked.
    def connect(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("외부 네트워크는 테스트에서 금지됩니다.")
        return _SOCKET_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)


def g2b(key, **changes):
    # Real G2B record keys are "<notice no>-<notice ord>".
    return record(f"{key}-000", **{"bid_ntce_no": key, "bid_ntce_ord": "000", "bid_ntce_dt": "2026-10-02 10:00:00",
                          "deadline": FUTURE, "ntce_instt_nm": "합성기관", **changes})


def provider(key, source="lh", **changes):
    row = {"key": f"{source}:{key}", "source": source, "source_label": source, "notice_no": key, "version": None,
           "title": "합성 방수 공사", "agency": "합성기관", "business": "공사", "business_basis": None,
           "notice_date": "2026-10-07", "deadline": "2099-01-02T10:00:00", "deadline_label": "마감", "amount": 100,
           "amount_label": "추정가격", "amounts": {"추정가격": 100}, "allowed_regions": ["경기도"],
           "allowed_regions_basis": "참가지역", "site_region": None, "site_region_label": None, "office": None,
           "licenses": ["습식·방수공사업"], "license_text": None, "license_basis": "요구면허", "contract": "제한경쟁",
           "cancelled": False, "progress": None, "url": None, "url_label": None, "flags": [],
           "source_response_id": 1, "detail_response_id": None, "collected_at": "2026-10-08", "snapshot": "2026-10-08",
           "versions_observed": 1}
    row.update(changes)
    return row


RECORDS = [g2b("G-MATCH", regions=["경기도 남양주시"], deadline="2099-03-01 10:00:00"),
           g2b("G-NOREGION", regions=[]),
           g2b("G-OTHER", regions=["부산광역시"]),
           g2b("G-CLOSED", regions=["경기도"], deadline="2026-10-01 10:00:00"),
           g2b("G-OTHERLICENSE", regions=["경기도"], license_codes=["0001"])]
ROWS = [provider("L-MATCH"), provider("L-BUSAN", allowed_regions=["부산광역시"]),
        provider("K-UNKNOWN", source="kapt", licenses=None, allowed_regions=None, license_basis="면허 정보 미제공",
                 allowed_regions_basis="참가지역 정보 미제공", site_region="경기도", site_region_label="단지 소재 시·도",
                 deadline="2099-01-01T09:00:00")]
VOCAB = {"경기도", "경기도 남양주시", "경기도 광주시", "광주광역시 북구", "부산광역시"}


def test_profile_inputs_resolve_or_return_candidates():
    assert resolve_region("남양주", VOCAB) == "경기도 남양주시"
    assert resolve_region("경기 남양주시", VOCAB) == "경기도 남양주시"
    assert resolve_region("광주시", VOCAB) == "경기도 광주시"
    with pytest.raises(ProfileError):
        resolve_region("없는곳", VOCAB)
    assert resolve_license("도장습식방수") == resolve_license("4992") == "4992"
    with pytest.raises(ProfileError):
        resolve_license("전기공사업")


def test_recommendations_merge_sources_and_count_what_is_held_back():
    result = recommend(RECORDS, ROWS, region="남양주", license="도장·습식·방수·석공사업", known_regions=VOCAB, now=NOW)
    keys = [i["key"] for i in result["items"]]
    # Published conditions that match come first, then unknowns ordered by the nearest deadline.
    assert keys == ["lh:L-MATCH", "g2b:G-MATCH-000", "kapt:K-UNKNOWN", "g2b:G-NOREGION-000"]
    assert result["counts"]["by_state"] == {"조건 일치": 2, "확인 필요": 2}
    assert result["counts"]["held_back"] == {"참가지역 불일치": 2}
    kapt = next(i for i in result["items"] if i["source"] == "kapt")
    assert kapt["allowed_regions"] is None and kapt["site"] == {"value": "경기도", "kind": "단지 소재 시·도"}
    assert result["profile"]["region"] == "경기도 남양주시" and result["complete"]
    only_match = recommend(RECORDS, ROWS, region="경기도 남양주시", include_unknown=False, known_regions=VOCAB, now=NOW)
    assert [i["key"] for i in only_match["items"]] == ["lh:L-MATCH", "g2b:G-MATCH-000"]


def test_unknown_region_notices_are_scoped_by_site_province_and_counted():
    far = provider("K-FAR", source="kapt", licenses=None, allowed_regions=None, site_region="부산광역시",
                   site_region_label="단지 소재 시·도", license_basis="면허 정보 미제공", allowed_regions_basis="미제공")
    nosite = provider("K-NOSITE", source="kapt", licenses=None, allowed_regions=None, site_region=None,
                      license_basis="면허 정보 미제공", allowed_regions_basis="미제공")
    rows = ROWS + [far, nosite]
    default = recommend(RECORDS, rows, region="남양주", known_regions=VOCAB, now=NOW)
    keys = {i["key"] for i in default["items"]}
    assert "kapt:K-FAR" not in keys and "kapt:K-NOSITE" in keys
    assert default["counts"]["held_back"]["참가지역 정보 없음 · 현장 시·도 범위 밖"] == 1
    assert default["profile"]["site_provinces"] == ["경기도"]
    wide = recommend(RECORDS, rows, region="남양주", site_provinces="all", known_regions=VOCAB, now=NOW)
    assert "kapt:K-FAR" in {i["key"] for i in wide["items"]} and wide["profile"]["site_provinces"] == "all"
    listed = recommend(RECORDS, rows, region="남양주", site_provinces="경기,부산", known_regions=VOCAB, now=NOW)
    assert "kapt:K-FAR" in {i["key"] for i in listed["items"]}
    with pytest.raises(ProfileError):
        recommend(RECORDS, rows, region="남양주", site_provinces="달나라", known_regions=VOCAB, now=NOW)


def test_missing_amount_is_held_back_and_g2b_loading_is_explicit():
    rows = ROWS + [provider("L-NOPRICE", amount=None)]
    result = recommend(RECORDS, rows, region="경기도 남양주시", min_amount=1, known_regions=VOCAB, now=NOW)
    assert "lh:L-NOPRICE" not in [i["key"] for i in result["items"]]
    assert result["counts"]["held_back"]["추정가격 미제공"] >= 1
    loading = recommend([], ROWS, region="경기도 남양주시", known_regions=VOCAB, g2b_ready=False, now=NOW)
    assert not loading["complete"] and loading["warnings"] and {i["source"] for i in loading["items"]} == {"lh", "kapt"}


class FakeSnapshots(Snapshots):
    def __init__(self, records, rows, ready=True):
        super().__init__("synthetic.sqlite3", "synthetic", "providers.sqlite3")
        self._records, self._rows, self._ready = records, rows, ready

    def g2b(self):
        return (self._records, {"snapshot_id": "synthetic"}, "id") if self._ready else None

    def providers(self):
        return self._rows, {"sources": {}}


def test_http_recommendations_validation_and_paging():
    client = TestClient(create_app(FakeSnapshots(RECORDS, ROWS), data_mode="synthetic"))
    page = client.get("/api/v1/recommendations", params={"region": "남양주", "license": "4992", "limit": 2})
    body = page.json()
    assert page.status_code == 200 and len(body["items"]) == 2 and body["next_offset"] == 2
    assert body["data"]["data_mode"] == "synthetic" and body["counts"]["total"] == 4
    assert client.get("/api/v1/recommendations", params={"region": "남양주", "offset": 2}).json()["next_offset"] is None
    bad = client.get("/api/v1/recommendations", params={"region": "남양주", "license": "전기공사업"})
    assert bad.status_code == 400 and bad.json()["candidates"] == ["4992"]
    assert client.get("/api/v1/recommendations", params={"region": "남양주", "limit": "500"}).status_code == 400
    assert client.get("/api/v1/recommendations", params={"region": ""}).status_code == 400
    status = client.get("/api/v1/collection/status").json()
    assert status["g2b"]["state"] == "READY" and status["g2b"]["records"] == len(RECORDS)


def test_token_is_required_when_configured_and_remote_bind_needs_it(monkeypatch):
    client = TestClient(create_app(FakeSnapshots(RECORDS, ROWS), token="SYNTHETIC-TOKEN"))
    assert client.get("/api/v1/health").status_code == 401
    assert client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/v1/health", headers={"Authorization": "Bearer SYNTHETIC-TOKEN"})
    assert ok.status_code == 200 and ok.json()["g2b_snapshot"] == "READY"
    monkeypatch.delenv("BIDLOC_API_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        main(["--host", "0.0.0.0"])


def test_snapshot_reload_keeps_serving_previous_data(tmp_path):
    database = tmp_path / "bidloc.sqlite3"
    database.write_bytes(b"synthetic")
    calls, stamps, clock = [], ["v1"], [0.0]
    def reader(path, mode):
        calls.append(stamps[0])
        if stamps[0] == "broken":
            raise RuntimeError("synthetic failure")
        return [g2b("G-" + stamps[0])], {"snapshot_id": stamps[0]}
    snaps = Snapshots(database, "synthetic", tmp_path / "none.sqlite3", reader=reader,
                      stamp=lambda *a: stamps[0], clock=lambda: clock[0])
    snaps.refresh_g2b(wait=True)
    assert snaps.g2b()[1]["snapshot_id"] == "v1" and calls == ["v1"]
    stamps[0] = "broken"
    snaps.refresh_g2b(wait=True)
    assert calls == ["v1"]  # rechecked only after the interval
    clock[0] = 120.0
    snaps.refresh_g2b(wait=True)
    # A failed rebuild leaves the previous snapshot in service and records only the error class.
    assert snaps.g2b()[1]["snapshot_id"] == "v1" and snaps.error == "RuntimeError"
    stamps[0], clock[0] = "v2", 240.0
    snaps.refresh_g2b(wait=True)
    assert snaps.g2b()[1]["snapshot_id"] == "v2" and snaps.error is None
    assert snaps.providers()[1]["status"] == "NOT_COLLECTED"
