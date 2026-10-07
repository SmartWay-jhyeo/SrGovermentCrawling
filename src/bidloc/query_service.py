"""Shared offline query contract for the UI and a future HTTP adapter.

No Streamlit, collector, HTTP client, credentials, or persistent writes here.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import hashlib
import io
import json
from pathlib import Path

from bidloc.analysis import AnalysisRepository, parse_datetime, region_matches
from bidloc.exports import safe_cell
from bidloc.repositories.db import connect
from bidloc.timeutil import now_kst


@dataclass(frozen=True)
class SearchFilters:
    query: str = ''
    begin: str = ''
    end: str = ''
    hq: str = ''
    contract: str = ''
    min_amount: int | None = None
    max_amount: int | None = None
    status: str = '전체'
    scope: str = '4992'
    profile: str = '미확정'
    sort: str = '공고일 최신순'

    def validate(self):
        for value in (self.begin, self.end):
            if value:
                datetime.strptime(value, '%Y-%m-%d')
        if self.begin and self.end and self.begin > self.end:
            raise ValueError('시작일은 종료일보다 늦을 수 없습니다.')
        if self.min_amount is not None and self.min_amount < 0:
            raise ValueError('추정가격은 0 이상이어야 합니다.')
        if self.max_amount is not None and self.max_amount < 0:
            raise ValueError('추정가격은 0 이상이어야 합니다.')
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError('최소 금액은 최대 금액보다 클 수 없습니다.')
        if self.scope not in ('4992', '분석 후보 전체'):
            raise ValueError('현재 검증된 면허 분석은 4992입니다.')


def load_snapshot(database: str, data_mode='real'):
    """One read transaction; only temporary SQLite analysis tables are created."""
    if not Path(database).is_file():
        return [], {'data_mode': data_mode, 'collection_status': 'NOT_COLLECTED',
                    'generated_at_kst': now_kst().isoformat(), 'filters': {}, 'snapshot_id': 'empty'}
    conn = connect(Path(database), readonly=True)
    try:
        conn.execute('BEGIN')
        repo = AnalysisRepository(conn)
        records = repo.records()
        # The app does not display the global relevance census. Do not block its
        # first useful screen on half a million unrelated state/index lookups.
        meta = repo.metadata(include_relevance=False)
        meta['data_mode'] = data_mode
        # Identity covers the observed database revision, not an export filename.
        # Avoid scanning the multi-GB raw notice table just to derive cache identity.
        stamp = [(p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
                 for p in (Path(database),Path(database+'-wal'))]
        identity = [stamp, meta['last_response_at_utc'], meta['job'], meta['partitions'], len(records)]
        meta['snapshot_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return records, meta
    finally:
        conn.close()


def review_reasons(row):
    reasons = []
    if not row.get('regions'):
        reasons.append('허용지역 미수집')
    if row.get('presmpt_prce') is None:
        reasons.append('추정가격 미수집')
    if row.get('region_revision_mismatch'):
        reasons.append('지역 차수 확인')
    if row.get('quality_flag') or any(x.get('quality_flag') for x in row.get('licenses', [])):
        reasons.append('원본 품질 확인')
    if row.get('relevance') != 'RELEVANT':
        reasons.append('업종 검토')
    return reasons or ['주력분야·원문 확인']


def search(records, filters: SearchFilters, *, now=None):
    filters.validate()
    current = now or now_kst()
    terms = filters.query.casefold().split()
    selected = []
    for d in records:
        if filters.scope == '4992' and not (d.get('relevance') == 'RELEVANT' and '4992' in d.get('license_codes', [])):
            continue
        published = (d.get('bid_ntce_dt') or '')[:10]
        if filters.begin and (not published or published < filters.begin):
            continue
        if filters.end and (not published or published > filters.end):
            continue
        haystack = ' '.join(str(d.get(k) or '') for k in ('bid_ntce_nm','bid_ntce_no','bid_ntce_ord','ntce_instt_nm','dminstt_nm')).casefold()
        if not all(term in haystack for term in terms):
            continue
        if filters.hq and not any(region_matches(filters.hq, r) for r in d.get('regions', [])):
            continue
        if filters.contract and d.get('cntrct_cncls_mthd_nm') != filters.contract:
            continue
        amount = d.get('presmpt_prce')
        if filters.min_amount is not None and (amount is None or amount < filters.min_amount):
            continue
        if filters.max_amount is not None and (amount is None or amount > filters.max_amount):
            continue
        deadline = parse_datetime(d.get('deadline'))
        if filters.status == '마감 전' and not (deadline and deadline > current):
            continue
        if filters.status == '마감' and not (deadline and deadline <= current):
            continue
        if filters.status == '허용지역 미수집' and d.get('regions'):
            continue
        if filters.status == '자료·차수 검토' and review_reasons(d) == ['주력분야·원문 확인']:
            continue
        selected.append(d)
    if filters.sort == '추정가격 높은순':
        selected.sort(key=lambda d: (d.get('presmpt_prce') is not None, d.get('presmpt_prce') or 0, d['key']), reverse=True)
    elif filters.sort == '마감 빠른순':
        selected.sort(key=lambda d: (parse_datetime(d.get('deadline')) is None, parse_datetime(d.get('deadline')) or datetime.max.replace(tzinfo=current.tzinfo), d['key']))
    else:
        selected.sort(key=lambda d: (d.get('bid_ntce_dt') or '', d['key']), reverse=True)
    return selected


def detail(records, no, ord_):
    return next((d for d in records if d['bid_ntce_no'] == no and d['bid_ntce_ord'] == ord_), None)


def notice_export_rows(records):
    return [{'공고번호': d['bid_ntce_no'], '차수': d['bid_ntce_ord'], '공고명': d['bid_ntce_nm'],
             '공고일': d.get('bid_ntce_dt'), '기관': d.get('ntce_instt_nm'),
             '허용지역': d.get('regions'), '지역자료차수': d.get('region_ord'),
             '추정가격_원': d.get('presmpt_prce'), '배정예산_원': d.get('bdgt_amt'),
             '마감': d.get('deadline'), '검토사유': review_reasons(d),
             '공고응답ID': d.get('last_response_id')} for d in records]


def csv_bytes(rows, metadata=None):
    enriched = [dict(r, **({'데이터모드': metadata.get('data_mode'), '스냅샷': metadata.get('snapshot_id'),
                           '조회시점': metadata.get('generated_at_kst'), '조회조건': metadata.get('applied_filters')} if metadata else {})) for r in rows]
    fields = list(dict.fromkeys(k for row in enriched for k in row)) or ['상태']
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(fields)
    for row in enriched:
        writer.writerow([safe_cell(row.get(k)) for k in fields])
    return stream.getvalue().encode('utf-8-sig')


def export_metadata(meta, filters):
    return {**meta, 'applied_filters': asdict(filters), 'evaluated_at_kst': now_kst().isoformat()}
