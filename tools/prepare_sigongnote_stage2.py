"""Prepare an offline, exact-key attachment review plan; no network operations."""
import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/sigongnote_market_stage2'
PRIORITY = ROOT / 'docs/시공노트_2차검토_우선확인공고.csv'
CACHE = ROOT / '.local/sigongnote_stage1/derived.sqlite3'


def link_check(url, no, order):
    try:
        p = urlsplit(url)
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            return '불가: 비공개/비HTTP 형식'
        if re.search(r'(?i)(servicekey|api[_-]?key|access[_-]?token|authorization)=', unquote(p.query)):
            return '불가: 인증정보 의심'
        if not (p.hostname == 'g2b.go.kr' or p.hostname.endswith('.g2b.go.kr')):
            return '보류: 공식 호스트 별도 확인 필요'
        params = parse_qs(p.query)
        for name in ('bidPbancNo', 'bidNtceNo', 'bidno'):
            if name in params and params[name] != [no]:
                return '불가: 공고번호 불일치'
        for name in ('bidPbancOrd', 'bidNtceOrd', 'bidseq'):
            if name in params and params[name] != [order]:
                return '불가: 공고차수 불일치'
        return '형식·호스트 정상(접속 미실행)'
    except ValueError:
        return '불가: URL 파싱 실패'


def write(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if Path(str(CACHE)+'-wal').exists() and Path(str(CACHE)+'-wal').stat().st_size:
        raise RuntimeError('기존 파생 캐시에 WAL 존재: 고정성 확인 필요')
    c = sqlite3.connect(CACHE.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    c.execute('PRAGMA query_only=ON')
    supplied = list(csv.DictReader(PRIORITY.open(encoding='utf-8-sig', newline='')))
    assert len({(r['공고번호'], r['공고차수']) for r in supplied}) == len(supplied)
    notices, downloads = [], []
    for index, r in enumerate(supplied, 1):
        no, order = r['공고번호'], r['공고차수']
        record = c.execute('SELECT payload FROM classified WHERE no=? AND ord=?', (no, order)).fetchone()
        p = json.loads(record[0]) if record else {}
        cached = p.get('attachment_urls', [])
        raw = r.get('첨부링크', '')
        try:
            offered = json.loads(raw) if raw else []
            if isinstance(offered, str): offered = [offered]
            if not isinstance(offered, list): offered = []
        except json.JSONDecodeError:
            offered = re.findall(r'https?://[^\s<>"\[\]]+', raw)
        offered = [str(u).rstrip(';|,') for u in offered]
        urls = list(dict.fromkeys([*cached, *offered]))
        differences = [field for field, target in [('공고명', 'title'), ('수요기관', 'dminstt_nm'), ('공고연도', 'notice_year'), ('추정가격_원', 'presmpt_prce'), ('예산금액_원', 'bdgt_amt')]
                       if record and str(r[field]) != str(p.get(target, ''))]
        notices.append(dict(priority_id=f'P{index:02}', bid_ntce_no=no, bid_ntce_ord=order,
                            title=r['공고명'], institution=r['수요기관'], review_reason=r['검토이유'],
                            cache_key_match=bool(record), field_mismatches=json.dumps(differences, ensure_ascii=False),
                            original_link_count=len(cached), supplied_link_count=len(offered), distinct_link_count=len(urls),
                            supplied_links_match_cache=set(offered)==set(cached), source_response_id=p.get('source_response_id',''),
                            notice_url=p.get('notice_url',''), local_document_status='미수집(프로젝트의 해당 공고문 미발견)',
                            source_review_status='SKIPPED_APPROVAL_PENDING', external_attempts=0))
        for number, url in enumerate(urls, 1):
            status = link_check(url, no, order)
            # Do not re-export unsafe strings or widen the proposal beyond stored links.
            safe = not status.startswith('불가')
            downloads.append(dict(priority_id=f'P{index:02}', bid_ntce_no=no, bid_ntce_ord=order,
                                  attachment_index=number, document_id=hashlib.sha256(url.encode()).hexdigest()[:16],
                                  attachment_url=url if safe else '', link_check=status,
                                  link_origin='기존 캐시 일치' if url in cached else '제공 CSV에만 있음(추가 대조 필요)',
                                  proposed_in_scope=int(safe and url in cached and status.startswith('형식')),
                                  approval_status='미승인', fetch_status='SKIPPED_APPROVAL_PENDING', http_status='',
                                  file_name='', page_or_table='', evidence_sentence='', error_reason='외부 다운로드 승인 전',
                                  request_limit=1, max_bytes=20971520))
    c.close()
    write(OUT/'priority_input_check.csv', notices, list(notices[0]))
    write(OUT/'attachment_download_plan.csv', downloads, list(downloads[0]))
    result = dict(priority_file=str(PRIORITY), priority_sha256=hashlib.sha256(PRIORITY.read_bytes()).hexdigest(),
                  priority_rows=len(supplied), matched_keys=sum(r['cache_key_match'] for r in notices),
                  field_mismatch_rows=sum(r['field_mismatches']!='[]' for r in notices),
                  distinct_attachments=len(downloads), proposed_downloads=sum(r['proposed_in_scope'] for r in downloads),
                  external_calls=0)
    (OUT/'priority_preparation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return notices, downloads


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    prepare()
