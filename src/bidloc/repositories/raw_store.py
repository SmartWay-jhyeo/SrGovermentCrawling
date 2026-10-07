"""원본 응답 저장.

- 본문은 비밀값 변형을 제거한 뒤 저장한다(API 키가 본문에 되돌아온 경우 포함).
- 파일 경로는 RAW_RESPONSE_DIR/<KST 날짜>/<service>/<operation>/ 아래로 고정하고 경로 이탈을 차단한다.
- 메타데이터(요청조건·HTTP 상태·업무코드·해시·파서 버전)는 source_response 테이블에 기록한다.
- 원본 디렉터리는 .gitignore 대상(.local/)이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bidloc import PARSER_VERSION
from bidloc.clients.envelope import ParsedBody
from bidloc.redaction import REGISTRY, redact
from bidloc.timeutil import KST, to_iso_utc

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_]+$")


class RawStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredBody:
    relative_path: str
    sha256: str
    size: int
    redaction_applied: bool


class ResponseRecorder:
    def __init__(self, conn: sqlite3.Connection, raw_dir: Path, data_mode: str) -> None:
        if data_mode not in {"real", "demo"}:
            raise ValueError("data_mode는 real 또는 demo")
        self._conn = conn
        self._raw_dir = Path(raw_dir).resolve()
        self._data_mode = data_mode

    @property
    def raw_dir(self) -> Path:
        return self._raw_dir

    def store_body(self, *, service_id: str, operation: str, requested_at: datetime, attempt_no: int,
                   body: bytes, fmt: str) -> StoredBody:
        for segment in (service_id, operation):
            if not _SAFE_SEGMENT.match(segment):
                raise RawStoreError("안전하지 않은 경로 구성요소")
        redacted, changed = REGISTRY.redact_bytes(body)
        digest = hashlib.sha256(redacted).hexdigest()
        ext = {"json": "json", "xml": "xml"}.get(fmt, "txt")
        local = requested_at.astimezone(KST)
        rel = Path(local.strftime("%Y-%m-%d")) / service_id / operation / (
            f"{local.strftime('%H%M%S')}_a{attempt_no}_{digest[:16]}.{ext}"
        )
        target = (self._raw_dir / rel).resolve()
        if self._raw_dir not in target.parents:
            raise RawStoreError("원본 저장 경로가 RAW_RESPONSE_DIR 밖이다")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp_", suffix=f".{ext}")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(redacted)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return StoredBody(relative_path=rel.as_posix(), sha256=digest, size=len(redacted), redaction_applied=changed)

    def record(
        self,
        *,
        run_id: str | None,
        service_id: str,
        operation: str,
        params_redacted: dict[str, str],
        url_redacted: str,
        attempt_no: int,
        requested_at: datetime,
        elapsed_ms: int | None,
        http_status: int | None,
        content_type: str | None,
        body: bytes | None,
        parsed: ParsedBody | None,
        outcome: str,
        classification_basis: str | None,
        error_detail: str | None,
    ) -> int:
        stored: StoredBody | None = None
        fmt = parsed.fmt if parsed is not None else "none"
        if body is not None:
            stored = self.store_body(service_id=service_id, operation=operation, requested_at=requested_at,
                                     attempt_no=attempt_no, body=body, fmt=fmt)
        if REGISTRY.contains_secret(url_redacted) or REGISTRY.contains_secret(json.dumps(params_redacted)):
            raise RawStoreError("마스킹되지 않은 비밀값이 요청 메타데이터에 남아 있다")
        cur = self._conn.execute(
            """
            INSERT INTO source_response (
                run_id, service_id, operation, request_params_redacted_json, request_url_redacted, attempt_no,
                requested_at_utc, elapsed_ms, http_status, content_type, response_format, envelope_shape,
                result_code, result_msg, outcome, classification_basis, page_no, num_of_rows, total_count,
                item_count, body_sha256, raw_path, body_bytes, parser_version, redaction_applied, data_mode, error_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, service_id, operation, json.dumps(params_redacted, ensure_ascii=False, sort_keys=True),
                url_redacted, attempt_no, to_iso_utc(requested_at), elapsed_ms, http_status,
                redact(content_type) if content_type else None, fmt,
                parsed.shape if parsed else None,
                redact(parsed.result_code_raw) if parsed and parsed.result_code_raw is not None else None,
                redact(parsed.result_msg)[:500] if parsed and parsed.result_msg is not None else None,
                outcome, redact(classification_basis) if classification_basis else None,
                parsed.page_no if parsed else None, parsed.num_of_rows if parsed else None,
                parsed.total_count if parsed else None, parsed.item_count if parsed else None,
                stored.sha256 if stored else None, stored.relative_path if stored else None,
                stored.size if stored else None, PARSER_VERSION, 1 if (stored and stored.redaction_applied) else 0,
                self._data_mode, redact(error_detail)[:2000] if error_detail else None,
            ),
        )
        return int(cur.lastrowid)

    def read_raw(self, relative_path: str) -> bytes:
        target = (self._raw_dir / relative_path).resolve()
        if self._raw_dir not in target.parents:
            raise RawStoreError("원본 경로가 RAW_RESPONSE_DIR 밖이다")
        return target.read_bytes()
