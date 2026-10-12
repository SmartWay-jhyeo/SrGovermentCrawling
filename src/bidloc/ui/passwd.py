"""화면 로그인 비밀번호 설정 도구.

    python -m bidloc.ui.passwd --user smartroad2025@gmail.com --write .env

비밀번호는 화면에 보이지 않게 두 번 입력받고, 해시만 `.env`에 쓴다(기존 줄은 바꾸고 없으면 덧붙인다). 평문은 어디에도
남지 않는다. `--write`가 없으면 `.env`에 붙여 넣을 두 줄만 출력한다.
"""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from bidloc.ui.auth import HASH_KEY, USER_KEY, hash_password


def upsert_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    out: list[str] = []
    # 해시에는 `$`가 들어 있어 docker compose가 변수로 오해한다. 작은따옴표로 감싸면 compose와 python-dotenv 모두
    # 그대로 읽는다.
    def render(key: str) -> str:
        value = values[key]
        return f"{key}='{value}'" if "$" in value else f"{key}={value}"

    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            out.append(render(key))
            seen.add(key)
        else:
            out.append(line)
    for key in values:
        if key not in seen:
            out.append(render(key))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, prompt=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description="화면 로그인 아이디·비밀번호 설정")
    parser.add_argument("--user", required=True, help="로그인 아이디(예: 이메일)")
    parser.add_argument("--write", metavar=".env", help="이 파일의 BIDLOC_UI_USER·BIDLOC_UI_PASSWORD_HASH를 갱신한다")
    args = parser.parse_args(argv)
    first = prompt("비밀번호(10자 이상): ")
    second = prompt("비밀번호 확인: ")
    if first != second:
        print("두 입력이 다르다. 다시 실행한다.")
        return 2
    try:
        encoded = hash_password(first)
    except ValueError as exc:
        print(str(exc))
        return 2
    values = {USER_KEY: args.user.strip(), HASH_KEY: encoded}
    if args.write:
        upsert_env(Path(args.write), values)
        print(f"{args.write}에 {USER_KEY}와 {HASH_KEY}를 기록했다. 화면을 다시 시작하면 적용된다.")
    else:
        print(f"{USER_KEY}={values[USER_KEY]}")
        print(f"{HASH_KEY}={encoded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
