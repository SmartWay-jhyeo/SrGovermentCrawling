"""P0 실연동 검증 (verify-api).

목적: 호출예산 안에서 소수 표본으로 공고·면허·지역·기초금액·개찰·명부·낙찰의 실제 연결과 count 의미를 확인한다.
- 전수 수집이 아니다. 표본 결과를 전국·전기간 완전성으로 해석하지 않는다.
- 발견한 사실은 관측(evidence)으로만 기록하고, 문서와 다르면 불일치로 남긴다.
- 인증 오류는 해당 서비스 단계만 중단, 일일 한도·예산 소진은 전체 중단 후 이어받기 상태(NOT_RUN_*)를 남긴다.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from bidloc.clients.envelope import ItemDict
from bidloc.clients.errors import DATA_OK, RUN_FATAL, SERVICE_FATAL, Outcome
from bidloc.collectors.pagination import PageCollection, collect_all_pages
from bidloc.normalizers.keys import (
    NoticeRevisionKey,
    OpeningUnitKey,
    link_openings_to_notices,
    notice_key,
    opening_key,
)
from bidloc.normalizers.participation import (
    compare_official_to_roster,
    official_count_from_item,
    roster_counts,
)
from bidloc.normalizers.values import (
    normalize_industry_name,
    parse_count,
    parse_krw_amount,
    parse_mfrc_field_list,
    parse_name_code_list,
    split_name_code,
)
from bidloc.redaction import redact
from bidloc.repositories.runs import RunRepository
from bidloc.timeutil import now_kst

SERVICE_BID = "bid_notice"
SERVICE_AWARD = "bid_award"
SERVICE_INDUSTRY = "industry_law"

_NOTICE_NO_RE = re.compile(r"^[A-Za-z0-9-]{8,40}$")
_INQRY_DT_RE = re.compile(r"^\d{12}$")

NOTICE_PRESENCE_FIELDS = (
    "bidNtceNo", "bidNtceOrd", "reNtceYn", "ntceKindNm", "befBidBbancNo", "chgNtceRsn", "bidNtceDt", "rgstDt",
    "chgDt", "ntceInsttCd", "ntceInsttNm", "dminsttCd", "dminsttNm", "cntrctCnclsMthdNm", "bidMethdNm",
    "sucsfbidMthdCd", "sucsfbidMthdNm", "sucsfbidMthdAppStd", "bdgtAmt", "presmptPrce", "VAT", "govsplyAmt",
    "mainCnsttyNm", "mainCnsttyPresmptPrce", "indstrytyLmtYn", "bidPrtcptLmtYn", "indstrytyMfrcFldEvlYn",
    "cnstrtsiteRgnNm", "rgnLmtBidLocplcJdgmBssCd", "rgnLmtBidLocplcJdgmBssNm", "brffcBidprcPermsnYn",
    "cmmnSpldmdMethdCd", "cmmnSpldmdMethdNm", "cmmnSpldmdCorpRgnLmtYn", "rgnDutyJntcontrctYn",
    "jntcontrctDutyRgnNm1", "incntvRgnNm1", "arsltCmptYn", "pqEvalYn", "sucsfbidLwltRate", "opengDt",
    "rbidPermsnYn", "rbidOpengDt", "untyNtceNo", "bidNtceDtlUrl", "d2bMngRgnLmtYn",
)


class VerifyConfigError(ValueError):
    pass


# ------------------------------------------------------------------ 계획 설정


@dataclass(frozen=True)
class DiscoveryWindow:
    label: str
    inqry_bgn_dt: str
    inqry_end_dt: str


@dataclass(frozen=True)
class VerifyPlan:
    max_samples: int
    industry_params: dict[str, str]
    industry_rows: int
    industry_max_pages: int
    target_keywords: tuple[str, ...]
    fallback_name_queries: tuple[str, ...]
    fixed_notices: tuple[tuple[str, str], ...]
    discovery_params: dict[str, str]
    discovery_rows: int
    pick_per_window: int
    windows: tuple[DiscoveryWindow, ...]
    per_sample: dict[str, Any]

    def estimate_calls(self) -> tuple[int, str]:
        per_sample_min = 7  # notice, license, region, basis, mfrc, opening, award (명부·선택 단계 제외)
        samples = min(self.max_samples, len(self.fixed_notices) + self.pick_per_window * len(self.windows))
        low = 1 + len(self.windows) + per_sample_min * samples
        note = ("하한 추정: 업종 1 + 탐색 창 수 + 표본×7. 명부 페이지·재입찰/유찰·변경이력·재시도는 추가된다. "
                "실제 호출 수는 예산 한도에서 멈춘다.")
        return low, note


def load_verify_plan(path: Path) -> VerifyPlan:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise VerifyConfigError("verify_samples.yaml schema_version 1이 필요하다")
    try:
        fixed = []
        for entry in data.get("fixed_notices") or []:
            no = str(entry["bid_ntce_no"]).strip()
            if not _NOTICE_NO_RE.match(no):
                raise VerifyConfigError(f"잘못된 공고번호 형식: {no}")
            fixed.append((no, str(entry.get("label", ""))))
        disc = data["discovery"]
        windows = []
        for w in disc.get("windows") or []:
            bgn, end = str(w["inqryBgnDt"]), str(w["inqryEndDt"])
            if not (_INQRY_DT_RE.match(bgn) and _INQRY_DT_RE.match(end)) or end < bgn:
                raise VerifyConfigError(f"잘못된 탐색 기간: {w}")
            windows.append(DiscoveryWindow(str(w["label"]), bgn, end))
        ind = data["industry_lookup"]
        return VerifyPlan(
            max_samples=int(data.get("max_samples", 20)),
            industry_params={k: str(v) for k, v in (ind.get("params") or {}).items()},
            industry_rows=int(ind.get("num_of_rows", 100)),
            industry_max_pages=int(ind.get("max_pages", 3)),
            target_keywords=tuple(str(k) for k in ind.get("target_name_keywords") or []),
            fallback_name_queries=tuple(str(k) for k in ind.get("fallback_name_queries") or []),
            fixed_notices=tuple(fixed),
            discovery_params={k: str(v) for k, v in (disc.get("params") or {}).items()},
            discovery_rows=int(disc.get("num_of_rows", 30)),
            pick_per_window=int(disc.get("pick_per_window", 2)),
            windows=tuple(windows),
            per_sample=dict(data.get("per_sample") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, VerifyConfigError):
            raise
        raise VerifyConfigError(f"verify_samples.yaml 형식 오류: {exc}") from exc


# ------------------------------------------------------------------ 실행 상태


@dataclass
class StepOutcome:
    status: str
    summary: dict[str, Any]
    api_outcome: Outcome | None = None
    calls: int = 0
    reused: bool = False


@dataclass
class Sample:
    bid_ntce_no: str
    label: str
    source: str
    notice_items: list[ItemDict] = field(default_factory=list)
    notice_keys: set[NoticeRevisionKey] = field(default_factory=set)
    opening_items: dict[OpeningUnitKey, ItemDict] = field(default_factory=dict)
    roster_items: dict[OpeningUnitKey, list[ItemDict]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.bid_ntce_no


def status_from_outcome(outcome: Outcome | None, has_rows: bool) -> str:
    if outcome is None:
        return "SKIPPED"
    if outcome in DATA_OK:
        return "DONE" if has_rows else "DONE_EMPTY"
    if outcome in (Outcome.BUDGET_EXHAUSTED_RUN, Outcome.BUDGET_EXHAUSTED_DAY):
        return "NOT_RUN_BUDGET"
    if outcome in SERVICE_FATAL:
        return "BLOCKED"
    return "FAILED"


def presence_matrix(items: list[ItemDict], fields: tuple[str, ...]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name in fields:
        counts = {"PRESENT": 0, "BLANK": 0, "ABSENT": 0}
        for item in items:
            if name not in item:
                counts["ABSENT"] += 1
            elif item[name] is None or str(item[name]).strip() == "":
                counts["BLANK"] += 1
            else:
                counts["PRESENT"] += 1
        out[name] = counts
    return out


def collection_summary(col: PageCollection) -> dict[str, Any]:
    return {
        "outcome": col.final_outcome.value if col.final_outcome else None,
        "complete": col.complete,
        "confirmed_empty": col.confirmed_empty,
        "total_count": col.total_count,
        "rows": len(col.items),
        "pages_fetched": col.pages_fetched,
        "issues": [f"{i.code}: {i.detail}" for i in col.issues],
        "envelope_shapes": sorted(set(col.envelope_shapes)),
        "source_response_ids": col.source_response_ids,
    }


class VerifyRunner:
    def __init__(self, *, client: Any, runs: RunRepository, conn: sqlite3.Connection, run_id: str, plan: VerifyPlan,
                 reuse: Mapping[str, dict[str, Any]] | None = None, reuse_from_run_id: str | None = None,
                 raw_items_reader: Callable[[int], list[ItemDict] | None] | None = None) -> None:
        self.client = client
        self.raw_items_reader = raw_items_reader
        self.runs = runs
        self.conn = conn
        self.run_id = run_id
        self.plan = plan
        self.reuse = dict(reuse or {})
        self.reuse_from_run_id = reuse_from_run_id
        self.stop_reason: str | None = None
        self.stop_step_status: str | None = None
        self.blocked_services: dict[str, str] = {}
        self.steps: list[dict[str, Any]] = []
        self.samples: list[Sample] = []
        self.industry_matches: list[dict[str, Any]] = []
        self.discovery: dict[str, dict[str, Any]] = {}

    # -------------------------------------------------------------- 공통

    def _record(self, step_key: str, status: str, *, service_id: str | None, operation: str | None,
                sample_key: str | None, calls: int, summary: dict[str, Any]) -> None:
        self.runs.record_step(run_id=self.run_id, step_key=step_key, status=status, sample_key=sample_key,
                              service_id=service_id, operation=operation, calls_used=calls, summary=summary)
        self.steps.append({"step_key": step_key, "status": status, "service_id": service_id, "operation": operation,
                           "sample_key": sample_key, "calls": calls,
                           "outcome": summary.get("outcome"), "note": summary.get("note")})

    def run_step(self, step_key: str, service_id: str, operation: str, fn: Callable[[], StepOutcome], *,
                 sample_key: str | None = None) -> StepOutcome | None:
        if step_key in self.reuse:
            summary = dict(self.reuse[step_key])
            summary["reused_from_run_id"] = self.reuse_from_run_id
            summary["reuse_note"] = "이전 실행의 DONE 결과 재사용(이번 실행 호출 없음)"
            self._record(step_key, "DONE", service_id=service_id, operation=operation, sample_key=sample_key,
                         calls=0, summary=summary)
            return StepOutcome("DONE", summary, None, 0, reused=True)
        if self.stop_reason:
            self._record(step_key, self.stop_step_status or "NOT_RUN_BUDGET", service_id=service_id,
                         operation=operation, sample_key=sample_key, calls=0,
                         summary={"note": f"중단됨: {self.stop_reason}"})
            return None
        if service_id in self.blocked_services:
            self._record(step_key, "NOT_RUN_SERVICE_BLOCKED", service_id=service_id, operation=operation,
                         sample_key=sample_key, calls=0,
                         summary={"note": f"서비스 차단: {self.blocked_services[service_id]}"})
            return None
        before = self.client.budget.run_used
        try:
            outcome = fn()
        except Exception as exc:  # 단계 오류가 전체 실행 기록을 잃게 하지 않는다
            outcome = StepOutcome("FAILED", {"error": redact(f"{type(exc).__name__}: {exc}")}, None)
        outcome.calls = self.client.budget.run_used - before
        api = outcome.api_outcome
        if api in RUN_FATAL:
            if api == Outcome.QUOTA_DAILY_EXCEEDED:
                self.stop_reason, self.stop_step_status = "제공기관 일일 호출 한도 초과(22)", "NOT_RUN_QUOTA"
            elif api == Outcome.IP_NOT_ALLOWED:
                self.stop_reason, self.stop_step_status = "호출 IP/도메인 차단(29/32)", "NOT_RUN_SERVICE_BLOCKED"
            else:
                self.stop_reason, self.stop_step_status = f"내부 호출예산 소진({api.value})", "NOT_RUN_BUDGET"
        elif api in SERVICE_FATAL:
            self.blocked_services[service_id] = api.value
        self._record(step_key, outcome.status, service_id=service_id, operation=operation, sample_key=sample_key,
                     calls=outcome.calls, summary=outcome.summary)
        return outcome

    # -------------------------------------------------------------- 업종코드

    def step_industry_lookup(self) -> StepOutcome:
        plan = self.plan
        col = collect_all_pages(self.client, SERVICE_INDUSTRY, "getIndstrytyBaseLawrgltInfoList", plan.industry_params,
                                num_of_rows=plan.industry_rows, max_pages=plan.industry_max_pages)
        matches = self._industry_matches(col.items)
        summary = collection_summary(col)
        summary["query"] = dict(plan.industry_params)
        summary["classification_names"] = sorted({str(i.get("indstrytyClsfcNm") or "") for i in col.items})[:10]
        fallback: list[dict[str, Any]] = []
        final_outcome = col.final_outcome
        if col.query_ok and not matches:
            for name in plan.fallback_name_queries:
                sub = collect_all_pages(self.client, SERVICE_INDUSTRY, "getIndstrytyBaseLawrgltInfoList",
                                        {"indstrytyNm": name}, num_of_rows=plan.industry_rows, max_pages=1)
                fallback.append({"indstrytyNm": name, **collection_summary(sub)})
                matches.extend(m for m in self._industry_matches(sub.items) if m not in matches)
                final_outcome = sub.final_outcome
                if not sub.query_ok:
                    break
        summary["fallback_queries"] = fallback
        summary["matches"] = matches
        summary["note"] = "업종코드는 조회시점 현재 유효 정보다. 과거 공고의 업종 판정 정답으로 소급하지 않는다."
        self.industry_matches = matches
        return StepOutcome(status_from_outcome(final_outcome, bool(matches)), summary, final_outcome)

    def _industry_matches(self, items: list[ItemDict]) -> list[dict[str, Any]]:
        out = []
        for item in items:
            name = str(item.get("indstrytyNm") or "")
            norm = normalize_industry_name(name)
            if any(k in norm for k in self.plan.target_keywords):
                out.append({
                    "indstrytyCd": item.get("indstrytyCd"), "indstrytyNm": name,
                    "indstrytyClsfcCd": item.get("indstrytyClsfcCd"), "indstrytyUseYn": item.get("indstrytyUseYn"),
                    "baseLawordNm": item.get("baseLawordNm"), "inclsnLcns": item.get("inclsnLcns"),
                })
        return out

    # -------------------------------------------------------------- 표본 탐색

    def step_discovery(self, window: DiscoveryWindow) -> StepOutcome:
        params = dict(self.plan.discovery_params)
        params.update({"inqryBgnDt": window.inqry_bgn_dt, "inqryEndDt": window.inqry_end_dt,
                       "pageNo": "1", "numOfRows": str(self.plan.discovery_rows)})
        result = self.client.call(SERVICE_BID, "getBidPblancListInfoCnstwkPPSSrch", params)
        items = result.items or []
        picked = self._pick(items)
        final_outcome = result.outcome
        probe_summary = None
        if result.outcome in DATA_OK and not items:
            # 업종 필터 결과가 0건이면, 같은 기간에 공사 공고 자체가 조회되는지 1행만 확인한다(필터 문제와 기간 미제공 구분).
            probe_params = {k: v for k, v in params.items() if k not in ("indstrytyNm", "indstrytyCd")}
            probe_params["numOfRows"] = "1"
            probe = self.client.call(SERVICE_BID, "getBidPblancListInfoCnstwkPPSSrch", probe_params)
            probe_summary = {"outcome": probe.outcome.value, "total_count": probe.total_count,
                             "rows": len(probe.items or []), "source_response_ids": probe.source_response_ids,
                             "note": "업종 필터를 뺀 같은 기간 조회(1행). 0건이면 해당 기간 데이터 미제공 가능성, 1건 이상이면 필터 조건 문제 가능성"}
            if probe.outcome not in DATA_OK:
                final_outcome = probe.outcome
        summary = {
            "unfiltered_probe": probe_summary,
            "outcome": result.outcome.value, "basis": result.basis, "http_status": result.http_status,
            "result_code": result.result_code, "total_count": result.total_count, "rows_on_first_page": len(items),
            "envelope_shape": result.envelope_shape, "window": [window.inqry_bgn_dt, window.inqry_end_dt],
            "query": {k: v for k, v in params.items() if k not in ("pageNo", "numOfRows")},
            "picked": picked, "year_rows_observed": bool(items),
            "note": "첫 페이지만 받은 표본 추출용 조회다. 해당 주간 전수 수집이나 서버 업종필터 정확성의 근거가 아니다.",
            "source_response_ids": result.source_response_ids,
        }
        self.discovery[window.label] = summary
        return StepOutcome(status_from_outcome(final_outcome, bool(items)), summary, final_outcome)

    def _pick(self, items: list[ItemDict]) -> list[dict[str, Any]]:
        """표본 선정. 같은 페이지에 취소공고 행이 있는 공고는 개찰결과를 검증할 수 없어 제외하고,
        개찰일시가 이미 지난 공고를 우선한다. 결과를 보고 고르는 것이 아니라 공고 목록 필드만 쓴다."""
        now_text = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        by_no: dict[str, list[ItemDict]] = {}
        for item in items:
            no = str(item.get("bidNtceNo") or "").strip()
            if no and _NOTICE_NO_RE.match(no):
                by_no.setdefault(no, []).append(item)
        candidates = []
        for no in sorted(by_no):
            rows = sorted(by_no[no], key=lambda i: str(i.get("bidNtceOrd") or ""))
            if any("취소" in str(r.get("ntceKindNm") or "") for r in rows):
                continue
            latest = rows[-1]
            opened = bool(str(latest.get("opengDt") or "")) and str(latest.get("opengDt")) < now_text
            candidates.append((0 if opened else 1, no, latest))
        seen_combo: set[tuple[str, str]] = set()
        first_pass, second_pass = [], []
        for _, no, item in sorted(candidates, key=lambda c: (c[0], c[1])):
            entry = {"bid_ntce_no": no, "bid_ntce_ord": item.get("bidNtceOrd"), "ntceKindNm": item.get("ntceKindNm"),
                     "cntrctCnclsMthdNm": item.get("cntrctCnclsMthdNm"), "sucsfbidMthdNm": item.get("sucsfbidMthdNm"),
                     "mainCnsttyNm": item.get("mainCnsttyNm"), "opengDt": item.get("opengDt")}
            combo = (str(item.get("cntrctCnclsMthdNm")), str(item.get("sucsfbidMthdNm")))
            (first_pass if combo not in seen_combo else second_pass).append(entry)
            seen_combo.add(combo)
        return (first_pass + second_pass)[: self.plan.pick_per_window]

    # -------------------------------------------------------------- 표본별 단계

    def _query_by_ord(self, sample: Sample, operation: str) -> tuple[PageCollection, str, list[dict[str, Any]]]:
        """최신 차수로 조회하고, 결과가 비면 최초 차수로 한 번 더 조회한다(어느 차수에 행이 등록되는지 관측)."""
        ords = sorted({k.bid_ntce_ord for k in sample.notice_keys}) or ["000"]
        tried: list[dict[str, Any]] = []
        chosen_ord = ords[-1]
        col = collect_all_pages(self.client, SERVICE_BID, operation,
                                {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no, "bidNtceOrd": chosen_ord},
                                num_of_rows=100, max_pages=2)
        tried.append({"bid_ntce_ord": chosen_ord, "outcome": col.final_outcome.value if col.final_outcome else None,
                      "rows": len(col.items)})
        if col.query_ok and not col.items and len(ords) > 1:
            fallback = collect_all_pages(self.client, SERVICE_BID, operation,
                                         {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no, "bidNtceOrd": ords[0]},
                                         num_of_rows=100, max_pages=2)
            tried.append({"bid_ntce_ord": ords[0],
                          "outcome": fallback.final_outcome.value if fallback.final_outcome else None,
                          "rows": len(fallback.items)})
            if fallback.items or not fallback.query_ok:
                col, chosen_ord = fallback, ords[0]
        return col, chosen_ord, tried

    def step_notice(self, sample: Sample) -> StepOutcome:
        ps = self.plan.per_sample
        col = collect_all_pages(self.client, SERVICE_BID, "getBidPblancListInfoCnstwk",
                                {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no},
                                num_of_rows=int(ps.get("notice_num_of_rows", 50)),
                                max_pages=int(ps.get("notice_max_pages", 2)), key_fn=lambda i: notice_key(i).key)
        sample.notice_items = list(col.items)
        revisions = []
        other_numbers = 0
        for item in col.items:
            extraction = notice_key(item)
            if extraction.key is None:
                revisions.append({"key_error": {"missing": extraction.missing_fields, "blank": extraction.blank_fields}})
                continue
            if extraction.key.bid_ntce_no != sample.bid_ntce_no:
                other_numbers += 1
            sample.notice_keys.add(extraction.key)
            amounts = {}
            for fname in ("bdgtAmt", "presmptPrce", "VAT", "govsplyAmt", "mainCnsttyPresmptPrce"):
                parsed = parse_krw_amount(item, fname)
                amounts[fname] = {"status": parsed.status.value, "value": parsed.value}
            revisions.append({
                "bid_ntce_ord": extraction.key.bid_ntce_ord,
                **{f: item.get(f) for f in ("ntceKindNm", "reNtceYn", "befBidBbancNo", "bidNtceDt", "rgstDt", "chgDt",
                                            "cntrctCnclsMthdNm", "bidMethdNm", "sucsfbidMthdNm", "indstrytyLmtYn",
                                            "bidPrtcptLmtYn", "cnstrtsiteRgnNm", "rgnLmtBidLocplcJdgmBssNm",
                                            "mainCnsttyNm", "cmmnSpldmdMethdNm", "opengDt", "bidNtceNm",
                                            "ntceInsttNm", "dminsttNm")},
                "amounts": amounts,
            })
            self.conn.execute(
                """INSERT OR IGNORE INTO verify_notice_revision
                   (run_id, bid_ntce_no, bid_ntce_ord, ntce_kind_nm, re_ntce_yn, bef_bid_ntce_no, bid_ntce_dt_raw,
                    rgst_dt_raw, source_response_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.run_id, extraction.key.bid_ntce_no, extraction.key.bid_ntce_ord, item.get("ntceKindNm"),
                 item.get("reNtceYn"), item.get("befBidBbancNo"), item.get("bidNtceDt"), item.get("rgstDt"),
                 col.source_response_ids[0] if col.source_response_ids else None),
            )
        summary = collection_summary(col)
        summary.update({
            "revisions": revisions,
            "distinct_revision_keys": len(sample.notice_keys),
            "rows_with_other_notice_no": other_numbers,
            "field_presence": presence_matrix(col.items, NOTICE_PRESENCE_FIELDS),
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_license(self, sample: Sample) -> StepOutcome:
        col, ord_used, tried = self._query_by_ord(sample, "getBidPblancListInfoLicenseLimit")
        rows = []
        target_seen = False
        target_codes = {str(m.get("indstrytyCd")) for m in self.industry_matches if m.get("indstrytyCd")}
        for item in col.items:
            name, code, st = split_name_code(item.get("lcnsLmtNm"))
            permsn = parse_name_code_list(item.get("permsnIndstrytyList"))
            mfrc = parse_mfrc_field_list(item.get("indstrytyMfrcFldList"))
            norm = normalize_industry_name(name or "")
            if any(k in norm for k in self.plan.target_keywords) or (code and code in target_codes):
                target_seen = True
            rows.append({
                "bid_ntce_ord": item.get("bidNtceOrd"), "lmtGrpNo": item.get("lmtGrpNo"), "lmtSno": item.get("lmtSno"),
                "lcnsLmtNm": item.get("lcnsLmtNm"), "lcnsLmtNm_parse": st.value, "license_code": code,
                "permsnIndstrytyList_status": permsn.status.value, "permsn_count": len(permsn.groups or []),
                "indstrytyMfrcFldList": item.get("indstrytyMfrcFldList"), "mfrc_parse_status": mfrc.status.value,
                "mfrc_alternatives": mfrc.alternatives, "bsnsDivNm": item.get("bsnsDivNm"),
            })
        summary = collection_summary(col)
        groups = Counter(str(r["lmtGrpNo"]) for r in rows)
        summary.update({
            "bid_ntce_ord_used": ord_used, "ord_attempts": tried, "rows_detail": rows,
            "rows_per_lmtGrpNo": dict(groups), "target_industry_seen": target_seen,
            "note": "제한그룹 간/그룹 내 AND·OR 의미는 문서에 없다. 공고문 원문 대조 전까지 판정에 쓰지 않는다.",
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_region(self, sample: Sample) -> StepOutcome:
        col, ord_used, tried = self._query_by_ord(sample, "getBidPblancListInfoPrtcptPsblRgn")
        names = [str(i.get("prtcptPsblRgnNm") or "") for i in col.items]
        site = sorted({str(i.get("cnstrtsiteRgnNm") or "") for i in sample.notice_items} - {""})
        summary = collection_summary(col)
        summary.update({
            "bid_ntce_ord_used": ord_used, "ord_attempts": tried,
            "allowed_region_names": names,
            "lmtSno_values": [i.get("lmtSno") for i in col.items],
            "distinct_allowed_regions": len(set(names)),
            "has_sub_provincial_name": any(len(n.split()) >= 2 for n in names),
            "construction_site_region_names_from_notice": site,
            "agency_names_from_notice": sorted({str(i.get("ntceInsttNm") or "") for i in sample.notice_items} - {""}),
            "note": "허용지역(prtcptPsblRgnNm)·공사현장(cnstrtsiteRgnNm)·발주기관을 분리해 기록한다. 발주기관 주소는 이 응답에 없다.",
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_basis_amount(self, sample: Sample) -> StepOutcome:
        col = collect_all_pages(self.client, SERVICE_BID, "getBidPblancListInfoCnstwkBsisAmount",
                                {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no}, num_of_rows=50, max_pages=2)
        rows = []
        for item in col.items:
            amt = parse_krw_amount(item, "bssamt")
            rows.append({"bid_ntce_ord": item.get("bidNtceOrd"), "bidClsfcNo": item.get("bidClsfcNo"),
                         "bssamt_status": amt.status.value, "bssamt": amt.value, "bssamtOpenDt": item.get("bssamtOpenDt"),
                         "rsrvtnPrceRngBgnRate": item.get("rsrvtnPrceRngBgnRate"),
                         "rsrvtnPrceRngEndRate": item.get("rsrvtnPrceRngEndRate")})
        summary = collection_summary(col)
        summary.update({"rows_detail": rows,
                        "note": "기초금액 부가세 포함 여부는 문서에 없다(UNKNOWN). 재입찰마다 합산하지 않는다."})
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_mfrc_eval(self, sample: Sample) -> StepOutcome:
        col = collect_all_pages(self.client, SERVICE_BID, "getBidPblancListEvaluationIndstrytyMfrcInfo",
                                {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no}, num_of_rows=50, max_pages=2)
        summary = collection_summary(col)
        summary["rows_detail"] = [{k: i.get(k) for k in ("bidNtceOrd", "cnsttyTyNm", "tmpNm", "indstrytyMfrcFldNm",
                                                          "evlRt", "cnstrtWkrarDivCd", "bidwinrSlctnBssCd")}
                                  for i in col.items]
        summary["note"] = "평가대상 주력분야는 낙찰심사(적격심사 등) 관련 정보일 수 있다. 참가자격 판정과 분리한다."
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_opening(self, sample: Sample) -> StepOutcome:
        ps = self.plan.per_sample
        col = collect_all_pages(self.client, SERVICE_AWARD, "getOpengResultListInfoCnstwk",
                                {"inqryDiv": "4", "bidNtceNo": sample.bid_ntce_no},
                                num_of_rows=int(ps.get("opening_num_of_rows", 50)),
                                max_pages=int(ps.get("opening_max_pages", 2)), key_fn=lambda i: opening_key(i).key)
        units = []
        keys: list[OpeningUnitKey] = []
        for item in col.items:
            extraction = opening_key(item)
            if extraction.key is None:
                units.append({"key_error": {"missing": extraction.missing_fields, "blank": extraction.blank_fields}})
                continue
            keys.append(extraction.key)
            sample.opening_items[extraction.key] = item
        link = link_openings_to_notices(sample.notice_keys, keys)
        for key in keys:
            item = sample.opening_items[key]
            official = official_count_from_item(item, col.final_outcome)
            units.append({
                "key": [key.bid_ntce_no, key.bid_ntce_ord, key.bid_clsfc_no, key.rbid_no],
                "progrsDivCdNm": item.get("progrsDivCdNm"), "opengDt": item.get("opengDt"),
                "prtcptCnum_raw": item.get("prtcptCnum"), "official_count_status": official.status.value,
                "official_count": official.value, "link_status": link.status_of(key),
                "opengCorpInfo_format": _corp_info_format(item.get("opengCorpInfo")),
            })
        summary = collection_summary(col)
        summary.update({
            "units": units, "distinct_units": len(set(keys)),
            "link_counts": {"LINKED": len(link.linked), "SAME_NO_OTHER_ORD": len(link.same_no_other_ord),
                            "FORMAT_MISMATCH": len(link.format_mismatch), "NOT_LINKED": len(link.not_linked)},
            "note": "개찰결과가 없으면 참여 0이 아니라 개찰기록 없음(미개찰·미등록·조회범위 밖 구분 불가)으로 둔다.",
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def roster_selection(self, sample: Sample) -> tuple[list[OpeningUnitKey], list[dict[str, Any]]]:
        """명부를 받을 개찰단위 선택.

        공식 참가업체수가 페이지 한도(roster_num_of_rows × roster_max_pages) 안인 개찰완료 단위만 받는다.
        한도를 넘거나 공식 수가 없는 단위는 전체 페이지를 받을 수 없어 비교가 불가능하므로 예산을 쓰지 않는다.
        최초·마지막 단위를 우선하고, 남는 자리는 참가업체수가 작은 단위로 채운다.
        """
        ps = self.plan.per_sample
        limit = int(ps.get("roster_max_units", 2))
        cap = int(ps.get("roster_num_of_rows", 100)) * int(ps.get("roster_max_pages", 5))
        completed = sorted(k for k, i in sample.opening_items.items() if str(i.get("progrsDivCdNm") or "") == "개찰완료")
        counts = {k: parse_count(sample.opening_items[k], "prtcptCnum").value for k in completed}
        eligible = [k for k in completed if counts[k] is not None and counts[k] <= cap]
        chosen: list[OpeningUnitKey] = []
        for key in ([completed[0], completed[-1]] if completed else []):
            if key in eligible and key not in chosen:
                chosen.append(key)
        for key in sorted(eligible, key=lambda k: (counts[k], k)):
            if key not in chosen:
                chosen.append(key)
        chosen = chosen[:limit]
        skipped = []
        for key in completed:
            if key in chosen:
                continue
            if counts[key] is None:
                reason = "공식 참가업체수가 없어 명부 완전성 비교 불가"
            elif counts[key] > cap:
                reason = f"공식 참가업체수 {counts[key]} > 페이지 한도 {cap}"
            else:
                reason = f"표본당 명부 단위 상한 {limit}"
            skipped.append({"unit": [key.bid_ntce_no, key.bid_ntce_ord, key.bid_clsfc_no, key.rbid_no],
                            "official_prtcpt_cnum": counts[key], "reason": reason})
        return chosen, skipped

    def step_roster(self, sample: Sample, unit: OpeningUnitKey) -> StepOutcome:
        ps = self.plan.per_sample
        col = collect_all_pages(
            self.client, SERVICE_AWARD, "getOpengResultListInfoOpengCompt",
            {"bidNtceNo": unit.bid_ntce_no, "bidNtceOrd": unit.bid_ntce_ord, "bidClsfcNo": unit.bid_clsfc_no,
             "rbidNo": unit.rbid_no},
            num_of_rows=int(ps.get("roster_num_of_rows", 100)), max_pages=int(ps.get("roster_max_pages", 5)),
            key_fn=lambda i: (str(i.get("prcbdrBizno") or "").strip() or None),
        )
        sample.roster_items[unit] = list(col.items)
        official = official_count_from_item(sample.opening_items.get(unit), Outcome.SUCCESS)
        roster = roster_counts(col.items, queried=True, query_ok=col.query_ok, complete=col.complete,
                               confirmed_empty=col.confirmed_empty)
        comparison, detail = compare_official_to_roster(official, roster)
        other_key_rows = 0
        for item in col.items:
            extraction = opening_key(item)
            if extraction.key is not None and extraction.key != unit:
                other_key_rows += 1
        ranks = [str(i.get("opengRank") or "").strip() for i in col.items]
        summary = collection_summary(col)
        summary.update({
            "unit": [unit.bid_ntce_no, unit.bid_ntce_ord, unit.bid_clsfc_no, unit.rbid_no],
            "official_prtcpt_cnum": official.value, "official_status": official.status.value,
            "roster_status": roster.status.value, "roster_row_count": roster.row_count,
            "roster_unique_bizno": roster.unique_bizno, "roster_blank_bizno_rows": roster.blank_bizno_rows,
            "count_comparison": comparison, "count_comparison_detail": detail,
            "rows_with_other_unit_key": other_key_rows,
            "rank_blank_rows": sum(1 for r in ranks if not r),
            "rank_duplicate_values": sum(c - 1 for c in Counter(r for r in ranks if r).values() if c > 1),
            "rmrk_values": dict(Counter(str(i.get("rmrk") or "") for i in col.items)),
            "opengRsltDivNm_values": dict(Counter(str(i.get("opengRsltDivNm") or "") for i in col.items)),
            "note": "명부 행의 유효/무효/공동수급 표현은 문서에 없다. 비고(rmrk) 값 분포로만 관측한다.",
        })
        self._upsert_opening_unit(sample, unit, official, roster, comparison, col)
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def _upsert_opening_unit(self, sample: Sample, unit: OpeningUnitKey, official: Any, roster: Any,
                             comparison: str | None, col: PageCollection | None) -> None:
        link = link_openings_to_notices(sample.notice_keys, [unit])
        notice_row = self.conn.execute(
            "SELECT id FROM verify_notice_revision WHERE run_id = ? AND bid_ntce_no = ? AND bid_ntce_ord = ?",
            (self.run_id, unit.bid_ntce_no, unit.bid_ntce_ord),
        ).fetchone()
        item = sample.opening_items.get(unit) or {}
        self.conn.execute(
            """
            INSERT INTO verify_opening_unit (run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no, progrs_div_cd_nm,
                openg_dt_raw, official_prtcpt_cnum, official_prtcpt_cnum_status, roster_row_count, roster_unique_bizno,
                roster_status, count_comparison, linked_notice_revision_id, link_status, source_response_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, bid_ntce_no, bid_ntce_ord, bid_clsfc_no, rbid_no) DO UPDATE SET
                roster_row_count = excluded.roster_row_count, roster_unique_bizno = excluded.roster_unique_bizno,
                roster_status = excluded.roster_status, count_comparison = excluded.count_comparison
            """,
            (self.run_id, unit.bid_ntce_no, unit.bid_ntce_ord, unit.bid_clsfc_no, unit.rbid_no,
             item.get("progrsDivCdNm"), item.get("opengDt"), official.value,
             official.status.value if official.status.value in {"OBSERVED", "BLANK_IN_RESPONSE", "FIELD_ABSENT", "INVALID", "NOT_QUERIED", "QUERY_FAILED"} else "NOT_QUERIED",
             roster.row_count, roster.unique_bizno, roster.status.value, comparison,
             notice_row["id"] if notice_row else None, link.status_of(unit),
             col.source_response_ids[0] if col and col.source_response_ids else None),
        )

    def record_unqueried_units(self, sample: Sample, queried: set[OpeningUnitKey]) -> None:
        for unit, item in sample.opening_items.items():
            if unit in queried:
                continue
            official = official_count_from_item(item, Outcome.SUCCESS)
            roster = roster_counts(None, queried=False, query_ok=False, complete=False, confirmed_empty=False)
            self._upsert_opening_unit(sample, unit, official, roster, None, None)

    def step_award(self, sample: Sample) -> StepOutcome:
        col = collect_all_pages(self.client, SERVICE_AWARD, "getScsbidListSttusCnstwk",
                                {"inqryDiv": "4", "bidNtceNo": sample.bid_ntce_no}, num_of_rows=50, max_pages=2)
        rows = []
        for item in col.items:
            extraction = opening_key(item)
            unit = extraction.key
            rank1 = None
            if unit is not None and unit in sample.roster_items:
                rank1_rows = [r for r in sample.roster_items[unit] if str(r.get("opengRank") or "").strip() == "1"]
                if len(rank1_rows) == 1:
                    rank1 = str(rank1_rows[0].get("prcbdrBizno") or "").strip()
            winner = str(item.get("bidwinnrBizno") or "").strip()
            opening_item = sample.opening_items.get(unit) if unit else None
            rows.append({
                "key": [unit.bid_ntce_no, unit.bid_ntce_ord, unit.bid_clsfc_no, unit.rbid_no] if unit else None,
                "key_error": None if unit else {"missing": extraction.missing_fields, "blank": extraction.blank_fields},
                "sucsfbidAmt_status": parse_krw_amount(item, "sucsfbidAmt").status.value,
                "sucsfbidRate": item.get("sucsfbidRate"), "rlOpengDt": item.get("rlOpengDt"),
                "fnlSucsfDate": item.get("fnlSucsfDate"),
                "prtcptCnum_raw": item.get("prtcptCnum"),
                "prtcptCnum_equals_opening_list": (None if opening_item is None
                                                    else item.get("prtcptCnum") == opening_item.get("prtcptCnum")),
                "winner_equals_roster_rank1": (None if (rank1 is None or not winner) else winner == rank1),
                "unit_in_opening_list": unit in sample.opening_items if unit else False,
            })
        summary = collection_summary(col)
        summary.update({"rows_detail": rows,
                        "note": "개찰순위 1위와 최종낙찰자를 같은 것으로 가정하지 않는다. 비교 결과만 기록한다."})
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_rebid_failing(self, sample: Sample, operation: str) -> StepOutcome:
        target = "재입찰" if operation == "getOpengResultListInfoRebid" else "유찰"
        units = sorted(k for k, i in sample.opening_items.items() if str(i.get("progrsDivCdNm") or "") == target)
        params = {"bidNtceNo": sample.bid_ntce_no}
        if units and operation == "getOpengResultListInfoFailing":
            params["bidClsfcNo"] = units[0].bid_clsfc_no  # 포털 Swagger는 필수, 참고자료는 옵션으로 표기
        col = collect_all_pages(self.client, SERVICE_AWARD, operation, params, num_of_rows=50, max_pages=2)
        reason_field = "rbidRsn" if target == "재입찰" else "nobidRsn"
        summary = collection_summary(col)
        summary.update({
            "query": params,
            "rows_detail": [{"key": [i.get("bidNtceNo"), i.get("bidNtceOrd"), i.get("bidClsfcNo"), i.get("rbidNo")],
                             reason_field: i.get(reason_field), "opengRsltDivNm": i.get("opengRsltDivNm")}
                            for i in col.items],
            "note": "유찰·재입찰은 사유별 보조 관측이다. 경쟁이 낮다는 근거로 쓰지 않는다.",
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    def step_change_history(self, sample: Sample) -> StepOutcome:
        col = collect_all_pages(self.client, SERVICE_BID, "getBidPblancListInfoChgHstryCnstwk",
                                {"inqryDiv": "2", "bidNtceNo": sample.bid_ntce_no}, num_of_rows=100, max_pages=2)
        summary = collection_summary(col)
        summary.update({
            "chgItemNm_values": dict(Counter(str(i.get("chgItemNm") or "") for i in col.items)),
            "chgDataDivNm_values": dict(Counter(str(i.get("chgDataDivNm") or "") for i in col.items)),
            "keys": sorted({(str(i.get("bidNtceOrd")), str(i.get("bidClsfcNo")), str(i.get("rbidNo"))) for i in col.items}),
        })
        return StepOutcome(status_from_outcome(col.final_outcome, bool(col.items)), summary, col.final_outcome)

    # -------------------------------------------------------------- 전체 실행

    def run(self) -> dict[str, Any]:
        self.run_step("industry:lookup", SERVICE_INDUSTRY, "getIndstrytyBaseLawrgltInfoList", self.step_industry_lookup)

        samples: list[Sample] = [Sample(no, label, "fixed") for no, label in self.plan.fixed_notices]
        picked_by_window: list[tuple[DiscoveryWindow, list[dict[str, Any]]]] = []
        for window in self.plan.windows:
            out = self.run_step(f"discovery:{window.label}", SERVICE_BID, "getBidPblancListInfoCnstwkPPSSrch",
                                lambda w=window: self.step_discovery(w))
            if out is not None and out.reused and self.raw_items_reader is not None:
                # 재사용한 탐색 결과는 저장된 원본에서 현재 선정 규칙으로 다시 고른다(추가 호출 없음).
                ids = out.summary.get("source_response_ids") or []
                raw_items = self.raw_items_reader(ids[0]) if ids else None
                if raw_items is not None:
                    out.summary["picked"] = self._pick(raw_items)
                    out.summary["repicked_from_raw"] = True
                    self.runs.record_step(run_id=self.run_id, step_key=f"discovery:{window.label}", status="DONE",
                                          service_id=SERVICE_BID, operation="getBidPblancListInfoCnstwkPPSSrch",
                                          calls_used=0, summary=out.summary)
            if out is not None:
                picked_by_window.append((window, list(out.summary.get("picked") or [])))
        # 예산이 먼저 떨어져도 연도별 표본이 고르게 남도록 창별 1건씩 번갈아 배치한다.
        rounds = max((len(p) for _, p in picked_by_window), default=0)
        for index in range(rounds):
            for window, picked in picked_by_window:
                if index >= len(picked):
                    continue
                no = str(picked[index].get("bid_ntce_no"))
                if no and all(s.bid_ntce_no != no for s in samples):
                    samples.append(Sample(no, f"탐색 {window.label}", f"discovery:{window.label}"))
        samples = samples[: self.plan.max_samples]
        self.samples = samples
        ps = self.plan.per_sample

        for sample in samples:
            sk = sample.key
            outcomes: dict[str, StepOutcome | None] = {}
            outcomes["notice"] = self.run_step(f"sample:{sk}:notice", SERVICE_BID, "getBidPblancListInfoCnstwk",
                                               lambda s=sample: self.step_notice(s), sample_key=sk)
            if outcomes["notice"] is not None and outcomes["notice"].reused:
                self._restore_notice(sample, outcomes["notice"].summary)
            outcomes["license"] = self.run_step(f"sample:{sk}:license", SERVICE_BID, "getBidPblancListInfoLicenseLimit",
                                                lambda s=sample: self.step_license(s), sample_key=sk)
            outcomes["region"] = self.run_step(f"sample:{sk}:region", SERVICE_BID, "getBidPblancListInfoPrtcptPsblRgn",
                                               lambda s=sample: self.step_region(s), sample_key=sk)
            outcomes["basis_amount"] = self.run_step(f"sample:{sk}:basis_amount", SERVICE_BID,
                                                     "getBidPblancListInfoCnstwkBsisAmount",
                                                     lambda s=sample: self.step_basis_amount(s), sample_key=sk)
            if ps.get("include_mfrc_eval", True):
                outcomes["mfrc_eval"] = self.run_step(f"sample:{sk}:mfrc_eval", SERVICE_BID,
                                                      "getBidPblancListEvaluationIndstrytyMfrcInfo",
                                                      lambda s=sample: self.step_mfrc_eval(s), sample_key=sk)
            amended = any(("변경" in str(i.get("ntceKindNm") or "")) or ("취소" in str(i.get("ntceKindNm") or ""))
                          for i in sample.notice_items)
            if ps.get("include_change_history_when_amended", True) and amended:
                outcomes["change_history"] = self.run_step(f"sample:{sk}:change_history", SERVICE_BID,
                                                           "getBidPblancListInfoChgHstryCnstwk",
                                                           lambda s=sample: self.step_change_history(s), sample_key=sk)
            outcomes["opening"] = self.run_step(f"sample:{sk}:opening", SERVICE_AWARD, "getOpengResultListInfoCnstwk",
                                                lambda s=sample: self.step_opening(s), sample_key=sk)
            if outcomes["opening"] is not None and outcomes["opening"].reused:
                self._restore_opening(sample, outcomes["opening"].summary)
            queried: set[OpeningUnitKey] = set()
            chosen_units, skipped_units = self.roster_selection(sample)
            if skipped_units:
                self._record(f"sample:{sk}:roster_selection", "SKIPPED", service_id=SERVICE_AWARD,
                             operation="getOpengResultListInfoOpengCompt", sample_key=sk, calls=0,
                             summary={"skipped_units": skipped_units,
                                      "note": "명부를 받지 않은 개찰완료 단위(예산 보호). 참여수는 NOT_QUERIED로 남는다."})
            for unit in chosen_units:
                step_key = f"sample:{sk}:roster:{unit.bid_ntce_ord}-{unit.bid_clsfc_no}-{unit.rbid_no}"
                out = self.run_step(step_key, SERVICE_AWARD, "getOpengResultListInfoOpengCompt",
                                    lambda s=sample, u=unit: self.step_roster(s, u), sample_key=sk)
                if out is not None and out.calls > 0:
                    queried.add(unit)
            self.record_unqueried_units(sample, queried)
            outcomes["award"] = self.run_step(f"sample:{sk}:award", SERVICE_AWARD, "getScsbidListSttusCnstwk",
                                              lambda s=sample: self.step_award(s), sample_key=sk)
            if ps.get("include_rebid_failing_when_present", True):
                statuses = {str(i.get("progrsDivCdNm") or "") for i in sample.opening_items.values()}
                if "재입찰" in statuses:
                    self.run_step(f"sample:{sk}:rebid", SERVICE_AWARD, "getOpengResultListInfoRebid",
                                  lambda s=sample: self.step_rebid_failing(s, "getOpengResultListInfoRebid"),
                                  sample_key=sk)
                if "유찰" in statuses:
                    self.run_step(f"sample:{sk}:failing", SERVICE_AWARD, "getOpengResultListInfoFailing",
                                  lambda s=sample: self.step_rebid_failing(s, "getOpengResultListInfoFailing"),
                                  sample_key=sk)
            sample.summary = {name: (o.status if o else "NOT_RUN") for name, o in outcomes.items()}
        return self.build_summary()

    def _restore_notice(self, sample: Sample, summary: dict[str, Any]) -> None:
        for rev in summary.get("revisions") or []:
            if "bid_ntce_ord" in rev:
                sample.notice_keys.add(NoticeRevisionKey(sample.bid_ntce_no, str(rev["bid_ntce_ord"])))

    def _restore_opening(self, sample: Sample, summary: dict[str, Any]) -> None:
        for unit in summary.get("units") or []:
            key = unit.get("key")
            if key:
                okey = OpeningUnitKey(*[str(k) for k in key])
                sample.opening_items[okey] = {"progrsDivCdNm": unit.get("progrsDivCdNm"),
                                              "prtcptCnum": unit.get("prtcptCnum_raw"), "opengDt": unit.get("opengDt")}

    # -------------------------------------------------------------- 요약

    def build_summary(self) -> dict[str, Any]:
        status_counts = Counter(s["status"] for s in self.steps)
        steps_by_key = {s["step_key"]: s for s in self.steps}
        core_links = []
        for sample in self.samples:
            sk = sample.key
            notice_ok = steps_by_key.get(f"sample:{sk}:notice", {}).get("status") == "DONE"
            license_rows = steps_by_key.get(f"sample:{sk}:license", {}).get("status") == "DONE"
            region_rows = steps_by_key.get(f"sample:{sk}:region", {}).get("status") == "DONE"
            linked_units = self.conn.execute(
                "SELECT COUNT(*) FROM verify_opening_unit WHERE run_id = ? AND bid_ntce_no = ? AND link_status = 'LINKED'",
                (self.run_id, sk),
            ).fetchone()[0]
            core_links.append({"sample": sk, "source": sample.source, "notice_rows": notice_ok,
                               "license_rows": license_rows, "region_rows": region_rows,
                               "linked_opening_units": int(linked_units),
                               "core_link_confirmed": bool(notice_ok and license_rows and region_rows and linked_units)})
        comparisons = Counter(
            r[0] for r in self.conn.execute(
                "SELECT COALESCE(count_comparison, 'NOT_QUERIED') FROM verify_opening_unit WHERE run_id = ?", (self.run_id,)
            )
        )
        return {
            "run_id": self.run_id,
            "generated_at_kst": now_kst().isoformat(timespec="seconds"),
            "calls_used_run": self.client.budget.run_used,
            "stop_reason": self.stop_reason,
            "blocked_services": self.blocked_services,
            "step_status_counts": dict(status_counts),
            "industry_matches": self.industry_matches,
            "discovery": self.discovery,
            "samples": [{"bid_ntce_no": s.bid_ntce_no, "label": s.label, "source": s.source, "steps": s.summary}
                        for s in self.samples],
            "core_links": core_links,
            "any_core_link_confirmed": any(c["core_link_confirmed"] for c in core_links),
            "count_comparisons": dict(comparisons),
            "steps": self.steps,
        }


def _corp_info_format(raw: str | None) -> str:
    if raw is None:
        return "ABSENT"
    text = raw.strip()
    if not text:
        return "BLANK"
    if "다수" in text:
        return "MULTIPLE_WINNER_TEXT"
    parts = text.split("^")
    return f"CARET_{len(parts)}_PARTS"


def final_run_status(runner: VerifyRunner) -> str:
    statuses = [s["status"] for s in runner.steps]
    if not statuses:
        return "FAILED"
    if runner.stop_reason:
        return "PARTIAL"
    executed = [s for s in statuses if s in {"DONE", "DONE_EMPTY", "FAILED"}]
    if not executed and all(s in {"BLOCKED", "NOT_RUN_SERVICE_BLOCKED"} for s in statuses):
        return "BLOCKED"
    if any(s in {"FAILED", "BLOCKED", "NOT_RUN_SERVICE_BLOCKED", "NOT_RUN_BUDGET", "NOT_RUN_QUOTA"} for s in statuses):
        return "PARTIAL"
    return "COMPLETED"


def write_reports(report_dir: Path, summary: dict[str, Any]) -> tuple[Path, Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    run_id = summary["run_id"]
    json_path = report_dir / f"verify-{run_id}.json"
    md_path = report_dir / f"verify-{run_id}.md"
    json_path.write_text(redact(json.dumps(summary, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    lines = [
        f"# verify-api 실연동 검증 결과 ({run_id})",
        "",
        "이 파일은 실데이터를 포함하므로 Git 제외 경로(.local)에만 둔다. 표본 결과이며 전수·전기간 완전성 검증이 아니다.",
        "",
        f"- 생성시각(KST): {summary.get('generated_at_kst')}",
        f"- 실제 HTTP 시도 수(재시도 포함): {summary.get('calls_used_run')}",
        f"- 중단 사유: {summary.get('stop_reason') or '없음'}",
        f"- 차단된 서비스: {summary.get('blocked_services') or '없음'}",
        f"- 단계 상태 집계: {summary.get('step_status_counts')}",
        f"- 핵심 연결(공고+면허+지역+개찰단위 복합키 연결) 확인 표본 존재: {summary.get('any_core_link_confirmed')}",
        f"- 공식 참가업체수와 명부 비교: {summary.get('count_comparisons')}",
        "",
        "## 업종코드 조회 결과(조회시점 현재값)",
        "",
    ]
    matches = summary.get("industry_matches") or []
    if matches:
        lines += ["| indstrytyCd | indstrytyNm | 사용여부 |", "|---|---|---|"]
        lines += [f"| {m.get('indstrytyCd')} | {m.get('indstrytyNm')} | {m.get('indstrytyUseYn')} |" for m in matches]
    else:
        lines.append("일치 항목 없음 또는 조회 실패")
    lines += ["", "## 표본별 핵심 연결", "", "| 표본 | 출처 | 공고 | 면허 | 지역 | 연결된 개찰단위 | 핵심연결 |",
              "|---|---|---|---|---|---|---|"]
    for c in summary.get("core_links") or []:
        lines.append(f"| {c['sample']} | {c['source']} | {c['notice_rows']} | {c['license_rows']} | {c['region_rows']} | "
                     f"{c['linked_opening_units']} | {c['core_link_confirmed']} |")
    lines += ["", "## 단계 기록", "", "| 단계 | 상태 | 호출 | 결과 |", "|---|---|---|---|"]
    for s in summary.get("steps") or []:
        lines.append(f"| {s['step_key']} | {s['status']} | {s['calls']} | {s.get('outcome') or s.get('note') or ''} |")
    md_path.write_text(redact("\n".join(lines) + "\n"), encoding="utf-8")
    return md_path, json_path
