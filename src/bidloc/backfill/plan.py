"""백필 설정(config/backfill.yaml) 로딩과 수집 범위 결정."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from bidloc.timeutil import kst_today


class BackfillConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BackfillConfig:
    raw: dict[str, Any]
    job_name: str
    list_service: str
    list_operation: str
    list_params: dict[str, str]
    window_days: int
    overlap_minutes: int
    num_of_rows: int
    max_partition_restarts: int
    target_license_codes: tuple[str, ...]
    detail_for_unknown: bool
    detail: dict[str, Any]
    limits: dict[str, int]
    recall_min_positives: int = 100
    recall_min_lower95: float = 0.95

    def range_for_new_job(self, today: date | None = None) -> tuple[date, date]:
        rng = self.raw.get("range") or {}
        if rng.get("from") and rng.get("to"):
            begin, end = date.fromisoformat(str(rng["from"])), date.fromisoformat(str(rng["to"]))
        else:
            base = today or kst_today()
            years = int(rng.get("years_back", 3))
            end = base - timedelta(days=1)
            try:
                begin = base.replace(year=base.year - years)
            except ValueError:  # 2월 29일
                begin = base.replace(year=base.year - years, day=28)
        if end < begin:
            raise BackfillConfigError("수집 범위 끝일이 시작일보다 앞선다")
        return begin, end

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_backfill_config(path: Path) -> BackfillConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise BackfillConfigError("backfill.yaml schema_version 1이 필요하다")
    try:
        lst = data["list"]
        rel = data.get("relevance") or {}
        detail = dict(data.get("detail") or {})
        cfg = BackfillConfig(
            raw=data,
            job_name=str(data["job_name"]),
            list_service=str(lst["service"]),
            list_operation=str(lst["operation"]),
            list_params={str(k): str(v) for k, v in (lst.get("params") or {}).items()},
            window_days=int(lst.get("window_days", 7)),
            overlap_minutes=int(lst.get("overlap_minutes", 1)),
            num_of_rows=int(lst.get("num_of_rows", 100)),
            max_partition_restarts=int(lst.get("max_partition_restarts", 3)),
            target_license_codes=tuple(str(c) for c in rel.get("target_license_codes") or []),
            detail_for_unknown=bool(rel.get("detail_for_unknown", True)),
            detail=detail,
            limits={str(k): int(v) for k, v in (data.get("limits") or {}).items()},
            recall_min_positives=int((data.get("recall_gate") or {}).get("min_positives", 100)),
            recall_min_lower95=float((data.get("recall_gate") or {}).get("min_recall_lower95", 0.95)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BackfillConfigError(f"backfill.yaml 형식 오류: {exc}") from exc
    if cfg.window_days < 1 or cfg.num_of_rows < 1 or not cfg.target_license_codes:
        raise BackfillConfigError("window_days·num_of_rows는 1 이상, target_license_codes는 1개 이상이어야 한다")
    for key in ("inqryBgnDt", "inqryEndDt", "pageNo", "numOfRows"):
        if key in cfg.list_params:
            raise BackfillConfigError(f"list.params에 {key}를 넣지 않는다(수집기가 관리)")
    return cfg
