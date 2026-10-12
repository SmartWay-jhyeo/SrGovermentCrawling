"""단위테스트는 네트워크를 쓰지 않는다. 소켓 연결·DNS 조회를 차단해 실수로 실호출하면 실패시킨다."""

from __future__ import annotations

import logging
import socket

import pytest


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """configure_logging이 바꾼 루트 핸들러를 테스트마다 닫고 원래대로 돌린다(Windows 임시파일 잠금 방지)."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in saved_handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(saved_level)
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)


class NetworkBlocked(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def deny(*args, **kwargs):
        raise NetworkBlocked("단위테스트에서 네트워크 접근이 차단되었다")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    yield


@pytest.fixture(autouse=True)
def _no_ui_login(monkeypatch):
    """화면 테스트는 이 PC의 .env에 로그인 값이 있어도 로그인 화면을 거치지 않는다. 로그인 자체를 검사하는
    테스트는 `real_credentials` 픽스처나 자기 monkeypatch로 다시 덮어쓴다."""
    import bidloc.ui.auth as auth

    monkeypatch.setattr(auth, "load_credentials", lambda *a, **k: (None, None))
    monkeypatch.delenv("BIDLOC_UI_REQUIRE_LOGIN", raising=False)


@pytest.fixture
def real_credentials(monkeypatch):
    """`.env`·환경변수를 실제로 읽는 원래 load_credentials로 되돌린다."""
    import bidloc.ui.auth as auth

    monkeypatch.setattr(auth, "load_credentials", _REAL_LOAD_CREDENTIALS)


def _real_load_credentials():
    import bidloc.ui.auth as auth

    return auth.load_credentials


_REAL_LOAD_CREDENTIALS = _real_load_credentials()
