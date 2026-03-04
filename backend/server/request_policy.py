from __future__ import annotations

from dataclasses import dataclass

from kimina_client import CheckRequest

from .settings import Settings


@dataclass(frozen=True)
class NormalizedRequestPolicy:
    timeout: float
    debug: bool
    reuse: bool


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
    effective_reuse = reuse if settings.allow_client_reuse_override else True
    return NormalizedRequestPolicy(
        timeout=effective_timeout,
        debug=effective_debug,
        reuse=effective_reuse,
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
        }
    )
