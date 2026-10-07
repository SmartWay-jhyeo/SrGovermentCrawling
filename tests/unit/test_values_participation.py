"""CNT-01~03(참여수 구분, null과 0), AMT(금액 빈값과 0) 해석."""

from __future__ import annotations

from datetime import timedelta

from bidloc.clients.errors import Outcome
from bidloc.normalizers.participation import (
    CountStatus,
    RosterStatus,
    compare_official_to_roster,
    official_count_from_item,
    roster_counts,
)
from bidloc.normalizers.values import (
    ValueStatus,
    normalize_industry_name,
    parse_count,
    parse_krw_amount,
    parse_kst_datetime,
    parse_mfrc_field_list,
    parse_name_code_list,
    split_name_code,
)


def test_amount_blank_absent_zero_invalid_are_distinct():
    item = {"bdgtAmt": "", "presmptPrce": "0", "VAT": "5000000", "bssamt": "1,000", "plnprc": "123.00"}
    assert parse_krw_amount(item, "bdgtAmt") .status == ValueStatus.BLANK and parse_krw_amount(item, "bdgtAmt").value is None
    zero = parse_krw_amount(item, "presmptPrce")
    assert zero.status == ValueStatus.PRESENT and zero.value == 0
    assert parse_krw_amount(item, "VAT").value == 5_000_000
    assert parse_krw_amount(item, "bssamt").status == ValueStatus.INVALID
    assert parse_krw_amount(item, "plnprc").value == 123
    missing = parse_krw_amount(item, "sucsfbidAmt")
    assert missing.status == ValueStatus.ABSENT and missing.value is None


def test_count_parsing():
    assert parse_count({"prtcptCnum": "12"}, "prtcptCnum").value == 12
    assert parse_count({"prtcptCnum": "-1"}, "prtcptCnum").status == ValueStatus.INVALID
    assert parse_count({"prtcptCnum": None}, "prtcptCnum").status == ValueStatus.BLANK


def test_datetime_formats_are_kst_aware():
    parsed = parse_kst_datetime({"opengDt": "2099-07-08 11:00:00"}, "opengDt")
    assert parsed.value.utcoffset() == timedelta(hours=9) and parsed.precision == "second"
    assert parse_kst_datetime({"d": "2099-07-07 18:00"}, "d").precision == "minute"
    assert parse_kst_datetime({"d": "20990707"}, "d").status == ValueStatus.INVALID


def test_official_count_states_are_not_zero():
    assert official_count_from_item({"prtcptCnum": "0"}, Outcome.SUCCESS) .value == 0
    blank = official_count_from_item({"prtcptCnum": ""}, Outcome.SUCCESS)
    assert blank.value is None and blank.status == CountStatus.BLANK_IN_RESPONSE
    assert official_count_from_item({}, Outcome.SUCCESS).status == CountStatus.FIELD_ABSENT
    assert official_count_from_item(None, Outcome.SUCCESS_EMPTY).status == CountStatus.NO_OPENING_RECORD
    failed = official_count_from_item({"prtcptCnum": "5"}, Outcome.UPSTREAM_ERROR)
    assert failed.status == CountStatus.QUERY_FAILED and failed.value is None
    assert official_count_from_item(None, None).status == CountStatus.NOT_QUERIED


def test_roster_states_are_not_zero():
    assert roster_counts(None, queried=False, query_ok=False, complete=False, confirmed_empty=False).status == RosterStatus.NOT_QUERIED
    failed = roster_counts(None, queried=True, query_ok=False, complete=False, confirmed_empty=False)
    assert failed.status == RosterStatus.QUERY_FAILED and failed.row_count is None
    empty = roster_counts([], queried=True, query_ok=True, complete=True, confirmed_empty=True)
    assert empty.status == RosterStatus.NO_DATA and empty.row_count is None
    partial = roster_counts([{"prcbdrBizno": "1"}], queried=True, query_ok=True, complete=False, confirmed_empty=False)
    assert partial.status == RosterStatus.INCOMPLETE and partial.unique_bizno is None


def test_roster_unique_vs_rows_and_comparison():
    rows = [{"prcbdrBizno": "0000000001"}, {"prcbdrBizno": "0000000002"}, {"prcbdrBizno": "0000000002"}]
    roster = roster_counts(rows, queried=True, query_ok=True, complete=True, confirmed_empty=False)
    assert roster.row_count == 3 and roster.unique_bizno == 2
    official = official_count_from_item({"prtcptCnum": "3"}, Outcome.SUCCESS)
    status, detail = compare_official_to_roster(official, roster)
    assert status == "MISMATCH" and "재검토" in detail
    assert official.value == 3 and roster.unique_bizno == 2  # 어느 쪽도 덮어쓰지 않는다
    match_roster = roster_counts(rows[:2], queried=True, query_ok=True, complete=True, confirmed_empty=False)
    assert compare_official_to_roster(official_count_from_item({"prtcptCnum": "2"}, Outcome.SUCCESS), match_roster)[0] == "MATCH"
    incomplete = roster_counts(rows, queried=True, query_ok=True, complete=False, confirmed_empty=False)
    assert compare_official_to_roster(official, incomplete)[0] == "NOT_COMPARABLE"
    blank_bizno = roster_counts([{"prcbdrBizno": ""}], queried=True, query_ok=True, complete=True, confirmed_empty=False)
    assert compare_official_to_roster(official_count_from_item({"prtcptCnum": "1"}, Outcome.SUCCESS), blank_bizno)[0] == "NOT_COMPARABLE"


def test_documented_list_formats():
    mfrc = parse_mfrc_field_list("[1^포장공사^보링.그라우팅.파일공사],[2^토공사]")
    assert mfrc.status == ValueStatus.PRESENT
    assert mfrc.alternatives == [("1", ["포장공사", "보링.그라우팅.파일공사"]), ("2", ["토공사"])]
    assert parse_mfrc_field_list("").status == ValueStatus.BLANK
    assert parse_mfrc_field_list("no brackets").status == ValueStatus.INVALID
    permsn = parse_name_code_list("[액화석유가스충전사업/4615],[A/B업종/1234]")
    assert permsn.groups == [["액화석유가스충전사업", "4615"], ["A/B업종", "1234"]]
    assert split_name_code("액화석유가스판매사업/4617")[:2] == ("액화석유가스판매사업", "4617")
    assert split_name_code("코드없음")[2] == ValueStatus.INVALID


def test_industry_name_normalization_handles_middle_dot_variants():
    names = ["도장·습식·방수·석공사업", "도장ㆍ습식ㆍ방수ㆍ석공사업", "도장 습식 방수 석공사업", "도장.습식.방수.석공사업"]
    assert len({normalize_industry_name(n) for n in names}) == 1
