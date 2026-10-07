"""조회기간 분할과 경계 중복 제거.

오퍼레이션별 최대 조회기간은 대부분 참고자료에 없다(명시된 것: 입찰가격산식A·평가대상주력분야 '최대 1개월').
경계(inqryBgnDt/inqryEndDt) 포함 여부도 문서에 없어 실응답 검증 전까지 겹침 조회 + 식별키 중복 제거를 기본으로 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Hashable, Iterable

from bidloc.timeutil import KST, format_inqry_datetime

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class QueryWindow:
    begin: datetime
    end: datetime

    @property
    def inqry_bgn_dt(self) -> str:
        return format_inqry_datetime(self.begin)

    @property
    def inqry_end_dt(self) -> str:
        return format_inqry_datetime(self.end)


def split_windows(begin: datetime, end: datetime, *, max_span: timedelta,
                  overlap: timedelta = timedelta(0)) -> list[QueryWindow]:
    """[begin, end] (분 단위, 양끝 포함 의도)를 max_span 이하 창으로 나눈다. overlap만큼 다음 창과 겹친다."""
    if begin.tzinfo is None or end.tzinfo is None:
        raise ValueError("timezone-aware datetime이 필요하다")
    begin = begin.astimezone(KST).replace(second=0, microsecond=0)
    end = end.astimezone(KST).replace(second=0, microsecond=0)
    if end < begin:
        raise ValueError("end < begin")
    if max_span < MINUTE:
        raise ValueError("max_span은 1분 이상")
    if overlap < timedelta(0) or overlap >= max_span:
        raise ValueError("overlap은 0 이상, max_span 미만")
    windows: list[QueryWindow] = []
    cursor = begin
    while cursor <= end:
        stop = min(cursor + max_span - MINUTE, end)
        windows.append(QueryWindow(cursor, stop))
        if stop >= end:
            break
        cursor = stop + MINUTE - overlap
    return windows


@dataclass
class DedupeResult:
    unique: list[dict] = field(default_factory=list)
    exact_duplicates: int = 0
    conflicting_keys: list[Hashable] = field(default_factory=list)
    keyless: int = 0


def dedupe_by_key(items: Iterable[dict], key_fn: Callable[[dict], Hashable | None]) -> DedupeResult:
    """같은 키·같은 내용은 1건으로, 같은 키·다른 내용은 충돌로 남기고 둘 다 보존한다(조용히 덮어쓰지 않음)."""
    result = DedupeResult()
    seen: dict[Hashable, set[str]] = {}
    for item in items:
        key = key_fn(item)
        if key is None:
            result.keyless += 1
            result.unique.append(item)
            continue
        body = json.dumps(item, ensure_ascii=False, sort_keys=True)
        bodies = seen.setdefault(key, set())
        if body in bodies:
            result.exact_duplicates += 1
            continue
        if bodies and key not in result.conflicting_keys:
            result.conflicting_keys.append(key)
        bodies.add(body)
        result.unique.append(item)
    return result
