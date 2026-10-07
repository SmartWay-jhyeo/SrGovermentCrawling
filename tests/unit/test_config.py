from __future__ import annotations

from pathlib import Path

import pytest

from bidloc.config import ConfigError, load_settings
from tests.conftest import FAKE_KEY, make_settings


def test_defaults_without_env_file(project: Path):
    s = make_settings(project)
    assert s.service_key is None
    assert s.allow_live_api is False
    assert s.data_mode == "real"
    assert s.database_path == (project / ".local/real/bidloc.sqlite3").resolve()
    assert s.live_max_calls_per_run == 100 and s.live_max_calls_per_day == 100
    assert s.request_interval_seconds == 1.0


def test_env_file_and_process_env_precedence(project: Path):
    (project / ".env").write_text(
        f"DATA_GO_KR_SERVICE_KEY={FAKE_KEY}\nALLOW_LIVE_API=true\nLIVE_MAX_CALLS_PER_RUN=7\n", encoding="utf-8"
    )
    s = load_settings(project, environ={"LIVE_MAX_CALLS_PER_RUN": "5", "ALLOW_LIVE_API": ""})
    assert s.service_key is not None and s.service_key.reveal() == FAKE_KEY
    assert s.value_sources["DATA_GO_KR_SERVICE_KEY"] == ".env"
    assert s.allow_live_api is True  # 빈 프로세스 환경변수는 미설정으로 본다
    assert s.live_max_calls_per_run == 5
    assert s.value_sources["LIVE_MAX_CALLS_PER_RUN"] == "env"


def test_invalid_bool_is_rejected(project: Path):
    with pytest.raises(ConfigError, match="ALLOW_LIVE_API"):
        make_settings(project, ALLOW_LIVE_API="yes")


def test_key_with_whitespace_error_does_not_leak_key(project: Path):
    bad = "SECRET PART"
    with pytest.raises(ConfigError) as exc:
        make_settings(project, DATA_GO_KR_SERVICE_KEY=bad)
    assert "SECRET" not in str(exc.value)


def test_encoded_key_format_validation(project: Path):
    with pytest.raises(ConfigError):
        make_settings(project, DATA_GO_KR_SERVICE_KEY="abc+def", DATA_GO_KR_SERVICE_KEY_FORMAT="encoded")
    s = make_settings(project, DATA_GO_KR_SERVICE_KEY="abc%2Bdef", DATA_GO_KR_SERVICE_KEY_FORMAT="encoded")
    assert s.service_key_format == "encoded"


def test_decoded_key_that_looks_encoded_warns(project: Path):
    s = make_settings(project, DATA_GO_KR_SERVICE_KEY="abc%2Bdef%3D%3D")
    assert any("이중 인코딩" in w for w in s.warnings)


def test_settings_repr_hides_key(project: Path):
    s = make_settings(project, DATA_GO_KR_SERVICE_KEY=FAKE_KEY)
    assert FAKE_KEY not in repr(s)
    assert all(FAKE_KEY not in value for _, value, _ in s.safe_summary())


def test_demo_mode_uses_separate_paths(project: Path):
    s = make_settings(project, DATA_MODE="demo")
    assert "demo" in s.database_path.parts and "real" not in s.database_path.parts
    with pytest.raises(ConfigError):
        make_settings(project, DATA_MODE="demo", DATABASE_PATH=".local/real/x.sqlite3")
    with pytest.raises(ConfigError):
        make_settings(project, DATA_MODE="real", DATABASE_PATH=".local/demo/x.sqlite3")


@pytest.mark.parametrize("name,value", [("RETRY_MAX_ATTEMPTS", "0"), ("HTTP_TIMEOUT_SECONDS", "0"),
                                        ("LIVE_MAX_CALLS_PER_DAY", "-1"), ("DATA_MODE", "prod")])
def test_out_of_range_values(project: Path, name: str, value: str):
    with pytest.raises(ConfigError):
        make_settings(project, **{name: value})


def test_non_loopback_host_warns(project: Path):
    s = make_settings(project, APP_HOST="0.0.0.0")
    assert any("루프백" in w for w in s.warnings)


def test_env_example_keys_are_known(project: Path):
    repo_root = Path(__file__).resolve().parents[2]
    (project / ".env").write_text((repo_root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    s = load_settings(project, environ={})
    assert s.unknown_env_file_keys == ()
    assert s.service_key is None and s.allow_live_api is False


@pytest.mark.parametrize("name", ["HTTP_TIMEOUT_SECONDS", "RETRY_MAX_ATTEMPTS"])
def test_invalid_number_traceback_does_not_include_raw_value(project, name):
    import traceback

    with pytest.raises(ConfigError) as exc:
        make_settings(project, **{name: FAKE_KEY})
    assert FAKE_KEY not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb))


def test_collector_settings_load(project):
    from bidloc.collector_config import load_collector_config

    config = load_collector_config(project / "config/collector.yaml")
    assert config.target_license_codes == ("4992",)
    assert config.years_back == 3 and config.window_days == 1
    assert config.daily_limits == {"bid_notice": 800, "bid_award": 800}


@pytest.mark.parametrize("field,value", [
    ("num_of_rows", 1000), ("num_of_rows", True), ("window_days", 7),
    ("max_attempts", 0), ("max_restarts", 4), ("target_license_codes", []),
    ("stages", ["LIST", "LICENSE", "REGION", "LICENSE"]),
    ("daily_limits", {"bid_notice": 801, "bid_award": 800}),
])
def test_unsafe_collector_settings_rejected(project, field, value):
    import yaml
    from bidloc.collector_config import load_collector_config

    path = project / "config/collector.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_collector_config(path)
