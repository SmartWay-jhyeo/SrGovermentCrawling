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
    for n, (provider, item, updated, *extra) in enumerate(rows):
        job = f"job-{n}"
        params, status = (extra + [{}, "COMPLETE_RANGE"][len(extra):])[:2]
        store.execute("INSERT INTO collection_job(job_id,provider,params_json,status,received,total_count,updated_at,snapshot) "
                      "VALUES(?,?,?,?,?,?,?,?)", (job, provider, json.dumps(params), status, 1, 1, updated, updated[:10]))
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


NURI = {"bidNtceNo": "R26BK09990001", "bidNtceOrd": "000", "ntceNm": "합성아파트 외벽 재도장 공사", "ntceDivNm": "등록공고",
        "ntceInsttNm": "합성아파트 입주자대표회의", "rgstDt": "2026-10-07 10:00:00", "nticeDt": "2026-10-07 10:00:00",
        "bidClseDt": "2026-10-20 10:00:00", "opengDt": "2026-10-20 11:00:00", "cntrctMthdNm": "제한경쟁",
        "bidMethdNm": "전자입찰", "rgnLmtDivNm": "투찰제한", "asignBdgtAmt": "50000000", "refAmtUseYn": "N",
        "cnstwkDtlList": "[1^합성 재도장^경기도 합성시 합성동 1^2026-12-31]", "aptCeoAdrs": "경기도 합성시 합성로 1",
        "ofclNm": "합성담당", "ofclTelNo": "000-0000-0000", "ofclEmail": "synthetic@example.invalid",
        "aptMngOfficeTelNo": "000-0000-0001",
        "ntceSpecDocUrl1": "https://www.g2b.go.kr/pn/pnp/pnpe/UntyAtchFile/downloadFile.do?bidPbancNo=R26BK09990001"}
NURI_WINDOW = {"inqryDiv": "1", "inqryBgnDt": "202610050000", "inqryEndDt": "202610072359"}
LCNS = {"bidNtceNo": "R26BK09990001", "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": "1",
        "lcnsLmtNm": "실내건축공사업/0005", "permsnIndstrytyList": "[도장ㆍ습식ㆍ방수ㆍ석공사업/4992]",
        "rgstDt": "2026-10-07 10:00:00"}
RGN = {"bidNtceNo": "R26BK09990001", "bidNtceOrd": "000", "lmtSno": "1", "prtcptPsblRgnNm": "경기도",
       "rgstDt": "2026-10-07 10:00:00"}
KOSPO_WORK = {"announceno": "E0426SYN01", "title": "하동 합성설비 도장공사", "category": "공사영역", "annday3": "20261007",
              "applsdt3": "20261007100000", "appledt3": "20261015100000", "subsdt3": "20261015100000",
              "subedt3": "20261017100000", "estprc3": "110000000", "estprcv3": "100000000", "estbas3": "0",
              "estprc12": " ", "limreg4": " ", "dprtnm": "합성부서", "name": "합성담당", "ofcphn": "000", "email": "x"}
KOSPO_GOODS = {"announceno": "G0426SYN02", "title": "합성 자재 구매", "category": "자재", "annday3": "20261007",
               "appledt3": "20261014100000", "deadl2": "20261016140000", "estprc12": "5000000", "estprc3": "0"}


def nuri_store(tmp_path, *, licence=(LCNS,), region=(RGN,), aux_status="COMPLETE_RANGE", notice=NURI):
    rows = [("nuri", dict(notice), "2026-10-08T00:12:00+09:00", NURI_WINDOW)]
    rows += [("nuri_lcns", dict(r), "2026-10-08T00:13:00+09:00", NURI_WINDOW, aux_status) for r in licence]
    rows += [("nuri_rgn", dict(r), "2026-10-08T00:14:00+09:00", NURI_WINDOW, aux_status) for r in region]
    return make_store(tmp_path, rows)


def test_nuri_joins_licence_and_region_lists_by_number_and_version(tmp_path):
    rows, meta = load_provider_notices(nuri_store(tmp_path))
    assert [r["source"] for r in rows] == ["nuri"]
    row = rows[0]
    assert row["key"] == "nuri:R26BK09990001" and row["version"] == "000 등록공고" and row["business"] == "공사"
    assert row["allowed_regions"] == ["경기도"] and row["allowed_regions_basis"] == "참가가능지역(PrtcptPsblRgn)"
    # 4992 appears only in the permitted-industry list; the code, not the name, decides the match.
    assert row["license_codes"] == {"0005", "4992"} and row["licenses"] == [
        "그룹1-1 실내건축공사업/0005 (허용업종 [도장ㆍ습식ㆍ방수ㆍ석공사업/4992])"]
    assert row["amount"] is None and row["amounts"] == {"배정예산": 50000000, "기준금액(투찰 상한)": None}
    assert row["site_region"] == "경기도 합성시 합성동 1" and row["office"] == "경기도 합성시 합성로 1"
    assert row["deadline"] == "2026-10-20T10:00:00" and row["deadline_label"] == "입찰 마감"
    assert row["url"].startswith("https://www.g2b.go.kr/")
    # Contact fields of the source never reach the normalized row.
    assert not any(v in json.dumps(row, ensure_ascii=False, default=str)
                   for v in ("합성담당", "000-0000-0000", "synthetic@example.invalid", "000-0000-0001"))
    assert meta["sources"]["nuri"]["aux"] == {"nuri_lcns": "COMPLETE_RANGE", "nuri_rgn": "COMPLETE_RANGE"}
    found, _ = search(rows, hq="경기도 합성시", scope="4992")
    assert [r["evaluation"]["overall"] for r in found] == ["조건 일치"]
    outside, held = search(rows, hq="충청남도 합성군", scope="4992")
    assert outside == [] and held["참가지역 불일치"] == 1


def test_nuri_licence_codes_without_4992_do_not_match_by_name(tmp_path):
    other = dict(LCNS, lcnsLmtNm="합성도장설비업/0099", permsnIndstrytyList="")
    rows, _ = load_provider_notices(nuri_store(tmp_path, licence=(other,)))
    found, held = search(rows, hq="경기도 합성시", scope="4992")
    assert found == [] and held["요구 면허에 4992 없음"] == 1


def test_nuri_missing_lists_are_unknown_not_unrestricted(tmp_path):
    # Lists not received for that registration day: conditions stay unknown.
    rows, _ = load_provider_notices(nuri_store(tmp_path, licence=(), region=()))
    row = rows[0]
    assert row["licenses"] is None and row["license_basis"] == "면허제한 목록 미수집"
    assert row["allowed_regions"] is None and row["allowed_regions_basis"] == "참가가능지역 목록 미수집"
    assert {"면허제한 미수집", "참가가능지역 미수집"} <= set(row["flags"])
    # Rows saved before a list job stopped are real limits and are used; only the absence of rows needs coverage.
    rows, _ = load_provider_notices(nuri_store(tmp_path / "a", aux_status="BLOCKED"))
    assert rows[0]["allowed_regions"] == ["경기도"] and rows[0]["license_codes"] == {"0005", "4992"}
    # Fully received but no rows for this notice: '공고서 참조', still not a pass.
    reference = dict(NURI, rgnLmtDivNm="공고서 참조")
    path = make_store(tmp_path / "b", [("nuri", reference, "2026-10-08T00:12:00+09:00", NURI_WINDOW),
                                       ("nuri_lcns", dict(LCNS, bidNtceNo="R26BK09990999"), "2026-10-08T00:13:00+09:00", NURI_WINDOW),
                                       ("nuri_rgn", dict(RGN, bidNtceNo="R26BK09990999"), "2026-10-08T00:14:00+09:00", NURI_WINDOW)])
    rows, _ = load_provider_notices(path)
    row = rows[0]
    assert row["licenses"] == [] and row["license_basis"] == "면허제한 행 없음(공고서 참조)"
    assert row["allowed_regions"] is None and row["allowed_regions_basis"] == "지역제한 구분: 공고서 참조"
    found, _ = search(rows, hq="경기도 합성시", scope="4992")
    assert [r["evaluation"]["overall"] for r in found] == ["확인 필요"]
    titled = make_store(tmp_path / "c", [("nuri", dict(reference, ntceNm="합성 시공자 선정"), "2026-10-08T00:12:00+09:00",
                                          NURI_WINDOW),
                                         ("nuri_lcns", dict(LCNS, bidNtceNo="X"), "2026-10-08T00:13:00+09:00", NURI_WINDOW),
                                         ("nuri_rgn", dict(RGN, bidNtceNo="X"), "2026-10-08T00:14:00+09:00", NURI_WINDOW)])
    rows, _ = load_provider_notices(titled)
    found, held = search(rows, hq="경기도 합성시", scope="4992")
    assert found == [] and held["누리장터 면허 정보 없음 · 제목 후보 아님"] == 1
    assert len(search(rows, hq="경기도 합성시", scope="4992", title_shortlist=False)[0]) == 1


def test_nuri_later_version_replaces_and_missing_close_uses_labelled_opening(tmp_path):
    cancelled = dict(NURI, bidNtceOrd="001", ntceDivNm="취소공고", rgstDt="2026-10-07 12:00:00")
    manual = dict(NURI, bidNtceNo="R26BK09990002", bidClseDt="", bidMethdNm="직찰", asignBdgtAmt="0")
    path = make_store(tmp_path, [("nuri", dict(NURI), "2026-10-08T00:12:00+09:00", NURI_WINDOW),
                                 ("nuri", cancelled, "2026-10-08T00:12:00+09:00", NURI_WINDOW),
                                 ("nuri", manual, "2026-10-08T00:12:00+09:00", NURI_WINDOW)])
    rows, _ = load_provider_notices(path)
    by_no = {r["notice_no"]: r for r in rows}
    first = by_no["R26BK09990001"]
    assert first["cancelled"] and first["version"] == "001 취소공고" and first["versions_observed"] == 2
    second = by_no["R26BK09990002"]
    assert second["deadline"] == "2026-10-20T11:00:00" and second["deadline_label"] == "개찰 일시(입찰 마감 미제공)"
    assert second["amounts"]["배정예산"] is None
    assert {"배정예산 0 표시는 미제공으로 처리", "입찰 마감 미제공 · 개찰 일시로 표시", "입찰방식 직찰"} <= set(second["flags"])
    found, _ = search(rows, hq="경기도 합성시", scope="4992", status="마감 전")
    assert "R26BK09990001" not in {r["notice_no"] for r in found}


def test_kospo_construction_area_is_unclassified_and_amount_labels_are_kept(tmp_path):
    path = make_store(tmp_path, [("kospo", dict(KOSPO_WORK), "2026-10-08T00:12:00+09:00"),
                                 ("kospo", dict(KOSPO_GOODS), "2026-10-08T00:12:00+09:00")])
    rows, meta = load_provider_notices(path)
    work = next(r for r in rows if r["notice_no"] == "E0426SYN01")
    goods = next(r for r in rows if r["notice_no"] == "G0426SYN02")
    assert work["business"] == "미분류" and goods["business"] == "물품"
    assert work["amount"] is None and work["amounts"] == {"추정가격(공사) 표기값": 110000000,
                                                          "부가세포함추정가격(공사) 표기값": 100000000, "예비가격기초금액": None}
    assert {"공사·용역 구분 없음(공사영역)", "금액 0 표시는 미제공으로 처리",
            "금액 라벨 확인 필요: '추정가격' 표기값이 '부가세포함' 표기값의 1.1배"} <= set(work["flags"])
    assert work["deadline"] == "2026-10-15T10:00:00" and work["deadline_label"].startswith("입찰참가신청 마감")
    assert work["site_region"] == "경상남도 하동군" and "추정" in work["site_region_label"]
    assert work["licenses"] is None and work["allowed_regions"] is None
    assert not any(v in json.dumps(work, ensure_ascii=False, default=str) for v in ("합성부서", "합성담당"))
    assert meta["sources"]["kospo"]["label"] == "남부발전"
    found, _ = search(rows, hq="경기도 합성시", scope="4992")
    assert [r["notice_no"] for r in found] == ["E0426SYN01"] and found[0]["evaluation"]["overall"] == "확인 필요"
    plain = make_store(tmp_path / "b", [("kospo", dict(KOSPO_WORK, title="합성 설비 정비 용역"), "2026-10-08T00:12:00+09:00")])
    rows, _ = load_provider_notices(plain)
    found, held = search(rows, hq="경기도 합성시", scope="4992")
    assert found == [] and held["면허 정보 없는 출처 · 제목 후보 아님"] == 1
    assert rows[0]["site_region"] is None


def test_kospo_plant_estimate_ignores_words_that_only_contain_a_plant_name():
    from bidloc.provider_notices import KOSPO_PLANT_IN_TITLE
    found = {t: (m.group(1) if (m := KOSPO_PLANT_IN_TITLE.search(t)) else None) for t in (
        "[긴급공고] 부산복합 #4ST 배관복구공사", "석탄 부산물 처리 용역", "남제주빛드림본부 수질 공사", "신인천 배관 공사",
        "한림대 합성 공사")}
    assert found == {"[긴급공고] 부산복합 #4ST 배관복구공사": "부산", "석탄 부산물 처리 용역": None,
                     "남제주빛드림본부 수질 공사": "남제주", "신인천 배관 공사": "신인천", "한림대 합성 공사": None}


def test_city_style_province_names_are_read_but_ambiguous_gwangju_is_not():
    from bidloc.provider_notices import canonical_region
    assert canonical_region("서울시 영등포구 영등포로 5길") == "서울특별시 영등포구 영등포로 5길"
    assert canonical_region("세종시 한누리대로") == "세종특별자치시 한누리대로"
    assert canonical_region("광주시 오포읍") == "광주시 오포읍"
