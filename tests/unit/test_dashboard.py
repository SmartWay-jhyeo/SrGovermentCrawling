"""Synthetic profile, records and staged rows only; no network or real data."""
from datetime import date, datetime
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from bidloc.recommend import (ProfileError, load_profile, mark_new, outside_site_scope, previous_daily,
                              resolve_site_provinces, save_profile, write_daily)
from bidloc.timeutil import KST
from tests.unit.test_analysis import record
from tests.unit.test_provider_notices import KAPT, LH, make_store

_SOCKET_CONNECT = socket.socket.connect
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=KST)
VOCAB = {"경기도", "경기도 남양주시", "경기도 광주시", "광주광역시 북구"}
PROFILE = {"region": "남양주", "license": "도장습식방수", "site_scope": "home"}


def test_profile_is_validated_before_it_is_saved(tmp_path):
    saved = save_profile(tmp_path, PROFILE, VOCAB, now=NOW)
    assert saved["region"] == "경기도 남양주시" and saved["license"] == "4992" and saved["sources"][0] == "g2b"
    assert load_profile(tmp_path) == saved
    with pytest.raises(ProfileError) as exc:
        save_profile(tmp_path / "other", {"region": "광주", "site_scope": "달나라"}, VOCAB)
    assert exc.value.candidates
    # A rejected profile leaves nothing behind.
    assert load_profile(tmp_path / "other") is None


def test_site_scope_applies_only_to_unknown_participation_regions():
    capital = resolve_site_provinces("capital", "경기도 남양주시")
    assert capital == {"서울특별시", "경기도", "인천광역시"}
    home = resolve_site_provinces("home", "경기도 남양주시")
    assert outside_site_scope("충청남도", "확인 필요", home)
    assert not outside_site_scope("충청남도", "충족", home)          # a published region decides on its own
    assert not outside_site_scope(None, "확인 필요", home)            # an unknown site is kept
    assert not outside_site_scope("충청남도", "확인 필요", None)      # 'all' disables the scope


def result(keys, notice_date="2026-10-01"):
    items = [{"key": k, "evaluation": {"state": "확인 필요"}, "source": "kapt", "title": k, "notice_no": k,
              "notice_date": notice_date, "deadline": None} for k in keys]
    return {"as_of_kst": NOW.isoformat(), "complete": True, "warnings": [], "counts": {}, "items": items}


def test_new_recommendations_compare_only_with_the_same_profile(tmp_path):
    profile = save_profile(tmp_path, PROFILE, VOCAB, now=NOW)
    write_daily(tmp_path, date(2026, 10, 8), profile, result(["A", "B"]))
    today = result(["A", "C"])["items"]
    basis = mark_new(today, previous_daily(tmp_path, NOW.date(), profile), NOW)
    assert [i["key"] for i in today if i["is_new"]] == ["C"] and "2026-10-08" in basis
    # Yesterday's list made with another profile is not a baseline; recent notice dates stand in.
    other = dict(profile, site_scope="all")
    fallback = result(["A", "C"], notice_date="2026-10-08")["items"]
    basis = mark_new(fallback, previous_daily(tmp_path, NOW.date(), other), NOW)
    assert all(i["is_new"] for i in fallback) and "이전 추천 기록 없음" in basis
    assert previous_daily(tmp_path, date(2026, 10, 8), profile) is None   # same-day file is not "previous"


def test_daily_cli_skips_without_profile_then_writes_the_days_list(tmp_path, monkeypatch, capsys):
    import bidloc.config
    import bidloc.recommend as module
    import bidloc.ui.cache
    database = tmp_path / "bidloc.sqlite3"
    monkeypatch.setattr(bidloc.config, "load_settings", lambda root: SimpleNamespace(database_path=database, data_mode="synthetic"))
    monkeypatch.setattr(bidloc.ui.cache, "read_snapshot", lambda *a: ([record("G-1-000", bid_ntce_no="G-1", bid_ntce_ord="000",
        regions=["경기도 남양주시"], deadline="2099-01-01 10:00:00", bid_ntce_dt="2026-10-08 10:00:00")], {}))
    monkeypatch.setattr(module, "now_kst", lambda: NOW)
    assert module.main(["--daily"]) == 0 and "SKIPPED" in capsys.readouterr().out
    save_profile(tmp_path / "recommend", PROFILE, VOCAB, now=NOW)
    assert module.main(["--daily"]) == 0
    saved = json.loads((tmp_path / "recommend" / "daily" / "2026-10-09.json").read_text(encoding="utf-8"))
    assert [i["key"] for i in saved["items"]] == ["g2b:G-1-000"] and saved["profile"]["region"] == "경기도 남양주시"


def test_dashboard_saves_profile_and_shows_scoped_recommendations(monkeypatch, tmp_path):
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("외부 네트워크는 테스트에서 금지됩니다.")
        return _SOCKET_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    import bidloc.ui.app as ui
    far = dict(KAPT, bidNum="SYN-K9", bidArea="44", bidTitle="합성 외벽 도장 공사", bidDeadline="2099-01-01 10:00:00")
    make_store(tmp_path, [("kapt", dict(KAPT, bidDeadline="2099-01-01 10:00:00"), "2026-10-08T00:12:00"),
                          ("kapt", far, "2026-10-08T00:12:00"),
                          ("lh", dict(LH, tndrdocAcptEndDtm="2099/01/01 10:00 "), "2026-10-08T00:12:00")])
    g2b = [record("SYN-G-000", bid_ntce_no="SYN-G", bid_ntce_ord="000", bid_ntce_dt="2026-10-08 10:00:00",
                  regions=["경기도 남양주시"], deadline="2099-01-01 10:00:00", bid_ntce_nm="합성 옥상 방수 공사")]
    meta = {"data_mode": "synthetic", "filters": {}, "collection_status": "PARTIAL", "snapshot_id": "test",
            "generated_at_kst": "2026-10-09", "partitions": [], "quality": {}, "notes": []}
    monkeypatch.setattr(ui, "load_settings", lambda: SimpleNamespace(database_path=tmp_path / "synthetic.sqlite3",
                                                                      data_mode="synthetic"))
    monkeypatch.setattr(ui, "cached_snapshot", lambda *args: (g2b, meta))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"), default_timeout=30).run()
    assert not at.exception and at.title[0].value == "조건 검색" and not at.metric
    at.text_input(key="profile_region").set_value("남양주")
    next(b for b in at.button if b.label == "조건 저장").click().run()
    assert not at.exception and any("조건을 저장했습니다" in s.value for s in at.success)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["조건 일치"] == "2건" and metrics["확인 필요"] == "1건"
    # The K-apt complex outside the HQ province is held back and counted, not shown.
    assert any("현장 시·도 범위 밖 1건" in c.value for c in at.caption)
    titles = [t for df in at.dataframe for t in df.value["공고명"]]
    assert "합성 외벽 도장 공사" not in titles and "합성 옥상 방수 공사" in titles
    at.selectbox(key="reco_pick_all").set_value("g2b:SYN-G-000").run()
    assert not at.exception and any(s.value == "합성 옥상 방수 공사" for s in at.subheader)
    assert (tmp_path / "recommend" / "profile.json").is_file()
    # Viewing the dashboard leaves the day's list as tomorrow's baseline.
    assert len(list((tmp_path / "recommend" / "daily").glob("*.json"))) == 1


def test_day_list_is_kept_once_per_profile_and_never_from_partial_data(tmp_path):
    from bidloc.recommend import ensure_daily
    profile = save_profile(tmp_path, PROFILE, VOCAB, now=NOW)
    first = result(["A"])
    assert ensure_daily(tmp_path, NOW.date(), profile, first)
    assert not ensure_daily(tmp_path, NOW.date(), profile, result(["A", "B"]))       # first list of the day is kept
    assert ensure_daily(tmp_path, NOW.date(), dict(profile, site_scope="all"), result(["A", "B"]))  # profile changed
    partial = dict(result(["Z"]), complete=False)
    assert not ensure_daily(tmp_path, date(2026, 10, 10), profile, partial)
