"""Read-only view of staged provider notices (K-apt, LH, K-water, D2B, 누리장터, 남부발전, 한전) for the search screen.

Each provider keeps its own meaning. Participation regions, site location and the agency office stay
in separate fields, every amount keeps its own label, and a condition a provider does not publish is
reported as "확인 필요" instead of silently passing or failing. Rows never enter the G2B analysis tables.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bidloc.analysis import parse_datetime, region_matches
from bidloc.provider_probe import DETAIL_KEYS
from bidloc.timeutil import KST, now_kst

SOURCE_LABELS = {"kapt": "K-apt 공동주택", "lh": "LH", "kwater": "K-water", "d2b": "국방조달(D2B)",
                 "nuri": "누리장터(민간)", "kospo": "남부발전", "kepco": "한전"}
# K-apt bidArea is documented as the 법정동 시·도 code; old and new province codes map to current names.
SIDO_CODES = {"11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시", "29": "광주광역시",
              "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시", "41": "경기도", "42": "강원특별자치도",
              "43": "충청북도", "44": "충청남도", "45": "전북특별자치도", "46": "전라남도", "47": "경상북도",
              "48": "경상남도", "50": "제주특별자치도", "51": "강원특별자치도", "52": "전북특별자치도"}
SIDO_NAMES = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
              "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
              "경기": "경기도", "강원": "강원특별자치도", "강원도": "강원특별자치도", "충북": "충청북도",
              "충남": "충청남도", "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전남": "전라남도",
              "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도", "제주도": "제주특별자치도",
              # '서울시' style site text (누리장터). '광주시' is left out: it is also 경기도 광주시.
              "서울시": "서울특별시", "부산시": "부산광역시", "대구시": "대구광역시", "인천시": "인천광역시",
              "대전시": "대전광역시", "울산시": "울산광역시", "세종시": "세종특별자치시"}
# Licence 4992 is 도장·습식·방수·석공사업. Name matching is only used where a provider gives names, not codes.
LICENSE_4992_TERMS = ("도장", "습식", "방수", "석공")
# Title words for the licence-4992 kind of work; used only to shortlist providers that publish no licence data.
TITLE_TERMS_4992 = ("방수", "도장", "도색", "페인트", "습식", "석공", "외벽", "옥상", "균열", "크랙",
                    "실링", "코킹", "차선", "노면")
KAPT_KIND = {"1": "신규공고", "2": "수정공고", "3": "재공고"}
KAPT_CONTRACT = {"01": "일반경쟁", "02": "제한경쟁", "03": "지명경쟁"}
# Observed only: the K-apt Swagger says "코드정의서 참조" and no code table is published.
KAPT_BUSINESS = {"01": "용역", "02": "공사", "03": "용역", "04": "물품", "05": "기타"}
LINK_HOSTS = {"www.k-apt.go.kr", "www.g2b.go.kr", "srm.kepco.net"}
# Providers that publish no licence or participation-region data at all.
NO_CONDITION_SOURCES = {"kapt", "kwater", "kospo", "kepco"}
# 한전 전자입찰 계약정보 companyId (portal API guide, 2026-10-20). COM04 한국남부발전 is skipped: the 남부발전 source
# (kospo) already lists those notices.
KEPCO_COMPANIES = {"COM01": "한국전력공사", "COM02": "한국서부발전(주)", "COM03": "한국전력국제원자력대학원대학교",
                   "COM04": "한국남부발전(주)", "COM05": "한국중부발전(주)", "COM06": "한국남동발전(주)",
                   "COM08": "한국동서발전(주)", "COM09": "한국전력기술(주)", "COM10": "한전KPS(주)", "COM11": "한국전력거래소",
                   "COM12": "한국원자력연료(주)", "COM14": "한국발전교육원", "COM16": "한국해상풍력(주)", "COM19": "KAPES주식회사"}
KEPCO_SKIPPED_COMPANIES = {"COM04"}
# Ordering offices (placeName) seen in the 2026-10-06..19 answer whose area is a single province or plant site.
# Used only to narrow the site scope of notices that carry no location, labelled as an estimate; it never decides
# eligibility. Offices covering several provinces (대전세종충남·광주전남·대구·부산울산본부) and nationwide
# construction offices (중부·남부·경인·HVDC건설본부) are left out on purpose.
KEPCO_OFFICES = {"서울본부": "서울특별시", "남서울본부": "서울특별시", "경기본부": "경기도", "경기북부본부": "경기도",
                 "인천본부": "인천광역시", "강원본부": "강원특별자치도", "충북본부": "충청북도", "전북본부": "전북특별자치도",
                 "경북본부": "경상북도", "경남본부": "경상남도", "제주본부": "제주특별자치도",
                 "당진발전본부": "충청남도 당진시", "태안발전본부": "충청남도 태안군", "영흥발전본부": "인천광역시 옹진군",
                 "일산발전본부": "경기도 고양시", "세종발전본부": "세종특별자치시", "제주발전본부": "제주특별자치도",
                 "울산발전본부": "울산광역시", "삼천포발전본부": "경상남도 사천시", "신보령발전본부": "충청남도 보령시",
                 "영동에코발전본부": "강원특별자치도 강릉시", "신서천발전본부": "충청남도 서천군"}
# Regional offices covering several provinces. The site is one of them; a notice is out of a scope only when every
# one of them is (recommend.outside_site_scope). 대구본부 also covers 경상북도 (e.g. 포항 죽장 work, 2026-10).
KEPCO_OFFICE_AREAS = {"대전세종충남본부": ("대전광역시", "세종특별자치시", "충청남도"), "광주전남본부": ("광주광역시", "전라남도"),
                      "대구본부": ("대구광역시", "경상북도"), "부산울산본부": ("부산광역시", "울산광역시", "경상남도")}
KEPCO_BUSINESS = {"Construction": "공사", "Service": "용역", "Product": "물품"}
KEPCO_CONTRACT = {"Open": "일반경쟁", "Destination": "지명경쟁", "Limited": "제한경쟁", "Private": "수의"}
KEPCO_AWARD = {"LimitedLowestPrice": "제한적최저가", "LowestPrice": "최저가", "QualifiedEval": "적격심사",
               "CollectivelyBid": "일괄입찰", "Nego": "협상", "TotalEvalSuccess": "종합심사낙찰제"}
# 누리장터 licence-limit and participation-region lists cover every business type; they join construction
# notices by (bidNtceNo, bidNtceOrd).
NURI_AUX = ("nuri_lcns", "nuri_rgn")
# 남부발전 category values observed on 2026-10-12. 공사영역 mixes construction and services with no field to split them.
KOSPO_BUSINESS = {"공사영역": "미분류", "자재": "물품"}
# 남부발전 plants named in titles, used only to narrow the site scope of notices without location data. It is an
# estimate from the title, labelled as such; it never decides eligibility.
KOSPO_PLANTS = {"신인천": "인천광역시", "남제주": "제주특별자치도", "한림": "제주특별자치도", "하동": "경상남도 하동군",
                "삼척": "강원특별자치도 삼척시", "영월": "강원특별자치도 영월군", "안동": "경상북도 안동시", "부산": "부산광역시"}
KOSPO_PLANT_IN_TITLE = re.compile(r"(?<![가-힣])(" + "|".join(KOSPO_PLANTS) + r")(?=복합|빛드림|본부|화력|발전|\s|\d|#|$)")


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


def nuri_licenses(rows):
    """Licence rows as listed (group-sequence order) plus every 4-digit code in names and permitted lists."""
    entries, codes = [], set()
    for r in sorted(rows, key=lambda r: (to_int(r.get("lmtGrpNo")) or 0, to_int(r.get("lmtSno")) or 0)):
        name, permitted = clean(r.get("lcnsLmtNm")), clean(r.get("permsnIndstrytyList"))
        codes |= set(re.findall(r"/\s*(\d{4})\s*$", name or "")) | set(re.findall(r"/\s*(\d{4})\s*\]", permitted or ""))
        entries.append(f"그룹{clean(r.get('lmtGrpNo')) or '?'}-{clean(r.get('lmtSno')) or '?'} {name or '면허명 없음'}"
                       + (f" (허용업종 {permitted})" if permitted else ""))
    return entries, codes


def nuri_sites(text):
    """공사현장명 from cnstwkDtlList entries '[순번^공사명^공사현장명^준공기한일자]'."""
    sites = []
    for entry in re.split(r"\]\s*,?\s*\[", (clean(text) or "").strip("[]")):
        parts = entry.split("^")
        if len(parts) >= 4 and clean(parts[-2]) and clean(parts[-2]) not in sites:
            sites.append(clean(parts[-2]))
    return sites


def nuri_row(item, licence_rows, region_rows, covered, *meta):
    row = base_row("nuri", (clean(item.get("bidNtceNo")) or "",), *meta)
    kind, method = clean(item.get("ntceDivNm")), clean(item.get("bidMethdNm"))
    registered = iso_date(item.get("rgstDt"))
    close, opening = iso_datetime(item.get("bidClseDt")), iso_datetime(item.get("opengDt"))
    budget, reference = to_int(item.get("asignBdgtAmt")), to_int(item.get("refAmt"))
    sites = nuri_sites(item.get("cnstwkDtlList"))
    regions = []
    for r in sorted(region_rows, key=lambda r: to_int(r.get("lmtSno")) or 0):
        name = canonical_region(r.get("prtcptPsblRgnNm"))
        if name and name not in regions:
            regions.append(name)
    licenses, codes = nuri_licenses(licence_rows)
    links = [clean(item.get(f"ntceSpecDocUrl{n}")) for n in range(1, 11)]
    link = next((u for u in links if u and u.startswith("https://www.g2b.go.kr/")), None)
    row.update(notice_no=clean(item.get("bidNtceNo")),
               version=" ".join(x for x in (clean(item.get("bidNtceOrd")), kind) if x) or None,
               title=clean(item.get("ntceNm")), agency=clean(item.get("ntceInsttNm")), business="공사",
               business_basis="공사 전용 오퍼레이션(getPrvtBidPblancListInfoCnstwk)",
               notice_date=iso_date(item.get("nticeDt")) or registered,
               deadline=close or opening,
               deadline_label="입찰 마감" if close else "개찰 일시(입찰 마감 미제공)" if opening else None,
               # 누리장터 publishes no 추정가격; 배정예산 and the bid-cap 기준금액 stay under their own labels.
               amounts={"배정예산": budget or None, "기준금액(투찰 상한)": reference or None},
               site_region=sites[0] if sites else None, site_region_label="공사현장명(cnstwkDtlList)",
               office=clean(item.get("aptCeoAdrs")), contract=clean(item.get("cntrctMthdNm")),
               cancelled=kind == "취소공고", url=link, url_label="누리장터 공고서 첨부" if link else None)
    row["amount_label"] = "추정가격 미제공(배정예산·기준금액 별도)"
    if budget == 0:
        row["flags"].append("배정예산 0 표시는 미제공으로 처리")
    if len(sites) > 1:
        row["flags"].append(f"공사현장 {len(sites)}곳")
    if not close and opening:
        row["flags"].append("입찰 마감 미제공 · 개찰 일시로 표시")
    if method and method != "전자입찰":
        row["flags"].append(f"입찰방식 {method}")
    limit_kind = clean(item.get("rgnLmtDivNm"))
    if regions:
        row["allowed_regions"], row["allowed_regions_basis"] = regions, "참가가능지역(PrtcptPsblRgn)"
    elif not covered("nuri_rgn", registered):
        row["allowed_regions_basis"] = "참가가능지역 목록 미수집"
        row["flags"].append("참가가능지역 미수집")
    elif limit_kind == "투찰제한":
        row["allowed_regions_basis"] = "지역제한(투찰제한) 표시인데 참가가능지역 행 없음"
        row["flags"].append("투찰제한인데 참가가능지역 없음")
    else:
        row["allowed_regions_basis"] = "지역제한 구분: " + (limit_kind or "표시 없음")
    if licenses:
        row.update(licenses=licenses, license_codes=codes, license_text="\n".join(licenses),
                   license_basis="면허제한(LicenseLimit) · 그룹·순번의 AND/OR 미확인")
        if len(licenses) > 1:
            row["flags"].append("복수 면허 행(AND/OR 미확인)")
    elif not covered("nuri_lcns", registered):
        row["license_basis"] = "면허제한 목록 미수집"
        row["flags"].append("면허제한 미수집")
    else:
        row["licenses"], row["license_basis"] = [], "면허제한 행 없음(공고서 참조)"
    return row


def kospo_row(item, *meta):
    row = base_row("kospo", (clean(item.get("announceno")) or "",), *meta)
    category = clean(item.get("category"))
    construction = category == "공사영역"
    apply_end = iso_datetime(item.get("appledt3"))
    bid_end = iso_datetime(item.get("subedt3")) or iso_datetime(item.get("deadl2"))
    title = clean(item.get("title"))
    plant = KOSPO_PLANT_IN_TITLE.search(title or "")
    raw = {k: re.sub(r"\s", "", clean(item.get(k)) or "") for k in ("estprc3", "estprcv3", "estbas3", "estprc12")}
    shown, vat, base, goods = (to_int(raw[k]) for k in ("estprc3", "estprcv3", "estbas3", "estprc12"))
    row.update(notice_no=clean(item.get("announceno")), title=title, agency="한국남부발전",
               business=KOSPO_BUSINESS.get(category or "", "미분류"),
               business_basis=f"입찰정보범주(category) {category or '없음'}" + (" · 공사·용역 구분 없음" if construction else ""),
               notice_date=iso_date(item.get("annday3")),
               deadline=apply_end or bid_end,
               deadline_label="입찰참가신청 마감(투찰 마감은 별도)" if apply_end else "투찰·입찰 마감" if bid_end else None,
               # The two 공사 amounts are kept as labelled; observed values contradict the labels (see flags).
               amounts=({"추정가격(공사) 표기값": shown or None, "부가세포함추정가격(공사) 표기값": vat or None,
                         "예비가격기초금액": base or None} if construction else {"추정가격(자재)": goods or None}),
               site_region=KOSPO_PLANTS[plant.group(1)] if plant else None,
               site_region_label="제목의 발전소 이름으로 추정한 위치(공식 현장 정보 아님)" if plant else "현장 위치 정보 없음")
    row["amount_label"] = "추정가격 라벨 불명확(표기값 별도)" if construction else "추정가격(자재)"
    if construction:
        row["flags"].append("공사·용역 구분 없음(공사영역)")
        if shown and vat and shown * 10 == vat * 11:
            row["flags"].append("금액 라벨 확인 필요: '추정가격' 표기값이 '부가세포함' 표기값의 1.1배")
    if any(raw[k] == "0" for k in (("estprc3", "estprcv3", "estbas3") if construction else ("estprc12",))):
        row["flags"].append("금액 0 표시는 미제공으로 처리")
    return row


def kepco_link(item):
    """The attachment named as the notice (else the first), on the SRM host and over https; others are not linked."""
    files = []
    for n in range(1, 6):
        link, name = clean(item.get(f"filenlink{n}")), clean(item.get(f"filename{n}")) or ""
        parts = urlsplit(link or "")
        if parts.scheme in ("http", "https") and parts.hostname == "srm.kepco.net" and not parts.username:
            files.append((urlunsplit(("https",) + tuple(parts)[1:]), name))
    chosen = next((f for f in files if "공고" in f[1]), files[0] if files else None)
    if not chosen:
        return None, None
    return chosen[0], "공고문 첨부" if "공고" in chosen[1] else "첨부 파일"


def kepco_row(item, *meta):
    row = base_row("kepco", (clean(item.get("no")) or "",), *meta)
    company = clean(item.get("companyId")) or ""
    etc = clean(item.get("etc"))
    url, url_label = kepco_link(item)
    contract = " · ".join(x for x in (KEPCO_CONTRACT.get(clean(item.get("competitionType")) or "", clean(item.get("competitionType"))),
                                       KEPCO_AWARD.get(clean(item.get("bidType")) or "", clean(item.get("bidType")))) if x)
    row.update(notice_no=clean(item.get("no")), title=clean(item.get("name")),
               agency=KEPCO_COMPANIES.get(company, f"한전 계열({company or '회사코드 없음'})"),
               business=KEPCO_BUSINESS.get(clean(item.get("itemType")) or "", "미분류"),
               business_basis="도급구분(itemType)", notice_date=iso_date(item.get("noticeDate")),
               deadline=iso_datetime(item.get("bidAttendReqCloseDatetime")),
               deadline_label="입찰참가신청 마감(투찰 마감은 별도)",
               amount=to_int(item.get("presumedPrice")),
               amounts={"추정가격": to_int(item.get("presumedPrice")), "추정금액": to_int(item.get("presumedAmount")),
                        "예비가격기초금액": to_int(item.get("estimatedPriceBasicAmount"))},
               office=clean(item.get("placeName")), license_text=None if etc in (None, "-") else etc,
               contract=contract or None, progress=clean(item.get("progressState")), url=url, url_label=url_label)
    row["license_basis"] = "면허 정보 미제공(입찰참가자격 문장·공고문 확인)"
    row["allowed_regions_basis"] = "참가지역 정보 미제공(공고문 확인)"
    office = (row["office"] or "").split()
    estimate = KEPCO_OFFICES.get(office[0]) if office else None
    areas = KEPCO_OFFICE_AREAS.get(office[0]) if office else None
    if areas:
        estimate = "·".join(areas) + " 중 한 곳"
        row["site_provinces"] = list(areas)
    row["site_region"] = estimate
    row["site_region_label"] = ("발주 본부로 추정한 위치(공식 현장 정보 아님)" if estimate
                                else "현장 위치 정보 없음(발주기관은 현장이 아님)")
    if clean(item.get("emergencyNoticeYn")) == "1":
        row["flags"].append("긴급공고")
    if clean(item.get("jointSupplyDemandYn")) == "1":
        row["flags"].append("공동수급 공고")
    return row


def shortlist_candidate(row):
    """Title words, or for 한전 also the 입찰참가자격 sentence naming 4992 work (no licence field exists there)."""
    if title_candidate(row["title"]):
        return True
    return row["source"] == "kepco" and any(term in (row["license_text"] or "") for term in LICENSE_4992_TERMS)


def version_rank(row, item):
    if row["source"] == "lh":
        return to_int(item.get("bidDegree")) or 0
    if row["source"] == "d2b":
        return to_int(item.get("pblancOdr")) or 0
    if row["source"] == "nuri":
        return to_int(item.get("bidNtceOrd")) or 0
    if row["source"] == "kepco":
        # The same number came twice with new dates and files (a relisting); the later registration is current.
        return to_int(re.sub(r"\D", "", clean(item.get("createDatetime")) or "")) or 0
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
        jobs = [dict(zip(("provider", "status", "received", "total_count", "updated_at", "snapshot", "params_json"), r))
                for r in conn.execute("SELECT provider, status, received, total_count, updated_at, "
                                      + ("snapshot" if has_snapshot else "NULL")
                                      + ", params_json FROM collection_job ORDER BY updated_at")]
        observations = conn.execute(
            "SELECT o.provider, o.payload_json, o.source_response_id, j.updated_at, "
            + ("j.snapshot" if has_snapshot else "NULL") + " FROM notice_observation o JOIN collection_job j "
            "ON j.job_id=o.job_id ORDER BY j.updated_at, o.job_id, o.page_no, o.row_no").fetchall()
    finally:
        conn.close()
    # 누리장터 licence/region rows by notice version; a later observation of the same row replaces an earlier one.
    aux = {name: defaultdict(dict) for name in NURI_AUX}
    for provider, payload, *_ in observations:
        if provider in aux:
            item = json.loads(payload)
            key = (clean(item.get("bidNtceNo")) or "", clean(item.get("bidNtceOrd")) or "")
            aux[provider][key][(clean(item.get("lmtGrpNo")), clean(item.get("lmtSno")))] = item
    # Registration days whose list was fully received: there, a notice without rows really has none.
    windows = {name: [] for name in NURI_AUX}
    for job in jobs:
        if job["provider"] in windows and job["status"] == "COMPLETE_RANGE":
            params = json.loads(job["params_json"] or "{}")
            windows[job["provider"]].append((iso_date(params.get("inqryBgnDt")), iso_date(params.get("inqryEndDt"))))

    def covered(name, day):
        return bool(day) and any(b and e and b <= day <= e for b, e in windows[name])

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
        elif provider == "nuri":
            key = (clean(item.get("bidNtceNo")) or "", clean(item.get("bidNtceOrd")) or "")
            row = nuri_row(item, list(aux["nuri_lcns"][key].values()), list(aux["nuri_rgn"][key].values()), covered,
                           *meta_args)
        elif provider == "kospo":
            row = kospo_row(item, *meta_args)
        elif provider == "kepco":
            if (clean(item.get("companyId")) or "") in KEPCO_SKIPPED_COMPANIES:
                continue
            row = kepco_row(item, *meta_args)
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
            if source == "nuri":
                # The joined lists are collected as their own jobs; their last state is shown with the source.
                meta["sources"][source]["aux"] = {
                    name: next((j["status"] for j in reversed(jobs) if j["provider"] == name), "미수집") for name in NURI_AUX}
    meta["status"] = "COLLECTED" if rows else "EMPTY"
    return rows, meta


def evaluate(row, hq):
    licenses = row["licenses"]
    codes = row.get("license_codes")
    if licenses is None or not licenses:
        license_state = ("확인 필요", row["license_basis"])
    elif codes is not None:
        # Providers that give licence codes (누리장터) are matched by code, names included in permitted lists.
        license_state = ("포함", row["license_basis"]) if "4992" in codes else ("불충족", "요구 면허·허용업종에 4992 없음")
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
            if row["source"] in NO_CONDITION_SOURCES and title_shortlist and not shortlist_candidate(row):
                held_back["면허 정보 없는 출처 · 제목 후보 아님"] += 1
                continue
            # 누리장터 notices whose licence limits are absent or not yet collected get the same title shortlist.
            if row["source"] == "nuri" and not row["licenses"] and title_shortlist and not title_candidate(row["title"]):
                held_back["누리장터 면허 정보 없음 · 제목 후보 아님"] += 1
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
