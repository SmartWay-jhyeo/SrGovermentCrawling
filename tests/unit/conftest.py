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
