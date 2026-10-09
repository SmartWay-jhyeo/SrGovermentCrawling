"""Synthetic staged provider rows only; no network or real notices."""
from datetime import datetime
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from bidloc.provider_collect import init_store
from bidloc.provider_notices import load_provider_notices, search_provider_notices
from bidloc.query_service import SearchFilters
from bidloc.timeutil import KST

_SOCKET_CONNECT = socket.socket.connect
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=KST)
FILTERS = SearchFilters(begin="2026-10-01", end="2026-10-31")

KAPT = {"codeAuth": "01", "bidNum": "SYN-K1", "bidState": "1", "bidTitle": "합성 옥상방수 공사", "bidKaptname": "합성아파트",
        "codeClassifyType2": "02", "bidRegDate": "2026-10-07", "bidDeadline": "2026-10-20 10:00:00", "bidArea": "41",
        "codeKind": "02", "bidFileSeq": "1234567"}
LH = {"bidNum": "SYN-L1", "bidDegree": "00 ", "bidKind": "일반공고 ", "bidnmKor": "합성 시설 공사 ", "zoneHqCd": "합성본부 ",
      "cstrtnJobGbNm": "시설공사 ", "tndrbidRegDt": "20261007 ", "tndrdocAcptEndDtm": "2026/10/20 10:00 ",
      "presmtPrc": "100000000 ", "fdmtlAmt": "110000000 ", "designPrc": "120000000 ", "zoneRstrct1": "경기 ",
      "req1Reqlic1Nm": "습식·방수공사업", "req1LicctNm": "1번 면허를 등록한 경우", "tndrCtrctMedCd": "지역제한 "}
KWATER = {"tndrPbanno": "SYN-W1", "tndrPblancNm": "합성 도장 공사", "cntrctDeptNm": "합성지사", "cntrctDivNm": "공사",
          "tndrPblancDe": "20261007", "tndrPblancEnddt": "20261009", "tndrPlnprc": "0", "ctrmthdCdNm": "제한경쟁"}
D2B = {"pblancYear": "2026", "pblancSeCode": "B", "pblancSe": "긴급공고", "pblancNo": "SYN0001", "pblancOdr": "1",
       "cntrwkNo": "2026-00001", "orntCode": "SYN", "ornt": "합성부대", "cntrwkNm": "합성 외벽 공사", "busiDivs": "공사",
       "pblancDate": "20261007", "biddocPresentnClosDt": "202610201000", "cntrctMth": "제한경쟁", "baseAmnt": "90000000"}
D2B_KEYS = {k: D2B[k] for k in ("pblancYear", "pblancSeCode", "pblancNo", "pblancOdr", "cntrwkNo", "orntCode")}


def make_store(tmp_path, rows, details=()):
    path = tmp_path / "providers" / "notices.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = sqlite3.connect(path)
    init_store(store)
    for n, (provider, item, updated) in enumerate(rows):
        job = f"job-{n}"
        store.execute("INSERT INTO collection_job(job_id,provider,params_json,status,received,total_count,updated_at,snapshot) "
                      "VALUES(?,?,?,?,?,?,?,?)", (job, provider, "{}", "COMPLETE_RANGE", 1, 1, updated, updated[:10]))
        store.execute("INSERT INTO notice_observation VALUES(?,?,?,?,?,?,?)",
                      (job, 1, 0, provider, 100 + n, f"hash-{n}", json.dumps(item, ensure_ascii=False)))
    for keys, payload in details:
        store.execute("INSERT INTO notice_detail VALUES(?,?,?,?,?,?)",
                      ("d2b", json.dumps(keys, ensure_ascii=False, sort_keys=True), "job-x", 900,
                       json.dumps(payload, ensure_ascii=False), "2026-10-08T00:00:00+09:00"))
    store.commit()
    store.close()
    return path


def search(rows, **changes):
    options = {k: changes.pop(k) for k in ("sources", "include_unmatched", "title_shortlist") if k in changes}
    return search_provider_notices(rows, SearchFilters(**{**FILTERS.__dict__, **changes}), now=NOW, **options)


def test_missing_store_is_not_collected_and_old_schema_still_loads(tmp_path):
    rows, meta = load_provider_notices(tmp_path / "absent.sqlite3")
    assert rows == [] and meta["status"] == "NOT_COLLECTED"
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE collection_job (job_id TEXT, provider TEXT, params_json TEXT, next_page INT, total_count INT,"
                      " received INT, status TEXT, reason TEXT, updated_at TEXT);"
                      "CREATE TABLE notice_observation (job_id TEXT, page_no INT, row_no INT, provider TEXT,"
                      " source_response_id INT, row_hash TEXT, payload_json TEXT);")
    old.execute("INSERT INTO collection_job VALUES('j','kwater','{}',2,1,1,'COMPLETE_RANGE',NULL,'2026-10-08')")
    old.execute("INSERT INTO notice_observation VALUES('j',1,0,'kwater',1,'h',?)", (json.dumps(KWATER, ensure_ascii=False),))
    old.commit(); old.close()
    rows, meta = load_provider_notices(path)
    assert len(rows) == 1 and rows[0]["snapshot"] is None and meta["sources"]["kwater"]["notices"] == 1


def test_versions_collapse_to_latest_and_cancellation_is_kept(tmp_path):
    cancelled_lh = dict(LH, bidDegree="01 ", bidKind="취소공고 ")
    cancelled_d2b = dict(D2B, pblancOdr="2", pblancSeCode="J", pblancSe="취소공고")
    rows, _ = load_provider_notices(make_store(tmp_path, [
        ("lh", cancelled_lh, "2026-10-08T00:00:00"), ("lh", LH, "2026-10-08T00:10:00"),
        ("d2b", D2B, "2026-10-08T00:00:00"), ("d2b", cancelled_d2b, "2026-10-08T00:01:00")]))
    by_source = {r["source"]: r for r in rows}
    assert len(rows) == 2
    # The later observation of the older degree must not replace the newer cancelled version.
    assert by_source["lh"]["cancelled"] and by_source["lh"]["versions_observed"] == 2
    assert by_source["d2b"]["cancelled"] and by_source["d2b"]["version"] == "2차 취소공고"
    result, _ = search(rows, status="마감 전")
    assert result == []


def test_region_kinds_stay_separate_and_unknown_never_passes(tmp_path):
    rows, _ = load_provider_notices(make_store(tmp_path, [
        ("kapt", KAPT, "2026-10-08T00:00:00"), ("lh", LH, "2026-10-08T00:00:00"),
        ("lh", dict(LH, bidNum="SYN-L2", zoneRstrct1="부산 "), "2026-10-08T00:00:00")]))
    result, held = search(rows, hq="경기도 합성시")
    kapt = next(r for r in result if r["source"] == "kapt")
    # The complex's province matches the HQ, but it is a site location, not a participation region.
    assert kapt["site_region"] == "경기도" and kapt["allowed_regions"] is None
    assert kapt["evaluation"]["region"] == "확인 필요" and kapt["evaluation"]["overall"] == "확인 필요"
    lh = next(r for r in result if r["source"] == "lh")
    assert lh["allowed_regions"] == ["경기도"] and lh["evaluation"]["overall"] == "조건 일치"
    assert lh["office"] == "합성본부" and lh["site_region"] is None
    assert held["참가지역 불일치"] == 1
    shown, _ = search(rows, hq="경기도 합성시", include_unmatched=True)
    assert {r["evaluation"]["overall"] for r in shown if r["source"] == "lh"} == {"조건 일치", "불충족"}


def test_license_codes_names_and_missing_detail(tmp_path):
    other = dict(D2B, pblancNo="SYN0002", cntrwkNm="합성 토목 공사")
    missing = dict(D2B, pblancNo="SYN0003", cntrwkNm="합성 정비 공사")
    rows, _ = load_provider_notices(make_store(tmp_path, [
        ("d2b", D2B, "2026-10-08T00:00:00"), ("d2b", other, "2026-10-08T00:00:00"),
        ("d2b", missing, "2026-10-08T00:00:00"), ("lh", LH, "2026-10-08T00:00:00")], details=[
        (D2B_KEYS, {"lcnsLmttList": "[0002] 건축공사업^[4992] 도장ㆍ습식ㆍ방수ㆍ석공사업", "areaLmttList": "[14] 합성도",
                    "lc": "합성도 합성구", "estmPrce": "80000000", "budgetAmount": "88000000"}),
        (dict(D2B_KEYS, pblancNo="SYN0002"), {"lcnsLmttList": "[0003] 토목건축공사업", "areaLmttList": ""})]))
    result, held = search(rows)
    by_no = {r["notice_no"]: r for r in result}
    assert by_no["SYN0001"]["evaluation"]["license"] == "포함" and by_no["SYN0001"]["allowed_regions"] == ["합성도"]
    assert by_no["SYN0001"]["amount"] == 80000000 and by_no["SYN0001"]["amounts"]["기초예비가격"] == 90000000
    assert "SYN0002" not in by_no and held["요구 면허에 4992 없음"] == 1
    # A construction row without its detail stays visible as unknown; the title shortlist does not hide it.
    assert by_no["SYN0003"]["evaluation"]["license"] == "확인 필요" and "상세 미수집" in by_no["SYN0003"]["flags"]
    assert by_no["SYN-L1"]["evaluation"]["license"] == "포함"


def test_title_shortlist_only_for_sources_without_condition_data(tmp_path):
    guard = dict(KAPT, bidNum="SYN-K2", bidTitle="합성 승강기 교체 공사")
    service = dict(KAPT, bidNum="SYN-K3", bidTitle="합성 방수 용역", codeClassifyType2="03")
    rows, _ = load_provider_notices(make_store(tmp_path, [
        ("kapt", KAPT, "2026-10-08T00:00:00"), ("kapt", guard, "2026-10-08T00:00:00"),
        ("kapt", service, "2026-10-08T00:00:00")]))
    assert [r["notice_no"] for r in search(rows)[0]] == ["SYN-K1"]
    assert {r["notice_no"] for r in search(rows, title_shortlist=False)[0]} == {"SYN-K1", "SYN-K2"}
    # Services and goods never appear in the construction view, whatever the scope.
    assert {r["notice_no"] for r in search(rows, scope="분석 후보 전체")[0]} == {"SYN-K1", "SYN-K2"}


def test_zero_amount_is_missing_and_amount_filter_holds_it_back(tmp_path):
    rows, _ = load_provider_notices(make_store(tmp_path, [("kwater", KWATER, "2026-10-08T00:00:00"),
                                                          ("lh", LH, "2026-10-08T00:00:00")]))
    kwater = next(r for r in rows if r["source"] == "kwater")
    assert kwater["amount"] is None and kwater["amounts"] == {"금액(의미 미확인)": None}
    assert "금액 0 표시는 미제공으로 처리" in kwater["flags"]
    result, held = search(rows, min_amount=1)
    assert [r["source"] for r in result] == ["lh"] and held["추정가격 미제공"] == 1
    # A date-only deadline is the end of that day and is labelled as such.
    open_rows, _ = search(rows, status="마감 전")
    assert {r["source"] for r in open_rows} == {"kwater", "lh"}
    assert kwater["deadline"] == "2026-10-09T23:59:00" and "시각 미제공" in kwater["deadline_label"]


def test_app_shows_provider_tab_detail_and_status(monkeypatch, tmp_path):
    # Windows asyncio uses a loopback socketpair; every external connection stays blocked.
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("외부 네트워크는 테스트에서 금지됩니다.")
        return _SOCKET_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    import bidloc.ui.app as ui
    from tests.unit.test_analysis import record
    make_store(tmp_path, [("kapt", KAPT, "2026-10-08T00:12:00"), ("lh", LH, "2026-10-08T00:12:00")])
    meta = {"data_mode": "synthetic", "filters": {"begin": "2026-10-01", "end": "2026-10-31"}, "collection_status": "PARTIAL",
            "snapshot_id": "test", "generated_at_kst": "2026-10-09", "partitions": [], "quality": {}, "notes": []}
    g2b = [record("SYNTHETIC-A", bid_ntce_no="SYNTHETIC-A", bid_ntce_ord="000", bid_ntce_dt="2026-10-02 10:00:00")]
    monkeypatch.setattr(ui, "load_settings", lambda: SimpleNamespace(database_path=tmp_path / "synthetic.sqlite3",
                                                                      data_mode="synthetic"))
    monkeypatch.setattr(ui, "cached_snapshot", lambda *args: (g2b, meta))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "app.py"), default_timeout=30).run()
    at.button(key="nav_공고 검색").click().run()
    assert not at.exception
    assert "1건" in at.subheader[0].value  # the G2B list keeps its own count
    assert any("**2건**" in m.value for m in at.markdown)
    at.selectbox(key="provider_selected").set_value("lh:SYN-L1").run()
    assert not at.exception and any(s.value == "합성 시설 공사" for s in at.subheader)
    at.button(key="nav_수집·품질").click().run()
    assert not at.exception and any("추가 수집처" in s.value for s in at.subheader)
