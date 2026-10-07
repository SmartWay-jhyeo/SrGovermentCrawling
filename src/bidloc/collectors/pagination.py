"""페이지 수집과 완전성 검사.

첫 페이지만 받은 결과를 완전하다고 표시하지 않는다.
완전(complete=True) 조건:
- 모든 페이지 호출이 데이터 정상(SUCCESS/SUCCESS_EMPTY/NO_DATA)이고
- totalCount가 확인되며 페이지 동안 변하지 않고
- 반복 페이지·페이지 번호 불일치·짧은 중간 페이지가 없고
- 수집 행 수가 totalCount와 같다.
또는 첫 페이지에서 결과 없음이 확인(confirmed_empty)된 경우.
totalCount가 목록 행 수·개찰단위 수·투찰 행 수 중 무엇을 세는지는 오퍼레이션별로 실응답 검증이 필요하다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable, Hashable, Mapping, Protocol

from bidloc.clients.envelope import ItemDict
from bidloc.clients.errors import DATA_OK, Outcome


class _CallsApi(Protocol):
    def call(self, service_id: str, operation: str, params: Mapping[str, str], *,
             response_type: str | None = "json"): ...


@dataclass(frozen=True)
class PageIssue:
    code: str
    detail: str


@dataclass
class PageCollection:
    service_id: str
    operation: str
    items: list[ItemDict] = field(default_factory=list)
    total_count: int | None = None
    pages_fetched: int = 0
    expected_pages: int | None = None
    complete: bool = False
    confirmed_empty: bool = False
    final_outcome: Outcome | None = None
    issues: list[PageIssue] = field(default_factory=list)
    attempts: int = 0
    source_response_ids: list[int] = field(default_factory=list)
    envelope_shapes: list[str] = field(default_factory=list)

    @property
    def query_ok(self) -> bool:
        return self.final_outcome in DATA_OK

    def issue_codes(self) -> list[str]:
        return [i.code for i in self.issues]


def _signature(items: list[ItemDict]) -> tuple[str, ...]:
    return tuple(sorted(json.dumps(i, ensure_ascii=False, sort_keys=True) for i in items))


def collect_all_pages(
    client: _CallsApi,
    service_id: str,
    operation: str,
    params: Mapping[str, str],
    *,
    num_of_rows: int,
    max_pages: int,
    key_fn: Callable[[ItemDict], Hashable | None] | None = None,
) -> PageCollection:
    if num_of_rows < 1 or max_pages < 1:
        raise ValueError("num_of_rows와 max_pages는 1 이상")
    col = PageCollection(service_id=service_id, operation=operation)
    previous_signature: tuple[str, ...] | None = None
    page = 1
    while True:
        if page > max_pages:
            col.issues.append(PageIssue("TRUNCATED_MAX_PAGES", f"max_pages={max_pages}에서 중단"))
            break
        request = dict(params)
        request["pageNo"] = str(page)
        request["numOfRows"] = str(num_of_rows)
        result = client.call(service_id, operation, request)
        col.attempts += result.attempts
        col.source_response_ids.extend(result.source_response_ids)
        col.final_outcome = result.outcome
        if result.envelope_shape:
            col.envelope_shapes.append(result.envelope_shape)
        if result.outcome not in DATA_OK:
            col.issues.append(PageIssue("PAGE_FAILED", f"page {page}: {result.outcome.value} ({result.basis})"))
            break
        col.pages_fetched = page
        page_items = list(result.items or [])

        if page == 1:
            if result.outcome == Outcome.NO_DATA and not page_items:
                col.confirmed_empty = True
                col.total_count = result.total_count
                break
            if result.total_count is None:
                col.issues.append(PageIssue("TOTAL_COUNT_MISSING", "totalCount가 없거나 정수가 아니다"))
            else:
                col.total_count = result.total_count
                col.expected_pages = math.ceil(result.total_count / num_of_rows) if result.total_count else 0
                if result.total_count == 0 and not page_items:
                    col.confirmed_empty = True
                    break
        else:
            if result.total_count != col.total_count:
                col.issues.append(PageIssue("TOTAL_COUNT_CHANGED", f"page {page}: {col.total_count} -> {result.total_count}"))
        if result.page_no is not None and result.page_no != page:
            col.issues.append(PageIssue("PAGE_NO_MISMATCH", f"요청 {page}, 응답 {result.page_no}"))
        if len(page_items) > num_of_rows:
            col.issues.append(PageIssue("PAGE_OVERSIZE", f"page {page}: {len(page_items)} > {num_of_rows}"))

        signature = _signature(page_items)
        if page_items and signature == previous_signature:
            col.issues.append(PageIssue("REPEATED_PAGE", f"page {page}가 이전 페이지와 동일"))
            break
        previous_signature = signature

        if not page_items:
            if col.expected_pages is not None and page <= col.expected_pages:
                col.issues.append(PageIssue("EMPTY_PAGE_BEFORE_END", f"page {page}/{col.expected_pages}"))
            break
        col.items.extend(page_items)

        if col.expected_pages is not None:
            if page < col.expected_pages and len(page_items) != num_of_rows:
                col.issues.append(PageIssue("SHORT_PAGE", f"page {page}: {len(page_items)} < {num_of_rows}"))
            if page >= col.expected_pages:
                break
        elif len(page_items) < num_of_rows:
            break
        page += 1

    if col.total_count is not None and not col.confirmed_empty and len(col.items) != col.total_count:
        if not any(i.code in {"PAGE_FAILED", "TRUNCATED_MAX_PAGES"} for i in col.issues):
            col.issues.append(PageIssue("COUNT_MISMATCH", f"수집 {len(col.items)}행 != totalCount {col.total_count}"))
    if key_fn is not None and col.items:
        seen: set[Hashable] = set()
        dup = 0
        for item in col.items:
            key = key_fn(item)
            if key is None:
                continue
            if key in seen:
                dup += 1
            seen.add(key)
        if dup:
            col.issues.append(PageIssue("DUPLICATE_KEYS", f"키 중복 {dup}건"))
    blocking = [i for i in col.issues if i.code != "DUPLICATE_KEYS"]
    col.complete = col.query_ok and not blocking and (col.confirmed_empty or col.total_count is not None)
    return col
