"""verify-api 실행기 흐름을 합성 가짜 API로 검사한다.

주의: 여기의 응답은 모두 합성 데이터다(공고번호 R99BK…, 업종코드 9999 등). 실제 API 동작의 증거가 아니다.
검사 대상은 프로그램 로직(복합키 연결, 참여수 구분, 중단·차단 처리, 보고서 마스킹)이다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import httpx

from bidloc.collectors.verify import DiscoveryWindow, VerifyRunner, final_run_status, load_verify_plan, write_reports
from bidloc.repositories.runs import RunRepository
from tests.conftest import FAKE_KEY
from tests.unit.helpers import build_client, gateway_xml, std_json

NO = "R99BK99990001"
NO2 = "R99BK99990002"


def items(rows):
    return {"item": rows}


class SyntheticApi:
    """경로의 오퍼레이션 이름과 쿼리로 합성 응답을 고른다."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        op = request.url.path.rsplit("/", 1)[-1]
        params = dict(request.url.params)
        self.calls.append((op, params))
        if op in self.overrides:
            return self.overrides[op](params)
        handler = getattr(self, op, None)
        if handler is None:
            return httpx.Response(200, content=std_json("", 0))
        return handler(params)

    def getIndstrytyBaseLawrgltInfoList(self, p):
        rows = [{"indstrytyClsfcCd": "49", "indstrytyClsfcNm": "건설업", "indstrytyCd": "9999",
                 "indstrytyNm": "합성도장ㆍ습식ㆍ방수ㆍ석공사업", "indstrytyUseYn": "Y"},
                {"indstrytyClsfcCd": "49", "indstrytyClsfcNm": "건설업", "indstrytyCd": "9998",
                 "indstrytyNm": "합성토목공사업", "indstrytyUseYn": "Y"}]
        return httpx.Response(200, content=std_json(items(rows), 2, rows=int(p["numOfRows"])))

    def getBidPblancListInfoCnstwkPPSSrch(self, p):
        if p["inqryBgnDt"].startswith("2025"):
            rows = [{"bidNtceNo": NO2, "bidNtceOrd": "000", "ntceKindNm": "등록공고", "cntrctCnclsMthdNm": "제한경쟁",
                     "sucsfbidMthdNm": "적격심사", "mainCnsttyNm": "합성도장공사업"}]
            return httpx.Response(200, content=std_json(items(rows), 1))
        return httpx.Response(200, content=std_json("", 0))

    def getBidPblancListInfoCnstwk(self, p):
        if p["bidNtceNo"] == NO:
            rows = [{"bidNtceNo": NO, "bidNtceOrd": "000", "ntceKindNm": "등록공고", "bdgtAmt": "110000000",
                     "presmptPrce": "100000000", "VAT": "10000000", "cnstrtsiteRgnNm": "합성도 가군", "ntceInsttNm": "합성기관"},
                    {"bidNtceNo": NO, "bidNtceOrd": "001", "ntceKindNm": "변경공고", "bdgtAmt": "", "presmptPrce": "100000000",
                     "cnstrtsiteRgnNm": "합성도 가군", "ntceInsttNm": "합성기관"}]
            return httpx.Response(200, content=std_json(items(rows), 2))
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": "000", "ntceKindNm": "등록공고"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getBidPblancListInfoLicenseLimit(self, p):
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": p["bidNtceOrd"], "lmtGrpNo": "1", "lmtSno": "1",
                 "lcnsLmtNm": "합성도장ㆍ습식ㆍ방수ㆍ석공사업/9999", "permsnIndstrytyList": "",
                 "indstrytyMfrcFldList": "[1^습식ㆍ방수]", "bsnsDivNm": "공사"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getBidPblancListInfoPrtcptPsblRgn(self, p):
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": p["bidNtceOrd"], "lmtSno": "1", "prtcptPsblRgnNm": "합성도 가군"},
                {"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": p["bidNtceOrd"], "lmtSno": "2", "prtcptPsblRgnNm": "합성도 나시"}]
        return httpx.Response(200, content=std_json(items(rows), 2))

    def getBidPblancListInfoCnstwkBsisAmount(self, p):
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": "001", "bidClsfcNo": "0", "bssamt": "105000000"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getOpengResultListInfoCnstwk(self, p):
        if p["bidNtceNo"] != NO:
            return httpx.Response(200, content=std_json("", 0))
        rows = [
            {"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNo": "000", "progrsDivCdNm": "개찰완료", "prtcptCnum": ""},
            {"bidNtceNo": NO, "bidNtceOrd": "001", "bidClsfcNo": "0", "rbidNo": "000", "progrsDivCdNm": "유찰", "prtcptCnum": "1"},
            {"bidNtceNo": NO, "bidNtceOrd": "001", "bidClsfcNo": "0", "rbidNo": "001", "progrsDivCdNm": "개찰완료", "prtcptCnum": "3",
             "opengCorpInfo": "합성업체B^0000000002^대표^90000000^90.1"},
            {"bidNtceNo": NO, "bidNtceOrd": "002", "bidClsfcNo": "0", "rbidNo": "000", "progrsDivCdNm": "재입찰", "prtcptCnum": "0"},
        ]
        return httpx.Response(200, content=std_json(items(rows), 4))

    def getOpengResultListInfoOpengCompt(self, p):
        key = (p["bidNtceOrd"], p["bidClsfcNo"], p["rbidNo"])
        if key == ("001", "0", "001"):
            rows = [{"bidNtceNo": NO, "bidNtceOrd": "001", "bidClsfcNo": "0", "rbidNo": "001", "opengRank": str(i),
                     "prcbdrBizno": f"000000000{i}", "rmrk": "낙찰" if i == 2 else ""} for i in (1, 2, 3)]
            return httpx.Response(200, content=std_json(items(rows), 3, rows=int(p["numOfRows"])))
        rows = [{"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNo": "000", "opengRank": "1", "prcbdrBizno": "0000000009"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getScsbidListSttusCnstwk(self, p):
        if p["bidNtceNo"] != NO:
            return httpx.Response(200, content=std_json("", 0))
        rows = [{"bidNtceNo": NO, "bidNtceOrd": "001", "bidClsfcNo": "0", "rbidNo": "001", "prtcptCnum": "3",
                 "bidwinnrBizno": "0000000002", "sucsfbidAmt": "90000000", "sucsfbidRate": "90.1"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getOpengResultListInfoRebid(self, p):
        rows = [{"bidNtceNo": NO, "bidNtceOrd": "002", "bidClsfcNo": "0", "rbidNo": "001", "rbidRsn": "합성사유", "opengRsltDivNm": "재입찰"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    def getOpengResultListInfoFailing(self, p):
        rows = [{"bidNtceNo": NO, "bidNtceOrd": "001", "bidClsfcNo": "0", "rbidNo": "000", "nobidRsn": "합성유찰", "opengRsltDivNm": "유찰"}]
        return httpx.Response(200, content=std_json(items(rows), 1))


def make_plan(project: Path, *, windows=True):
    plan = load_verify_plan(project / "config" / "verify_samples.yaml")
    plan = replace(plan, fixed_notices=((NO, "합성 고정 표본"),),
                   windows=(DiscoveryWindow("2025", "202505120000", "202505182359"),) if windows else ())
    return plan


def run_runner(project, catalog, api, *, max_run=200, plan=None, reuse=None, reuse_from=None, run_id="verify-test"):
    b = build_client(project, catalog, api, max_run=max_run, run_id=run_id)
    runs = RunRepository(b.conn)
    runs.start(run_id=run_id, command="verify-api", data_mode="real", live=True, status="RUNNING", max_calls_run=max_run,
               catalog_sha256=catalog.sha256)
    runner = VerifyRunner(client=b.client, runs=runs, conn=b.conn, run_id=run_id, plan=plan or make_plan(project),
                          reuse=reuse, reuse_from_run_id=reuse_from)
    summary = runner.run()
    return runner, summary, b


def test_full_synthetic_flow_links_by_composite_key_and_separates_counts(project: Path, catalog):
    api = SyntheticApi()
    runner, summary, b = run_runner(project, catalog, api)
    assert final_run_status(runner) == "COMPLETED", summary["step_status_counts"]
    assert summary["industry_matches"][0]["indstrytyCd"] == "9999"
    assert [s["bid_ntce_no"] for s in summary["samples"]] == [NO, NO2]

    units = {(r["bid_ntce_ord"], r["bid_clsfc_no"], r["rbid_no"]): r for r in b.conn.execute(
        "SELECT * FROM verify_opening_unit WHERE run_id = 'verify-test' AND bid_ntce_no = ?", (NO,))}
    assert units[("000", "0", "000")]["link_status"] == "LINKED"
    assert units[("001", "0", "001")]["link_status"] == "LINKED"
    assert units[("002", "0", "000")]["link_status"] == "SAME_NO_OTHER_ORD"   # 차수 불일치는 연결하지 않음
    blank = units[("000", "0", "000")]
    assert blank["official_prtcpt_cnum"] is None and blank["official_prtcpt_cnum_status"] == "BLANK_IN_RESPONSE"
    # 공식 수가 빈 단위는 명부 완전성 비교가 불가능해 예산을 쓰지 않는다(0으로 채우지 않음)
    assert blank["roster_status"] == "NOT_QUERIED" and blank["roster_row_count"] is None and blank["count_comparison"] is None
    selection = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?",
                                          (f"sample:{NO}:roster_selection",)).fetchone()[0])
    assert selection["skipped_units"][0]["unit"] == [NO, "000", "0", "000"]
    match = units[("001", "0", "001")]
    assert match["official_prtcpt_cnum"] == 3 and match["roster_unique_bizno"] == 3 and match["count_comparison"] == "MATCH"
    failed = units[("001", "0", "000")]
    assert failed["roster_status"] == "NOT_QUERIED" and failed["roster_row_count"] is None
    rebid_zero = units[("002", "0", "000")]
    assert rebid_zero["official_prtcpt_cnum"] == 0 and rebid_zero["official_prtcpt_cnum_status"] == "OBSERVED"

    steps = {s["step_key"]: s for s in summary["steps"]}
    award = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?", (f"sample:{NO}:award",)).fetchone()[0])
    assert award["rows_detail"][0]["winner_equals_roster_rank1"] is False   # 1순위와 최종낙찰자를 동일시하지 않음
    assert steps[f"sample:{NO}:change_history"]["status"] in {"DONE", "DONE_EMPTY"}
    assert steps[f"sample:{NO}:rebid"]["status"] == "DONE" and steps[f"sample:{NO}:failing"]["status"] == "DONE"
    region = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?", (f"sample:{NO}:region",)).fetchone()[0])
    assert region["distinct_allowed_regions"] == 2 and region["construction_site_region_names_from_notice"] == ["합성도 가군"]
    license_ = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?", (f"sample:{NO}:license",)).fetchone()[0])
    assert license_["bid_ntce_ord_used"] == "001" and license_["target_industry_seen"] is True
    # 두 번째 표본은 개찰결과가 없다: 참여 0이 아니라 개찰단위 없음
    assert b.conn.execute("SELECT COUNT(*) FROM verify_opening_unit WHERE bid_ntce_no = ?", (NO2,)).fetchone()[0] == 0
    assert steps[f"sample:{NO2}:opening"]["status"] == "DONE_EMPTY"
    assert summary["any_core_link_confirmed"] is True


def test_roster_selection_skips_units_beyond_page_cap(project: Path, catalog):
    def openings(p):
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": "000", "bidClsfcNo": str(i), "rbidNo": "000",
                 "progrsDivCdNm": "개찰완료", "prtcptCnum": cnum} for i, cnum in enumerate(["900", "12", "7", "499"])]
        return httpx.Response(200, content=std_json(items(rows), 4))

    api = SyntheticApi({"getOpengResultListInfoCnstwk": openings})
    runner, summary, b = run_runner(project, catalog, api, plan=make_plan(project, windows=False))
    roster_calls = [p for op, p in api.calls if op == "getOpengResultListInfoOpengCompt"]
    # 한도 500: 첫 단위(900)는 제외, 마지막 단위(499)는 우선 선택, 남은 한 자리는 가장 작은 7
    assert sorted(p["bidClsfcNo"] for p in roster_calls) == ["2", "3"]
    selection = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?",
                                          (f"sample:{NO}:roster_selection",)).fetchone()[0])
    reasons = {tuple(s["unit"]): s["reason"] for s in selection["skipped_units"]}
    assert "페이지 한도" in reasons[(NO, "000", "0", "000")] and "상한" in reasons[(NO, "000", "1", "000")]


def test_license_region_fall_back_to_first_order_when_latest_is_empty(project: Path, catalog):
    def license_only_first_order(p):
        if p["bidNtceOrd"] != "000":
            return httpx.Response(200, content=std_json("", 0))
        rows = [{"bidNtceNo": p["bidNtceNo"], "bidNtceOrd": "000", "lmtGrpNo": "1", "lmtSno": "1",
                 "lcnsLmtNm": "합성업종/9999"}]
        return httpx.Response(200, content=std_json(items(rows), 1))

    api = SyntheticApi({"getBidPblancListInfoLicenseLimit": license_only_first_order})
    runner, summary, b = run_runner(project, catalog, api, plan=make_plan(project, windows=False))
    lic = json.loads(b.conn.execute("SELECT summary_json, status FROM verify_step WHERE step_key = ?",
                                    (f"sample:{NO}:license",)).fetchone()[0])
    assert [a["bid_ntce_ord"] for a in lic["ord_attempts"]] == ["001", "000"]
    assert lic["bid_ntce_ord_used"] == "000" and lic["rows"] == 1
    region = json.loads(b.conn.execute("SELECT summary_json FROM verify_step WHERE step_key = ?",
                                       (f"sample:{NO}:region",)).fetchone()[0])
    assert [a["bid_ntce_ord"] for a in region["ord_attempts"]] == ["001"]  # 최신 차수에 행이 있으면 추가 조회 없음


def test_empty_discovery_window_runs_unfiltered_probe(project: Path, catalog):
    def ppssrch(p):
        if "indstrytyNm" in p:
            return httpx.Response(200, content=std_json("", 0))
        return httpx.Response(200, content=std_json(items([{"bidNtceNo": "R99BK99990077", "bidNtceOrd": "000"}]), 5, rows=1))

    api = SyntheticApi({"getBidPblancListInfoCnstwkPPSSrch": ppssrch})
    plan = replace(make_plan(project), fixed_notices=(), windows=(DiscoveryWindow("2024", "202405130000", "202405192359"),))
    runner, summary, b = run_runner(project, catalog, api, plan=plan)
    disc = summary["discovery"]["2024"]
    assert disc["rows_on_first_page"] == 0 and disc["year_rows_observed"] is False
    assert disc["unfiltered_probe"]["total_count"] == 5
    probe_calls = [p for op, p in api.calls if op == "getBidPblancListInfoCnstwkPPSSrch" and "indstrytyNm" not in p]
    assert len(probe_calls) == 1 and probe_calls[0]["numOfRows"] == "1"
    assert summary["samples"] == []  # 필터 결과가 없으면 표본을 임의로 만들지 않는다


def test_pick_skips_cancelled_notices_and_prefers_opened(project: Path, catalog):
    b = build_client(project, catalog, SyntheticApi())
    runs = RunRepository(b.conn)
    runner = VerifyRunner(client=b.client, runs=runs, conn=b.conn, run_id="x", plan=make_plan(project))
    rows = [
        {"bidNtceNo": "R99BK00000001", "bidNtceOrd": "000", "ntceKindNm": "등록공고", "opengDt": "2099-01-01 11:00:00"},
        {"bidNtceNo": "R99BK00000002", "bidNtceOrd": "000", "ntceKindNm": "등록공고", "opengDt": "2020-01-01 11:00:00"},
        {"bidNtceNo": "R99BK00000002", "bidNtceOrd": "001", "ntceKindNm": "취소공고", "opengDt": "2020-01-01 11:00:00"},
        {"bidNtceNo": "R99BK00000003", "bidNtceOrd": "000", "ntceKindNm": "등록공고", "opengDt": "2020-02-01 11:00:00"},
    ]
    picked = runner._pick(rows)
    assert [p["bid_ntce_no"] for p in picked] == ["R99BK00000003", "R99BK00000001"]


def test_reused_discovery_is_repicked_from_raw(project: Path, catalog):
    raw_rows = [{"bidNtceNo": NO2, "bidNtceOrd": "000", "ntceKindNm": "등록공고", "opengDt": "2020-01-01 11:00:00"}]
    reuse = {"discovery:2025": {"picked": [{"bid_ntce_no": "R99BK00000999"}], "source_response_ids": [42]}}
    plan = replace(make_plan(project), fixed_notices=())
    b = build_client(project, catalog, SyntheticApi(), run_id="verify-repick")
    runs = RunRepository(b.conn)
    runs.start(run_id="verify-repick", command="verify-api", data_mode="real", live=True, status="RUNNING",
               max_calls_run=100, catalog_sha256=None)
    runner = VerifyRunner(client=b.client, runs=runs, conn=b.conn, run_id="verify-repick", plan=plan, reuse=reuse,
                          reuse_from_run_id="old", raw_items_reader=lambda sid: raw_rows if sid == 42 else None)
    summary = runner.run()
    assert [s["bid_ntce_no"] for s in summary["samples"]] == [NO2]


def test_quota_error_stops_run_and_leaves_resume_state(project: Path, catalog):
    quota = lambda p: httpx.Response(200, content=gateway_xml("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"))
    api = SyntheticApi({"getOpengResultListInfoCnstwk": quota})
    runner, summary, b = run_runner(project, catalog, api)
    assert final_run_status(runner) == "PARTIAL"
    assert "일일" in runner.stop_reason
    statuses = [s["status"] for s in summary["steps"]]
    first_quota = statuses.index("FAILED")
    assert all(s == "NOT_RUN_QUOTA" for s in statuses[first_quota + 1:])
    assert sum(1 for op, _ in api.calls if op == "getOpengResultListInfoCnstwk") == 1  # 재시도하지 않음


def test_service_access_denied_blocks_only_that_service(project: Path, catalog):
    denied = lambda p: httpx.Response(200, content=gateway_xml("20", "SERVICE_ACCESS_DENIED_ERROR"))
    api = SyntheticApi({op: denied for op in ("getOpengResultListInfoCnstwk", "getOpengResultListInfoOpengCompt",
                                              "getScsbidListSttusCnstwk")})
    runner, summary, b = run_runner(project, catalog, api, plan=make_plan(project, windows=False))
    assert runner.blocked_services == {"bid_award": "ACCESS_DENIED"}
    steps = {s["step_key"]: s["status"] for s in summary["steps"]}
    assert steps[f"sample:{NO}:notice"] == "DONE" and steps[f"sample:{NO}:opening"] == "BLOCKED"
    assert steps[f"sample:{NO}:award"] == "NOT_RUN_SERVICE_BLOCKED"
    assert final_run_status(runner) == "PARTIAL"


def test_run_budget_exhaustion_marks_not_run(project: Path, catalog):
    runner, summary, b = run_runner(project, catalog, SyntheticApi(), max_run=4)
    assert final_run_status(runner) == "PARTIAL" and "예산" in runner.stop_reason
    assert b.budget.run_used == 4
    assert "NOT_RUN_BUDGET" in {s["status"] for s in summary["steps"]}


def test_reports_are_redacted_and_resume_reuses_done_steps(project: Path, catalog):
    runner, summary, b = run_runner(project, catalog, SyntheticApi(), max_run=6, run_id="verify-first")
    summary["debug_echo"] = f"serviceKey={quote(FAKE_KEY, safe='')} {FAKE_KEY}"
    md, js = write_reports(project / ".local" / "real" / "reports", summary)
    for path in (md, js):
        text = path.read_text(encoding="utf-8")
        assert FAKE_KEY not in text and quote(FAKE_KEY, safe="") not in text
    done = RunRepository(b.conn).done_steps("verify-first")
    assert done
    b.client.close()
    api2 = SyntheticApi()
    runner2, summary2, b2 = run_runner(project, catalog, api2, reuse=done, reuse_from="verify-first", run_id="verify-second")
    reused = [s for s in summary2["steps"] if s["step_key"] in done]
    assert reused and all(s["calls"] == 0 for s in reused)
    called_ops = [op for op, _ in api2.calls]
    assert "getIndstrytyBaseLawrgltInfoList" not in called_ops
