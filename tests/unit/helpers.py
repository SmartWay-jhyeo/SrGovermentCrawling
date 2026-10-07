"""단위테스트 공용 도우미 (합성 응답 생성, MockTransport 클라이언트)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from bidloc.clients.budget import CallBudget
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.raw_store import ResponseRecorder
from tests.conftest import FAKE_KEY, make_settings


def std_json(items, total: int | str | None, code: str = "00", msg: str = "정상", page: int = 1, rows: int = 10) -> bytes:
    body: dict = {"items": items, "numOfRows": str(rows), "pageNo": str(page)}
    if total is not None:
        body["totalCount"] = str(total)
    return json.dumps({"response": {"header": {"resultCode": code, "resultMsg": msg}, "body": body}},
                      ensure_ascii=False).encode("utf-8")


def gateway_xml(code: str, auth_msg: str) -> bytes:
    return (f"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>{auth_msg}"
            f"</returnAuthMsg><returnReasonCode>{code}</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>").encode()


@dataclass
class FakeClock:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class ClientBundle:
    client: DataGoKrClient
    settings: object
    conn: object
    budget: CallBudget
    clock: FakeClock
    requests: list[httpx.Request]


def build_client(project: Path, catalog, handler: Callable[[httpx.Request], httpx.Response], *,
                 key: str = FAKE_KEY, key_format: str = "decoded", max_run: int = 100, max_day: int = 100,
                 retries: int = 3, interval: str = "0", run_id: str | None = None, **env: str) -> ClientBundle:
    settings = make_settings(project, DATA_GO_KR_SERVICE_KEY=key, DATA_GO_KR_SERVICE_KEY_FORMAT=key_format,
                             ALLOW_LIVE_API="true", RETRY_MAX_ATTEMPTS=str(retries), REQUEST_INTERVAL_SECONDS=interval,
                             **env)
    conn = open_database(settings.database_path, default_migrations_dir(project))
    budget = CallBudget(settings.database_path, max_per_run=max_run, max_per_day=max_day)
    recorder = ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode)
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    clock = FakeClock()
    client = DataGoKrClient(settings=settings, catalog=catalog, gate=evaluate_live_gate(settings, True), budget=budget,
                            recorder=recorder, run_id=run_id, transport=httpx.MockTransport(recording_handler),
                            sleep=clock.sleep, monotonic=clock.monotonic, jitter=lambda: 0.0)
    return ClientBundle(client, settings, conn, budget, clock, requests)
