"""config/api_catalog.yaml 로딩과 검증.

카탈로그는 공식 문서에서 확인한 계약(DOCUMENTED)과 실응답 검증 상태를 구분해 담는다.
클라이언트는 카탈로그에 문서화된 서비스·오퍼레이션·요청 파라미터만 호출할 수 있다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ALLOWED_STATUSES = frozenset({"DOCUMENTED", "LIVE_VERIFIED", "UNVERIFIED", "BLOCKED"})
ALLOWED_HOSTS = frozenset({"apis.data.go.kr"})
_OP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_SERVICE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ParamSpec:
    name: str
    ko: str
    required_guide: bool | None
    required_swagger: bool | None
    description: str


@dataclass(frozen=True)
class OperationSpec:
    service_id: str
    name: str
    title_ko: str
    business_div: str
    status: str
    base_url: str
    auth_param: str
    params: dict[str, ParamSpec]
    response_fields: tuple[str, ...]
    in_p0_scope: bool
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    @property
    def url(self) -> str:
        return f"{self.base_url}/{self.name}"


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    name_ko: str
    base_url: str
    status: str
    operations: dict[str, OperationSpec]


@dataclass
class Catalog:
    path: Path
    sha256: str
    data: dict[str, Any]
    services: dict[str, ServiceSpec]

    def operation(self, service_id: str, name: str) -> OperationSpec:
        try:
            return self.services[service_id].operations[name]
        except KeyError as exc:
            raise CatalogError(f"카탈로그에 없는 오퍼레이션: {service_id}.{name}") from exc

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for svc in self.services.values():
            for op in svc.operations.values():
                counts[op.status] = counts.get(op.status, 0) + 1
        return counts


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise CatalogError(f"불리언 또는 null이어야 한다: {value!r}")


def validate_catalog_data(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["카탈로그 최상위는 매핑이어야 한다"]
    if data.get("catalog_schema_version") != 1:
        errors.append("catalog_schema_version은 1이어야 한다")
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        errors.append("services가 비어 있다")
        return errors
    for sid, svc in services.items():
        if not _SERVICE_ID.match(str(sid)):
            errors.append(f"잘못된 service id: {sid}")
        if not isinstance(svc, dict):
            errors.append(f"{sid}: 매핑이 아니다")
            continue
        base = svc.get("base_url", "")
        parts = urlsplit(str(base))
        if parts.scheme != "https":
            errors.append(f"{sid}: base_url은 https여야 한다")
        if parts.hostname not in ALLOWED_HOSTS:
            errors.append(f"{sid}: 허용되지 않은 호스트 {parts.hostname}")
        if parts.query or parts.fragment:
            errors.append(f"{sid}: base_url에 query/fragment가 있으면 안 된다")
        if svc.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{sid}: status가 허용값이 아니다")
        ops = svc.get("operations")
        if not isinstance(ops, dict) or not ops:
            errors.append(f"{sid}: operations가 비어 있다")
            continue
        for name, op in ops.items():
            where = f"{sid}.{name}"
            if not _OP_NAME.match(str(name)):
                errors.append(f"{where}: 오퍼레이션 이름 형식 오류")
            if not isinstance(op, dict):
                errors.append(f"{where}: 매핑이 아니다")
                continue
            status = op.get("status")
            if status not in ALLOWED_STATUSES:
                errors.append(f"{where}: status가 허용값이 아니다")
            if status == "LIVE_VERIFIED" and not op.get("live_evidence"):
                errors.append(f"{where}: LIVE_VERIFIED에는 live_evidence(실행 ID·일시)가 필요하다")
            if not op.get("auth_param"):
                errors.append(f"{where}: auth_param이 없다")
            seen: set[str] = set()
            for param in op.get("request_params") or []:
                pname = str(param.get("name", ""))
                if not _PARAM_NAME.match(pname):
                    errors.append(f"{where}: 잘못된 파라미터 이름 {pname!r}")
                if pname.lower() in seen:
                    errors.append(f"{where}: 중복 파라미터 {pname}")
                seen.add(pname.lower())
    return errors


def load_catalog(path: Path) -> Catalog:
    path = Path(path)
    raw_bytes = path.read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8"))
    errors = validate_catalog_data(data)
    if errors:
        raise CatalogError("카탈로그 검증 실패: " + " | ".join(errors[:20]))
    services: dict[str, ServiceSpec] = {}
    for sid, svc in data["services"].items():
        ops: dict[str, OperationSpec] = {}
        for name, op in svc["operations"].items():
            params = {}
            for param in op.get("request_params") or []:
                spec = ParamSpec(
                    name=str(param["name"]),
                    ko=str(param.get("ko", "")),
                    required_guide=_bool_or_none(param.get("required_guide")),
                    required_swagger=_bool_or_none(param.get("required_swagger")),
                    description=str(param.get("description", "")),
                )
                params[spec.name] = spec
            ops[name] = OperationSpec(
                service_id=sid,
                name=name,
                title_ko=str(op.get("title_ko", "")),
                business_div=str(op.get("business_div", "")),
                status=str(op["status"]),
                base_url=str(svc["base_url"]).rstrip("/"),
                auth_param=str(op["auth_param"]),
                params=params,
                response_fields=tuple(str(f.get("name")) for f in op.get("response_fields") or []),
                in_p0_scope=bool(op.get("in_p0_scope", False)),
                raw=op,
            )
        services[sid] = ServiceSpec(
            service_id=sid,
            name_ko=str(svc.get("service_name_ko", "")),
            base_url=str(svc["base_url"]).rstrip("/"),
            status=str(svc["status"]),
            operations=ops,
        )
    return Catalog(path=path, sha256=hashlib.sha256(raw_bytes).hexdigest(), data=data, services=services)


def default_catalog_path(project_root: Path) -> Path:
    return Path(project_root) / "config" / "api_catalog.yaml"
