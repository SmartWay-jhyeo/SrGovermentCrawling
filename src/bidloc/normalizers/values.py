"""원문 값 해석. 빈값·요소 없음·형식 오류를 0으로 바꾸지 않는다.

금액 필드의 단위는 참고자료에 '(원화,원)'으로 표기된 경우가 많지만 부가세 포함 여부는 필드마다 다르거나 미기재다.
이 모듈은 숫자 해석만 하고 금액 종류(기초금액/추정가격/예정가격/낙찰금액)는 호출자가 필드명으로 구분해 별도로 보관한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from bidloc.timeutil import KST


class ValueStatus(str, Enum):
    PRESENT = "PRESENT"
    BLANK = "BLANK"      # 요소는 있으나 빈 문자열/None
    ABSENT = "ABSENT"    # 응답 항목에 필드 자체가 없음
    INVALID = "INVALID"  # 형식 오류
    OUT_OF_RANGE = "OUT_OF_RANGE"  # 형식은 맞으나 SQLite 64비트 정수 범위를 벗어남(원본 이상값)


@dataclass(frozen=True)
class ParsedInt:
    value: int | None
    status: ValueStatus
    raw: str | None


@dataclass(frozen=True)
class ParsedDecimal:
    value: Decimal | None
    status: ValueStatus
    raw: str | None


@dataclass(frozen=True)
class ParsedDatetime:
    value: datetime | None
    status: ValueStatus
    raw: str | None
    precision: str | None = None


_INT_RE = re.compile(r"^-?\d+$")
_INT_WITH_ZERO_FRACTION = re.compile(r"^(-?\d+)\.0+$")


def _raw(item: Mapping[str, str | None], name: str) -> tuple[str | None, ValueStatus | None]:
    if name not in item:
        return None, ValueStatus.ABSENT
    value = item[name]
    if value is None or str(value).strip() == "":
        return (None if value is None else str(value)), ValueStatus.BLANK
    return str(value).strip(), None


SQLITE_INT_MIN, SQLITE_INT_MAX = -(2**63), 2**63 - 1


def _ranged(value: int, raw: str | None) -> ParsedInt:
    """SQLite가 저장할 수 없는 크기는 0이나 절단값으로 바꾸지 않고 OUT_OF_RANGE로 둔다.

    실관측: 공고 R25BK01131785의 bdgtAmt가 20자리(약 1.2경)로 들어왔다(발주기관 입력 오류로 보임).
    """
    if SQLITE_INT_MIN <= value <= SQLITE_INT_MAX:
        return ParsedInt(value, ValueStatus.PRESENT, raw)
    return ParsedInt(None, ValueStatus.OUT_OF_RANGE, raw)


def parse_krw_amount(item: Mapping[str, str | None], name: str) -> ParsedInt:
    """원화 정수 금액. '1,000' 같은 구분기호는 문서에 없으므로 INVALID로 둔다."""
    raw, status = _raw(item, name)
    if status is not None:
        return ParsedInt(None, status, raw)
    assert raw is not None
    if _INT_RE.match(raw):
        return _ranged(int(raw), raw)
    match = _INT_WITH_ZERO_FRACTION.match(raw)
    if match:
        return _ranged(int(match.group(1)), raw)
    return ParsedInt(None, ValueStatus.INVALID, raw)


def parse_count(item: Mapping[str, str | None], name: str) -> ParsedInt:
    raw, status = _raw(item, name)
    if status is not None:
        return ParsedInt(None, status, raw)
    assert raw is not None
    if raw.isdigit():
        return _ranged(int(raw), raw)
    return ParsedInt(None, ValueStatus.INVALID, raw)


def parse_decimal(item: Mapping[str, str | None], name: str) -> ParsedDecimal:
    raw, status = _raw(item, name)
    if status is not None:
        return ParsedDecimal(None, status, raw)
    assert raw is not None
    try:
        return ParsedDecimal(Decimal(raw), ValueStatus.PRESENT, raw)
    except InvalidOperation:
        return ParsedDecimal(None, ValueStatus.INVALID, raw)


_DT_FORMATS = (
    ("%Y-%m-%d %H:%M:%S", "second"),
    ("%Y-%m-%d %H:%M", "minute"),
    ("%Y-%m-%d", "day"),
)


def parse_kst_datetime_value(raw_value: str | None) -> ParsedDatetime:
    if raw_value is None or raw_value.strip() == "":
        return ParsedDatetime(None, ValueStatus.BLANK, raw_value)
    text = raw_value.strip()
    for fmt, precision in _DT_FORMATS:
        try:
            return ParsedDatetime(datetime.strptime(text, fmt).replace(tzinfo=KST), ValueStatus.PRESENT, text, precision)
        except ValueError:
            continue
    return ParsedDatetime(None, ValueStatus.INVALID, text)


def parse_kst_datetime(item: Mapping[str, str | None], name: str) -> ParsedDatetime:
    raw, status = _raw(item, name)
    if status is not None:
        return ParsedDatetime(None, status, raw)
    return parse_kst_datetime_value(raw)


@dataclass(frozen=True)
class ParsedList:
    groups: list[list[str]] | None
    status: ValueStatus
    raw: str | None
    problems: tuple[str, ...] = ()


def parse_bracket_groups(raw_value: str | None, *, inner_sep: str) -> ParsedList:
    """'[a^b],[c^d]' 형식. 문서상 면허제한 주력분야 목록, 업종 포함면허 등에 쓰인다."""
    if raw_value is None or raw_value.strip() == "":
        return ParsedList(None, ValueStatus.BLANK, raw_value)
    text = raw_value.strip()
    groups = re.findall(r"\[([^\[\]]*)\]", text)
    leftover = re.sub(r"\[[^\[\]]*\]", "", text).replace(",", "").strip()
    problems: list[str] = []
    if leftover:
        problems.append("대괄호 밖에 해석하지 않은 문자가 있다")
    if not groups:
        return ParsedList(None, ValueStatus.INVALID, text, ("대괄호 그룹이 없다",))
    return ParsedList([g.split(inner_sep) for g in groups], ValueStatus.PRESENT if not problems else ValueStatus.INVALID,
                      text, tuple(problems))


@dataclass(frozen=True)
class MfrcCondition:
    """주력분야 조건 해석 결과(문서 기준 후보). 실응답·공고문 대조 전에는 판정에 쓰지 않는다."""

    alternatives: list[tuple[str, list[str]]]  # [(주력분야제한그룹순번, [AND로 묶인 주력분야명...]), ...] 그룹 간 OR
    status: ValueStatus
    raw: str | None
    basis: str = "참고자료: '와'는 '^', '또는'은 대괄호 [] 로 구분 — 실응답 검증 전(DOCUMENTED)"


def parse_mfrc_field_list(raw_value: str | None) -> MfrcCondition:
    parsed = parse_bracket_groups(raw_value, inner_sep="^")
    if parsed.groups is None:
        return MfrcCondition([], parsed.status, parsed.raw)
    alternatives: list[tuple[str, list[str]]] = []
    for group in parsed.groups:
        if len(group) < 2:
            return MfrcCondition([], ValueStatus.INVALID, parsed.raw)
        alternatives.append((group[0], [g for g in group[1:]]))
    return MfrcCondition(alternatives, parsed.status, parsed.raw)


def parse_name_code_list(raw_value: str | None) -> ParsedList:
    """'[허용업종명/코드],[허용업종명/코드]' — 업종명에 '/'가 들어갈 수 있어 마지막 '/' 기준으로 나눈다."""
    parsed = parse_bracket_groups(raw_value, inner_sep="\x00")
    if parsed.groups is None:
        return parsed
    out: list[list[str]] = []
    for group in parsed.groups:
        text = group[0]
        if "/" not in text:
            return ParsedList(None, ValueStatus.INVALID, parsed.raw, ("'/' 구분자가 없다",))
        name, code = text.rsplit("/", 1)
        out.append([name, code])
    return ParsedList(out, parsed.status, parsed.raw, parsed.problems)


def split_name_code(raw_value: str | None) -> tuple[str | None, str | None, ValueStatus]:
    """lcnsLmtNm '면허명/코드' (참고자료 예시: 액화석유가스판매사업/4617)."""
    if raw_value is None or raw_value.strip() == "":
        return None, None, ValueStatus.BLANK
    text = raw_value.strip()
    if "/" not in text:
        return text, None, ValueStatus.INVALID
    name, code = text.rsplit("/", 1)
    return name.strip(), code.strip(), ValueStatus.PRESENT


def normalize_industry_name(name: str) -> str:
    """업종명 비교용 정규화: 가운뎃점 변형(·, ㆍ, ･, .)과 공백을 제거한다. 표시·저장에는 원문을 쓴다."""
    return re.sub(r"[\s·ㆍ･.‧・]", "", name)
