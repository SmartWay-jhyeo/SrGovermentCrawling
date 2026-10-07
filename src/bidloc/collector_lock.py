"""프로세스 종료 시 OS가 해제하는 수집 잠금. 남은 lock 파일은 잠금 상태가 아니다."""
from contextlib import contextmanager
import os
from pathlib import Path

from bidloc.config import ConfigError


@contextmanager
def collector_lock(database: Path):
    path = database.with_suffix(database.suffix + ".collector.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConfigError("같은 DB의 스윕 수집이 실행 중이다. 기존 실행 종료 후 재개한다") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
