"""응답 본문(JSON/XML/텍스트) 파싱.

문서상 정상 envelope:
  response.header.{resultCode,resultMsg}
  response.body.{items.item[*], numOfRows, pageNo, totalCount}

실응답의 JSON items 형태(list / items.item object / items.item list / 빈 문자열)는 문서에 확정되어 있지 않다.
관측된 형태를 shape 문자열로 남기고, 알 수 없는 형태는 problems에 기록해 MALFORMED로 분류한다.
게이트웨이 오류 형식도 문서에 envelope가 없어 알려진 형태(OpenAPI_ServiceResponse, 평문)를 방어적으로 인식한다.
값은 원문 문자열로 보존한다. 빈 요소는 "", 요소 없음은 키 없음, JSON null은 None이다. 0으로 바꾸지 않는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from defusedxml import ElementTree as SafeET

ItemDict = dict[str, "str | None"]

_DIGITS = re.compile(r"^\s*\d+\s*$")


@dataclass
class ParsedBody:
    fmt: str  # json | xml | text | empty
    kind: str  # standard | gateway_error | flat_error | unknown
    result_code_raw: str | None = None
    result_code: str | None = None
    result_msg: str | None = None
    items: list[ItemDict] | None = None
    total_count_raw: str | None = None
    total_count: int | None = None
    page_no: int | None = None
    num_of_rows: int | None = None
    shape: str = ""
    problems: list[str] = field(default_factory=list)
    auth_msg: str | None = None
    reason_code: str | None = None
    err_msg: str | None = None
    text_snippet: str | None = None

    @property
    def item_count(self) -> int | None:
        return None if self.items is None else len(self.items)


def normalize_result_code(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 1:
        return "0" + text
    return text


def _to_int(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if _DIGITS.match(text):
        return int(text)
    return None


def _scalar_to_str(value: Any, problems: list[str], field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        problems.append(f"boolean value in field {field_name}")
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        problems.append(f"float value in field {field_name} (정밀도 손실 가능)")
        return repr(value)
    if isinstance(value, str):
        return value
    problems.append(f"nested value in field {field_name}")
    return json.dumps(value, ensure_ascii=False)


def sniff_format(body: bytes) -> str:
    stripped = body.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not stripped:
        return "empty"
    head = stripped[:1]
    if head in (b"{", b"["):
        return "json"
    if head == b"<":
        return "xml"
    return "text"


def parse_body(body: bytes | None, *, encoding_hint: str | None = None) -> ParsedBody:
    if body is None:
        return ParsedBody(fmt="empty", kind="unknown", shape="no-body")
    fmt = sniff_format(body)
    if fmt == "empty":
        return ParsedBody(fmt="empty", kind="unknown", shape="empty-body")
    text = _decode(body, encoding_hint)
    if fmt == "json":
        return _parse_json(text)
    if fmt == "xml":
        return _parse_xml(text)
    return ParsedBody(fmt="text", kind="unknown", shape="text", text_snippet=text.strip()[:300])


def _decode(body: bytes, encoding_hint: str | None) -> str:
    for enc in [encoding_hint, "utf-8"]:
        if not enc:
            continue
        try:
            return body.decode(enc).lstrip("﻿")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace").lstrip("﻿")


# ---------------------------------------------------------------- JSON

def _parse_json(text: str) -> ParsedBody:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedBody(fmt="json", kind="unknown", shape="json:invalid", problems=[f"json decode error: {exc.msg}"],
                          text_snippet=text.strip()[:300])
    if not isinstance(data, dict):
        return ParsedBody(fmt="json", kind="unknown", shape="json:root-not-object", problems=["json root is not object"])

    if "OpenAPI_ServiceResponse" in data and isinstance(data["OpenAPI_ServiceResponse"], dict):
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader") or {}
        return _gateway(fmt="json", header=header if isinstance(header, dict) else {})

    root = data.get("response") if isinstance(data.get("response"), dict) else None
    shape_root = "json:response"
    if root is None and ("header" in data or "body" in data):
        root = data
        shape_root = "json:no-response-wrapper"
    if root is None:
        if "resultCode" in data or "resultMsg" in data:
            parsed = ParsedBody(fmt="json", kind="flat_error", shape="json:flat")
            parsed.result_code_raw = None if data.get("resultCode") is None else str(data.get("resultCode"))
            parsed.result_code = normalize_result_code(data.get("resultCode"))
            parsed.result_msg = None if data.get("resultMsg") is None else str(data.get("resultMsg"))
            return parsed
        return ParsedBody(fmt="json", kind="unknown", shape="json:unknown-root", problems=["unknown json root keys"])

    parsed = ParsedBody(fmt="json", kind="standard")
    header = root.get("header") if isinstance(root.get("header"), dict) else {}
    parsed.result_code_raw = None if header.get("resultCode") is None else str(header.get("resultCode"))
    parsed.result_code = normalize_result_code(header.get("resultCode"))
    parsed.result_msg = None if header.get("resultMsg") is None else str(header.get("resultMsg"))
    body = root.get("body")
    if body is None:
        parsed.shape = f"{shape_root}:no-body"
        parsed.items = None
        return parsed
    if not isinstance(body, dict):
        parsed.shape = f"{shape_root}:body-not-object"
        parsed.problems.append("body is not object")
        return parsed
    parsed.total_count_raw = None if body.get("totalCount") is None else str(body.get("totalCount"))
    parsed.total_count = _to_int(body.get("totalCount"))
    if parsed.total_count_raw is not None and parsed.total_count is None:
        parsed.problems.append("totalCount is not a non-negative integer")
    parsed.page_no = _to_int(body.get("pageNo"))
    parsed.num_of_rows = _to_int(body.get("numOfRows"))

    items_shape, items = _json_items(body, parsed.problems)
    parsed.shape = f"{shape_root}:{items_shape}"
    parsed.items = items
    return parsed


def _json_items(body: dict[str, Any], problems: list[str]) -> tuple[str, list[ItemDict] | None]:
    if "items" not in body:
        return "items=absent", None
    raw = body["items"]
    if raw is None or raw == "":
        return "items=empty", []
    if isinstance(raw, list):
        return "items=list", _json_item_list(raw, problems)
    if isinstance(raw, dict):
        if "item" not in raw:
            if not raw:
                return "items=empty-object", []
            problems.append("items object without item key")
            return "items=object-without-item", None
        inner = raw["item"]
        if inner is None or inner == "":
            return "items.item=empty", []
        if isinstance(inner, dict):
            return "items.item=object", _json_item_list([inner], problems)
        if isinstance(inner, list):
            return "items.item=list", _json_item_list(inner, problems)
        problems.append("items.item has unexpected type")
        return "items.item=unexpected", None
    problems.append("items has unexpected type")
    return "items=unexpected", None


def _json_item_list(raw_items: list[Any], problems: list[str]) -> list[ItemDict] | None:
    out: list[ItemDict] = []
    for idx, element in enumerate(raw_items):
        if not isinstance(element, dict):
            problems.append(f"item #{idx} is not object")
            return None
        out.append({str(k): _scalar_to_str(v, problems, str(k)) for k, v in element.items()})
    return out


# ---------------------------------------------------------------- XML

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(elem: Any, name: str) -> str | None:
    for child in list(elem):
        if _local(child.tag) == name:
            return child.text if child.text is not None else ""
    return None


def _find(elem: Any, name: str) -> Any:
    for child in list(elem):
        if _local(child.tag) == name:
            return child
    return None


def _parse_xml(text: str) -> ParsedBody:
    try:
        root = SafeET.fromstring(text.encode("utf-8"))
    except Exception as exc:  # defusedxml 차단 예외 포함
        return ParsedBody(fmt="xml", kind="unknown", shape="xml:invalid", problems=[f"xml parse error: {type(exc).__name__}"],
                          text_snippet=text.strip()[:300])
    tag = _local(root.tag)
    if tag == "OpenAPI_ServiceResponse":
        header = _find(root, "cmmMsgHeader")
        values = {}
        if header is not None:
            for name in ("errMsg", "returnAuthMsg", "returnReasonCode"):
                values[name] = _child_text(header, name)
        return _gateway(fmt="xml", header=values)
    if tag != "response":
        return ParsedBody(fmt="xml", kind="unknown", shape=f"xml:root={tag}", problems=["unknown xml root"])

    parsed = ParsedBody(fmt="xml", kind="standard")
    header = _find(root, "header")
    if header is not None:
        parsed.result_code_raw = _child_text(header, "resultCode")
        parsed.result_code = normalize_result_code(parsed.result_code_raw)
        parsed.result_msg = _child_text(header, "resultMsg")
    body = _find(root, "body")
    if body is None:
        parsed.shape = "xml:response:no-body"
        return parsed
    parsed.total_count_raw = _child_text(body, "totalCount")
    parsed.total_count = _to_int(parsed.total_count_raw)
    if parsed.total_count_raw not in (None, "") and parsed.total_count is None:
        parsed.problems.append("totalCount is not a non-negative integer")
    parsed.page_no = _to_int(_child_text(body, "pageNo"))
    parsed.num_of_rows = _to_int(_child_text(body, "numOfRows"))
    items_elem = _find(body, "items")
    if items_elem is None:
        parsed.shape = "xml:response:items=absent"
        parsed.items = None
        return parsed
    out: list[ItemDict] = []
    for item in list(items_elem):
        if _local(item.tag) != "item":
            parsed.problems.append(f"unexpected element in items: {_local(item.tag)}")
            parsed.items = None
            parsed.shape = "xml:response:items=unexpected-child"
            return parsed
        record: ItemDict = {}
        for fld in list(item):
            if len(list(fld)):
                parsed.problems.append(f"nested element in item field {_local(fld.tag)}")
            record[_local(fld.tag)] = fld.text if fld.text is not None else ""
        out.append(record)
    parsed.items = out
    parsed.shape = "xml:response:items.item*" if out else "xml:response:items=empty"
    return parsed


def _gateway(*, fmt: str, header: dict[str, Any]) -> ParsedBody:
    parsed = ParsedBody(fmt=fmt, kind="gateway_error", shape=f"{fmt}:OpenAPI_ServiceResponse")
    parsed.err_msg = None if header.get("errMsg") is None else str(header.get("errMsg"))
    parsed.auth_msg = None if header.get("returnAuthMsg") is None else str(header.get("returnAuthMsg"))
    parsed.reason_code = normalize_result_code(header.get("returnReasonCode"))
    parsed.result_code_raw = None if header.get("returnReasonCode") is None else str(header.get("returnReasonCode"))
    parsed.result_code = parsed.reason_code
    parsed.result_msg = parsed.auth_msg or parsed.err_msg
    return parsed
