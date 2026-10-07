"""후보 필터 recall 측정.

설계
- 수집 범위를 연도(층)로 나누고, 층마다 고정 시드로 무작위 시간창(기본 120분)을 뽑는다.
- 각 시간창에서 같은 오퍼레이션(getBidPblancListInfoCnstwkPPSSrch, inqryDiv=1 공고게시일시)으로
  ALL(업종 필터 없음)과 설정한 필터들(예: indstrytyNm=도장, indstrytyCd=4992)을 모두 전체 페이지까지 받는다.
- ALL에 나온 모든 공고의 면허제한을 조회해 목표 면허코드 보유 여부(정답)를 만든다.
- recall(필터) = 정답 양성 공고 중 같은 시간창의 필터 결과에 나온 공고 비율(공고번호 기준), Wilson 95% 구간.
- 받은 면허제한 결과는 bf_license_limit·bf_license_fetch에 저장해 본 백필에서 재사용한다.
제한
- 시간창 표본이므로 결과는 표본 추정치다. 층(연도)별 표본 수가 작으면 층별 추정은 불안정하다.
- 정답은 공고 최신 차수(ALL에서 본 차수)의 면허제한 기준이다.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from bidloc.backfill.store import BackfillStore, transaction
from bidloc.clients.budget import OperationBudget
from bidloc.clients.errors import DATA_OK, SERVICE_FATAL, Outcome
from bidloc.collectors.pagination import collect_all_pages
from bidloc.redaction import redact
from bidloc.timeutil import KST, format_inqry_datetime, now_kst, now_utc, to_iso_utc

LICENSE_SVC, LICENSE_OP = "bid_notice", "getBidPblancListInfoLicenseLimit"


def _now() -> str:
    return to_iso_utc(now_utc())


@dataclass(frozen=True)
class StudyConfig:
    raw: dict[str, Any]
    name: str
    seed: int
    begin: date
    end: date
    window_minutes: int
    windows_per_stratum: int
    list_service: str
    list_operation: str
    base_params: dict[str, str]
    num_of_rows: int
    filters: dict[str, dict[str, str]]
    target_codes: tuple[str, ...]
    target_min_positives: int
    max_attempts: int

    @property
    def study_id(self) -> str:
        return f"{self.name}:{self.seed}:{self.begin}:{self.end}:{self.window_minutes}x{self.windows_per_stratum}"


def load_study_config(path: Path) -> StudyConfig:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict) or d.get("schema_version") != 1:
        raise ValueError("recall_study.yaml schema_version 1 필요")
    filters = {str(k): {str(a): str(b) for a, b in v.items()} for k, v in (d.get("filters") or {}).items()}
    if not filters or "ALL" in filters:
        raise ValueError("filters는 1개 이상, 이름 ALL은 예약어")
    lst = d["list"]
    return StudyConfig(
        raw=d, name=str(d["study_name"]), seed=int(d["seed"]),
        begin=date.fromisoformat(str(d["range"]["from"])), end=date.fromisoformat(str(d["range"]["to"])),
        window_minutes=int(d.get("window_minutes", 120)), windows_per_stratum=int(d.get("windows_per_stratum", 9)),
        list_service=str(lst["service"]), list_operation=str(lst["operation"]),
        base_params={str(k): str(v) for k, v in (lst.get("base_params") or {}).items()},
        num_of_rows=int(lst.get("num_of_rows", 100)), filters=filters,
        target_codes=tuple(str(c) for c in d["target_license_codes"]),
        target_min_positives=int(d.get("target_min_positives", 100)), max_attempts=int(d.get("max_attempts", 3)),
    )


def sample_windows(cfg: StudyConfig) -> list[tuple[str, str, str]]:
    """(stratum, window_begin, window_end) — 연도 층마다 균등 무작위 시작 시각. 시드 고정으로 재현 가능."""
    rng = random.Random(cfg.seed)
    out = []
    for year in range(cfg.begin.year, cfg.end.year + 1):
        s = max(cfg.begin, date(year, 1, 1))
        e = min(cfg.end, date(year, 12, 31))
        if e < s:
            continue
        start = datetime(s.year, s.month, s.day, tzinfo=KST)
        stop = datetime(e.year, e.month, e.day, 23, 59, tzinfo=KST) - timedelta(minutes=cfg.window_minutes - 1)
        span = int((stop - start).total_seconds() // 60)
        if span <= 0:
            continue
        for _ in range(cfg.windows_per_stratum):
            begin = start + timedelta(minutes=rng.randrange(span + 1))
            end = begin + timedelta(minutes=cfg.window_minutes - 1)
            out.append((str(year), format_inqry_datetime(begin), format_inqry_datetime(end)))
    return out


def ensure_study(conn, cfg: StudyConfig) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT OR IGNORE INTO rc_study (study_id, seed, range_begin_kst, range_end_kst, window_minutes, config_json,
               created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cfg.study_id, cfg.seed, cfg.begin.isoformat(), cfg.end.isoformat(), cfg.window_minutes,
             json.dumps(cfg.raw, ensure_ascii=False, sort_keys=True), _now()))
        for stratum, wb, we in sample_windows(cfg):
            for kind in ["ALL", *cfg.filters]:
                conn.execute(
                    """INSERT OR IGNORE INTO rc_window_query (study_id, window_begin, window_end, stratum, query_kind, status,
                       updated_at_utc) VALUES (?, ?, ?, ?, ?, 'PENDING', ?)""",
                    (cfg.study_id, wb, we, stratum, kind, _now()))


class RecallRunner:
    def __init__(self, *, client: Any, budget: OperationBudget, conn, cfg: StudyConfig) -> None:
        self.client, self.budget, self.conn, self.cfg = client, budget, conn, cfg
        self.store = BackfillStore(conn)
        self.stop_reason: str | None = None
        self.list_pages = 0
        self.truth_called = 0
        self.truth_reused = 0

    def _fatal(self, outcome: Outcome | None, svc: str, op: str) -> bool:
        if outcome in (Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY):
            self.stop_reason = f"{svc} 일일 예산 또는 실행 상한 도달"
            return True
        if outcome == Outcome.QUOTA_DAILY_EXCEEDED:
            self.budget.mark_quota_exhausted(svc, op)
            self.stop_reason = f"{svc} 제공기관 일일 한도(22)"
            return True
        if outcome in SERVICE_FATAL or outcome == Outcome.IP_NOT_ALLOWED:
            self.stop_reason = f"{svc}.{op}: {outcome.value}"
            return True
        return False

    def run(self) -> None:
        while self.stop_reason is None:
            if self.budget.remaining(self.cfg.list_service, self.cfg.list_operation) <= 0:
                self.stop_reason = "입찰공고정보서비스 오늘 예산 소진"
                break
            if self.step_list():
                continue
            if self.step_truth():
                continue
            break

    def step_list(self) -> bool:
        q = self.conn.execute(
            """SELECT * FROM rc_window_query WHERE study_id = ? AND (status IN ('PENDING', 'IN_PROGRESS')
               OR (status = 'FAILED' AND attempts < ?)) ORDER BY window_begin, CASE query_kind WHEN 'ALL' THEN 0 ELSE 1 END,
               query_kind LIMIT 1""", (self.cfg.study_id, self.cfg.max_attempts)).fetchone()
        if q is None:
            return False
        kind = q["query_kind"]
        page = int(q["next_page"])
        params = dict(self.cfg.base_params)
        if kind != "ALL":
            params.update(self.cfg.filters[kind])
        params.update({"inqryBgnDt": q["window_begin"], "inqryEndDt": q["window_end"], "pageNo": str(page),
                       "numOfRows": str(self.cfg.num_of_rows)})
        result = self.client.call(self.cfg.list_service, self.cfg.list_operation, params)
        if self._fatal(result.outcome, self.cfg.list_service, self.cfg.list_operation):
            return False
        key = (self.cfg.study_id, q["window_begin"], kind)
        if result.outcome not in DATA_OK or (result.total_count is None and result.outcome != Outcome.NO_DATA):
            with transaction(self.conn):
                self.conn.execute(
                    """UPDATE rc_window_query SET status = 'FAILED', attempts = attempts + 1, last_error = ?, updated_at_utc = ?
                       WHERE study_id = ? AND window_begin = ? AND query_kind = ?""",
                    (redact(f"{result.outcome.value}: {result.basis}")[:500], _now(), *key))
            return True
        self.list_pages += 1
        items = list(result.items or [])
        total = 0 if result.outcome == Outcome.NO_DATA else int(result.total_count or 0)
        response_id = result.source_response_ids[-1] if result.source_response_ids else None
        with transaction(self.conn):
            for item in items:
                no = self.store.upsert_notice(item, response_id)
                ord_ = str(item.get("bidNtceOrd") or "").strip()
                if not no or not ord_:
                    continue
                self.conn.execute(
                    "INSERT OR IGNORE INTO rc_hit (study_id, query_kind, window_begin, bid_ntce_no, bid_ntce_ord) VALUES (?, ?, ?, ?, ?)",
                    (self.cfg.study_id, kind, q["window_begin"], no, ord_))
                if kind == "ALL":
                    row = self.conn.execute("SELECT bid_ntce_ord, status FROM rc_truth WHERE study_id = ? AND bid_ntce_no = ?",
                                            (self.cfg.study_id, no)).fetchone()
                    if row is None:
                        self.conn.execute(
                            """INSERT INTO rc_truth (study_id, bid_ntce_no, bid_ntce_ord, status, updated_at_utc)
                               VALUES (?, ?, ?, 'PENDING', ?)""", (self.cfg.study_id, no, ord_, _now()))
                    elif ord_ > row["bid_ntce_ord"] and row["status"] != "DONE":
                        self.conn.execute("UPDATE rc_truth SET bid_ntce_ord = ? WHERE study_id = ? AND bid_ntce_no = ?",
                                          (ord_, self.cfg.study_id, no))
            rows = (0 if page == 1 else int(q["rows_received"])) + len(items)
            finished = total == 0 or rows >= total or len(items) < self.cfg.num_of_rows
            if page > 1 and q["total_count"] is not None and int(q["total_count"]) != total:
                status, next_page, rows, err = "PENDING", 1, 0, f"totalCount 변경 {q['total_count']} -> {total}"
            elif finished and rows != total:
                status, next_page, rows, err = "PENDING", 1, 0, f"수신 {rows} != totalCount {total}"
            elif finished:
                status, next_page, err = "DONE", page, None
            else:
                status, next_page, err = "IN_PROGRESS", page + 1, None
            if status == "PENDING":
                attempts_row = self.conn.execute("SELECT attempts FROM rc_window_query WHERE study_id = ? AND window_begin = ? AND query_kind = ?", key).fetchone()
                if int(attempts_row["attempts"]) + 1 >= self.cfg.max_attempts:
                    status = "FAILED"
            self.conn.execute(
                """UPDATE rc_window_query SET status = ?, next_page = ?, total_count = ?, rows_received = ?, last_error = ?,
                   attempts = attempts + ?, updated_at_utc = ? WHERE study_id = ? AND window_begin = ? AND query_kind = ?""",
                (status, next_page, total if status != "PENDING" else None, rows, err, 1 if err else 0, _now(), *key))
        return True

    def step_truth(self) -> bool:
        t = self.conn.execute(
            """SELECT * FROM rc_truth WHERE study_id = ? AND (status = 'PENDING' OR (status = 'FAILED' AND attempts < ?))
               ORDER BY bid_ntce_no LIMIT 1""", (self.cfg.study_id, self.cfg.max_attempts)).fetchone()
        if t is None:
            return False
        no, ord_ = t["bid_ntce_no"], t["bid_ntce_ord"]
        fetched = self.conn.execute("SELECT rows_received FROM bf_license_fetch WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                                    (no, ord_)).fetchone()
        reused = fetched is not None
        if reused:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT license_code, permsn_indstryty_list, quality_flag FROM bf_license_limit WHERE bid_ntce_no = ? AND bid_ntce_ord = ?",
                (no, ord_))]
            parsed = [{"code": r["license_code"], "permsn": r["permsn_indstryty_list"] or "", "quality": r["quality_flag"]} for r in rows]
            self.truth_reused += 1
        else:
            col = collect_all_pages(self.client, LICENSE_SVC, LICENSE_OP,
                                    {"inqryDiv": "2", "bidNtceNo": no, "bidNtceOrd": ord_}, num_of_rows=100, max_pages=10)
            if not col.complete:
                if self._fatal(col.final_outcome, LICENSE_SVC, LICENSE_OP):
                    return False
                with transaction(self.conn):
                    self.conn.execute(
                        "UPDATE rc_truth SET status = 'FAILED', attempts = attempts + 1, last_error = ?, updated_at_utc = ? WHERE study_id = ? AND bid_ntce_no = ?",
                        (redact("; ".join(i.code for i in col.issues) or str(col.final_outcome))[:300], _now(), self.cfg.study_id, no))
                return True
            response_id = col.source_response_ids[-1] if col.source_response_ids else None
            with transaction(self.conn):
                parsed = self.store.replace_license_rows(no, ord_, col.items, response_id)
                self.conn.execute(
                    "INSERT OR REPLACE INTO bf_license_fetch (bid_ntce_no, bid_ntce_ord, rows_received, response_id, fetched_at_utc) VALUES (?, ?, ?, ?, ?)",
                    (no, ord_, len(col.items), response_id, _now()))
            self.truth_called += 1
        codes = set(self.cfg.target_codes)
        lcns = any(p["code"] in codes for p in parsed)
        anyhit = lcns or any(f"/{c}]" in (p["permsn"] or "") for p in parsed for c in codes)
        with transaction(self.conn):
            self.conn.execute(
                """UPDATE rc_truth SET status = 'DONE', attempts = attempts + ?, license_rows = ?, has_target_lcns = ?, has_target_any = ?,
                   flagged_rows = ?, reused = ?, last_error = NULL, updated_at_utc = ? WHERE study_id = ? AND bid_ntce_no = ?""",
                (0 if reused else 1, len(parsed), int(lcns), int(anyhit), sum(1 for p in parsed if p["quality"]), int(reused), _now(),
                 self.cfg.study_id, no))
        return True


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    if n == 0:
        return None, None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(p, 4), round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)


def recall_report(conn, cfg: StudyConfig) -> dict[str, Any]:
    sid = cfg.study_id
    wq = [dict(r) for r in conn.execute(
        "SELECT query_kind, status, COUNT(*) AS n FROM rc_window_query WHERE study_id = ? GROUP BY query_kind, status", (sid,))]
    truth_status = dict(conn.execute("SELECT status, COUNT(*) FROM rc_truth WHERE study_id = ? GROUP BY status", (sid,)).fetchall())
    windows_all_done = {r[0] for r in conn.execute(
        "SELECT window_begin FROM rc_window_query WHERE study_id = ? GROUP BY window_begin HAVING SUM(status = 'DONE') = COUNT(*)", (sid,))}
    # 모든 조회방식이 끝난 시간창만 비교에 쓴다
    def notices(kind: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in conn.execute("SELECT window_begin, bid_ntce_no FROM rc_hit WHERE study_id = ? AND query_kind = ?", (sid, kind)):
            if r["window_begin"] in windows_all_done:
                out[r["bid_ntce_no"]] = r["window_begin"]
        return out
    all_notices = notices("ALL")
    strata = {r["window_begin"]: r["stratum"] for r in conn.execute(
        "SELECT DISTINCT window_begin, stratum FROM rc_window_query WHERE study_id = ?", (sid,))}
    truth = {r["bid_ntce_no"]: dict(r) for r in conn.execute("SELECT * FROM rc_truth WHERE study_id = ? AND status = 'DONE'", (sid,))}
    evaluated = {no for no in all_notices if no in truth}
    pos_any = {no for no in evaluated if truth[no]["has_target_any"]}
    pos_lcns = {no for no in evaluated if truth[no]["has_target_lcns"]}
    filters_out = {}
    for kind in cfg.filters:
        hits = notices(kind)
        tp = pos_any & set(hits)
        missed = sorted(pos_any - set(hits))
        hits_eval = [no for no in hits if no in truth]
        by_stratum = {}
        for stratum in sorted(set(strata.values())):
            p = {no for no in pos_any if strata.get(all_notices[no]) == stratum}
            by_stratum[stratum] = {"positives": len(p), "found": len(p & set(hits)), "recall": wilson(len(p & set(hits)), len(p))}
        miss_detail = []
        for no in missed[:50]:
            rev = conn.execute("SELECT bid_ntce_ord, ntce_kind_nm, bid_ntce_nm, main_cnstty_nm, cntrct_cncls_mthd_nm, bid_ntce_dt FROM bf_notice_revision WHERE bid_ntce_no = ? ORDER BY bid_ntce_ord DESC LIMIT 1", (no,)).fetchone()
            lic = [r[0] for r in conn.execute("SELECT lcns_lmt_nm FROM bf_license_limit WHERE bid_ntce_no = ? AND bid_ntce_ord = ?", (no, truth[no]["bid_ntce_ord"]))]
            miss_detail.append({"bid_ntce_no": no, **(dict(rev) if rev else {}), "license_limits": lic})
        filters_out[kind] = {
            "params": cfg.filters[kind],
            "recall_any": wilson(len(tp), len(pos_any)),
            "recall_lcns_only": wilson(len(pos_lcns & set(hits)), len(pos_lcns)),
            "true_positive": len(tp), "missed": len(missed),
            "hits_total": len(hits), "hits_not_in_all": len(set(hits) - set(all_notices)),
            "precision": wilson(sum(1 for no in hits_eval if truth[no]["has_target_any"]), len(hits_eval)),
            "by_stratum": by_stratum, "missed_examples": miss_detail,
        }
    return {
        "study_id": sid, "generated_at_kst": now_kst().isoformat(timespec="seconds"),
        "design": {"windows_per_stratum": cfg.windows_per_stratum, "window_minutes": cfg.window_minutes, "seed": cfg.seed,
                   "range": [cfg.begin.isoformat(), cfg.end.isoformat()], "list_operation": cfg.list_operation,
                   "truth": f"공고 최신 차수 면허제한에 {list(cfg.target_codes)} (lcnsLmtNm 코드 또는 허용업종목록)"},
        "progress": {"window_queries": wq, "windows_fully_done": len(windows_all_done), "truth": truth_status},
        "universe_notices": len(all_notices), "evaluated_notices": len(evaluated),
        "positives_any": len(pos_any), "positives_lcns_only": len(pos_lcns),
        "sufficient_sample": len(pos_any) >= cfg.target_min_positives, "target_min_positives": cfg.target_min_positives,
        "filters": filters_out,
    }


def aggregate_recall(conn, filter_params: dict[str, str], target_codes: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """모든 recall 연구에서 같은 필터 파라미터·같은 목표 코드로 측정한 결과를 공고번호 기준으로 합산한다.

    각 연구에서 모든 조회방식이 끝난 시간창만 쓰고, 면허 판정이 끝난 공고만 분모에 넣는다.
    연구끼리 시간창이 겹치면 같은 공고는 한 번만 센다.
    """
    wanted = {str(k): str(v) for k, v in filter_params.items()}
    codes = sorted(str(c) for c in target_codes)
    positives: set[str] = set()
    found: set[str] = set()
    evaluated: set[str] = set()
    hits_eval: set[str] = set()
    hits_pos: set[str] = set()
    studies = []
    for study in conn.execute("SELECT study_id, config_json FROM rc_study"):
        cfg = json.loads(study["config_json"])
        if sorted(str(c) for c in cfg.get("target_license_codes") or []) != codes:
            continue
        lst = cfg.get("list") or {}
        base = {str(a): str(b) for a, b in (lst.get("base_params") or {}).items()}
        kinds = [k for k, v in (cfg.get("filters") or {}).items()
                 if base | {str(a): str(b) for a, b in v.items()} == wanted]
        if not kinds:
            continue
        sid = study["study_id"]
        done_windows = {r[0] for r in conn.execute(
            "SELECT window_begin FROM rc_window_query WHERE study_id = ? GROUP BY window_begin HAVING SUM(status = 'DONE') = COUNT(*)",
            (sid,))}
        truth = {r["bid_ntce_no"]: r["has_target_any"] for r in conn.execute(
            "SELECT bid_ntce_no, has_target_any FROM rc_truth WHERE study_id = ? AND status = 'DONE'", (sid,))}
        all_nos = {r["bid_ntce_no"] for r in conn.execute(
            "SELECT window_begin, bid_ntce_no FROM rc_hit WHERE study_id = ? AND query_kind = 'ALL'", (sid,))
            if r["window_begin"] in done_windows}
        study_eval = {no for no in all_nos if no in truth}
        study_pos = {no for no in study_eval if truth[no]}
        for kind in kinds:
            hits = {r["bid_ntce_no"] for r in conn.execute(
                "SELECT window_begin, bid_ntce_no FROM rc_hit WHERE study_id = ? AND query_kind = ?", (sid, kind))
                if r["window_begin"] in done_windows}
            found |= study_pos & hits
            hits_eval |= {no for no in hits if no in truth}
            hits_pos |= {no for no in hits if no in truth and truth[no]}
        evaluated |= study_eval
        positives |= study_pos
        studies.append({"study_id": sid, "windows_done": len(done_windows), "evaluated": len(study_eval),
                        "positives": len(study_pos), "truth_pending": conn.execute(
                            "SELECT COUNT(*) FROM rc_truth WHERE study_id = ? AND status != 'DONE'", (sid,)).fetchone()[0]})
    return {
        "filter_params": wanted, "target_codes": codes, "studies": studies,
        "evaluated_notices": len(evaluated), "positives": len(positives), "found": len(found),
        "missed": len(positives - found), "recall": wilson(len(found), len(positives)),
        "precision": wilson(len(hits_pos), len(hits_eval)),
    }


def recall_gate(conn, filter_params: dict[str, str], target_codes, *, min_positives: int, min_recall_lower95: float) -> dict[str, Any]:
    agg = aggregate_recall(conn, filter_params, target_codes)
    lower = agg["recall"][1]
    passed = agg["positives"] >= min_positives and lower is not None and lower >= min_recall_lower95
    reasons = []
    if agg["positives"] < min_positives:
        reasons.append(f"정답 양성 {agg['positives']}건 < 기준 {min_positives}건")
    if lower is None or lower < min_recall_lower95:
        reasons.append(f"recall 95% 하한 {lower} < 기준 {min_recall_lower95}")
    return {**agg, "passed": passed, "reasons": reasons,
            "criteria": {"min_positives": min_positives, "min_recall_lower95": min_recall_lower95}}
