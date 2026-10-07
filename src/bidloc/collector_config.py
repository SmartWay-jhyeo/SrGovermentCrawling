"""CODEX_PROMPT 수집 설정. API 응답 검증 상태와는 별개인 내부 설정이다."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from bidloc.config import ConfigError


@dataclass(frozen=True)
class CollectorConfig:
    years_back: int
    target_license_codes: tuple[str, ...]
    num_of_rows: int
    window_days: int
    max_attempts: int
    max_restarts: int
    stages: tuple[str, ...]
    daily_limits: dict[str, int]


def load_collector_config(path: Path) -> CollectorConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise ConfigError("collector.yaml YAML 형식 오류 (내용 비표시)") from None
    fields = {"schema_version", "years_back", "target_license_codes", "num_of_rows",
              "window_days", "max_attempts", "max_restarts", "stages", "daily_limits"}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigError("collector.yaml 필수 항목과 schema_version=1을 확인한다")

    def integer(name: str, minimum: int, maximum: int) -> int:
        value = data[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ConfigError(f"collector.yaml {name}: 정수 {minimum}~{maximum} 필요")
        return value

    codes = data["target_license_codes"]
    if not isinstance(codes, list) or not codes or any(
        type(code) not in (str, int) or not str(code).isascii() or not str(code).isdigit() for code in codes
    ):
        raise ConfigError("collector.yaml target_license_codes: 숫자 코드 목록 필요")
    stages = data["stages"]
    if not isinstance(stages, list) or any(type(stage) is not str for stage in stages) or tuple(stages) != ("LIST", "LICENSE", "REGION", "OPENING"):
        raise ConfigError("collector.yaml stages: LIST, LICENSE, REGION, OPENING 순서 필요")
    limits = data["daily_limits"]
    if not isinstance(limits, dict) or set(limits) != {"bid_notice", "bid_award"} or any(
        type(value) is not int or not 0 <= value <= 800 for value in limits.values()
    ):
        raise ConfigError("collector.yaml daily_limits: 두 서비스별 내부 한도 0~800 필요")
    return CollectorConfig(
        years_back=integer("years_back", 1, 3), target_license_codes=tuple(str(code) for code in codes),
        num_of_rows=integer("num_of_rows", 1, 999), window_days=integer("window_days", 1, 1),
        max_attempts=integer("max_attempts", 1, 3), max_restarts=integer("max_restarts", 0, 3),
        stages=tuple(stages), daily_limits=dict(limits),
    )
