"""화면 로그인(아이디·비밀번호).

비밀번호는 평문으로 저장하지 않는다. `python -m bidloc.ui.passwd`가 만든 PBKDF2 해시를 `.env`의
BIDLOC_UI_PASSWORD_HASH에 두고, 아이디는 BIDLOC_UI_USER에 둔다. 두 값이 모두 있으면 화면이 로그인 화면을 먼저
보여 준다. 둘 다 없으면 로컬 개발로 보고 그냥 연다. 단, 환경변수 BIDLOC_UI_REQUIRE_LOGIN=1(NAS compose)이면 값이
없을 때 화면을 열지 않고 설정 안내만 보여 준다. 로그인 상태는 브라우저 세션(session_state)에만 있다.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from dotenv import dotenv_values

USER_KEY = "BIDLOC_UI_USER"
HASH_KEY = "BIDLOC_UI_PASSWORD_HASH"
REQUIRE_KEY = "BIDLOC_UI_REQUIRE_LOGIN"
_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_MIN_PASSWORD_LENGTH = 10
_MAX_FAILURES = 5
_LOCK_SECONDS = 60
# 실패 뒤 지연. 테스트는 이 이름만 바꾼다(전역 time.sleep을 바꾸면 Streamlit 테스트 러너가 멈춘다).
_delay = time.sleep


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    """`pbkdf2_sha256$<iterations>$<salt>$<hash>` 형식. 호출마다 다른 salt를 쓴다."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(f"비밀번호는 {_MIN_PASSWORD_LENGTH}자 이상이어야 한다")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    """형식이 깨졌거나 값이 없으면 False. 비교는 상수 시간이다."""
    if not encoded or not password:
        return False
    parts = encoded.strip().split("$")
    if len(parts) != 4 or parts[0] != _ALGORITHM:
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    if iterations < 1 or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def load_credentials(project_root: Path | None = None, environ=None) -> tuple[str | None, str | None]:
    """(아이디, 해시). 환경변수가 우선이고 비어 있으면 `.env` 파일 값을 쓴다. 둘 중 하나라도 없으면 (None, None)."""
    env = os.environ if environ is None else environ
    env_path = (project_root or Path.cwd()) / ".env"
    file_values = {k: (v or "") for k, v in dotenv_values(env_path).items()} if env_path.is_file() else {}

    def get(name: str) -> str | None:
        value = (env.get(name) or "").strip() or file_values.get(name, "").strip()
        return value or None

    user, encoded = get(USER_KEY), get(HASH_KEY)
    if user and encoded:
        return user, encoded
    return None, None


def login_required(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get(REQUIRE_KEY) or "").strip() in {"1", "true", "yes"}


def require_login() -> None:
    """Streamlit 화면 입구. 로그인 전이면 로그인 화면만 그리고 st.stop()한다."""
    import streamlit as st

    user, encoded = load_credentials()
    if user is None:
        if login_required():
            st.error("로그인 설정이 없어 화면을 열지 않습니다. `.env`에 BIDLOC_UI_USER와 BIDLOC_UI_PASSWORD_HASH를 넣으세요"
                     "(`python -m bidloc.ui.passwd --write .env`).")
            st.stop()
        return
    if st.session_state.get("auth_user") == user:
        return

    st.html('<div class="brand" style="font-size:1.4rem;margin:2rem 0 1rem">면허 입지 분석기</div>')
    locked_until = float(st.session_state.get("auth_locked_until", 0))
    now = time.time()
    if now < locked_until:
        st.warning(f"로그인 실패가 잦아 {int(locked_until - now)}초 뒤에 다시 시도할 수 있습니다.")
        st.stop()
    with st.form("login", clear_on_submit=False):
        typed_user = st.text_input("아이디", key="login_user", autocomplete="username")
        typed_password = st.text_input("비밀번호", type="password", key="login_password", autocomplete="current-password")
        submitted = st.form_submit_button("로그인", type="primary")
    if submitted:
        ok = hmac.compare_digest(typed_user.strip().encode("utf-8"), user.encode("utf-8")) and \
            verify_password(typed_password, encoded)
        if ok:
            st.session_state["auth_user"] = user
            st.session_state["auth_failures"] = 0
            st.rerun()
        failures = int(st.session_state.get("auth_failures", 0)) + 1
        st.session_state["auth_failures"] = failures
        _delay(1.0)
        if failures >= _MAX_FAILURES:
            st.session_state["auth_locked_until"] = time.time() + _LOCK_SECONDS
            st.session_state["auth_failures"] = 0
        st.error("아이디 또는 비밀번호가 맞지 않습니다.")
    st.stop()


def logout_button() -> None:
    import streamlit as st

    if st.session_state.get("auth_user"):
        if st.button("로그아웃", icon=":material/logout:", width="stretch", key="logout"):
            st.session_state.pop("auth_user", None)
            st.rerun()
