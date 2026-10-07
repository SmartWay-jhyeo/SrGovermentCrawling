"""실연동 통합테스트 (기본 SKIPPED).

실행 조건: CLI --live + BIDLOC_RUN_LIVE_TESTS=1 + ALLOW_LIVE_API=true + 인증키 + DATA_MODE=real.
실제 호출은 모듈 전체에서 최대 3회로 제한하고, 프로젝트 DB의 일일 예산 카운터에 포함한다.
여기서 SKIPPED된 항목은 실연동 성공이 아니다. 전체 연결 검증은 `python -m bidloc verify-api --live`로 한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bidloc.catalog import default_catalog_path, load_catalog
from bidloc.clients.budget import CallBudget
from bidloc.clients.errors import DATA_OK
from bidloc.clients.http import DataGoKrClient, evaluate_live_gate
from bidloc.config import load_settings
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.repositories.raw_store import ResponseRecorder
from bidloc.repositories.runs import RunRepository, new_run_id

pytestmark = pytest.mark.live
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("SKIPPED: CLI --live 미지정")
    if os.environ.get("BIDLOC_RUN_LIVE_TESTS") != "1":
        pytest.skip("SKIPPED: BIDLOC_RUN_LIVE_TESTS=1이 아니다 (실연동 테스트는 명시적 허용 시에만 실행)")
    settings = load_settings(REPO_ROOT)
    gate = evaluate_live_gate(settings, cli_live=request.config.getoption("--live"))
    if not gate.allowed:
        pytest.skip("BLOCKED: " + "; ".join(gate.reasons))
    catalog = load_catalog(default_catalog_path(REPO_ROOT))
    conn = open_database(settings.database_path, default_migrations_dir(REPO_ROOT))
    run_id = new_run_id("pytest-live")
    RunRepository(conn).start(run_id=run_id, command="pytest-live", data_mode="real", live=True, status="RUNNING",
                              max_calls_run=3, catalog_sha256=catalog.sha256)
    budget = CallBudget(settings.database_path, max_per_run=3, max_per_day=settings.live_max_calls_per_day)
    client = DataGoKrClient(settings=settings, catalog=catalog, gate=gate, budget=budget,
                            recorder=ResponseRecorder(conn, settings.raw_response_dir, settings.data_mode), run_id=run_id)
    yield client
    client.close()
    RunRepository(conn).finish(run_id, status="COMPLETED", calls_attempted=budget.run_used, stop_reason=None)
    conn.close()


def test_industry_service_returns_documented_envelope(live):
    result = live.call("industry_law", "getIndstrytyBaseLawrgltInfoList",
                       {"indstrytyClsfcCd": "49", "pageNo": "1", "numOfRows": "10"})
    assert result.outcome in DATA_OK, f"{result.outcome.value}: {result.basis}"
    assert result.total_count is not None


def test_bid_notice_lookup_by_public_sample_number(live):
    result = live.call("bid_notice", "getBidPblancListInfoCnstwk",
                       {"inqryDiv": "2", "bidNtceNo": "R26BK01448245", "pageNo": "1", "numOfRows": "10"})
    assert result.outcome in DATA_OK, f"{result.outcome.value}: {result.basis}"
