"""One approved, previously unattempted attachment; preserve detailed safe errors."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import traceback
import urllib.request

import httpx
from fetch_sigongnote_priority_documents import (
    ROOT, PLAN, WORK, MAX_FILE, MAX_TOTAL, MAX_REQUESTS,
    append, file_kind, load_ledger, utc,
)
from prepare_sigongnote_stage2 import link_check


def clean(value):
    text = str(value)
    text = re.sub(r'https?://[^\s\'\"<>]+', '[URL redacted]', text)
    text = re.sub(r'(?i)(servicekey|authorization|cookie|token|password)\s*[:=]\s*\S+', r'\1=[redacted]', text)
    return text[:2000]


def exception_chain(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append({
            'type': type(exc).__module__ + '.' + type(exc).__name__,
            'message': clean(exc), 'errno': getattr(exc, 'errno', None),
            'winerror': getattr(exc, 'winerror', None),
            'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                       for f in traceback.extract_tb(exc.__traceback__)],
        })
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return chain


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--approved-single-attempt', action='store_true')
    args = parser.parse_args()
    if not args.approved_single_attempt:
        raise SystemExit('Explicit single-attempt authorization flag required')
    sys.stdout.reconfigure(encoding='utf-8')
    plan = list(csv.DictReader(PLAN.open(encoding='utf-8-sig', newline='')))
    auth = json.loads((WORK/'authorization.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(PLAN.read_bytes()).hexdigest() == auth['plan_sha256']
    row = next(p for p in plan if p['document_id'] == 'fb414d38ad6d2162')
    assert row['proposed_in_scope'] == '1'
    assert link_check(row['attachment_url'], row['bid_ntce_no'], row['bid_ntce_ord']).startswith('형식')
    ledger = load_ledger()
    reserved = {e['document_id'] for e in ledger if e['event'] == 'reserved'}
    assert row['document_id'] not in reserved, 'Already attempted: no retry'
    assert len(reserved) < MAX_REQUESTS
    observed_bytes = sum(e.get('received_bytes', 0) for e in ledger if e['event'] == 'result')
    assert observed_bytes < MAX_TOTAL
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = ROOT/'outputs/sigongnote_market_stage2_documents'/('connection_diagnostic_'+stamp)
    out.mkdir(parents=True, exist_ok=False)
    original_ledger = (WORK/'fetch_ledger.jsonl').read_bytes()
    report = {
        'started_utc': utc(), 'document_id': row['document_id'],
        'priority_id': row['priority_id'], 'bid_ntce_no': row['bid_ntce_no'],
        'bid_ntce_ord': row['bid_ntce_ord'], 'plan_sha256': auth['plan_sha256'],
        'prior_ledger_sha256': hashlib.sha256(original_ledger).hexdigest(),
        'prior_log_limitation': 'Prior ConnectError handler discarded message and nested exception; original detail cannot be recovered.',
        'settings': {'tls_verify': True, 'trust_env': True, 'redirects': False,
                     'retries': 0, 'max_file_bytes': MAX_FILE, 'logical_requests_this_run': 1,
                     'proxy_configuration_keys': sorted(urllib.request.getproxies().keys()),
                     'network_env_keys_present': [k for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR') if k in os.environ]},
        'trace': [], 'dns_observations': [], 'exception_chain': [],
        'http_status': None, 'received_bytes': 0, 'downloaded': False, 'body_read': False,
    }
    real_getaddrinfo = socket.getaddrinfo

    def observed_getaddrinfo(*args, **kwargs):
        started = time.monotonic()
        entry = {'host': clean(args[0]) if args else '', 'port': args[1] if len(args)>1 else None}
        try:
            result = real_getaddrinfo(*args, **kwargs)
            entry.update(status='SUCCESS', result_count=len(result), address_families=sorted({int(x[0]) for x in result}))
            return result
        except Exception as exc:
            entry.update(status='FAILED', exception_chain=exception_chain(exc))
            raise
        finally:
            entry['elapsed_ms'] = round((time.monotonic()-started)*1000, 2)
            report['dns_observations'].append(entry)

    def trace(event, info):
        entry = {'event': event}
        if 'exception' in info:
            entry['exception_chain'] = exception_chain(info['exception'])
        report['trace'].append(entry)

    append(dict(event='reserved', document_id=row['document_id'], priority_id=row['priority_id'],
                reserved_utc=utc(), tool='httpx_diagnostic', diagnostic_path=str(out.relative_to(ROOT))))
    result = dict(event='result', document_id=row['document_id'], priority_id=row['priority_id'],
                  fetch_status='FAILED', http_status='', received_bytes=0, reason='', tool='httpx_diagnostic')
    started = time.monotonic()
    socket.getaddrinfo = observed_getaddrinfo
    try:
        with httpx.Client(verify=True, trust_env=True, follow_redirects=False,
                          timeout=httpx.Timeout(20, connect=12),
                          headers={'User-Agent': 'SigongNote-Document-Review/1.0','Accept':'*/*'}) as client:
            with client.stream('GET', row['attachment_url'], extensions={'trace': trace}) as response:
                report['http_status'] = response.status_code
                result['http_status'] = response.status_code
                report['content_type'] = response.headers.get('content-type', '').split(';')[0]
                if response.status_code != 200:
                    result['reason'] = 'HTTP_'+str(response.status_code)
                elif response.headers.get('content-length','').isdigit() and int(response.headers['content-length']) > min(MAX_FILE, MAX_TOTAL-observed_bytes):
                    result['reason'] = 'DECLARED_SIZE_LIMIT'
                else:
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        report['received_bytes'] += len(chunk)
                        if report['received_bytes'] > min(MAX_FILE,MAX_TOTAL-observed_bytes):
                            result['reason'] = 'STREAM_SIZE_LIMIT'
                            break
                        data.extend(chunk)
                        if time.monotonic()-started > 45:
                            result['reason'] = 'TOTAL_TIME_LIMIT'
                            break
                    if not result['reason']:
                        kind = file_kind(data[:4096])
                        report['file_kind'] = kind
                        if kind in ('pdf','compound','zip','rtf'):
                            dest = WORK/'downloads'/(row['document_id']+'.bin')
                            with dest.open('xb') as f:
                                f.write(data)
                            report.update(downloaded=True, sha256=hashlib.sha256(data).hexdigest(), local_path=str(dest.relative_to(ROOT)))
                            result.update(fetch_status='DOWNLOADED',file_kind=kind,sha256=report['sha256'],local_path=report['local_path'])
                        else:
                            result['reason'] = 'NON_DOCUMENT_'+kind.upper()
    except Exception as exc:
        report['exception_chain'] = exception_chain(exc)
        result['reason'] = type(exc).__name__
    finally:
        socket.getaddrinfo = real_getaddrinfo
        report['elapsed_ms'] = round((time.monotonic()-started)*1000,2)
        report['finished_utc'] = utc()
        report['prior_ledger_prefix_preserved'] = (WORK/'fetch_ledger.jsonl').read_bytes().startswith(original_ledger)
        result.update(received_bytes=report['received_bytes'],received_utc=utc(),diagnostic_path=str((out/'diagnostic.json').relative_to(ROOT)))
        append(result)
        (out/'diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('DIAGNOSTIC_PATH='+str(out.relative_to(ROOT)))


if __name__ == '__main__':
    main()
