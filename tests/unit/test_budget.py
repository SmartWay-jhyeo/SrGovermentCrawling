"""API-06: 프로세스 재시작·동시 작업에서도 일일 예산을 넘지 않는다."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

from bidloc.clients.budget import BudgetExhausted, CallBudget
from bidloc.repositories.db import default_migrations_dir, open_database

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "budget.sqlite3"
    open_database(path, default_migrations_dir(REPO_ROOT)).close()
    return path


def test_run_limit(db_path: Path):
    budget = CallBudget(db_path, max_per_run=2, max_per_day=10)
    budget.reserve()
    budget.reserve()
    with pytest.raises(BudgetExhausted) as exc:
        budget.reserve()
    assert exc.value.scope == "run"
    assert budget.day_used() == 2


def test_daily_counter_survives_restart(db_path: Path):
    first = CallBudget(db_path, max_per_run=5, max_per_day=6)
    for _ in range(5):
        first.reserve()
    restarted = CallBudget(db_path, max_per_run=5, max_per_day=6)
    restarted.reserve()
    with pytest.raises(BudgetExhausted) as exc:
        restarted.reserve()
    assert exc.value.scope == "day" and restarted.day_used() == 6


def test_new_kst_day_has_new_counter(db_path: Path):
    day = {"value": date(2026, 9, 16)}
    budget = CallBudget(db_path, max_per_run=100, max_per_day=1, today=lambda: day["value"])
    budget.reserve()
    with pytest.raises(BudgetExhausted):
        budget.reserve()
    day["value"] = date(2026, 9, 17)
    budget.reserve()
    assert budget.day_used(date(2026, 9, 16)) == 1 and budget.day_used(date(2026, 9, 17)) == 1


def test_zero_budget_blocks_everything(db_path: Path):
    with pytest.raises(BudgetExhausted):
        CallBudget(db_path, max_per_run=0, max_per_day=10).reserve()
    with pytest.raises(BudgetExhausted):
        CallBudget(db_path, max_per_run=10, max_per_day=0).reserve()


def test_concurrent_threads_never_exceed_daily_limit(db_path: Path):
    limit = 50
    successes: list[int] = []
    failures: list[int] = []
    lock = threading.Lock()

    def worker():
        budget = CallBudget(db_path, max_per_run=100, max_per_day=limit)
        for _ in range(20):
            try:
                budget.reserve()
                with lock:
                    successes.append(1)
            except BudgetExhausted:
                with lock:
                    failures.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(successes) == limit and len(failures) == 8 * 20 - limit
    assert CallBudget(db_path, max_per_run=1, max_per_day=limit).day_used() == limit


def test_concurrent_processes_never_exceed_daily_limit(db_path: Path):
    code = (
        "import sys\n"
        "from bidloc.clients.budget import CallBudget, BudgetExhausted\n"
        "b = CallBudget(sys.argv[1], max_per_run=1000, max_per_day=40)\n"
        "ok = 0\n"
        "for _ in range(30):\n"
        "    try:\n"
        "        b.reserve(); ok += 1\n"
        "    except BudgetExhausted:\n"
        "        pass\n"
        "print(ok)\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(db_path)], stdout=subprocess.PIPE, text=True)
             for _ in range(3)]
    totals = [int(p.communicate(timeout=120)[0].strip()) for p in procs]
    assert sum(totals) == 40
    assert CallBudget(db_path, max_per_run=1, max_per_day=40).day_used() == 40
