"""Profile-based notice recommendations across G2B and the staged providers.

A profile is the company's HQ region and licence. The result lists open notices that the published
conditions allow ("조건 일치") or that need the notice text to decide ("확인 필요"). Notices whose
published licence or participation region excludes the profile are held back and counted, never hidden.
Neither state is a confirmed eligibility decision.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from bidloc.analysis import parse_datetime, relevant_records, shortlist, tokens
from bidloc.exports import safe_url
from bidloc.provider_notices import (LICENSE_4992_TERMS, SIDO_NAMES, SOURCE_LABELS, canonical_region,
                                     search_provider_notices)
from bidloc.query_service import SearchFilters
from bidloc.timeutil import now_kst

SOURCES = {"g2b": "나라장터", **SOURCE_LABELS}
LICENSES = {"4992": "도장·습식·방수·석공사업"}
STATE_ORDER = {"조건 일치": 0, "확인 필요": 1}
PROVINCES = frozenset(SIDO_NAMES.values())
CAPITAL = ("서울특별시", "경기도", "인천광역시")
SITE_SCOPES = {"home": "본점 시·도", "capital": "수도권(서울·경기·인천)", "all": "전국"}
NOTES = ["조건 일치도 참가자격 확정이 아닙니다. 주력분야·실적·본점 기준일·복수면허 조건은 원문으로 확인합니다.",
         "참가지역(참가 허용 지역), 현장 위치, 기관 위치는 서로 다른 정보입니다.",
         "확인 필요는 출처가 면허·참가지역을 주지 않았거나 자료가 비어 있다는 뜻이며 참가 가능을 뜻하지 않습니다.",
         "참가지역 정보가 없는 공고는 기본적으로 현장 시·도가 본점과 같은 것만 보여줍니다(site_provinces). 자격 판정이 아니라 추천 범위입니다.",
         "같은 공고의 여러 차수는 최신 차수 하나로 보여줍니다. 출처 간 같은 사업의 중복은 합치지 않았습니다."]


class ProfileError(ValueError):
    def __init__(self, message, candidates=None):
        super().__init__(message)
        self.candidates = candidates or []


def resolve_license(value):
    text = "".join(str(value or "4992").split())
    if text in LICENSES:
        return text
    if any(term in text for term in LICENSE_4992_TERMS):
        return "4992"
    raise ProfileError("현재 검증된 면허는 4992(도장·습식·방수·석공사업)뿐입니다.", list(LICENSES))


def resolve_region(value, known_regions):
    """Accepts '경기도 남양주시', '남양주시' or '남양주'. An ambiguous name returns its candidates instead of a guess."""
    region = canonical_region(value)
    if not region:
        raise ProfileError("본점 소재지(region)가 필요합니다. 예: 경기도 남양주시")
    parts = region.split()
    provinces = set(SIDO_NAMES.values())
    if parts[0] in provinces:
        return region
    head = parts[0]
    matches = sorted({r for r in known_regions if len(tokens(r)) >= 2 and r.split()[0] in provinces
                      and any(t.startswith(head) for t in r.split()[1:2])})
    shortest = sorted({" ".join(r.split()[:2]) for r in matches})
    if len(shortest) == 1:
        return shortest[0] + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")
    if not shortest:
        raise ProfileError("본점 소재지를 찾지 못했습니다. 시·도부터 입력하세요. 예: 경기도 남양주시")
    raise ProfileError("같은 이름의 지역이 여러 곳입니다. 시·도를 함께 입력하세요.", shortest)


def g2b_items(records, hq, license, now):
    result = shortlist(records, hq, license, now)
    open_relevant = sum(1 for d in relevant_records(records, license)
                        if (parse_datetime(d.get("deadline")) or now) > now)
    kept = len(result["allowed"]) + len(result["unspecified"]) + len(result["revision_review"])
    held = Counter({"참가지역 불일치": open_relevant - kept} if open_relevant > kept else {})
    items = []
    for state, bucket, region_state, basis in (
            ("조건 일치", result["allowed"], "충족", None),
            ("확인 필요", result["unspecified"], "확인 필요", "허용지역 미수집 또는 지역제한 없음"),
            ("확인 필요", result["revision_review"], "충족", "지역 자료 차수·원본 품질 확인")):
        for d in bucket:
            flags = (["지역 자료 차수 불일치"] if d.get("region_revision_mismatch") else []) + \
                    (["원본 품질 확인"] if any(x.get("quality_flag") for x in d.get("licenses", [])) else [])
            items.append({
                "key": "g2b:" + d["key"], "source": "g2b", "source_label": SOURCES["g2b"], "title": d.get("bid_ntce_nm"),
                "notice_no": d.get("bid_ntce_no"), "version": d.get("bid_ntce_ord"), "agency": d.get("ntce_instt_nm"),
                "demand_agency": d.get("dminstt_nm"), "notice_date": (d.get("bid_ntce_dt") or "")[:10] or None,
                "deadline": d.get("deadline"), "deadline_label": "입찰 마감", "estimated_price": d.get("presmpt_prce"),
                "amounts": {"추정가격": d.get("presmpt_prce"), "배정예산": d.get("bdgt_amt")},
                "allowed_regions": d.get("regions") or None,
                "site": {"value": d.get("cnstrtsite_rgn_nm"), "kind": "공사현장(나라장터)"},
                "license_requirements": [x.get("lcns_lmt_nm") for x in d.get("licenses", []) if x.get("lcns_lmt_nm")] or None,
                "contract": d.get("cntrct_cncls_mthd_nm"),
                "evaluation": {"state": state, "license": "포함", "license_basis": "면허제한·허용업종 코드에 " + license,
                               "region": region_state, "region_basis": basis or ("참가가능지역: " + " · ".join(d.get("regions") or []))},
                "flags": flags, "url": safe_url(d.get("url")),
                "evidence": {"notice_response_id": d.get("last_response_id"), "region_response_ids": d.get("region_sources"),
                             "license_response_ids": [x.get("response_id") for x in d.get("licenses", [])]}})
    return items, held


def provider_items(rows, hq, now, *, sources, keyword="", min_amount=None, max_amount=None, title_shortlist=True):
    filters = SearchFilters(query=keyword, hq=hq, scope="4992", status="마감 전", min_amount=min_amount, max_amount=max_amount)
    found, held = search_provider_notices(rows, filters, sources=sources, title_shortlist=title_shortlist, now=now)
    items = []
    for r in found:
        ev = r["evaluation"]
        items.append({
            "key": r["key"], "source": r["source"], "source_label": r["source_label"], "title": r["title"],
            "notice_no": r["notice_no"], "version": r["version"], "agency": r["agency"], "demand_agency": None,
            "notice_date": r["notice_date"], "deadline": r["deadline"], "deadline_label": r["deadline_label"],
            "estimated_price": r["amount"], "amounts": r["amounts"], "allowed_regions": r["allowed_regions"] or None,
            "site": {"value": r["site_region"], "kind": r["site_region_label"]},
            "license_requirements": r["licenses"] or None, "contract": r["contract"],
            "evaluation": {"state": ev["overall"], "license": ev["license"], "license_basis": ev["license_basis"],
                           "region": ev["region"], "region_basis": ev["region_basis"] or r["allowed_regions_basis"]},
            "flags": r["flags"], "url": r["url"], "office": r["office"],
            "evidence": {"source_response_id": r["source_response_id"], "detail_response_id": r["detail_response_id"],
                         "collected_at": r["collected_at"], "snapshot": r["snapshot"]}})
    return items, held


def resolve_site_provinces(value, hq):
    """Site-province scope for notices whose participation region is unknown; 'all' disables it."""
    text = str(value or "").strip()
    if text.lower() == "all" or text == "전국":
        return None
    if text.lower() == "capital" or text == "수도권":
        return set(CAPITAL)
    if text.lower() == "home":
        text = ""
    names = [canonical_region(x).split()[0] for x in text.split(",") if canonical_region(x)] or [hq.split()[0]]
    unknown = [n for n in names if n not in PROVINCES]
    if unknown:
        raise ProfileError("site_provinces에는 시·도 이름, home, capital, all 중 하나를 넣으세요.", sorted(PROVINCES))
    return set(names)


def outside_site_scope(site, region_state, provinces):
    """True only for a notice without participation-region data whose readable site province is out of scope."""
    if provinces is None or region_state != "확인 필요":
        return False
    text = canonical_region(site)
    province = text.split()[0] if text and text.split()[0] in PROVINCES else None
    # An unreadable or missing site is kept: the scope cannot be judged without it.
    return bool(province and province not in provinces)


def recommend(g2b_records, provider_rows, *, region, license="4992", sources=None, keyword="", min_amount=None,
              max_amount=None, include_unknown=True, title_shortlist=True, sort="deadline", limit=50, offset=0,
              site_provinces=None, known_regions=(), g2b_ready=True, now=None):
    now = now or now_kst()
    license = resolve_license(license)
    hq = resolve_region(region, known_regions)
    provinces = resolve_site_provinces(site_provinces, hq)
    chosen = set(sources or SOURCES)
    unknown = chosen - set(SOURCES)
    if unknown:
        raise ProfileError("알 수 없는 출처입니다.", sorted(SOURCES))
    warnings = []
    items, held = [], Counter()
    if "g2b" in chosen:
        if g2b_ready:
            g2b, g2b_held = g2b_items(g2b_records, hq, license, now)
            held.update(g2b_held)
            terms = keyword.casefold().split()
            g2b = [i for i in g2b if all(t in " ".join(str(i[k] or "") for k in ("title", "notice_no", "agency")).casefold() for t in terms)]
            if min_amount is not None or max_amount is not None:
                kept = [i for i in g2b if i["estimated_price"] is not None
                        and (min_amount is None or i["estimated_price"] >= min_amount)
                        and (max_amount is None or i["estimated_price"] <= max_amount)]
                held["추정가격 미제공"] += sum(i["estimated_price"] is None for i in g2b)
                g2b = kept
            items += g2b
        else:
            warnings.append("나라장터 스냅샷을 읽는 중이어서 나라장터 공고를 제외했습니다. 잠시 후 다시 요청하세요.")
    provider_sources = chosen - {"g2b"}
    if provider_sources:
        found, provider_held = provider_items(provider_rows, hq, now, sources=provider_sources, keyword=keyword,
                                              min_amount=min_amount, max_amount=max_amount, title_shortlist=title_shortlist)
        items += found
        held.update(provider_held)
    # Only where the participation region is unknown: a far-away site is a scope choice, not an eligibility result.
    kept = []
    for i in items:
        if outside_site_scope((i.get("site") or {}).get("value"), i["evaluation"]["region"], provinces):
            held["참가지역 정보 없음 · 현장 시·도 범위 밖"] += 1
            continue
        kept.append(i)
    items = kept
    if not include_unknown:
        held["확인 필요 제외(include_unknown=false)"] += sum(i["evaluation"]["state"] == "확인 필요" for i in items)
        items = [i for i in items if i["evaluation"]["state"] == "조건 일치"]
    far = "9999-12-31T23:59:59"
    if sort == "notice_date":
        items.sort(key=lambda i: (i["notice_date"] or "", i["key"]), reverse=True)
    elif sort == "amount":
        items.sort(key=lambda i: (i["estimated_price"] is not None, i["estimated_price"] or 0), reverse=True)
    else:
        items.sort(key=lambda i: ((parse_datetime(i["deadline"]) or parse_datetime(far)).isoformat(), i["key"]))
    items.sort(key=lambda i: STATE_ORDER.get(i["evaluation"]["state"], 9))
    page = items[offset:offset + limit]
    return {"profile": {"region": hq, "region_input": region, "license": license, "license_name": LICENSES[license],
                        "site_provinces": sorted(provinces) if provinces is not None else "all"},
            "as_of_kst": now.isoformat(), "complete": not warnings, "warnings": warnings,
            "counts": {"total": len(items), "by_state": dict(Counter(i["evaluation"]["state"] for i in items)),
                       "by_source": dict(Counter(i["source"] for i in items)), "held_back": dict(held)},
            "limit": limit, "offset": offset, "next_offset": offset + limit if offset + limit < len(items) else None,
            "items": page, "notes": NOTES}


def region_vocabulary(g2b_records, provider_rows):
    """Region names seen in the data; used only to expand a short HQ name such as '남양주'."""
    found = {r for d in g2b_records for r in d.get("regions", [])}
    found |= {r for row in provider_rows for r in (row.get("allowed_regions") or [])}
    found |= {canonical_region(row["site_region"]) for row in provider_rows if row.get("site_region")}
    return found


# Saved company profile and daily recommendation snapshots (local files next to the DB, Git-excluded).

def recommend_dir(database_path):
    return Path(database_path).parent / "recommend"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def load_profile(directory):
    path = Path(directory) / "profile.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_profile(directory, profile, known_regions, now=None):
    """Validates like the API does, then stores the resolved values; an invalid profile is never written."""
    region = resolve_region(profile.get("region"), known_regions)
    license = resolve_license(profile.get("license"))
    scope = profile.get("site_scope") or "home"
    resolve_site_provinces(scope, region)
    sources = [s for s in (profile.get("sources") or SOURCES)]
    if not sources or set(sources) - set(SOURCES):
        raise ProfileError("출처를 하나 이상 고르세요.", sorted(SOURCES))
    saved = {"region": region, "license": license, "site_scope": scope, "sources": sources,
             "include_unknown": bool(profile.get("include_unknown", True)),
             "title_shortlist": bool(profile.get("title_shortlist", True)),
             "saved_at_kst": (now or now_kst()).isoformat()}
    write_json(Path(directory) / "profile.json", saved)
    return saved


def recommend_for_profile(profile, g2b_records, provider_rows, *, known_regions, g2b_ready=True, now=None, limit=100000):
    return recommend(g2b_records, provider_rows, region=profile["region"], license=profile["license"],
                     sources=profile.get("sources"), include_unknown=profile.get("include_unknown", True),
                     title_shortlist=profile.get("title_shortlist", True), site_provinces=profile.get("site_scope"),
                     limit=limit, known_regions=known_regions, g2b_ready=g2b_ready, now=now)


def same_profile(a, b):
    keys = ("region", "license", "site_scope", "sources", "include_unknown", "title_shortlist")
    return bool(a and b) and all(a.get(k) == b.get(k) for k in keys)


def daily_file(directory, day):
    return Path(directory) / "daily" / f"{day.isoformat()}.json"


def write_daily(directory, day, profile, result):
    items = [{"key": i["key"], "state": i["evaluation"]["state"], "source": i["source"], "title": i["title"],
              "notice_no": i["notice_no"], "notice_date": i["notice_date"], "deadline": i["deadline"]} for i in result["items"]]
    return write_json(daily_file(directory, day),
                      {"date": day.isoformat(), "generated_at_kst": result["as_of_kst"], "profile": profile,
                       "complete": result["complete"], "warnings": result["warnings"], "counts": result["counts"], "items": items})


def ensure_daily(directory, day, profile, result):
    """Keeps the first complete list of the day for this profile; returns True when it wrote one."""
    if not result["complete"]:
        return False
    try:
        saved = json.loads(daily_file(directory, day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if saved and same_profile(saved.get("profile"), profile) and saved.get("complete"):
        return False
    write_daily(directory, day, profile, result)
    return True


def previous_daily(directory, day, profile):
    """Latest snapshot before `day` made with the same profile; a different profile is not a valid baseline."""
    for path in sorted((Path(directory) / "daily").glob("*.json"), reverse=True):
        if path.stem >= day.isoformat():
            continue
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return saved if same_profile(saved.get("profile"), profile) and saved.get("complete") else None
    return None


def mark_new(items, previous, now, fallback_days=1):
    """Flags items absent from the previous day's recommendation; without one, recent notice dates stand in."""
    if previous:
        seen = {i["key"] for i in previous["items"]}
        for i in items:
            i["is_new"] = i["key"] not in seen
        return f"{previous['date']} 추천 대비 새로 추가"
    since = (now.date() - timedelta(days=fallback_days)).isoformat()
    for i in items:
        i["is_new"] = (i["notice_date"] or "") >= since
    return f"이전 추천 기록 없음 · 공고일 {since} 이후"


def main(argv=None):
    """Daily step: recommend for the saved profile and keep the day's list. Reads only stored data; no network."""
    from bidloc.config import load_settings
    from bidloc.provider_notices import load_provider_notices, store_path
    from bidloc.ui.cache import read_snapshot
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--daily", action="store_true", required=True)
    parser.parse_args(argv)
    settings = load_settings(Path(__file__).resolve().parents[2])
    directory = recommend_dir(settings.database_path)
    profile = load_profile(directory)
    if not profile:
        print(json.dumps({"status": "SKIPPED", "reason": "저장된 조건이 없습니다(조건 검색 화면에서 저장)"}, ensure_ascii=False))
        return 0
    records, _ = read_snapshot(str(settings.database_path), settings.data_mode)
    rows, _ = load_provider_notices(store_path(settings.database_path))
    now = now_kst()
    result = recommend_for_profile(profile, records, rows, known_regions=region_vocabulary(records, rows), now=now)
    path = write_daily(directory, now.date(), profile, result)
    print(json.dumps({"status": "WRITTEN", "file": str(path), "counts": result["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
