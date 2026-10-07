"""API-08: 3페이지·반복 페이지·절단. 첫 페이지만 받은 결과를 완전하다고 하지 않는다."""

from __future__ import annotations

from bidloc.clients.errors import Outcome
from bidloc.clients.http import ApiResult
from bidloc.collectors.pagination import collect_all_pages


class FakeApi:
    def __init__(self, pages: dict[int, ApiResult]):
        self.pages = pages
        self.calls: list[dict[str, str]] = []

    def call(self, service_id, operation, params, *, response_type="json"):
        self.calls.append(dict(params))
        page = int(params["pageNo"])
        result = self.pages[page]
        return result


def page(items, total, outcome=Outcome.SUCCESS, page_no=None):
    if outcome == Outcome.SUCCESS and not items:
        outcome = Outcome.SUCCESS_EMPTY
    return ApiResult("svc", "op", outcome, "test", items=items, total_count=total, page_no=page_no, attempts=1,
                     envelope_shape="json:response:items.item=list")


def rows(start, count):
    return [{"bidNtceNo": f"R99BK{n:08d}", "bidNtceOrd": "000"} for n in range(start, start + count)]


def test_three_pages_complete():
    api = FakeApi({1: page(rows(0, 10), 25, page_no=1), 2: page(rows(10, 10), 25, page_no=2), 3: page(rows(20, 5), 25, page_no=3)})
    col = collect_all_pages(api, "svc", "op", {"inqryDiv": "1"}, num_of_rows=10, max_pages=10)
    assert col.complete and len(col.items) == 25 and col.pages_fetched == 3 and col.expected_pages == 3
    assert [c["pageNo"] for c in api.calls] == ["1", "2", "3"]
    assert all(c["numOfRows"] == "10" for c in api.calls)


def test_first_page_only_is_not_complete():
    api = FakeApi({1: page(rows(0, 10), 25)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=1)
    assert not col.complete and "TRUNCATED_MAX_PAGES" in col.issue_codes() and len(col.items) == 10


def test_repeated_page_detected():
    same = rows(0, 10)
    api = FakeApi({1: page(same, 30), 2: page(list(same), 30), 3: page(rows(20, 10), 30)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=10)
    assert not col.complete and "REPEATED_PAGE" in col.issue_codes()


def test_total_count_changed_and_count_mismatch():
    api = FakeApi({1: page(rows(0, 10), 20), 2: page(rows(10, 9), 21)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=10)
    assert not col.complete
    assert "TOTAL_COUNT_CHANGED" in col.issue_codes() and "COUNT_MISMATCH" in col.issue_codes()


def test_page_failure_midway_is_incomplete():
    api = FakeApi({1: page(rows(0, 10), 20), 2: ApiResult("svc", "op", Outcome.UPSTREAM_ERROR, "HTTP 500", attempts=3)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=10)
    assert not col.complete and not col.query_ok and "PAGE_FAILED" in col.issue_codes()
    assert col.attempts == 4


def test_confirmed_empty_variants():
    col = collect_all_pages(FakeApi({1: page([], 0)}), "svc", "op", {}, num_of_rows=10, max_pages=3)
    assert col.complete and col.confirmed_empty and col.items == []
    col = collect_all_pages(FakeApi({1: page([], None, outcome=Outcome.NO_DATA)}), "svc", "op", {}, num_of_rows=10, max_pages=3)
    assert col.complete and col.confirmed_empty


def test_missing_total_count_is_not_complete_even_if_short_page():
    col = collect_all_pages(FakeApi({1: page(rows(0, 3), None)}), "svc", "op", {}, num_of_rows=10, max_pages=3)
    assert not col.complete and "TOTAL_COUNT_MISSING" in col.issue_codes() and len(col.items) == 3


def test_short_middle_page_and_empty_page_before_end():
    api = FakeApi({1: page(rows(0, 10), 30), 2: page(rows(10, 5), 30), 3: page([], 30)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=10)
    assert not col.complete
    assert "SHORT_PAGE" in col.issue_codes() and "EMPTY_PAGE_BEFORE_END" in col.issue_codes()


def test_page_no_mismatch_flagged():
    api = FakeApi({1: page(rows(0, 10), 20, page_no=1), 2: page(rows(10, 10), 20, page_no=1)})
    col = collect_all_pages(api, "svc", "op", {}, num_of_rows=10, max_pages=10)
    assert "PAGE_NO_MISMATCH" in col.issue_codes() and not col.complete


def test_duplicate_keys_reported_but_not_blocking():
    dup = rows(0, 2) + rows(0, 1)
    col = collect_all_pages(FakeApi({1: page(dup, 3)}), "svc", "op", {}, num_of_rows=10, max_pages=2,
                            key_fn=lambda i: (i["bidNtceNo"], i["bidNtceOrd"]))
    assert "DUPLICATE_KEYS" in col.issue_codes() and col.complete
