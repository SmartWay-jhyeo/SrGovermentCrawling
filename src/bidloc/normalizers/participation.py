"""참여업체 수 관측값.

세 가지 숫자를 섞지 않는다.
1) OFFICIAL_PRTCPT_CNUM: 개찰결과/낙찰 목록의 prtcptCnum(문서 명칭 '참가업체수'). 무효·공동수급 포함 범위는 문서에 없다.
2) ROSTER_ROW_COUNT: 개찰완료 목록(getOpengResultListInfoOpengCompt)의 행 수. 모든 페이지를 받은 경우만 유효.
3) ROSTER_UNIQUE_BIZNO: 같은 명부에서 투찰업체사업자등록번호(prcbdrBizno) 고유 수.
빈 응답·오류·미조회·개찰기록 없음은 0이 아니다. 값이 None이면 상태로 이유를 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from bidloc.clients.errors import DATA_OK, Outcome
from bidloc.normalizers.values import ValueStatus, parse_count


class CountStatus(str, Enum):
    OBSERVED = "OBSERVED"
    BLANK_IN_RESPONSE = "BLANK_IN_RESPONSE"
    FIELD_ABSENT = "FIELD_ABSENT"
    INVALID = "INVALID"
    NOT_QUERIED = "NOT_QUERIED"
    QUERY_FAILED = "QUERY_FAILED"
    NO_OPENING_RECORD = "NO_OPENING_RECORD"


class RosterStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    NO_DATA = "NO_DATA"
    NOT_QUERIED = "NOT_QUERIED"
    QUERY_FAILED = "QUERY_FAILED"


@dataclass(frozen=True)
class OfficialCount:
    value: int | None
    status: CountStatus
    raw: str | None


@dataclass(frozen=True)
class RosterCounts:
    row_count: int | None
    unique_bizno: int | None
    blank_bizno_rows: int | None
    status: RosterStatus
    detail: str = ""


def official_count_from_item(item: Mapping[str, str | None] | None, query_outcome: Outcome | None) -> OfficialCount:
    if query_outcome is None:
        return OfficialCount(None, CountStatus.NOT_QUERIED, None)
    if query_outcome not in DATA_OK:
        return OfficialCount(None, CountStatus.QUERY_FAILED, None)
    if item is None:
        return OfficialCount(None, CountStatus.NO_OPENING_RECORD, None)
    parsed = parse_count(item, "prtcptCnum")
    mapping = {
        ValueStatus.PRESENT: CountStatus.OBSERVED,
        ValueStatus.BLANK: CountStatus.BLANK_IN_RESPONSE,
        ValueStatus.ABSENT: CountStatus.FIELD_ABSENT,
        ValueStatus.INVALID: CountStatus.INVALID,
    }
    return OfficialCount(parsed.value, mapping[parsed.status], parsed.raw)


def roster_counts(items: Iterable[Mapping[str, str | None]] | None, *, queried: bool, query_ok: bool,
                  complete: bool, confirmed_empty: bool) -> RosterCounts:
    if not queried:
        return RosterCounts(None, None, None, RosterStatus.NOT_QUERIED)
    if not query_ok:
        return RosterCounts(None, None, None, RosterStatus.QUERY_FAILED)
    rows = list(items or [])
    if confirmed_empty and not rows:
        return RosterCounts(None, None, None, RosterStatus.NO_DATA, "명부 조회 결과 없음(참여 0으로 해석하지 않음)")
    if not complete:
        return RosterCounts(None, None, None, RosterStatus.INCOMPLETE, f"수집 {len(rows)}행, 전체 페이지 확인 실패")
    biznos = [str(r.get("prcbdrBizno") or "").strip() for r in rows]
    blank = sum(1 for b in biznos if not b)
    unique = len({b for b in biznos if b})
    return RosterCounts(len(rows), unique, blank, RosterStatus.COMPLETE)


def compare_official_to_roster(official: OfficialCount, roster: RosterCounts) -> tuple[str, str]:
    """(MATCH|MISMATCH|NOT_COMPARABLE, 설명). 불일치를 어느 한쪽으로 덮어쓰지 않는다."""
    if official.status != CountStatus.OBSERVED or official.value is None:
        return "NOT_COMPARABLE", f"공식 참가업체수 상태 {official.status.value}"
    if roster.status != RosterStatus.COMPLETE or roster.unique_bizno is None or roster.row_count is None:
        return "NOT_COMPARABLE", f"명부 상태 {roster.status.value}"
    if roster.blank_bizno_rows:
        return "NOT_COMPARABLE", f"사업자번호 빈 행 {roster.blank_bizno_rows}개"
    if official.value == roster.unique_bizno == roster.row_count:
        return "MATCH", "공식 수 = 명부 행 수 = 고유 사업자번호 수"
    return "MISMATCH", (
        f"공식 {official.value}, 명부 행 {roster.row_count}, 고유 사업자번호 {roster.unique_bizno} — 원인 미확인(재검토 대상)"
    )
