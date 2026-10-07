"""로깅 설정. 모든 핸들러에 마스킹 필터를 붙이고, URL을 기록하는 httpx/httpcore 로거는 WARNING 이상만 남긴다."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from bidloc.redaction import RedactingFilter

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(log_dir: Path | None, *, console_level: int = logging.WARNING,
                      file_level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(min(console_level, file_level))
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(_FORMAT)
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(console_level)
    stream.setFormatter(formatter)
    stream.addFilter(RedactingFilter())
    root.addHandler(stream)
    if log_dir is not None:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(Path(log_dir) / "bidloc.log", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
