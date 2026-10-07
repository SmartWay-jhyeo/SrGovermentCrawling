"""시간 유틸리티.

내부 시각은 timezone-aware로 다룬다. API 조건은 한국시간(KST) 기준이다.
대한민국은 1988년 이후 일광절약시간을 쓰지 않으므로 고정 UTC+9 오프셋을 사용한다.
(Windows에는 IANA tz 데이터베이스가 기본 포함되지 않아 zoneinfo 대신 고정 오프셋을 쓴다.)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_kst() -> datetime:
    return datetime.now(KST)


def kst_today() -> date:
    return now_kst().date()


def to_iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def format_inqry_datetime(value: datetime) -> str:
    """조회조건용 'YYYYMMDDHHMM' (KST). 문서상 형식이며 경계 포함 여부는 미검증이다."""
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다")
    return value.astimezone(KST).strftime("%Y%m%d%H%M")
