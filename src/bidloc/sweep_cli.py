"""collector.yaml을 사용하는 스윕 CLI. 기존 backfill sweep-*도 계속 지원한다."""
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from bidloc.backfill.sweep import SweepConfig
from bidloc.collector_config import load_collector_config
from bidloc.collector_lock import collector_lock
from bidloc.config import ConfigError, load_settings
from bidloc.logging_setup import configure_logging
from bidloc.repositories.db import default_migrations_dir, open_database
from bidloc.timeutil import kst_today


def command(args):
    from bidloc.cli import _sweep

    root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    settings = load_settings(root)
    configure_logging(settings.log_dir)
    cfg = load_collector_config(Path(args.config) if args.config else root / "config/collector.yaml")
    today = kst_today()
    try:
        begin = today.replace(year=today.year - cfg.years_back)
    except ValueError:
        begin = today.replace(year=today.year - cfg.years_back, day=28)
    end = today - timedelta(days=1)
    if bool(args.begin) != bool(args.end):
        raise ConfigError("새 job 범위는 --begin과 --end를 함께 지정한다")
    if args.begin:
        begin, end = date.fromisoformat(args.begin), date.fromisoformat(args.end)
    if begin > end or end >= today:
        raise ConfigError("수집 범위는 시작일 <= 끝일 <= 어제(KST)여야 한다")
    args.sweep_config = SweepConfig(args.job_name, begin, end, cfg.num_of_rows, cfg.window_days,
                                   cfg.max_attempts, cfg.max_restarts, cfg.stages, cfg.target_license_codes,
                                   {**asdict(cfg), "range": [begin.isoformat(), end.isoformat()]})
    args.action = "sweep-" + args.action
    with collector_lock(settings.database_path):
        conn = open_database(settings.database_path, default_migrations_dir(root))
        try:
            return _sweep(args, settings, conn, root, SimpleNamespace(limits=cfg.daily_limits))
        finally:
            conn.close()
