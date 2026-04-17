from __future__ import annotations

import os
from typing import Iterable, Mapping

from pydantic import BaseModel

from .settings import Settings


class RuntimeDescriptor(BaseModel):
    runtime_id: str
    display_name: str
    lean_version: str
    repl_branch: str
    mathlib_branch: str
    project_type: str = "mathlib"
    project_label: str = "Mathlib"
    service_name: str
    service_id: str | None = None
    base_url: str | None = None
    is_default: bool = False


class RuntimeRegistryResponse(BaseModel):
    default_runtime_id: str
    runtimes: list[RuntimeDescriptor]


_SEEDED_RUNTIME_IDS = (
    "v4.9.0",
    "v4.15.0",
    "v4.24.0",
    "v4.27.0",
    "v4.28.0",
)


class RuntimeConfigurationError(RuntimeError):
    pass


def seeded_runtime_ids() -> tuple[str, ...]:
    return _SEEDED_RUNTIME_IDS


def runtime_env_key(runtime_id: str, suffix: str) -> str:
    return f"LEAN_SERVER_RUNTIME_{runtime_id.upper().replace('.', '_').replace('-', '_')}_{suffix}"


def _service_name_for(runtime_id: str) -> str:
    slug = runtime_id.lower().replace(".", "").replace("-", "")
    return f"lean-ui-{slug}"


def _build_descriptor(
    runtime_id: str,
    *,
    default_runtime_id: str,
    env: Mapping[str, str] | None = None,
) -> RuntimeDescriptor:
    env_map = os.environ if env is None else env
    base_url = env_map.get(runtime_env_key(runtime_id, "BASE_URL")) or None
    service_name = env_map.get(runtime_env_key(runtime_id, "SERVICE_NAME")) or _service_name_for(
        runtime_id
    )
    service_id = env_map.get(runtime_env_key(runtime_id, "SERVICE_ID")) or None
    label_suffix = runtime_id.removeprefix("v")
    return RuntimeDescriptor(
        runtime_id=runtime_id,
        display_name=f"Mathlib {label_suffix}",
        lean_version=runtime_id,
        repl_branch=runtime_id,
        mathlib_branch=runtime_id,
        service_name=service_name,
        service_id=service_id,
        base_url=base_url,
        is_default=runtime_id == default_runtime_id,
    )


class RuntimeRegistry:
    def __init__(self, runtimes: Iterable[RuntimeDescriptor], *, default_runtime_id: str) -> None:
        self._runtimes = list(runtimes)
        self._by_id = {runtime.runtime_id: runtime for runtime in self._runtimes}
        if default_runtime_id not in self._by_id:
            raise ValueError(f"Unknown default runtime: {default_runtime_id}")
        self.default_runtime_id = default_runtime_id

    def list(self) -> list[RuntimeDescriptor]:
        return list(self._runtimes)

    def get(self, runtime_id: str) -> RuntimeDescriptor | None:
        return self._by_id.get(runtime_id)

    def require(self, runtime_id: str) -> RuntimeDescriptor:
        runtime = self.get(runtime_id)
        if runtime is None:
            raise KeyError(runtime_id)
        return runtime

    def known_runtime_ids(self) -> list[str]:
        return [runtime.runtime_id for runtime in self._runtimes]

    def as_response(self) -> RuntimeRegistryResponse:
        return RuntimeRegistryResponse(
            default_runtime_id=self.default_runtime_id,
            runtimes=self.list(),
        )


def build_runtime_registry(
    default_runtime_id: str, env: Mapping[str, str] | None = None
) -> RuntimeRegistry:
    runtimes = [
        _build_descriptor(runtime_id, default_runtime_id=default_runtime_id, env=env)
        for runtime_id in _SEEDED_RUNTIME_IDS
    ]
    return RuntimeRegistry(runtimes, default_runtime_id=default_runtime_id)


def validate_runtime_configuration(
    settings: Settings,
    registry: RuntimeRegistry | None = None,
) -> None:
    selected_registry = registry or build_runtime_registry(settings.default_runtime_id)
    missing_env: list[str] = []
    errors: list[str] = []

    if settings.gateway_enabled:
        if not settings.railway_environment_id:
            missing_env.append("LEAN_SERVER_RAILWAY_ENVIRONMENT_ID")
        for runtime in selected_registry.list():
            if not runtime.service_id:
                missing_env.append(runtime_env_key(runtime.runtime_id, "SERVICE_ID"))
            if not runtime.base_url:
                missing_env.append(runtime_env_key(runtime.runtime_id, "BASE_URL"))
    elif settings.embedded_worker_enabled:
        if not settings.async_enabled:
            errors.append("LEAN_SERVER_EMBEDDED_WORKER_ENABLED requires LEAN_SERVER_ASYNC_ENABLED=true")
        if not settings.runtime_id:
            missing_env.append("LEAN_SERVER_RUNTIME_ID")
        if not settings.runtime_service_id:
            missing_env.append("LEAN_SERVER_RUNTIME_SERVICE_ID")
        if not settings.railway_environment_id:
            missing_env.append("LEAN_SERVER_RAILWAY_ENVIRONMENT_ID")
        if settings.init_repls:
            errors.append("LEAN_SERVER_INIT_REPLS must be {} for embedded runtime workers")
        runtime = selected_registry.get(settings.runtime_id)
        if runtime is None:
            errors.append(f"Unknown runtime_id: {settings.runtime_id}")
        else:
            if settings.lean_version != runtime.lean_version:
                errors.append(
                    "LEAN_SERVER_LEAN_VERSION must match LEAN_SERVER_RUNTIME_ID "
                    f"({settings.lean_version} != {runtime.lean_version})"
                )
            if runtime.service_id and settings.runtime_service_id != runtime.service_id:
                errors.append(
                    "LEAN_SERVER_RUNTIME_SERVICE_ID must match the seeded runtime registry "
                    f"({settings.runtime_service_id} != {runtime.service_id})"
                )

    if missing_env or errors:
        parts: list[str] = []
        if missing_env:
            parts.append("missing env vars: " + ", ".join(sorted(set(missing_env))))
        if errors:
            parts.append("invalid config: " + "; ".join(errors))
        raise RuntimeConfigurationError("Runtime configuration invalid: " + " | ".join(parts))
