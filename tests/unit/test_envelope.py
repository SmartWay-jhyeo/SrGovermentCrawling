"""API-07: singleton/list/빈 items, 비정상 envelope. API-03: HTTP 200 내부 XML 오류 형태."""

from __future__ import annotations

import json
from pathlib import Path

from bidloc.clients.envelope import parse_body

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"


def _json(items, total="1"):
    return json.dumps({"response": {"header": {"resultCode": "00", "resultMsg": "정상"},
                                    "body": {"items": items, "numOfRows": "10", "pageNo": "1", "totalCount": total}}},
                      ensure_ascii=False).encode("utf-8")


def test_json_items_item_list_fixture():
    parsed = parse_body((FIXTURES / "notice_list_items_item_list.json").read_bytes())
    assert parsed.kind == "standard" and parsed.result_code == "00"
    assert parsed.shape == "json:response:items.item=list"
    assert len(parsed.items) == 2 and parsed.total_count == 2
    second = parsed.items[1]
    assert second["bdgtAmt"] == "" and second["presmptPrce"] == "0" and "VAT" not in second


def test_json_singleton_item_object():
    parsed = parse_body(_json({"item": {"bidNtceNo": "R99BK99990001", "bidNtceOrd": "000"}}))
    assert parsed.shape.endswith("items.item=object") and len(parsed.items) == 1


def test_json_items_as_list():
    parsed = parse_body(_json([{"a": "1"}, {"a": "2"}], total="2"))
    assert parsed.shape.endswith("items=list") and [i["a"] for i in parsed.items] == ["1", "2"]


def test_json_empty_items_variants():
    for items in ("", None, {"item": ""}, {"item": []}, {}):
        parsed = parse_body(_json(items, total="0"))
        assert parsed.items == [], items
        assert parsed.total_count == 0


def test_json_items_absent_is_none_not_empty():
    body = json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": "0"}}}).encode()
    parsed = parse_body(body)
    assert parsed.items is None and parsed.shape.endswith("items=absent")


def test_json_malformed_items_flags_problem():
    parsed = parse_body(_json({"notItem": [1, 2]}))
    assert parsed.items is None and parsed.problems


def test_json_numbers_become_strings_and_null_preserved():
    parsed = parse_body(_json({"item": {"prtcptCnum": 3, "bssamt": None}}))
    assert parsed.items[0]["prtcptCnum"] == "3" and parsed.items[0]["bssamt"] is None


def test_total_count_non_numeric_is_problem_not_zero():
    parsed = parse_body(_json({"item": []}, total="abc"))
    assert parsed.total_count is None and any("totalCount" in p for p in parsed.problems)


def test_xml_single_item_fixture_and_blank_vs_missing():
    parsed = parse_body((FIXTURES / "region_single_item.xml").read_bytes())
    assert parsed.fmt == "xml" and parsed.kind == "standard"
    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item["prtcptPsblRgnNm"] == "합성도 합성군"
    assert item["bsnsDivNm"] == ""          # 빈 요소
    assert "lmtGrpNo" not in item           # 요소 없음


def test_xml_multiple_and_empty_items():
    xml = ("<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header><body><items>"
           "<item><a>1</a></item><item><a>2</a></item></items><numOfRows>10</numOfRows><pageNo>1</pageNo>"
           "<totalCount>2</totalCount></body></response>").encode()
    assert [i["a"] for i in parse_body(xml).items] == ["1", "2"]
    empty = b"<response><header><resultCode>00</resultCode></header><body><items/><totalCount>0</totalCount></body></response>"
    parsed = parse_body(empty)
    assert parsed.items == [] and parsed.total_count == 0


def test_gateway_xml_error_fixture():
    parsed = parse_body((FIXTURES / "gateway_error_key_not_registered.xml").read_bytes())
    assert parsed.kind == "gateway_error"
    assert parsed.result_code == "30" and parsed.auth_msg == "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"


def test_flat_json_error_and_text_body():
    flat = parse_body(json.dumps({"resultCode": "22", "resultMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"}).encode())
    assert flat.kind == "flat_error" and flat.result_code == "22"
    text = parse_body(b"Unauthorized")
    assert text.fmt == "text" and text.text_snippet == "Unauthorized"


def test_bom_and_single_digit_result_code():
    body = "﻿".encode() + _json({"item": []}, total="0").replace(b'"00"', b'"0"')
    parsed = parse_body(body)
    assert parsed.result_code == "00"


def test_invalid_payloads():
    assert parse_body(b"{not json").shape == "json:invalid"
    assert parse_body(b"<response><unclosed>").shape == "xml:invalid"
    assert parse_body(b"").fmt == "empty"
    assert parse_body(None).shape == "no-body"


def test_xml_entity_expansion_is_blocked():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
            b"<response><header><resultCode>&b;</resultCode></header></response>")
    parsed = parse_body(bomb)
    assert parsed.shape == "xml:invalid"
