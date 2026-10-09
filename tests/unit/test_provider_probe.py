"""Synthetic responses only; no network or actual procurement records."""

from datetime import date
from types import SimpleNamespace

import pytest

from bidloc.catalog import load_catalog, validate_catalog_data
from bidloc.clients.errors import Outcome
from bidloc.provider_probe import inspect_result, probe_provider, query_params


BEGIN, END = date(2026, 10, 1), date(2026, 10, 8)


def result(outcome=Outcome.SUCCESS, items=None, total=10):
    return SimpleNamespace(items=items, outcome=outcome, data_ok=outcome == Outcome.SUCCESS,
                           http_status=200, result_code="00" if outcome == Outcome.SUCCESS else "20",
                           result_msg="synthetic", attempts=1, source_response_ids=[], total_count=total)


def test_official_non_get_operation_and_underscore_parameter():
    from pathlib import Path
    catalog = load_catalog(Path(__file__).resolve().parents[2] / "config/provider_api_catalog.yaml")
    assert catalog.operation("kwater", "cntrwkList").params["_type"]
    catalog.data["services"]["kwater"]["operations"]["../escape"] = {}
    assert validate_catalog_data(catalog.data)


def test_error_does_not_become_zero_or_live_success():
    observation = inspect_result("lh", result(Outcome.ACCESS_DENIED), BEGIN, END)
    assert observation["status"] == "BLOCKED"
    assert observation["sample_rows"] is None
    assert observation["freshness"] == "UNVERIFIED"


def test_old_rows_do_not_verify_current_data():
    observation = inspect_result("kapt", result(items=[{"bidRegDate": "2025-01-01"}]), BEGIN, END)
    assert observation["freshness"] == "UNVERIFIED"
    assert observation["full_collection"] == "NOT_ATTEMPTED"


def test_recent_row_is_only_sample_not_complete_collection():
    observation = inspect_result("kapt", result(items=[{"bidRegDate": "2026-10-07 10:00:00"}]), BEGIN, END)
    assert observation["freshness"] == "RECENT_SAMPLE_OBSERVED"
    assert observation["full_collection"] == "NOT_ATTEMPTED"


def test_month_filter_is_not_silently_used_for_cross_month_period():
    with pytest.raises(ValueError):
        query_params("kwater", date(2026, 9, 29), END)


@pytest.mark.parametrize("outcome", [Outcome.ACCESS_DENIED, Outcome.QUOTA_DAILY_EXCEEDED])
def test_fatal_response_stops_provider_and_quota_is_persisted(outcome):
    calls, marks = [], []
    client = SimpleNamespace(call=lambda *a, **k: (calls.append(a), result(outcome))[1])
    budget = SimpleNamespace(mark_quota_exhausted=lambda *a: marks.append(a))
    probe_provider(client, budget, "lh", BEGIN, END)
    assert len(calls) == 1
    assert len(marks) == (1 if outcome == Outcome.QUOTA_DAILY_EXCEEDED else 0)


def test_repeated_page_is_review_required():
    client = SimpleNamespace(call=lambda *a, **k: result(items=[{"bidRegDate": "20261007"}]))
    observation = probe_provider(client, None, "kapt", BEGIN, END)
    assert observation["repeated_page"]
    assert observation["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("provider", ["kapt", "lh", "kwater", "d2b", "pps_openstd",
                                      "bid_notice", "bid_notice_reg", "bid_notice_etc"])
def test_separate_credentials_reach_only_the_selected_request(project, provider):
    # Synthetic secrets only. Verify request construction and redaction together.
    from pathlib import Path
    from bidloc.config import load_settings
    from bidloc.clients.http import build_request_url, evaluate_live_gate, validate_params
    from bidloc.provider_probe import G2B_PROVIDERS, OPERATIONS, service_id, settings_for_provider
    from bidloc.redaction import REGISTRY
    from urllib.parse import parse_qs, urlsplit

    old_key, new_key = "SYNTHETIC-G2B+KEY=", "SYNTHETIC-PROVIDER/KEY="
    settings = load_settings(project, environ={"DATA_GO_KR_SERVICE_KEY": old_key,
        "PROVIDER_DATA_GO_KR_SERVICE_KEY": new_key, "ALLOW_LIVE_API": "true"})
    selected = settings_for_provider(settings, provider)
    assert evaluate_live_gate(selected, True).allowed
    root = Path(__file__).resolve().parents[2]
    g2b = provider in G2B_PROVIDERS
    catalog = load_catalog(root / "config" / ("api_catalog.yaml" if g2b else "provider_api_catalog.yaml"))
    op = catalog.operation(service_id(provider), OPERATIONS[provider])
    params = query_params(provider, BEGIN, END)
    validate_params(op, params)
    url, safe_url, _ = build_request_url(op, selected.service_key, selected.service_key_format, params)
    assert parse_qs(urlsplit(url).query)[op.auth_param] == [old_key if g2b else new_key]
    assert not REGISTRY.contains_secret(safe_url)
    assert not REGISTRY.contains_secret(repr(settings))
    assert not REGISTRY.contains_secret(str(settings.safe_summary()))
    assert settings.service_key.reveal() == old_key


def test_missing_provider_key_blocks_without_g2b_fallback(project, monkeypatch, capsys):
    import bidloc.provider_probe as module
    from bidloc.config import load_settings
    settings = load_settings(project, environ={"DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-ONLY-G2B",
                                             "ALLOW_LIVE_API": "true"})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    monkeypatch.setattr(module, "DataGoKrClient", lambda **kwargs: pytest.fail("must not construct HTTP client"))
    assert module.main(["--live"]) == 2
    output = capsys.readouterr().out
    assert '"http_calls": 0' in output
    assert "PROVIDER_DATA_GO_KR_SERVICE_KEY" in output


@pytest.mark.parametrize("provider", ["d2b", "pps_openstd"])
def test_new_service_missing_provider_key_blocks_without_g2b_fallback(project, monkeypatch, capsys, provider):
    import bidloc.provider_probe as module
    from bidloc.config import load_settings
    settings = load_settings(project, environ={"DATA_GO_KR_SERVICE_KEY": "SYNTHETIC-ONLY-G2B",
                                             "ALLOW_LIVE_API": "true"})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    monkeypatch.setattr(module, "DataGoKrClient", lambda **kwargs: pytest.fail("must not construct HTTP client"))
    assert module.main(["--live", "--providers", provider]) == 2
    output = capsys.readouterr().out
    assert '"http_calls": 0' in output
    assert "PROVIDER_DATA_GO_KR_SERVICE_KEY" in output


def test_d2b_facility_list_uses_documented_notice_date_bounds():
    params = query_params("d2b", BEGIN, END, rows=20)
    assert params == {"pageNo": "1", "numOfRows": "20", "anmtDateBegin": "20261001", "anmtDateEnd": "20261008"}


def test_d2b_business_division_is_counted_not_assumed_construction():
    items = [{"pblancDate": "2026-10-07", "busiDivs": "공사", "pblancSe": "정상공고"},
             {"pblancDate": "20261006", "busiDivs": "용역", "pblancSe": "정정공고"}]
    observation = inspect_result("d2b", result(items=items), BEGIN, END)
    assert observation["value_counts"]["busiDivs"] == {"공사": 1, "용역": 1}
    assert observation["value_counts"]["pblancSe"] == {"정상공고": 1, "정정공고": 1}
    assert observation["recent_sample_rows"] == 2
    assert inspect_result("d2b", result(Outcome.ACCESS_DENIED), BEGIN, END)["value_counts"] is None


def test_g2b_registration_types_and_non_own_agencies_are_reported():
    from bidloc.provider_probe import G2B_OWN_REGISTRATION
    items = [{"rgstDt": "2026-10-07 09:00:00", "rgstTyNm": G2B_OWN_REGISTRATION, "ntceInsttNm": "기관A"},
             {"rgstDt": "2026-10-07 10:00:00", "rgstTyNm": "연계기관 공고건", "ntceInsttNm": "기관B"},
             {"rgstDt": "2026-10-07 11:00:00", "ntceInsttNm": "기관C"}]
    observation = inspect_result("bid_notice_reg", result(items=items), BEGIN, END)
    assert observation["value_counts"]["rgstTyNm"] == {G2B_OWN_REGISTRATION: 1, "연계기관 공고건": 1}
    # A missing registration type is not counted as a linked notice.
    assert observation["value_counts"]["ntceInsttNm_non_own_registration"] == {"기관B": 1}
    assert observation["date_field"] == "rgstDt"


def test_pps_open_standard_range_is_bounded_and_flags_non_pps_notices():
    params = query_params("pps_openstd", BEGIN, END)
    assert params["bidNtceBgnDt"] == "202610010000" and params["bidNtceEndDt"] == "202610082359"
    with pytest.raises(ValueError):
        query_params("pps_openstd", date(2026, 8, 1), END)
    items = [{"bidNtceDate": "2026-10-07", "ppsNtceYn": "Y", "bsnsDivNm": "공사", "ntceInsttNm": "기관A"},
             {"bidNtceDate": "2026-10-07", "ppsNtceYn": "N", "bsnsDivNm": "공사", "ntceInsttNm": "기관B"},
             {"bidNtceDate": "2026-10-07", "bsnsDivNm": "물품", "ntceInsttNm": "기관C"}]
    observation = inspect_result("pps_openstd", result(items=items), BEGIN, END)
    assert observation["value_counts"]["ppsNtceYn"] == {"Y": 1, "N": 1}
    assert observation["value_counts"]["bsnsDivNm"] == {"공사": 2, "물품": 1}
    # A missing flag is not treated as a non-G2B notice.
    assert observation["value_counts"]["ntceInsttNm_not_pps_notice"] == {"기관B": 1}


D2B_ROW = {"pblancDate": "20261006", "pblancYear": "2026", "pblancSeCode": "B", "pblancNo": "SYN0001",
           "pblancOdr": "1", "cntrwkNo": "2026-00001", "orntCode": "SYN", "busiDivs": "용역"}


def test_d2b_detail_prefers_construction_row_and_sends_full_composite_key():
    rows = [dict(D2B_ROW), dict(D2B_ROW, pblancNo="SYN0002", busiDivs="공사")]
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        if args[1] == "getFcltyCmpetBidPblancDetail":
            return result(items=[{"lc": "", "cntrwkNm": "합성 공사", "areaLmttList": "합성"}], total=None)
        return result(items=rows, total=2)

    observation = probe_provider(SimpleNamespace(call=call), None, "d2b", BEGIN, END, rows=30, detail=True)
    assert [c[1] for c in calls] == ["getFcltyCmpetBidPblancList", "getFcltyCmpetBidPblancDetail"]
    assert calls[1][2]["pblancNo"] == "SYN0002" and calls[1][2]["cntrwkNo"] == "2026-00001"
    detail = observation["detail"]
    assert detail["status"] == "LIVE_DETAIL_RECEIVED" and detail["chosen_busiDivs"] == "공사"
    # An empty location stays visible as missing instead of being reported as filled.
    assert "lc" in detail["observed_fields"] and "lc" not in detail["non_empty_fields"]


def test_d2b_detail_needs_every_key_and_explicit_flag():
    calls = []
    client = SimpleNamespace(call=lambda *a, **k: (calls.append(a), result(items=[dict(D2B_ROW, cntrwkNo="")], total=1))[1])
    observation = probe_provider(client, None, "d2b", BEGIN, END, rows=30, detail=True)
    assert observation["detail"]["status"] == "SKIPPED" and len(calls) == 1
    calls.clear()
    assert "detail" not in probe_provider(client, None, "d2b", BEGIN, END, rows=30)
    assert len(calls) == 1


def test_large_page_stops_when_total_fits_and_uses_g2b_service():
    calls = []
    client = SimpleNamespace(call=lambda *a, **k: (calls.append(a), result(items=[{"rgstDt": "20261007"}], total=600))[1])
    observation = probe_provider(client, None, "bid_notice_reg", BEGIN, END, rows=999)
    assert len(calls) == 1
    assert calls[0][0] == "bid_notice" and calls[0][1] == "getBidPblancListInfoCnstwk"
    assert calls[0][2]["numOfRows"] == "999"
    assert observation["status"] == "LIVE_SAMPLE_RECEIVED"


def test_rows_option_is_bounded(project, monkeypatch):
    import bidloc.provider_probe as module
    from bidloc.config import load_settings
    settings = load_settings(project, environ={})
    monkeypatch.setattr(module, "load_settings", lambda root: settings)
    monkeypatch.setattr(module, "DataGoKrClient", lambda **kwargs: pytest.fail("must not construct HTTP client"))
    with pytest.raises(SystemExit):
        module.main(["--providers", "d2b", "--rows", "1000"])


def test_provider_key_env_precedence_encoding_and_masking(project):
    from bidloc.config import load_settings
    from bidloc.provider_probe import settings_for_provider
    from bidloc.clients.http import encode_service_key
    (project / ".env").write_text("PROVIDER_DATA_GO_KR_SERVICE_KEY=SYNTHETIC-FILE\n", encoding="utf-8")
    settings = load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC%2BPROCESS%3D",
        "PROVIDER_DATA_GO_KR_SERVICE_KEY_FORMAT": "encoded"})
    selected = settings_for_provider(settings, "kapt")
    assert encode_service_key(selected.service_key, selected.service_key_format) == "SYNTHETIC%2BPROCESS%3D"
    assert settings.value_sources["PROVIDER_DATA_GO_KR_SERVICE_KEY"] == "env"
    assert settings.service_key is None


def test_invalid_provider_key_message_hides_value(project):
    from bidloc.config import ConfigError, load_settings
    with pytest.raises(ConfigError) as exc:
        load_settings(project, environ={"PROVIDER_DATA_GO_KR_SERVICE_KEY": "SYNTHETIC SECRET"})
    assert "SYNTHETIC" not in str(exc.value)
