"""Disposable JSON cache next to the DB; never changes the source database."""
import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from bidloc.query_service import load_snapshot


def fingerprint(database, mode):
    path=Path(database)
    stats=[(p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
           for p in (path,Path(str(path)+'-wal'))]
    root=Path(__file__).resolve().parents[1]
    code=hashlib.sha256(b''.join((root/name).read_bytes() for name in ('analysis.py','query_service.py'))).hexdigest()
    return hashlib.sha256(json.dumps([str(path.resolve()),mode,stats,code]).encode()).hexdigest()


def read_snapshot(database, mode):
    path=Path(database)
    if not path.is_file():
        return load_snapshot(database,mode)
    identity=fingerprint(database,mode)
    cache=path.parent/'ui-cache'/f'snapshot-{mode}.json.gz'
    try:
        with gzip.open(cache,'rt',encoding='utf-8') as stream:
            saved=json.load(stream)
        if saved['identity']==identity:
            return saved['records'],saved['metadata']
    except (OSError,ValueError,KeyError,EOFError):
        pass
    records,meta=load_snapshot(database,mode)
    # A concurrently changing DB can be read consistently, but should not seed a
    # disk cache advertised as the later database revision.
    if fingerprint(database,mode)==identity:
        temp=cache.with_name(uuid4().hex+'.tmp')
        try:
            cache.parent.mkdir(parents=True,exist_ok=True)
            with gzip.open(temp,'wt',encoding='utf-8',compresslevel=1) as stream:
                json.dump({'identity':identity,'records':records,'metadata':meta},stream,ensure_ascii=False)
            os.replace(temp,cache)
        except OSError:
            pass  # Cache is optional; readable DBs can reside in read-only folders.
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return records,meta
