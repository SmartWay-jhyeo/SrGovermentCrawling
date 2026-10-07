from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bidloc.catalog import load_catalog
from bidloc.config import load_settings
from bidloc.redaction import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]

# 합성 테스트 키. 실제 발급 키가 아니며 +, /, = 인코딩 검증용 문자를 포함한다.
FAKE_KEY = "SYNTHETICtestKEY+abc/def=ghi=="


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="실연동 테스트 허용(환경변수 허용과 키도 필요)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        skip = pytest.mark.skip(reason="SKIPPED: CLI --live 미지정 — 실제 API 호출 없음")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _clean_registry(request):
    if request.node.get_closest_marker("live"):
        yield  # 모듈 범위 live fixture가 등록한 키를 함수마다 지우지 않는다.
        return
    REGISTRY.clear()
    yield
    REGISTRY.clear()


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(REPO_ROOT / "config" / "api_catalog.yaml")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """임시 프로젝트 루트: config, migrations, .gitignore 복사본."""
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "config", root / "config")
    shutil.copytree(REPO_ROOT / "migrations", root / "migrations")
    shutil.copy(REPO_ROOT / ".gitignore", root / ".gitignore")
    return root


def make_settings(root: Path, **overrides: str):
    env = {
        "DATA_GO_KR_SERVICE_KEY": "",
        "ALLOW_LIVE_API": "false",
        "REQUEST_INTERVAL_SECONDS": "1.0",
        "RETRY_MAX_ATTEMPTS": "3",
        "LIVE_MAX_CALLS_PER_RUN": "100",
        "LIVE_MAX_CALLS_PER_DAY": "100",
    }
    env.update(overrides)
    return load_settings(root, environ=env, env_file=root / ".env.none")
