from __future__ import annotations

from dataclasses import dataclass

from kimina_client import CheckRequest

from .settings import Settings


@dataclass(frozen=True)
class NormalizedRequestPolicy:
    timeout: float
    debug: bool
    reuse: bool
    runtime_id: str


def normalize_request_policy(
    *,
    timeout: float,
    debug: bool,
    reuse: bool,
    settings: Settings,
) -> NormalizedRequestPolicy:
    max_timeout = float(settings.request_timeout_max_sec)
    if settings.allow_client_timeout_override:
        effective_timeout = min(timeout, max_timeout)
    else:
        effective_timeout = max_timeout

    effective_debug = debug if settings.allow_client_debug else False
    effective_runtime_id = (
        settings.default_runtime_id if settings.gateway_enabled else settings.runtime_id
    )
    return NormalizedRequestPolicy(
        timeout=effective_timeout,
        debug=effective_debug,
        reuse=reuse,
        runtime_id=effective_runtime_id,
    )


def normalize_check_request(request: CheckRequest, settings: Settings) -> CheckRequest:
    policy = normalize_request_policy(
        timeout=float(request.timeout),
        debug=request.debug,
        reuse=request.reuse,
        settings=settings,
    )
    return request.model_copy(
        update={
            "timeout": int(policy.timeout),
            "debug": policy.debug,
            "reuse": policy.reuse,
            "runtime_id": request.runtime_id or policy.runtime_id,
        }
    )
