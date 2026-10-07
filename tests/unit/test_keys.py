"""KEY-01: 같은 공고번호라도 차수·분류번호·재입찰번호가 다르면 분리하고 공고번호 단독으로 결합하지 않는다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bidloc.normalizers.keys import (
    NoticeRevisionKey,
    OpeningUnitKey,
    link_openings_to_notices,
    notice_key,
    opening_key,
)
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.runs import RunRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
NO = "R99BK99990001"


def test_same_notice_number_different_components_are_distinct():
    base = {"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNo": "000"}
    variants = [base, {**base, "bidNtceOrd": "001"}, {**base, "bidClsfcNo": "1"}, {**base, "rbidNo": "001"}]
    keys = {opening_key(v).key for v in variants}
    assert len(keys) == 4 and None not in keys


def test_key_strings_are_not_normalized():
    a = opening_key({"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNo": "000"}).key
    b = opening_key({"bidNtceNo": NO, "bidNtceOrd": "0", "bidClsfcNo": "0", "rbidNo": "000"}).key
    assert a != b


def test_missing_or_blank_key_fields_yield_no_key():
    ext = notice_key({"bidNtceNo": NO})
    assert ext.key is None and ext.missing_fields == ("bidNtceOrd",)
    ext = opening_key({"bidNtceNo": NO, "bidNtceOrd": "", "bidClsfcNo": "0", "rbidNo": "000"})
    assert ext.key is None and "bidNtceOrd" in ext.blank_fields
    ext = opening_key({"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0"})
    assert ext.key is None and "rbidNo" in ext.missing_fields


def test_rebid_field_name_variants():
    ext = opening_key({"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNtceNo": "001"})
    assert ext.key == OpeningUnitKey(NO, "000", "0", "001") and ext.rebid_field_used == "rbidNtceNo"
    conflict = opening_key({"bidNtceNo": NO, "bidNtceOrd": "000", "bidClsfcNo": "0", "rbidNo": "000", "rbidNtceNo": "001"})
    assert conflict.key is None


def test_linking_requires_order_match():
    notices = {NoticeRevisionKey(NO, "000"), NoticeRevisionKey("R99BK99990002", "000")}
    openings = [
        OpeningUnitKey(NO, "000", "0", "000"),     # 연결
        OpeningUnitKey(NO, "001", "0", "000"),     # 같은 번호, 다른 차수 → 연결 미확인
        OpeningUnitKey(NO, "0", "0", "001"),       # 숫자는 같고 형식이 다름 → 검토
        OpeningUnitKey("R99BK99990003", "000", "0", "000"),  # 다른 공고
    ]
    report = link_openings_to_notices(notices, openings)
    assert report.status_of(openings[0]) == "LINKED"
    assert report.status_of(openings[1]) == "SAME_NO_OTHER_ORD"
    assert report.status_of(openings[2]) == "FORMAT_MISMATCH"
    assert report.status_of(openings[3]) == "NOT_LINKED"
    assert all(n.bid_ntce_ord == "000" for _, n in report.linked)


@pytest.fixture
def conn(tmp_path: Path):
    connection = open_database(tmp_path / "k.sqlite3", default_migrations_dir(REPO_ROOT))
    RunRepository(connection).start(run_id="r1", command="test", data_mode="real", live=False, status="SKIPPED",
                                    max_calls_run=0, catalog_sha256=None)
    yield connection
    connection.close()


def _insert_unit(conn, ord_="000", clsfc="0", rbid="000"):
    conn.execute(
        "INSERT INTO verify_opening_unit (run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, "
        "official_prtcpt_cnum_status, roster_status, link_status) VALUES ('r1', ?, ?, ?, ?, 'NOT_QUERIED', 'NOT_QUERIED', 'NOT_LINKED')",
        (NO, ord_, clsfc, rbid),
    )


def test_db_composite_unique_constraint(conn):
    _insert_unit(conn)
    _insert_unit(conn, rbid="001")
    _insert_unit(conn, clsfc="1")
    _insert_unit(conn, ord_="001")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_unit(conn)
    assert conn.execute("SELECT COUNT(*) FROM verify_opening_unit").fetchone()[0] == 4


def test_db_notice_revision_unique_per_order(conn):
    conn.execute("INSERT INTO verify_notice_revision (run_id, bid_ntce_no, bid_ntce_ord) VALUES ('r1', ?, '000')", (NO,))
    conn.execute("INSERT INTO verify_notice_revision (run_id, bid_ntce_no, bid_ntce_ord) VALUES ('r1', ?, '001')", (NO,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO verify_notice_revision (run_id, bid_ntce_no, bid_ntce_ord) VALUES ('r1', ?, '000')", (NO,))
