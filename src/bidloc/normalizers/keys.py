"""복합키와 연결(linkage) 판정.

문서 근거(조달청 OpenAPI 참고자료, 확인일 2026-09-16):
- 입찰공고 계열 응답: bidNtceNo(입찰공고번호) + bidNtceOrd(입찰공고차수)
- 기초금액·변경이력·개찰결과 계열: bidClsfcNo(입찰분류번호, 낙찰정보 문서: '동일한 입찰공고번호에 대한 집행일련번호')
- 개찰결과·낙찰·재입찰·유찰: rbidNo(재입찰번호). 단, 공사 예비가격상세는 포털 Swagger에 rbidNtceNo로 표기되어 문서 간 불일치.

원칙:
- 공고번호 단독 JOIN 금지. 문자열 그대로 비교하고 0 채움·정수 변환을 하지 않는다.
- 같은 공고번호라도 차수가 다르면 연결하지 않고 SAME_NO_OTHER_ORD로 남긴다.
- 숫자로는 같지만 문자열 형식이 다른 경우(예: '0'과 '000')는 FORMAT_MISMATCH로 남겨 검토한다.
- 공고차수가 재공고·재입찰 중 무엇으로 증가하는지는 문서 설명이 모호하므로 실응답 검증 전까지 의미를 추정하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

REBID_FIELD_CANDIDATES = ("rbidNo", "rbidNtceNo")


@dataclass(frozen=True, order=True)
class NoticeRevisionKey:
    bid_ntce_no: str
    bid_ntce_ord: str


@dataclass(frozen=True, order=True)
class OpeningUnitKey:
    bid_ntce_no: str
    bid_ntce_ord: str
    bid_clsfc_no: str
    rbid_no: str

    @property
    def notice(self) -> NoticeRevisionKey:
        return NoticeRevisionKey(self.bid_ntce_no, self.bid_ntce_ord)


@dataclass(frozen=True)
class KeyExtraction:
    key: NoticeRevisionKey | OpeningUnitKey | None
    missing_fields: tuple[str, ...] = ()
    blank_fields: tuple[str, ...] = ()
    rebid_field_used: str | None = None


def _get(item: Mapping[str, str | None], name: str) -> tuple[str | None, str]:
    if name not in item:
        return None, "missing"
    value = item[name]
    if value is None or str(value).strip() == "":
        return None, "blank"
    return str(value).strip(), "ok"


def notice_key(item: Mapping[str, str | None]) -> KeyExtraction:
    missing: list[str] = []
    blank: list[str] = []
    values: dict[str, str] = {}
    for name in ("bidNtceNo", "bidNtceOrd"):
        value, state = _get(item, name)
        if state == "missing":
            missing.append(name)
        elif state == "blank":
            blank.append(name)
        else:
            values[name] = value  # type: ignore[assignment]
    if missing or blank:
        return KeyExtraction(None, tuple(missing), tuple(blank))
    return KeyExtraction(NoticeRevisionKey(values["bidNtceNo"], values["bidNtceOrd"]))


def opening_key(item: Mapping[str, str | None],
                rebid_fields: Iterable[str] = REBID_FIELD_CANDIDATES) -> KeyExtraction:
    missing: list[str] = []
    blank: list[str] = []
    values: dict[str, str] = {}
    for name in ("bidNtceNo", "bidNtceOrd", "bidClsfcNo"):
        value, state = _get(item, name)
        if state == "missing":
            missing.append(name)
        elif state == "blank":
            blank.append(name)
        else:
            values[name] = value  # type: ignore[assignment]
    rebid_used = None
    rebid_value = None
    present = [name for name in rebid_fields if name in item]
    if len(present) > 1:
        distinct = {str(item[n]).strip() for n in present if item[n] is not None}
        if len(distinct) > 1:
            blank.append("rbidNo/rbidNtceNo(값 충돌)")
    for name in present:
        value, state = _get(item, name)
        if state == "ok":
            rebid_used, rebid_value = name, value
            break
    if rebid_value is None:
        if present:
            blank.append(present[0])
        else:
            missing.append("rbidNo")
    if missing or blank:
        return KeyExtraction(None, tuple(missing), tuple(blank), rebid_used)
    return KeyExtraction(
        OpeningUnitKey(values["bidNtceNo"], values["bidNtceOrd"], values["bidClsfcNo"], rebid_value),  # type: ignore[arg-type]
        rebid_field_used=rebid_used,
    )


def _numeric_equal(a: str, b: str) -> bool:
    return a.isdigit() and b.isdigit() and int(a) == int(b)


@dataclass
class LinkReport:
    linked: list[tuple[OpeningUnitKey, NoticeRevisionKey]] = field(default_factory=list)
    same_no_other_ord: list[OpeningUnitKey] = field(default_factory=list)
    format_mismatch: list[tuple[OpeningUnitKey, NoticeRevisionKey]] = field(default_factory=list)
    not_linked: list[OpeningUnitKey] = field(default_factory=list)

    def status_of(self, key: OpeningUnitKey) -> str:
        if any(k == key for k, _ in self.linked):
            return "LINKED"
        if key in self.same_no_other_ord:
            return "SAME_NO_OTHER_ORD"
        if any(k == key for k, _ in self.format_mismatch):
            return "FORMAT_MISMATCH"
        return "NOT_LINKED"


def link_openings_to_notices(notice_keys: Iterable[NoticeRevisionKey],
                             opening_keys: Iterable[OpeningUnitKey]) -> LinkReport:
    notices = set(notice_keys)
    by_no: dict[str, list[NoticeRevisionKey]] = {}
    for n in notices:
        by_no.setdefault(n.bid_ntce_no, []).append(n)
    report = LinkReport()
    for opening in opening_keys:
        target = opening.notice
        if target in notices:
            report.linked.append((opening, target))
            continue
        candidates = by_no.get(opening.bid_ntce_no, [])
        near = [n for n in candidates if _numeric_equal(n.bid_ntce_ord, opening.bid_ntce_ord)]
        if near:
            report.format_mismatch.append((opening, near[0]))
        elif candidates:
            report.same_no_other_ord.append(opening)
        else:
            report.not_linked.append(opening)
    return report
