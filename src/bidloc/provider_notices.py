"""Read-only view of staged provider notices (K-apt, LH, K-water, D2B) for the search screen.

Each provider keeps its own meaning. Participation regions, site location and the agency office stay
in separate fields, every amount keeps its own label, and a condition a provider does not publish is
reported as "확인 필요" instead of silently passing or failing. Rows never enter the G2B analysis tables.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from bidloc.analysis import parse_datetime, region_matches
from bidloc.provider_probe import DETAIL_KEYS
from bidloc.timeutil import KST, now_kst

SOURCE_LABELS = {"kapt": "K-apt 공동주택", "lh": "LH", "kwater": "K-water", "d2b": "국방조달(D2B)"}
# K-apt bidArea is documented as the 법정동 시·도 code; old and new province codes map to current names.
SIDO_CODES = {"11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시", "29": "광주광역시",
              "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시", "41": "경기도", "42": "강원특별자치도",
              "43": "충청북도", "44": "충청남도", "45": "전북특별자치도", "46": "전라남도", "47": "경상북도",
              "48": "경상남도", "50": "제주특별자치도", "51": "강원특별자치도", "52": "전북특별자치도"}
SIDO_NAMES = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
              "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
              "경기": "경기도", "강원": "강원특별자치도", "강원도": "강원특별자치도", "충북": "충청북도",
              "충남": "충청남도", "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전남": "전라남도",
              "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도", "제주도": "제주특별자치도"}
# Licence 4992 is 도장·습식·방수·석공사업. Name matching is only used where a provider gives names, not codes.
LICENSE_4992_TERMS = ("도장", "습식", "방수", "석공")
# Title words for the licence-4992 kind of work; used only to shortlist providers that publish no licence data.
TITLE_TERMS_4992 = ("방수", "도장", "도색", "페인트", "습식", "석공", "외벽", "옥상", "균열", "크랙",
                    "실링", "코킹", "차선", "노면")
KAPT_KIND = {"1": "신규공고", "2": "수정공고", "3": "재공고"}
KAPT_CONTRACT = {"01": "일반경쟁", "02": "제한경쟁", "03": "지명경쟁"}
# Observed only: the K-apt Swagger says "코드정의서 참조" and no code table is published.
KAPT_BUSINESS = {"01": "용역", "02": "공사", "03": "용역", "04": "물품", "05": "기타"}
LINK_HOSTS = {"www.k-apt.go.kr"}
# Providers that publish no licence or participation-region data at all.
NO_CONDITION_SOURCES = {"kapt", "kwater"}


def store_path(database_path):
    return Path(database_path).parent / "providers" / "notices.sqlite3"


def clean(value):
    text = str(value if value is not None else "").strip()
    return text or None


def canonical_region(name):
    parts = (clean(name) or "").split()
    if parts:
        parts[0] = SIDO_NAMES.get(parts[0], parts[0])
    return " ".join(parts)


def hq_matches(hq, region):
    return region_matches(canonical_region(hq), canonical_region(region))


def to_int(value):
    text = re.sub(r"[,\s]", "", clean(value) or "")
    return int(text) if text.isdigit() else None


def iso_date(value):
    digits = re.sub(r"\D", "", clean(value) or "")[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def iso_datetime(value):
    digits = re.sub(r"\D", "", clean(value) or "")
    for size, fmt in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M")):
        if len(digits) >= size:
            try:
                return datetime.strptime(digits[:size], fmt).isoformat()
            except ValueError:
                continue
    return None


def title_candidate(title):
    return any(term in (title or "") for term in TITLE_TERMS_4992)


def base_row(source, identity, source_response_id, collected_at, snapshot):
    return {"key": source + ":" + "|".join(identity), "source": source, "source_label": SOURCE_LABELS[source],
            "notice_no": None, "version": None, "title": None, "agency": None, "business": "미분류",
            "business_basis": None, "notice_date": None, "deadline": None, "deadline_label": None,
            "amount": None, "amount_label": "추정가격", "amounts": {}, "allowed_regions": None,
            "allowed_regions_basis": "참가지역 정보 미제공", "site_region": None, "site_region_label": None,
            "office": None, "licenses": None, "license_text": None, "license_basis": "면허 정보 미제공",
            "contract": None, "cancelled": False, "progress": None, "url": None, "url_label": None,
            "flags": [], "source_response_id": source_response_id, "detail_response_id": None,
            "collected_at": collected_at, "snapshot": snapshot, "versions_observed": 1}


def kapt_row(item, *meta):
    row = base_row("kapt", (clean(item.get("codeAuth")) or "", clean(item.get("bidNum")) or ""), *meta)
    business = KAPT_BUSINESS.get(clean(item.get("codeClassifyType2")) or "", "미분류")
    seq = (clean(item.get("bidFileSeq")) or "").split(",")[0].strip()
    row.update(notice_no=clean(item.get("bidNum")), version=KAPT_KIND.get(clean(item.get("bidState")) or ""),
               title=clean(item.get("bidTitle")), agency=clean(item.get("bidKaptname")), business=business,
               business_basis="분류코드 관측 기반 추정(코드정의서 미공개)", notice_date=iso_date(item.get("bidRegDate")),
               deadline=iso_datetime(item.get("bidDeadline")), deadline_label="입찰 마감",
               site_region=SIDO_CODES.get(clean(item.get("bidArea")) or ""), site_region_label="단지 소재 시·도",
               contract=KAPT_CONTRACT.get(clean(item.get("codeKind")) or ""),
               url=f"http://www.k-apt.go.kr/bid/bidFileDownload.do?file_type=bid&file_num={seq}" if seq.isdigit() else None,
               url_label="K-apt 첨부 다운로드" if seq.isdigit() else None)
    row["amount_label"] = "금액 정보 없음"
    row["flags"].append("공사·용역 분류는 추정")
    return row


def lh_row(item, *meta):
    row = base_row("lh", (clean(item.get("bidNum")) or "",), *meta)
    zones = [canonical_region(item.get(f"zoneRstrct{i}")) for i in range(1, 5) if clean(item.get(f"zoneRstrct{i}"))]
    groups, names = [], []
    for g in range(1, 11):
        group = [clean(item.get(f"req{g}Reqlic{n}Nm")) for n in range(1, 11)]
        group = [n for n in group if n]
        if group:
            names += group
            kind = " / ".join(x for x in (clean(item.get(f"req{g}LicGbNm")), clean(item.get(f"req{g}MvgbNm"))) if x)
            groups.append(f"업종{g}" + (f"({kind})" if kind else "") + ": " + ", ".join(group)
                          + (f" — {clean(item.get(f'req{g}LicctNm'))}" if clean(item.get(f"req{g}LicctNm")) else ""))
    business = {"시설공사": "공사", "용역": "용역", "물품": "물품", "지급자재": "물품"}.get(clean(item.get("cstrtnJobGbNm")) or "", "미분류")
    contract = clean(item.get("tndrCtrctMedCd"))
    row.update(notice_no=clean(item.get("bidNum")), version=" ".join(x for x in (clean(item.get("bidDegree")),
               clean(item.get("bidKind"))) if x) or None, title=clean(item.get("bidnmKor")),
               agency="LH " + (clean(item.get("zoneHqCd")) or "").replace(" ", ""), business=business,
               business_basis="업무구분(cstrtnJobGbNm)", notice_date=iso_date(item.get("tndrbidRegDt")),
               deadline=iso_datetime(item.get("tndrdocAcptEndDtm")), deadline_label="입찰서 접수 마감",
               amount=to_int(item.get("presmtPrc")), amounts={"추정가격": to_int(item.get("presmtPrc")),
               "기초금액": to_int(item.get("fdmtlAmt")), "설계가격": to_int(item.get("designPrc"))},
               allowed_regions=zones, office=clean(item.get("zoneHqCd")), licenses=names,
               license_text="\n".join(groups) or None, contract=contract,
               cancelled=clean(item.get("bidKind")) == "취소공고", progress=clean(item.get("bidProgrsStatus")))
    row["allowed_regions_basis"] = "참가지역(zoneRstrct1~4)" if zones else (
        "지역제한 계약인데 참가지역 미표시" if contract == "지역제한" else "참가지역 표시 없음")
    row["license_basis"] = "요구면허(req…Reqlic…Nm)" if names else "요구면허 표시 없음"
    row["site_region_label"] = "현장 위치 정보 없음(담당 본부는 현장이 아님)"
    return row


def kwater_row(item, *meta):
    row = base_row("kwater", (clean(item.get("tndrPbanno")) or "",), *meta)
    end = iso_date(item.get("tndrPblancEnddt"))
    amount = to_int(item.get("tndrPlnprc"))
    row.update(notice_no=clean(item.get("tndrPbanno")), title=clean(item.get("tndrPblancNm")),
               agency="K-water " + (clean(item.get("cntrctDeptNm")) or ""), business="공사",
               business_basis="공사 전용 오퍼레이션(cntrwkList)", notice_date=iso_date(item.get("tndrPblancDe")),
               # Only a date is published; the end of that day is used and labelled as such.
               deadline=(end + "T23:59:00") if end else None, deadline_label="신청 마감일(시각 미제공)",
               amounts={"금액(의미 미확인)": amount or None}, contract=clean(item.get("ctrmthdCdNm")),
               progress=clean(item.get("tndrStat")))
    row["amount_label"] = "추정가격 정보 없음"
    if amount == 0:
        row["flags"].append("금액 0 표시는 미제공으로 처리")
    return row


def d2b_row(item, detail, *meta):
    identity = tuple(clean(item.get(k)) or "" for k in ("pblancYear", "pblancNo", "cntrwkNo", "orntCode"))
    row = base_row("d2b", identity, *meta)
    odr = clean(item.get("pblancOdr"))
    row.update(notice_no=clean(item.get("pblancNo")),
               version=" ".join(x for x in (f"{odr}차" if odr else None, clean(item.get("pblancSe"))) if x) or None,
               title=clean(item.get("cntrwkNm")), agency=clean(item.get("ornt")),
               business={"공사": "공사", "용역": "용역"}.get(clean(item.get("busiDivs")) or "", "미분류"),
               business_basis="업무구분(busiDivs)", notice_date=iso_date(item.get("pblancDate")),
               deadline=iso_datetime(item.get("biddocPresentnClosDt")), deadline_label="입찰서 제출 마감",
               amounts={"기초예비가격": to_int(item.get("baseAmnt"))}, contract=clean(item.get("cntrctMth")),
               cancelled=clean(item.get("pblancSe")) == "취소공고")
    if detail is None:
        row["allowed_regions_basis"] = row["license_basis"] = "상세 미수집"
        if row["business"] == "공사" and not row["cancelled"]:
            row["flags"].append("상세 미수집")
        return row
    payload, detail_id = detail
    areas = [canonical_region(m.group(1)) for m in re.finditer(r"\[\d+\]\s*([^\[\^|]+)", payload.get("areaLmttList") or "")]
    licenses = [f"{m.group(1)} {m.group(2).strip()}" for m in re.finditer(r"\[(\d+)\]\s*([^\[\^|]+)", payload.get("lcnsLmttList") or "")]
    row.update(detail_response_id=detail_id, allowed_regions=[a for a in areas if a], licenses=licenses,
               license_text=clean(payload.get("lcnsLmttList")), site_region=clean(payload.get("lc")),
               site_region_label="공사 위치(lc, 의미 미확인)", amount=to_int(payload.get("estmPrce")))
    row["amounts"].update({"추정가격": to_int(payload.get("estmPrce")), "예산금액": to_int(payload.get("budgetAmount"))})
    row["allowed_regions_basis"] = "지역제한목록(areaLmttList)" if row["allowed_regions"] else "지역제한 표시 없음"
    row["license_basis"] = "면허제한목록(lcnsLmttList) · 구분자 ^ | 의미(AND/OR) 미확인" if licenses else "면허제한 표시 없음"
    return row


def version_rank(row, item):
    if row["source"] == "lh":
        return to_int(item.get("bidDegree")) or 0
    if row["source"] == "d2b":
        return to_int(item.get("pblancOdr")) or 0
    return 0


def load_provider_notices(path):
    """Latest observed version per notice, plus per-source collection status. Opens the store read-only."""
    path = Path(path)
    meta = {"status": "NOT_COLLECTED", "sources": {}, "generated_at_kst": now_kst().isoformat()}
    if not path.is_file():
        return [], meta
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"collection_job", "notice_observation"} <= tables:
            return [], meta
        has_snapshot = "snapshot" in {r[1] for r in conn.execute("PRAGMA table_info(collection_job)")}
        details = {}
        if "notice_detail" in tables:
            for detail_key, payload, sid in conn.execute(
                    "SELECT detail_key, payload_json, source_response_id FROM notice_detail WHERE provider='d2b'"):
                keys = json.loads(detail_key)
                details[tuple(keys.get(k, "") for k in DETAIL_KEYS["d2b"])] = (json.loads(payload), sid)
        jobs = [dict(zip(("provider", "status", "received", "total_count", "updated_at", "snapshot"), r)) for r in conn.execute(
            "SELECT provider, status, received, total_count, updated_at, " + ("snapshot" if has_snapshot else "NULL")
            + " FROM collection_job ORDER BY updated_at")]
        observations = conn.execute(
            "SELECT o.provider, o.payload_json, o.source_response_id, j.updated_at, "
            + ("j.snapshot" if has_snapshot else "NULL") + " FROM notice_observation o JOIN collection_job j "
            "ON j.job_id=o.job_id ORDER BY j.updated_at, o.job_id, o.page_no, o.row_no").fetchall()
    finally:
        conn.close()
    latest, ranks, versions = {}, {}, set()
    for provider, payload, sid, collected_at, snapshot in observations:
        item = json.loads(payload)
        meta_args = (sid, collected_at, snapshot)
        if provider == "kapt":
            row = kapt_row(item, *meta_args)
        elif provider == "lh":
            row = lh_row(item, *meta_args)
        elif provider == "kwater":
            row = kwater_row(item, *meta_args)
        elif provider == "d2b":
            row = d2b_row(item, details.get(tuple(clean(item.get(k)) or "" for k in DETAIL_KEYS["d2b"])), *meta_args)
        else:
            continue
        rank = version_rank(row, item)
        versions.add((row["key"], rank))
        # A later observation of the same or a newer version replaces the earlier one; older versions never do.
        if row["key"] not in latest or rank >= ranks[row["key"]]:
            latest[row["key"]], ranks[row["key"]] = row, rank
    per_key = Counter(key for key, _ in versions)
    for key, row in latest.items():
        row["versions_observed"] = per_key[key]
    rows = list(latest.values())
    for source in SOURCE_LABELS:
        source_jobs = [j for j in jobs if j["provider"] == source]
        if source_jobs:
            last = source_jobs[-1]
            meta["sources"][source] = {
                "label": SOURCE_LABELS[source], "notices": sum(r["source"] == source for r in rows),
                "last_collected_at": last["updated_at"], "last_snapshot": last["snapshot"], "last_status": last["status"],
                "last_received": last["received"], "last_total": last["total_count"], "jobs": len(source_jobs),
                "details": sum(1 for r in rows if r["source"] == source and r["detail_response_id"] is not None)}
    meta["status"] = "COLLECTED" if rows else "EMPTY"
    return rows, meta


def evaluate(row, hq):
    licenses = row["licenses"]
    if licenses is None or not licenses:
        license_state = ("확인 필요", row["license_basis"])
    elif any(x.startswith("4992") or any(t in x for t in LICENSE_4992_TERMS) for x in licenses):
        license_state = ("포함", row["license_basis"])
    else:
        license_state = ("불충족", "요구 면허에 4992 없음")
    regions = row["allowed_regions"]
    if not hq:
        region_state = ("본점 미선택", "")
    elif regions is None or not regions:
        region_state = ("확인 필요", row["allowed_regions_basis"])
    elif any(hq_matches(hq, r) for r in regions):
        region_state = ("충족", "참가지역: " + " · ".join(regions))
    else:
        region_state = ("불충족", "참가지역: " + " · ".join(regions))
    if "불충족" in (license_state[0], region_state[0]):
        overall = "불충족"
    elif license_state[0] == "포함" and region_state[0] in ("충족", "본점 미선택"):
        overall = "조건 일치"
    else:
        overall = "확인 필요"
    return {"overall": overall, "license": license_state[0], "license_basis": license_state[1],
            "region": region_state[0], "region_basis": region_state[1]}


def search_provider_notices(rows, filters, *, sources=None, include_unmatched=False, title_shortlist=True, now=None):
    """Apply the screen's conditions. Returns (rows with evaluation, counts of rows held back and why)."""
    filters.validate()
    current = now or now_kst()
    terms = filters.query.casefold().split()
    selected, held_back = [], Counter()
    for row in rows:
        if sources is not None and row["source"] not in sources:
            continue
        # The provider view is about construction work; services and goods stay out of it.
        if row["business"] not in ("공사", "미분류"):
            continue
        published = row["notice_date"]
        if filters.begin and (not published or published < filters.begin):
            continue
        if filters.end and (not published or published > filters.end):
            continue
        haystack = " ".join(str(row.get(k) or "") for k in ("title", "notice_no", "agency")).casefold()
        if not all(term in haystack for term in terms):
            continue
        evaluation = evaluate(row, filters.hq)
        if filters.scope == "4992":
            if evaluation["license"] == "불충족" and not include_unmatched:
                held_back["요구 면허에 4992 없음"] += 1
                continue
            if row["source"] in NO_CONDITION_SOURCES and title_shortlist and not title_candidate(row["title"]):
                held_back["면허 정보 없는 출처 · 제목 후보 아님"] += 1
                continue
        if evaluation["region"] == "불충족" and not include_unmatched:
            held_back["참가지역 불일치"] += 1
            continue
        if filters.contract and row["contract"] != filters.contract:
            continue
        amount = row["amount"]
        if (filters.min_amount is not None or filters.max_amount is not None) and amount is None:
            held_back["추정가격 미제공"] += 1
            continue
        if filters.min_amount is not None and amount < filters.min_amount:
            continue
        if filters.max_amount is not None and amount > filters.max_amount:
            continue
        deadline = parse_datetime(row["deadline"])
        if filters.status == "마감 전" and (row["cancelled"] or not (deadline and deadline > current)):
            continue
        if filters.status == "마감" and not (deadline and deadline <= current):
            continue
        if filters.status == "허용지역 미수집" and row["allowed_regions"]:
            continue
        if filters.status == "자료·차수 검토" and not (row["flags"] or row["cancelled"]):
            continue
        selected.append(dict(row, evaluation=evaluation))
    order = {"조건 일치": 0, "확인 필요": 1, "불충족": 2}
    if filters.sort == "추정가격 높은순":
        selected.sort(key=lambda r: (r["amount"] is not None, r["amount"] or 0), reverse=True)
    elif filters.sort == "마감 빠른순":
        far = datetime.max.replace(tzinfo=KST) - timedelta(days=1)
        selected.sort(key=lambda r: (parse_datetime(r["deadline"]) is None, parse_datetime(r["deadline"]) or far))
    else:
        selected.sort(key=lambda r: (r["notice_date"] or "", r["key"]), reverse=True)
    selected.sort(key=lambda r: order[r["evaluation"]["overall"]])
    return selected, held_back


def export_rows(rows):
    return [{"판정": r["evaluation"]["overall"], "출처": r["source_label"], "공고명": r["title"], "공고번호": r["notice_no"],
             "차수·상태": r["version"], "기관": r["agency"], "공고일": r["notice_date"], "마감": r["deadline"],
             "마감 기준": r["deadline_label"], "참가지역": r["allowed_regions"], "참가지역 근거": r["allowed_regions_basis"],
             "현장·소재 정보": r["site_region"], "현장·소재 정보 종류": r["site_region_label"], "요구 면허": r["license_text"],
             "면허 판정": r["evaluation"]["license"], "지역 판정": r["evaluation"]["region"],
             "추정가격": r["amount"], "금액(종류별)": r["amounts"], "계약방식": r["contract"], "취소": r["cancelled"],
             "확인 사항": r["flags"], "원본응답ID": r["source_response_id"], "상세응답ID": r["detail_response_id"],
             "수집시각": r["collected_at"]} for r in rows]
