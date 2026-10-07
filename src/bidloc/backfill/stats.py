"""누적 데이터 기반 통계.

표본 평균이 아니라 bf_* 테이블에 누적된 수집 범위 전체 공고로 계산한다.
- 집합: 수집 범위(job) 안에서 후보 필터 목록에 나온 공고 중 면허제한에 목표 코드가 있는 공고(RELEVANT).
  필터 recall이 입증되지 않았으면 전체 모집단이 아니라 필터 기반 수집 집합이다.
  면허 해석 미확인(UNKNOWN)은 '검토 필요'로 따로 세고, 취소공고(CANCELLED)는 기회에서 제외해 따로 센다.
- 지역: 참가가능지역 행 이름 그대로 묶는다. 복수지역 공고는 각 지역에 한 번씩 들어가므로
  지역별 합계를 더하면 안 되고, 전국 고유 수는 별도 행으로 낸다.
- 참가업체수: 공고의 최초 개찰단위(가장 작은 차수·분류·재입찰 키)의 공식 prtcptCnum.
  실연동 관측상 낙찰하한선 미달·전자입찰취소신청 업체를 포함한 명부 전체 수다. 유효 투찰 수가 아니다.
- 금액: 공고 최신 차수의 추정가격(부가세·조달수수료 제외, 문서 기준). 확인된 값만 합산하고 결측 수를 함께 낸다.
- 종합점수·순위·낙찰확률은 만들지 않는다.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from bidloc.backfill.store import Job
from bidloc.timeutil import now_kst

NO_REGION_ROWS = "(참가가능지역 행 없음)"
REGION_NOT_COLLECTED = "(지역 미수집)"
NATIONAL_UNIQUE = "전국 고유 공고(지역 중복 제거)"


def percentile(sorted_values: list[int], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


@dataclass
class Group:
    relevant: int = 0
    review_needed: int = 0
    presmpt_known_sum: int = 0
    presmpt_known_n: int = 0
    presmpt_missing_n: int = 0
    opening_checked: int = 0
    opening_linked: int = 0
    counts: list[int] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        values = sorted(self.counts)
        n = len(values)
        return {
            "relevant_notices": self.relevant,
            "review_needed_notices": self.review_needed,
            "presmpt_prce_sum_known": self.presmpt_known_sum,
            "presmpt_prce_known_n": self.presmpt_known_n,
            "presmpt_prce_missing_n": self.presmpt_missing_n,
            "opening_checked_notices": self.opening_checked,
            "opening_linked_notices": self.opening_linked,
            "opening_link_rate": round(self.opening_linked / self.opening_checked, 4) if self.opening_checked else None,
            "prtcpt_cnum_n": n,
            "prtcpt_cnum_median": percentile(values, 0.5),
            "prtcpt_cnum_mean": round(sum(values) / n, 2) if n else None,
            "prtcpt_cnum_p25": percentile(values, 0.25),
            "prtcpt_cnum_p75": percentile(values, 0.75),
        }


def completeness(conn: sqlite3.Connection, job: Job) -> dict[str, Any]:
    parts = dict(conn.execute("SELECT status, COUNT(*) FROM bf_partition WHERE job_id = ? GROUP BY status", (job.job_id,)).fetchall())
    tasks: dict[str, dict[str, int]] = defaultdict(dict)
    for task_type, status, count in conn.execute(
            "SELECT task_type, status, COUNT(*) FROM bf_task WHERE job_id = ? GROUP BY task_type, status", (job.job_id,)):
        tasks[task_type][status] = count
    relevance = dict(conn.execute(
        """SELECT s.relevance, COUNT(*) FROM bf_notice_state s
           WHERE EXISTS (SELECT 1 FROM bf_task t WHERE t.job_id = ? AND t.bid_ntce_no = s.bid_ntce_no)
              OR s.relevance = 'CANCELLED'
           GROUP BY s.relevance""", (job.job_id,)).fetchall())
    job_row = conn.execute("SELECT status FROM bf_job WHERE job_id = ?", (job.job_id,)).fetchone()
    return {"job_status": job_row["status"] if job_row else None, "partitions": parts, "tasks": dict(tasks),
            "notice_relevance": relevance}


def compute_stats(conn: sqlite3.Connection, job: Job) -> dict[str, Any]:
    begin, end = job.range_begin.isoformat(), job.range_end.isoformat()
    notices = conn.execute(
        """SELECT r.bid_ntce_no, MIN(r.bid_ntce_dt) AS first_dt FROM bf_notice_revision r
           GROUP BY r.bid_ntce_no HAVING substr(MIN(r.bid_ntce_dt), 1, 10) BETWEEN ? AND ?""",
        (begin, end),
    ).fetchall()
    groups: dict[tuple[str, str], Group] = defaultdict(Group)
    excluded = defaultdict(int)
    for row in notices:
        no, year = row["bid_ntce_no"], (row["first_dt"] or "")[:4]
        state = conn.execute("SELECT * FROM bf_notice_state WHERE bid_ntce_no = ?", (no,)).fetchone()
        relevance = state["relevance"] if state else "PENDING"
        if relevance not in ("RELEVANT", "UNKNOWN"):
            excluded[relevance] += 1
            continue
        latest = conn.execute("SELECT * FROM bf_notice_revision WHERE bid_ntce_no = ? ORDER BY bid_ntce_ord DESC LIMIT 1",
                              (no,)).fetchone()
        region_task = conn.execute(
            """SELECT status FROM bf_task WHERE job_id = ? AND task_type = 'REGION' AND bid_ntce_no = ? AND bid_ntce_ord = ?""",
            (job.job_id, no, state["region_ord"] or latest["bid_ntce_ord"])).fetchone()
        if state["region_ord"] and region_task and region_task["status"] == "DONE":
            names = sorted({r[0] or "(이름 빈값)" for r in conn.execute(
                "SELECT prtcpt_psbl_rgn_nm FROM bf_allowed_region WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                (no, state["region_ord"]))}) or [NO_REGION_ROWS]
        else:
            names = [REGION_NOT_COLLECTED]
        opening_task = conn.execute(
            "SELECT status FROM bf_task WHERE job_id = ? AND task_type = 'OPENING' AND bid_ntce_no = ?", (job.job_id, no)).fetchone()
        first_unit = conn.execute(
            """SELECT prtcpt_cnum FROM bf_opening_unit WHERE bid_ntce_no = ?
               ORDER BY bid_ntce_ord, bid_clsfc_no, rbid_no LIMIT 1""", (no,)).fetchone()
        for region in names + [NATIONAL_UNIQUE]:
            for y in (year, "전체"):
                g = groups[(y, region)]
                if relevance == "UNKNOWN":
                    g.review_needed += 1
                    continue
                g.relevant += 1
                if latest["presmpt_prce"] is None:
                    g.presmpt_missing_n += 1
                else:
                    g.presmpt_known_n += 1
                    g.presmpt_known_sum += int(latest["presmpt_prce"])
                if opening_task and opening_task["status"] == "DONE":
                    g.opening_checked += 1
                    if first_unit is not None:
                        g.opening_linked += 1
                        if first_unit["prtcpt_cnum"] is not None:
                            g.counts.append(int(first_unit["prtcpt_cnum"]))
    rows = []
    for (year, region), g in sorted(groups.items(), key=lambda kv: (kv[0][0] != "전체", kv[0][0], kv[0][1] == NATIONAL_UNIQUE, kv[0][1])):
        rows.append({"year": year, "allowed_region": region, **g.row()})
    return {
        "job_id": job.job_id,
        "range_kst": [begin, end],
        "generated_at_kst": now_kst().isoformat(timespec="seconds"),
        "completeness": completeness(conn, job),
        "candidate_notices_in_range": len(notices),
        "excluded_notices": dict(excluded),
        "definitions": {
            "relevant_notices": "후보 필터 목록에 나온 공고 중 면허제한에 목표 면허코드가 있는 공고(취소공고 제외). 공고번호 단위. 필터 밖 공고는 포함되지 않으므로 recall 검증(candidate_filter) 결과와 함께 해석",
            "review_needed_notices": "면허제한 행 없음·해석 불가 등으로 관련성 미확인. 참가불가로 보지 않음",
            "allowed_region": "공고 최신 차수의 참가가능지역 행 이름. 복수지역 공고는 각 지역에 중복 포함 — 지역 행을 합산하지 말 것",
            "presmpt_prce": "추정가격(원, 문서상 부가세·조달수수료 제외). 기초금액·낙찰금액과 다름",
            "opening_link_rate": "개찰결과 조회를 마친 공고 중 개찰단위가 1개 이상 연결된 비율",
            "prtcpt_cnum": "최초 개찰단위의 공식 참가업체수. 낙찰하한선 미달·전자입찰취소신청 포함 명부 전체 수(실연동 표본 관측)",
            "not_provided": "종합점수·순위·낙찰확률은 산출하지 않음",
        },
        "rows": rows,
    }


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def write_stats(report_dir: Path, stats: dict[str, Any], job_name: str) -> tuple[Path, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%dT%H%M%S")
    json_path = report_dir / f"backfill-stats-{job_name}-{stamp}.json"
    csv_path = report_dir / f"backfill-stats-{job_name}-{stamp}.csv"
    json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = stats["rows"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _safe_cell(v) for k, v in row.items()})
    return json_path, csv_path
