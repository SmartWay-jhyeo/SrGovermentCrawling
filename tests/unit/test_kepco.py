"""한전 전자입찰 계약정보(KEPCO) — synthetic rows and responses only; no network or real notices."""
from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from bidloc.catalog import load_catalog
from bidloc.clients.envelope import parse_body
from bidloc.clients.errors import DATA_OK, Outcome, classify_response
from bidloc.provider_collect import collect_provider, daily_windows, init_store
from bidloc.provider_notices import load_provider_notices, search_provider_notices
from bidloc.provider_probe import OPERATIONS, query_params, service_id, settings_for_provider
from bidloc.query_service import SearchFilters
from bidloc.timeutil import KST

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=KST)
DAY = date(2026, 10, 7)
# Field names and value shapes as observed on 2026-10-20 (numbers arrive as strings after envelope parsing).
WORK = {"purchaseType": "ConstructionService", "companyId": "COM01", "no": "E012699001", "name": "합성변전소 옥상 방수공사",
        "presumedPrice": "59629739", "presumedAmount": "65592712", "estimatedPriceBasicAmount": "65592712",
        "noticeDate": "20261007", "createDatetime": "2026-10-07 11:18:56",
        "bidAttendReqCloseDatetime": "2026-10-14 18:00:00", "beginDatetime": "2026-10-07 14:00:00",
        "endDatetime": "2026-10-15 14:00:00", "etc": "공고문 참조", "deliveryLocation": "-", "bidAttendRestrict": None,
        "progressState": "AttendProgress", "emergencyNoticeYn": "0", "competitionType": "Limited",
        "bidType": "QualifiedEval", "placeName": "합성본부 기획관리실", "contractReqDepartmentName": "합성부서",
        "creatorName": "합성담당", "moreInformation": "합성담당 000-0000-0000", "itemType": "Construction",
        "jointSupplyDemandYn": "0", "pqEvalYn": "0",
        "filenlink1": "http://srm.kepco.net/printDownloadAttachment.do?id=syn-1", "filename1": "시방서.hwp",
        "filenlink2": "http://srm.kepco.net/printDownloadAttachment.do?id=syn-2", "filename2": "[공고용] 입찰공고문(합성).hwp"}


def make_store(tmp_path, items):
    path = tmp_path / "providers" / "notices.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = sqlite3.connect(path)
    init_store(store)
    for n, item in enumerate(items):
        store.execute("INSERT INTO collection_job(job_id,provider,params_json,status,received,total_count,updated_at,snapshot) "
                      "VALUES(?,?,?,?,?,?,?,?)", (f"job-{n}", "kepco", "{}", "COMPLETE_RANGE", 1, 1,
                                                  "2026-10-08T00:12:00+09:00", "2026-10-08"))
        store.execute("INSERT INTO notice_observation VALUES(?,?,?,?,?,?,?)",
                      (f"job-{n}", 1, 0, "kepco", 100 + n, f"hash-{n}", json.dumps(item, ensure_ascii=False)))
    store.commit()
    store.close()
    return path


def search(rows, **changes):
    return search_provider_notices(rows, SearchFilters(**{"begin": "2026-10-01", "end": "2026-10-31", "hq": "경기도 합성시",
                                                          "scope": "4992", **changes}), now=NOW)


# ---------------------------------------------------------------- settings, catalog, request

def test_kepco_key_is_its_own_secret_and_never_falls_back(project):
    from bidloc.config import ConfigError, load_settings
    from bidloc.clients.http import build_request_url, evaluate_live_gate, validate_params
    from bidloc.redaction import REGISTRY
    settings = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-PROVIDER",
                                               "KEPCO_API_KEY": "SYNTHETICKEPCOKEY0000000000000000000000000",
                                               "ALLOW_LIVE_API": "true"})
    assert ("KEPCO_API_KEY", "설정됨", "env") in settings.safe_summary()
    assert "KEPCO_API_KEY" not in settings.unknown_env_file_keys
    selected = settings_for_provider(settings, "kepco")
    assert evaluate_live_gate(selected, True).allowed
    catalog = load_catalog(ROOT / "config/provider_api_catalog.yaml")
    op = catalog.operation(service_id("kepco"), OPERATIONS["kepco"])
    assert op.url == "https://bigdata.kepco.co.kr/openapi/v1/electContract.do" and op.auth_param == "apiKey"
    params = query_params("kepco", date(2026, 10, 6), DAY)
    validate_params(op, params)
    url, safe, _ = build_request_url(op, selected.service_key, selected.service_key_format, params)
    assert parse_qs(urlsplit(url).query)["apiKey"] == ["SYNTHETICKEPCOKEY0000000000000000000000000"]
    assert not REGISTRY.contains_secret(safe) and not REGISTRY.contains_secret(str(settings.safe_summary()))
    # Without its own key KEPCO is blocked; the data.go.kr provider key is never sent to the KEPCO host.
    plain = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-PROVIDER", "ALLOW_LIVE_API": "true"})
    assert settings_for_provider(plain, "kepco").service_key is None
    assert not evaluate_live_gate(settings_for_provider(plain, "kepco"), True).allowed
    with pytest.raises(ConfigError) as exc:
        load_settings(project, environ={"KEPCO_API_KEY": "SYNTHETIC SECRET"})
    assert "SYNTHETIC" not in str(exc.value)


def test_kepco_request_has_day_bounds_no_paging_and_a_90_day_limit():
    assert query_params("kepco", date(2026, 10, 6), DAY) == {"noticeBeginDate": "20261006", "noticeEndDate": "20261007",
                                                             "returnType": "json"}
    with pytest.raises(ValueError):
        query_params("kepco", date(2026, 7, 1), date(2026, 10, 7))


def test_env_example_lists_the_kepco_key():
    assert "\nKEPCO_API_KEY=" in (ROOT / ".env.example").read_text(encoding="utf-8")


# ---------------------------------------------------------------- envelope

def test_data_list_root_is_a_success_and_errcd_body_is_an_error():
    parsed = parse_body(b'{"data":[{"no":"E1","presumedPrice":59629739,"bidAttendRestrict":null}]}')
    assert parsed.kind == "standard" and parsed.shape == "json:data-list"
    assert parsed.items == [{"no": "E1", "presumedPrice": "59629739", "bidAttendRestrict": None}]
    assert parsed.total_count == 1 and classify_response(200, parsed).outcome == Outcome.SUCCESS
    assert classify_response(200, parse_body(b'{"data":[]}')).outcome == Outcome.SUCCESS_EMPTY
    # Observed 2026-10-20: a wrong key and a too-long range both answer HTTP 401 with errCd/errMsg.
    for body, code in ((b'{"errCd":"401","errMsg":"InvalidApiKeyException"}', "401"),
                       (b'{"errCd":"400","errMsg":"The date range too long(max 90 days)"}', "400")):
        error = parse_body(body)
        assert error.kind == "flat_error" and error.result_code == code and error.result_msg
        assert classify_response(401, error).outcome not in DATA_OK
    # Anything else under "data", or extra root keys, is not taken as a KEPCO answer.
    assert parse_body(b'{"data":"x"}').kind == "unknown"
    assert parse_body(b'{"data":[],"other":1}').kind == "unknown"


# ---------------------------------------------------------------- collection

def test_single_page_job_is_complete_without_paging_parameters():
    store = sqlite3.connect(":memory:"); init_store(store)
    calls = []
    items = [{"no": f"E{n}"} for n in range(120)]

    def call(service, operation, params, **kwargs):
        calls.append((service, operation, dict(params)))
        return SimpleNamespace(items=items, total_count=len(items), page_no=None, source_response_ids=[1],
                               data_ok=True, outcome=Outcome.SUCCESS)
    job = collect_provider(SimpleNamespace(call=call), SimpleNamespace(mark_quota_exhausted=lambda *a: None),
                           store, "kepco", DAY, DAY, 100, "synthetic", "2026-10-07")
    assert job["status"] == "COMPLETE_RANGE" and job["received"] == 120 and job["total_count"] == 120
    assert calls == [("kepco", "electContract", {"noticeBeginDate": "20261007", "noticeEndDate": "20261007",
                                                 "returnType": "json"})]
    again = collect_provider(SimpleNamespace(call=call), SimpleNamespace(mark_quota_exhausted=lambda *a: None),
                             store, "kepco", DAY, DAY, 100, "synthetic", "2026-10-07")
    assert again["job_id"] == job["job_id"] and len(calls) == 1


def test_kepco_daily_windows_are_one_day_each():
    # One day stays far under the response size limit (about 54 rows, 0.35 MB a day on 2026-10-06..19).
    assert daily_windows(date(2026, 10, 15), ["kepco"], 3) == [("kepco", date(2026, 10, 13), date(2026, 10, 13)),
                                                               ("kepco", date(2026, 10, 14), date(2026, 10, 14)),
                                                               ("kepco", date(2026, 10, 15), date(2026, 10, 15))]


class FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.settings = kwargs["settings"]
        self.budget = kwargs["budget"]
        self.calls = []
        FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def call(self, service, operation, params, **kwargs):
        # Like the real client: every attempt reserves the budget it was given.
        self.budget.reserve(service_id=service, operation=operation)
        self.calls.append(service)
        return SimpleNamespace(items=[{"id": service + str(len(self.calls))}], total_count=1, page_no=1,
                               source_response_ids=[1], data_ok=True, outcome=Outcome.SUCCESS)


def run_daily(project, monkeypatch, environ):
    import bidloc.provider_collect as module
    from bidloc.config import load_settings
    settings = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-PROVIDER",
                                               "ALLOW_LIVE_API": "true", **environ})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    monkeypatch.setattr(module, "now_kst", lambda: datetime(2026, 10, 15, 0, 12, tzinfo=KST))
    monkeypatch.setattr(module, "DataGoKrClient", FakeClient)
    FakeClient.instances = []
    code = module.main(["--live", "--daily", "--providers", "lh,kepco"])
    report = json.loads(next((settings.database_path.parent / "providers").glob("provider-collect-*.json"))
                        .read_text(encoding="utf-8"))
    return code, report


def test_daily_run_reads_kepco_with_its_own_key_and_client(project, monkeypatch):
    code, report = run_daily(project, monkeypatch, {"KEPCO_API_KEY": "SYNTHETICKEPCOKEY0000000000000000000000000"})
    assert code == 0 and report["status"] == "COMPLETED"
    data_go_kr, kepco = FakeClient.instances
    assert data_go_kr.calls == ["lh"] and data_go_kr.settings.service_key.reveal() == "SYNTHETIC-PROVIDER"
    assert kepco.calls == ["kepco", "kepco", "kepco"]
    assert kepco.settings.service_key.reveal() == "SYNTHETICKEPCOKEY0000000000000000000000000"
    assert report["http_calls"] == 4


def test_daily_run_without_kepco_key_skips_it_without_failing_the_rest(project, monkeypatch):
    code, report = run_daily(project, monkeypatch, {})
    assert code == 0 and report["status"] == "COMPLETED"
    assert [c.calls for c in FakeClient.instances] == [["lh"]]
    assert report["skipped"] == {"kepco": "KEPCO_API_KEY가 설정되지 않았다"}


# ---------------------------------------------------------------- normalized rows and recommendation scope

def test_kepco_construction_row_keeps_labelled_amounts_and_links_the_notice_file(tmp_path):
    rows, meta = load_provider_notices(make_store(tmp_path, [WORK]))
    assert len(rows) == 1
    row = rows[0]
    assert row["key"] == "kepco:E012699001" and row["source_label"] == "한전" and row["agency"] == "한국전력공사"
    assert row["business"] == "공사" and row["notice_date"] == "2026-10-07"
    assert row["deadline"] == "2026-10-14T18:00:00" and row["deadline_label"].startswith("입찰참가신청 마감")
    assert row["amount"] == 59629739 and row["amount_label"] == "추정가격"
    assert row["amounts"] == {"추정가격": 59629739, "추정금액": 65592712, "예비가격기초금액": 65592712}
    assert row["contract"] == "제한경쟁 · 적격심사" and row["office"] == "합성본부 기획관리실"
    assert row["licenses"] is None and row["allowed_regions"] is None and row["site_region"] is None
    assert row["license_text"] == "공고문 참조"
    # The attachment named as the notice is linked, over https (the SRM host redirects http to https).
    assert row["url"] == "https://srm.kepco.net/printDownloadAttachment.do?id=syn-2"
    assert row["url_label"] == "공고문 첨부"
    # Contact fields never reach the normalized row.
    assert not any(v in json.dumps(row, ensure_ascii=False, default=str)
                   for v in ("합성담당", "000-0000-0000", "합성부서"))
    assert meta["sources"]["kepco"]["label"] == "한전"
    found, _ = search(rows)
    assert [r["evaluation"]["overall"] for r in found] == ["확인 필요"]


def test_kepco_shortlist_uses_title_or_qualification_text_and_skips_other_work(tmp_path):
    named = dict(WORK, no="E082699002", companyId="COM08", name="합성 가스터빈 공기흡입설비 정비공사",
                 etc="『건설산업기본법』에 의한 [도장·습식·방수·석공사업(주력분야 : 도장)] 면허를 보유한 업체",
                 emergencyNoticeYn="1")
    plain = dict(WORK, no="E012699003", name="합성 무인보안시스템 교체공사", etc="전기공사업 등록 업체")
    service = dict(WORK, no="E012699004", itemType="Service")
    goods = dict(WORK, no="G012699005", itemType="Product", purchaseType="Product")
    rows, _ = load_provider_notices(make_store(tmp_path, [named, plain, service, goods]))
    by_no = {r["notice_no"]: r for r in rows}
    assert by_no["E082699002"]["agency"] == "한국동서발전(주)" and "긴급공고" in by_no["E082699002"]["flags"]
    assert by_no["E012699004"]["business"] == "용역" and by_no["G012699005"]["business"] == "물품"
    found, held = search(rows)
    assert [r["notice_no"] for r in found] == ["E082699002"]
    assert held["면허 정보 없는 출처 · 제목 후보 아님"] == 1


def test_kepco_office_gives_a_labelled_site_estimate_only_for_single_province_offices(tmp_path):
    from bidloc.recommend import recommend
    north = dict(WORK, no="E012699011", placeName="경기북부본부 기획관리실")
    plant = dict(WORK, no="E082699012", companyId="COM08", placeName="당진발전본부 경영지원처")
    shared = dict(WORK, no="E012699013", placeName="광주전남본부 기획관리실")
    head = dict(WORK, no="E012699014", placeName="중부건설본부")
    rows, _ = load_provider_notices(make_store(tmp_path, [north, plant, shared, head]))
    site = {r["notice_no"]: (r["site_region"], r["site_region_label"]) for r in rows}
    assert site["E012699011"][0] == "경기도" and "추정" in site["E012699011"][1]
    assert site["E082699012"][0] == "충청남도 당진시" and "추정" in site["E082699012"][1]
    # An office covering several provinces names them all; a nationwide construction office gives no estimate.
    assert site["E012699013"][0] == "광주광역시·전라남도 중 한 곳" and "추정" in site["E012699013"][1]
    assert site["E012699014"] == (None, "현장 위치 정보 없음(발주기관은 현장이 아님)")
    result = recommend([], rows, region="경기도 남양주시", sources=["kepco"], site_provinces="capital", now=NOW)
    assert sorted(i["notice_no"] for i in result["items"]) == ["E012699011", "E012699014"]
    assert result["counts"]["held_back"]["참가지역 정보 없음 · 현장 시·도 범위 밖"] == 2
    # A multi-province office is held back only when none of its provinces is in scope.
    south = recommend([], rows, region="전라남도 완도군", sources=["kepco"], site_provinces="home", now=NOW)
    assert "E012699013" in {i["notice_no"] for i in south["items"]}


def test_kepco_skips_kospo_rows_keeps_the_latest_relisting_and_unknown_companies(tmp_path):
    kospo = dict(WORK, no="E042699006", companyId="COM04")
    first = dict(WORK, no="E012699007", createDatetime="2026-10-07 09:00:00",
                 bidAttendReqCloseDatetime="2026-10-10 18:00:00")
    relisted = dict(first, createDatetime="2026-10-08 09:00:00", bidAttendReqCloseDatetime="2026-10-16 18:00:00")
    other = dict(WORK, no="E992699008", companyId="COM99",
                 filenlink2="https://example.invalid/x.hwp", filename2="공고문.hwp", filenlink1=None)
    rows, _ = load_provider_notices(make_store(tmp_path, [kospo, relisted, first, other]))
    by_no = {r["notice_no"]: r for r in rows}
    # 남부발전 (COM04) is already collected as its own source; KEPCO's copy would list it twice.
    assert "E042699006" not in by_no
    assert by_no["E012699007"]["deadline"] == "2026-10-16T18:00:00" and by_no["E012699007"]["versions_observed"] == 2
    assert by_no["E992699008"]["agency"] == "한전 계열(COM99)"
    # Only SRM attachment links are used.
    assert by_no["E992699008"]["url"] is None
