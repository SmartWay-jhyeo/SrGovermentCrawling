"""화면 로그인: 해시 검증, .env 기록 도구, 로그인 게이트. 네트워크 없음."""
from pathlib import Path
import socket

import pytest
from streamlit.testing.v1 import AppTest

from bidloc.ui import auth, passwd

APP = str(Path(__file__).resolve().parents[2] / "app.py")
_SOCKET_CONNECT = socket.socket.connect


@pytest.fixture
def loopback(monkeypatch):
    """AppTest는 이벤트 루프용 루프백 소켓이 필요하다. 외부 주소는 계속 막는다."""
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise RuntimeError("외부 네트워크는 테스트에서 금지됩니다.")
        return _SOCKET_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)


def test_hash_roundtrip_rejects_wrong_and_malformed():
    encoded = auth.hash_password("correct horse battery", iterations=1000)
    assert encoded.startswith("pbkdf2_sha256$1000$") and auth.verify_password("correct horse battery", encoded)
    assert not auth.verify_password("correct horse batter", encoded)
    assert auth.hash_password("correct horse battery", iterations=1000) != encoded  # salt differs
    for bad in (None, "", "plain", "pbkdf2_sha256$x$y$z", "md5$1$00$00", encoded.rsplit("$", 1)[0] + "$"):
        assert not auth.verify_password("correct horse battery", bad)
    with pytest.raises(ValueError):
        auth.hash_password("short")


def test_credentials_prefer_environment_and_need_both(tmp_path: Path):
    assert auth.load_credentials(tmp_path, environ={}) == (None, None)
    (tmp_path / ".env").write_text("BIDLOC_UI_USER=a@b.c\n", encoding="utf-8")
    assert auth.load_credentials(tmp_path, environ={}) == (None, None)
    (tmp_path / ".env").write_text("BIDLOC_UI_USER=a@b.c\nBIDLOC_UI_PASSWORD_HASH=h1\n", encoding="utf-8")
    assert auth.load_credentials(tmp_path, environ={}) == ("a@b.c", "h1")
    assert auth.load_credentials(tmp_path, environ={"BIDLOC_UI_PASSWORD_HASH": "h2"}) == ("a@b.c", "h2")
    assert auth.login_required({"BIDLOC_UI_REQUIRE_LOGIN": "1"}) and not auth.login_required({})


def test_passwd_tool_writes_hash_not_plaintext(tmp_path: Path, capsys):
    env = tmp_path / ".env"
    env.write_text("DATA_MODE=real\nBIDLOC_UI_USER=old@x.y\n", encoding="utf-8")
    answers = iter(["correct horse battery", "correct horse battery"])
    assert passwd.main(["--user", "a@b.c", "--write", str(env)], prompt=lambda _: next(answers)) == 0
    text = env.read_text(encoding="utf-8")
    assert "correct horse battery" not in text and text.count("BIDLOC_UI_USER=") == 1
    assert "BIDLOC_UI_USER=a@b.c" in text and "DATA_MODE=real" in text
    user, encoded = auth.load_credentials(tmp_path, environ={})
    assert user == "a@b.c" and auth.verify_password("correct horse battery", encoded)
    mismatch = iter(["correct horse battery", "different"])
    assert passwd.main(["--user", "a@b.c"], prompt=lambda _: next(mismatch)) == 2
    assert "different" not in capsys.readouterr().out


def test_login_gate_blocks_until_correct_password(monkeypatch, loopback):
    encoded = auth.hash_password("correct horse battery", iterations=1000)
    monkeypatch.setattr(auth, "load_credentials", lambda *a, **k: ("a@b.c", encoded))
    monkeypatch.setattr(auth, "_delay", lambda _: None)
    import bidloc.ui.app as ui

    def no_database():
        raise ui.ConfigError("synthetic: no database in this test")
    monkeypatch.setattr(ui, "load_settings", no_database)  # after login the app stops at its DB error
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and at.text_input(key="login_password") and not at.sidebar.button
    at.text_input(key="login_user").set_value("a@b.c")
    at.text_input(key="login_password").set_value("wrong password!")
    at.button[0].click().run()
    assert at.error and at.text_input(key="login_password")
    at.text_input(key="login_user").set_value("a@b.c")
    at.text_input(key="login_password").set_value("correct horse battery")
    at.button[0].click().run()
    assert at.session_state["auth_user"] == "a@b.c" and all(t.key != "login_password" for t in at.text_input)
    assert any(b.label == "로그아웃" for b in at.sidebar.button)


def test_missing_credentials_block_when_login_is_required(monkeypatch, loopback):
    monkeypatch.setattr(auth, "load_credentials", lambda *a, **k: (None, None))
    monkeypatch.setenv("BIDLOC_UI_REQUIRE_LOGIN", "1")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and at.error and not at.sidebar.button and not at.text_input
