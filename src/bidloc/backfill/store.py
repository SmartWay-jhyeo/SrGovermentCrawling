"""백필 저장소: job, 기간 파티션 커서, 작업 큐, 누적 정규화 레코드."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from bidloc.backfill.plan import BackfillConfig
from bidloc.clients.envelope import ItemDict
from bidloc.collectors.windows import split_windows
from bidloc.normalizers.values import ValueStatus, parse_count, parse_krw_amount, split_name_code
from bidloc.timeutil import KST, now_utc, to_iso_utc

_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
TASK_TYPES = ("LICENSE", "REGION", "OPENING", "AWARD", "ROSTER")


def _now() -> str:
    return to_iso_utc(now_utc())


def _item_json(item: ItemDict) -> tuple[str, str]:
    text = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _s(item: ItemDict, name: str) -> str | None:
    value = item.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


@dataclass(frozen=True)
class Job:
    job_id: str
    job_name: str
    range_begin: date
    range_end: date
    status: str


class BackfillStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---------------------------------------------------------------- job / partition

    def find_active_job(self, job_name: str) -> Job | None:
        row = self.conn.execute(
            "SELECT * FROM bf_job WHERE job_name = ? ORDER BY created_at_utc DESC LIMIT 1", (job_name,)
        ).fetchone()
        if row is None:
            return None
        return Job(row["job_id"], row["job_name"], date.fromisoformat(row["range_begin_kst"]),
                   date.fromisoformat(row["range_end_kst"]), row["status"])

    def create_job(self, cfg: BackfillConfig, begin: date, end: date) -> Job:
        job_id = f"{cfg.job_name}:{begin.isoformat()}:{end.isoformat()}"
        with transaction(self.conn):
            self.conn.execute(
                """INSERT OR IGNORE INTO bf_job (job_id, job_name, range_begin_kst, range_end_kst, config_json,
                   config_sha256, status, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)""",
                (job_id, cfg.job_name, begin.isoformat(), end.isoformat(),
                 json.dumps(cfg.raw, ensure_ascii=False, sort_keys=True), cfg.sha256(), _now(), _now()),
            )
            windows = split_windows(
                datetime(begin.year, begin.month, begin.day, 0, 0, tzinfo=KST),
                datetime(end.year, end.month, end.day, 23, 59, tzinfo=KST),
                max_span=timedelta(days=cfg.window_days), overlap=timedelta(minutes=cfg.overlap_minutes),
            )
            for w in windows:
                self.conn.execute(
                    """INSERT OR IGNORE INTO bf_partition (job_id, window_begin, window_end, status, updated_at_utc)
                       VALUES (?, ?, ?, 'PENDING', ?)""",
                    (job_id, w.inqry_bgn_dt, w.inqry_end_dt, _now()),
                )
        job = self.find_active_job(cfg.job_name)
        assert job is not None
        return job

    def next_partition(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM bf_partition WHERE job_id = ? AND status IN ('IN_PROGRESS', 'PENDING')
               ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 0 ELSE 1 END, window_begin LIMIT 1""",
            (job_id,),
        ).fetchone()

    def update_partition(self, job_id: str, window_begin: str, **fields: Any) -> None:
        fields["updated_at_utc"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE bf_partition SET {cols} WHERE job_id = ? AND window_begin = ?",
                          (*fields.values(), job_id, window_begin))

    # ---------------------------------------------------------------- task queue

    def enqueue(self, job_id: str, task_type: str, no: str, ord_: str = "", clsfc: str = "", rbid: str = "", *,
                status: str = "PENDING", not_before: str | None = None, reason: str | None = None) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO bf_task (job_id, task_type, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, status,
               not_before_kst, reason, created_at_utc, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, task_type, no, ord_, clsfc, rbid, status, not_before, reason, _now(), _now()),
        )
        return cur.rowcount > 0

    def next_task(self, job_id: str, task_type: str, today_kst: str, max_attempts: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM bf_task WHERE job_id = ? AND task_type = ? AND (
                   status = 'PENDING'
                   OR (status = 'DEFERRED' AND (not_before_kst IS NULL OR not_before_kst <= ?))
                   OR (status = 'FAILED' AND attempts < ?))
               ORDER BY CASE status WHEN 'PENDING' THEN 0 WHEN 'DEFERRED' THEN 1 ELSE 2 END, bid_ntce_no, bid_ntce_ord,
                        bid_clsfc_no, rbid_no
               LIMIT 1""",
            (job_id, task_type, today_kst, max_attempts),
        ).fetchone()

    def update_task(self, task: sqlite3.Row, **fields: Any) -> None:
        fields["updated_at_utc"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"""UPDATE bf_task SET {cols} WHERE job_id = ? AND task_type = ? AND bid_ntce_no = ? AND bid_ntce_ord = ?
                AND bid_clsfc_no = ? AND rbid_no = ?""",
            (*fields.values(), task["job_id"], task["task_type"], task["bid_ntce_no"], task["bid_ntce_ord"],
             task["bid_clsfc_no"], task["rbid_no"]),
        )

    def skip_open_tasks(self, job_id: str, no: str, task_types: tuple[str, ...], reason: str) -> None:
        marks = ",".join("?" for _ in task_types)
        self.conn.execute(
            f"""UPDATE bf_task SET status = 'SKIPPED', reason = ?, updated_at_utc = ?
                WHERE job_id = ? AND bid_ntce_no = ? AND task_type IN ({marks}) AND status IN ('PENDING', 'DEFERRED', 'FAILED')""",
            (reason, _now(), job_id, no, *task_types),
        )

    # ---------------------------------------------------------------- records

    def _conflict(self, table: str, key: str, old_sha: str, new_sha: str, old_json: str, response_id: int | None) -> None:
        self.conn.execute(
            """INSERT INTO bf_record_conflict (table_name, record_key, old_sha256, new_sha256, old_item_json, response_id,
               detected_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (table, key, old_sha, new_sha, old_json, response_id, _now()),
        )

    def upsert_notice(self, item: ItemDict, response_id: int | None) -> str | None:
        no, ord_ = _s(item, "bidNtceNo"), _s(item, "bidNtceOrd")
        if not no or not ord_:
            return None
        text, sha = _item_json(item)
        amounts = {n: parse_krw_amount(item, n) for n in ("bdgtAmt", "presmptPrce", "VAT")}
        # 저장 못 하는 값은 0으로 바꾸지 않고 NULL + 표시로 둔다(원본은 item_json에 그대로 있다).
        bad = [f"{n}={a.raw[:24]}" for n, a in amounts.items() if a.status is ValueStatus.OUT_OF_RANGE]
        quality = "AMOUNT_OUT_OF_RANGE:" + ",".join(bad) if bad else None
        old = self.conn.execute("SELECT item_sha256, item_json FROM bf_notice_revision WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                                (no, ord_)).fetchone()
        values = (
            _s(item, "ntceKindNm"), _s(item, "reNtceYn"), _s(item, "befBidBbancNo"), _s(item, "bidNtceNm"),
            _s(item, "bidNtceDt"), _s(item, "rgstDt"), _s(item, "opengDt"), _s(item, "cntrctCnclsMthdNm"),
            _s(item, "sucsfbidMthdNm"), _s(item, "ntceInsttCd"), _s(item, "ntceInsttNm"), _s(item, "dminsttCd"),
            _s(item, "dminsttNm"), _s(item, "cnstrtsiteRgnNm"), _s(item, "mainCnsttyNm"), _s(item, "indstrytyLmtYn"),
            amounts["bdgtAmt"].value, amounts["presmptPrce"].value, amounts["VAT"].value, text, sha, quality,
        )
        if old is None:
            self.conn.execute(
                """INSERT INTO bf_notice_revision (ntce_kind_nm, re_ntce_yn, bef_bid_ntce_no, bid_ntce_nm, bid_ntce_dt, rgst_dt,
                   openg_dt, cntrct_cncls_mthd_nm, sucsfbid_mthd_nm, ntce_instt_cd, ntce_instt_nm, dminstt_cd, dminstt_nm,
                   cnstrtsite_rgn_nm, main_cnstty_nm, indstryty_lmt_yn, bdgt_amt, presmpt_prce, vat, item_json, item_sha256,
                   quality_flag, bid_ntce_no, bid_ntce_ord, first_response_id, last_response_id, first_seen_utc, last_seen_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, no, ord_, response_id, response_id, _now(), _now()),
            )
        else:
            if old["item_sha256"] != sha:
                self._conflict("bf_notice_revision", f"{no}|{ord_}", old["item_sha256"], sha, old["item_json"], response_id)
            self.conn.execute(
                """UPDATE bf_notice_revision SET ntce_kind_nm = ?, re_ntce_yn = ?, bef_bid_ntce_no = ?, bid_ntce_nm = ?,
                   bid_ntce_dt = ?, rgst_dt = ?, openg_dt = ?, cntrct_cncls_mthd_nm = ?, sucsfbid_mthd_nm = ?, ntce_instt_cd = ?,
                   ntce_instt_nm = ?, dminstt_cd = ?, dminstt_nm = ?, cnstrtsite_rgn_nm = ?, main_cnstty_nm = ?,
                   indstryty_lmt_yn = ?, bdgt_amt = ?, presmpt_prce = ?, vat = ?, item_json = ?, item_sha256 = ?,
                   quality_flag = ?, last_response_id = ?, last_seen_utc = ? WHERE bid_ntce_no = ? AND bid_ntce_ord = ?""",
                (*values, response_id, _now(), no, ord_),
            )
        return no

    def notice_revisions(self, no: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM bf_notice_revision WHERE bid_ntce_no = ? ORDER BY bid_ntce_ord", (no,)))

    def set_state(self, no: str, **fields: Any) -> None:
        row = self.conn.execute("SELECT bid_ntce_no FROM bf_notice_state WHERE bid_ntce_no = ?", (no,)).fetchone()
        fields["updated_at_utc"] = _now()
        if row is None:
            fields.setdefault("relevance", "PENDING")
            cols = ", ".join(fields)
            self.conn.execute(f"INSERT INTO bf_notice_state (bid_ntce_no, {cols}) VALUES (?, {', '.join('?' for _ in fields)})",
                              (no, *fields.values()))
        else:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(f"UPDATE bf_notice_state SET {cols} WHERE bid_ntce_no = ?", (*fields.values(), no))

    def state(self, no: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM bf_notice_state WHERE bid_ntce_no = ?", (no,)).fetchone()

    def replace_license_rows(self, no: str, ord_: str, items: list[ItemDict], response_id: int | None) -> list[dict[str, Any]]:
        self.conn.execute("DELETE FROM bf_license_limit WHERE bid_ntce_no = ? AND bid_ntce_ord = ?", (no, ord_))
        parsed = []
        for index, item in enumerate(items):
            text, _ = _item_json(item)
            name, code, _ = split_name_code(item.get("lcnsLmtNm"))
            rgst = _s(item, "rgstDt")
            mfrc = _s(item, "indstrytyMfrcFldList")
            permsn = _s(item, "permsnIndstrytyList")
            flags = []
            if rgst and not _DT_RE.match(rgst):
                flags.append("RGST_DT_NOT_DATETIME")
            if mfrc and not mfrc.startswith("["):
                flags.append("MFRC_LIST_NOT_BRACKET")
            if permsn and not permsn.startswith("["):
                flags.append("PERMSN_LIST_NOT_BRACKET")
            quality = ("FIELD_SHIFT_SUSPECTED:" + ",".join(flags)) if flags else None
            grp = _s(item, "lmtGrpNo") or f"#{index}"
            sno = _s(item, "lmtSno") or f"#{index}"
            self.conn.execute(
                """INSERT OR REPLACE INTO bf_license_limit (bid_ntce_no, bid_ntce_ord, lmt_grp_no, lmt_sno, lcns_lmt_nm,
                   license_code, permsn_indstryty_list, indstryty_mfrc_fld_list, rgst_dt, bsns_div_nm, quality_flag, item_json,
                   response_id, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (no, ord_, grp, sno, _s(item, "lcnsLmtNm"), code, permsn, mfrc, rgst, _s(item, "bsnsDivNm"), quality, text,
                 response_id, _now()),
            )
            parsed.append({"code": code, "name": name, "permsn": permsn or "", "quality": quality})
        return parsed

    def _license_fields(self, item: ItemDict, index: int) -> dict[str, Any]:
        name, code, _ = split_name_code(item.get("lcnsLmtNm"))
        rgst, mfrc, permsn = _s(item, "rgstDt"), _s(item, "indstrytyMfrcFldList"), _s(item, "permsnIndstrytyList")
        flags = []
        if rgst and not _DT_RE.match(rgst):
            flags.append("RGST_DT_NOT_DATETIME")
        if mfrc and not mfrc.startswith("["):
            flags.append("MFRC_LIST_NOT_BRACKET")
        if permsn and not permsn.startswith("["):
            flags.append("PERMSN_LIST_NOT_BRACKET")
        return {"name": name, "code": code, "permsn": permsn, "mfrc": mfrc, "rgst": rgst,
                "quality": ("FIELD_SHIFT_SUSPECTED:" + ",".join(flags)) if flags else None,
                "grp": _s(item, "lmtGrpNo") or f"#{index}", "sno": _s(item, "lmtSno") or f"#{index}"}

    def insert_license_row(self, item: ItemDict, response_id: int | None, index: int = 0) -> tuple[str, str, str | None, str] | None:
        """기간 스윕용: 공고 단위 삭제 없이 행 하나만 저장한다.

        같은 공고의 행이 여러 페이지에 나뉘어 올 수 있어 replace 방식을 쓰면 안 된다.
        반환: (공고번호, 차수, 면허코드, 허용업종목록)
        """
        no, ord_ = _s(item, "bidNtceNo"), _s(item, "bidNtceOrd")
        if not no or not ord_:
            return None
        f = self._license_fields(item, index)
        text, _ = _item_json(item)
        self.conn.execute(
            """INSERT OR REPLACE INTO bf_license_limit (bid_ntce_no, bid_ntce_ord, lmt_grp_no, lmt_sno, lcns_lmt_nm,
               license_code, permsn_indstryty_list, indstryty_mfrc_fld_list, rgst_dt, bsns_div_nm, quality_flag, item_json,
               response_id, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (no, ord_, f["grp"], f["sno"], _s(item, "lcnsLmtNm"), f["code"], f["permsn"], f["mfrc"], f["rgst"],
             _s(item, "bsnsDivNm"), f["quality"], text, response_id, _now()))
        return no, ord_, f["code"], f["permsn"] or ""

    def insert_region_row(self, item: ItemDict, response_id: int | None, index: int = 0) -> tuple[str, str] | None:
        """기간 스윕용: 참가가능지역 행 하나만 저장한다."""
        no, ord_ = _s(item, "bidNtceNo"), _s(item, "bidNtceOrd")
        if not no or not ord_:
            return None
        text, _ = _item_json(item)
        self.conn.execute(
            """INSERT OR REPLACE INTO bf_allowed_region (bid_ntce_no, bid_ntce_ord, lmt_sno, prtcpt_psbl_rgn_nm, item_json,
               response_id, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (no, ord_, _s(item, "lmtSno") or f"#{index}", _s(item, "prtcptPsblRgnNm"), text, response_id, _now()))
        return no, ord_

    def replace_region_rows(self, no: str, ord_: str, items: list[ItemDict], response_id: int | None) -> int:
        self.conn.execute("DELETE FROM bf_allowed_region WHERE bid_ntce_no = ? AND bid_ntce_ord = ?", (no, ord_))
        for index, item in enumerate(items):
            text, _ = _item_json(item)
            self.conn.execute(
                """INSERT OR REPLACE INTO bf_allowed_region (bid_ntce_no, bid_ntce_ord, lmt_sno, prtcpt_psbl_rgn_nm, item_json,
                   response_id, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (no, ord_, _s(item, "lmtSno") or f"#{index}", _s(item, "prtcptPsblRgnNm"), text, response_id, _now()),
            )
        return len(items)

    def upsert_opening(self, item: ItemDict, response_id: int | None) -> tuple[str, str, str, str] | None:
        key = tuple(_s(item, k) for k in ("bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"))
        if any(k is None for k in key):
            return None
        text, sha = _item_json(item)
        old = self.conn.execute(
            "SELECT item_json FROM bf_opening_unit WHERE bid_ntce_no = ? AND bid_ntce_ord = ? AND bid_clsfc_no = ? AND rbid_no = ?",
            key).fetchone()
        if old is not None:
            old_sha = hashlib.sha256(old["item_json"].encode("utf-8")).hexdigest()
            if old_sha != sha:
                self._conflict("bf_opening_unit", "|".join(key), old_sha, sha, old["item_json"], response_id)
        count = parse_count(item, "prtcptCnum")
        self.conn.execute(
            """INSERT OR REPLACE INTO bf_opening_unit (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, progrs_div_cd_nm, openg_dt,
               prtcpt_cnum, prtcpt_cnum_raw, openg_corp_info, item_json, response_id, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*key, _s(item, "progrsDivCdNm"), _s(item, "opengDt"), count.value, count.raw, _s(item, "opengCorpInfo"), text,
             response_id, _now()),
        )
        return key  # type: ignore[return-value]

    def upsert_award(self, item: ItemDict, response_id: int | None) -> bool:
        key = tuple(_s(item, k) for k in ("bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo"))
        if any(k is None for k in key):
            return False
        text, _ = _item_json(item)
        self.conn.execute(
            """INSERT OR REPLACE INTO bf_award (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, bidwinnr_bizno, sucsfbid_amt,
               sucsfbid_rate, rl_openg_dt, fnl_sucsf_date, prtcpt_cnum, item_json, response_id, updated_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*key, _s(item, "bidwinnrBizno"), parse_krw_amount(item, "sucsfbidAmt").value, _s(item, "sucsfbidRate"),
             _s(item, "rlOpengDt"), _s(item, "fnlSucsfDate"), parse_count(item, "prtcptCnum").value, text, response_id, _now()),
        )
        return True

    def replace_roster(self, key: tuple[str, str, str, str], items: list[ItemDict], response_id: int | None) -> None:
        self.conn.execute(
            "DELETE FROM bf_roster_row WHERE bid_ntce_no = ? AND bid_ntce_ord = ? AND bid_clsfc_no = ? AND rbid_no = ?", key)
        for index, item in enumerate(items):
            text, _ = _item_json(item)
            bizno = _s(item, "prcbdrBizno")
            self.conn.execute(
                """INSERT OR REPLACE INTO bf_roster_row (bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, row_key, prcbdr_bizno,
                   openg_rank, bidprc_amt, rmrk, item_json, response_id, updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*key, bizno or f"#{index}", bizno, _s(item, "opengRank"), parse_krw_amount(item, "bidprcAmt").value,
                 _s(item, "rmrk"), text, response_id, _now()),
            )
