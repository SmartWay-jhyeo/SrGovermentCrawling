"""Local read-only HTTP API for profile-based notice recommendations.

It serves the stored G2B snapshot and the staged provider notices; it never collects or calls an external
API. It binds to 127.0.0.1 by default. Listening on any other address requires BIDLOC_API_TOKEN, and every
request must then send "Authorization: Bearer <token>".
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import os
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from bidloc.config import load_settings
from bidloc.provider_notices import load_provider_notices, store_path
from bidloc.recommend import SOURCES, ProfileError, recommend, region_vocabulary
from bidloc.timeutil import now_kst
from bidloc.ui.cache import fingerprint, read_snapshot

TOKEN_ENV = "BIDLOC_API_TOKEN"
RECHECK_SECONDS = 60


class Snapshots:
    """Keeps the last good G2B snapshot and reloads a changed database in the background."""

    def __init__(self, database, data_mode, provider_store, *, reader=read_snapshot,
                 provider_reader=load_provider_notices, stamp=fingerprint, clock=time.monotonic):
        self.database, self.data_mode, self.provider_store = str(database), data_mode, Path(provider_store)
        self._reader, self._provider_reader, self._stamp, self._clock = reader, provider_reader, stamp, clock
        self._lock = threading.Lock()
        self._g2b = None          # (records, meta, identity)
        self._provider = None     # (file stamp, rows, meta)
        self._vocabulary = None   # (key, regions)
        self.loading, self.error, self._checked = False, None, None

    def refresh_g2b(self, wait=False):
        now = self._clock()
        with self._lock:
            # A failed load is not retried on every request; a multi-minute rebuild waits for the next check.
            if self.loading or (self._checked is not None and now - self._checked < RECHECK_SECONDS
                                and (self._g2b or self.error)):
                return
            self._checked = now
        identity = self._stamp(self.database, self.data_mode) if Path(self.database).is_file() else None
        with self._lock:
            if self._g2b and self._g2b[2] == identity:
                return
            self.loading = True

        def work():
            try:
                records, meta = self._reader(self.database, self.data_mode)
                with self._lock:
                    self._g2b, self.error = (records, meta, identity), None
            except Exception as exc:  # keep serving the previous snapshot; report only the error class
                with self._lock:
                    self.error = type(exc).__name__
            finally:
                with self._lock:
                    self.loading = False
        if wait:
            work()
        else:
            threading.Thread(target=work, name="g2b-snapshot", daemon=True).start()

    def g2b(self):
        self.refresh_g2b()
        with self._lock:
            return self._g2b

    def providers(self):
        path = self.provider_store
        stamp = (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else None
        with self._lock:
            cached = self._provider
        if cached and cached[0] == stamp:
            return cached[1], cached[2]
        rows, meta = self._provider_reader(path)
        with self._lock:
            self._provider = (stamp, rows, meta)
        return rows, meta

    def vocabulary(self, g2b, provider_rows):
        key = (id(g2b), id(provider_rows))
        with self._lock:
            if self._vocabulary and self._vocabulary[0] == key:
                return self._vocabulary[1]
        regions = region_vocabulary(g2b[0] if g2b else [], provider_rows)
        with self._lock:
            self._vocabulary = (key, regions)
        return regions


def flag(value, default):
    if value is None:
        return default
    if value.lower() in ("1", "true", "yes", "y"):
        return True
    if value.lower() in ("0", "false", "no", "n"):
        return False
    raise ProfileError("true 또는 false로 입력하세요.")


def number(value, name, low=0, high=None):
    if value in (None, ""):
        return None
    try:
        parsed = int(value.replace(",", ""))
    except ValueError:
        raise ProfileError(f"{name}는 숫자여야 합니다.") from None
    if parsed < low or (high is not None and parsed > high):
        raise ProfileError(f"{name} 범위를 벗어났습니다.")
    return parsed


def create_app(snapshots, *, token=None, data_mode="real"):
    def authorized(request):
        if not token:
            return True
        sent = request.headers.get("authorization", "")
        return hmac.compare_digest(sent.encode(), f"Bearer {token}".encode())

    def deny():
        return JSONResponse({"error": "인증이 필요합니다."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

    def health(request):
        if not authorized(request):
            return deny()
        g2b = snapshots.g2b()
        return JSONResponse({"status": "ok", "data_mode": data_mode,
                             "g2b_snapshot": "READY" if g2b else ("LOADING" if snapshots.loading else "UNAVAILABLE")})

    def recommendations(request):
        if not authorized(request):
            return deny()
        q = request.query_params
        try:
            sources = [s.strip() for s in q.get("sources", "").split(",") if s.strip()] or None
            limit = number(q.get("limit"), "limit", 1, 200) or 50
            offset = number(q.get("offset"), "offset", 0) or 0
            sort = q.get("sort", "deadline")
            if sort not in ("deadline", "notice_date", "amount"):
                raise ProfileError("sort는 deadline, notice_date, amount 중 하나입니다.")
            g2b = snapshots.g2b()
            provider_rows, _ = snapshots.providers()
            result = recommend(
                g2b[0] if g2b else [], provider_rows, region=q.get("region", ""), license=q.get("license", "4992"),
                sources=sources, keyword=q.get("keyword", ""), min_amount=number(q.get("min_amount"), "min_amount"),
                max_amount=number(q.get("max_amount"), "max_amount"),
                include_unknown=flag(q.get("include_unknown"), True), title_shortlist=flag(q.get("title_shortlist"), True),
                sort=sort, limit=limit, offset=offset, site_provinces=q.get("site_provinces"),
                known_regions=snapshots.vocabulary(g2b, provider_rows),
                g2b_ready=g2b is not None)
        except ProfileError as exc:
            return JSONResponse({"error": str(exc), "candidates": exc.candidates}, status_code=400)
        result["data"] = {"data_mode": data_mode,
                          "g2b": {k: (g2b[1] if g2b else {}).get(k) for k in ("snapshot_id", "generated_at_kst", "collection_status")},
                          "g2b_loading": snapshots.loading}
        return JSONResponse(result)

    def collection_status(request):
        if not authorized(request):
            return deny()
        g2b = snapshots.g2b()
        _, provider_meta = snapshots.providers()
        meta = g2b[1] if g2b else {}
        return JSONResponse({"data_mode": data_mode, "checked_at_kst": now_kst().isoformat(),
                             "g2b": {"state": "READY" if g2b else ("LOADING" if snapshots.loading else "UNAVAILABLE"),
                                     "error": snapshots.error, "records": len(g2b[0]) if g2b else None,
                                     **{k: meta.get(k) for k in ("snapshot_id", "generated_at_kst", "collection_status")}},
                             "providers": provider_meta.get("sources", {}), "sources": SOURCES})

    routes = [Route("/api/v1/health", health), Route("/api/v1/recommendations", recommendations),
              Route("/api/v1/collection/status", collection_status)]
    return Starlette(routes=routes)


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None):
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args(argv)
    token = os.environ.get(TOKEN_ENV) or None
    if not is_loopback(args.host) and not token:
        parser.error(f"127.0.0.1 밖으로 열려면 {TOKEN_ENV}를 설정하세요. 공개 배포는 별도 승인 사항입니다.")
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(root)
    snapshots = Snapshots(settings.database_path, settings.data_mode, store_path(settings.database_path))
    snapshots.refresh_g2b()
    print(f"bidloc API: http://{args.host}:{args.port}/api/v1/recommendations?region=경기도 남양주시&license=4992", flush=True)
    uvicorn.run(create_app(snapshots, token=token, data_mode=settings.data_mode), host=args.host, port=args.port,
                log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
