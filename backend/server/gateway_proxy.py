from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException
from kimina_client import CheckRequest, CheckResponse, VerifyRequestBody, VerifyResponse

from .async_jobs import AsyncPollResponse, AsyncQueueMetrics, AsyncSubmitResponse
from .environment_registry import LeanEnvironmentInfo
from .settings import Settings


def _internal_headers(settings: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = settings.gateway_internal_api_key or settings.api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def proxy_check_request(
    *,
    request: CheckRequest,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> CheckResponse:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )

    payload = request.model_copy(update={"environment": target_environment.id}).model_dump()
    return await _post_json(
        url=f"{target_environment.url}/api/check",
        payload=payload,
        settings=settings,
        model=CheckResponse,
        timeout=max(float(request.timeout) + 5.0, 10.0),
    )


async def proxy_verify_request(
    *,
    request: VerifyRequestBody,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> VerifyResponse:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )

    payload = request.model_copy(update={"environment": target_environment.id}).model_dump()
    return await _post_json(
        url=f"{target_environment.url}/verify",
        payload=payload,
        settings=settings,
        model=VerifyResponse,
        timeout=max(float(request.timeout) + 5.0, 10.0),
    )


async def proxy_async_submit_request(
    *,
    request: CheckRequest,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> AsyncSubmitResponse:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )

    payload = request.model_copy(update={"environment": target_environment.id}).model_dump()
    return await _post_json(
        url=f"{target_environment.url}/api/async/check",
        payload=payload,
        settings=settings,
        model=AsyncSubmitResponse,
        timeout=max(float(request.timeout) + 5.0, 10.0),
    )


async def proxy_async_poll_request(
    *,
    job_id: str,
    wait_sec: float,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> AsyncPollResponse:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )
    return await _get_json(
        url=f"{target_environment.url}/api/async/check/{job_id}",
        params={"wait_sec": wait_sec},
        settings=settings,
        model=AsyncPollResponse,
        timeout=max(wait_sec + 5.0, 10.0),
    )


async def proxy_async_metrics_request(
    *,
    include_environments: bool,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> AsyncQueueMetrics:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )
    return await _get_json(
        url=f"{target_environment.url}/api/async/metrics",
        params={"include_environments": include_environments},
        settings=settings,
        model=AsyncQueueMetrics,
        timeout=10.0,
    )


async def proxy_health_request(
    *,
    target_environment: LeanEnvironmentInfo,
    settings: Settings,
) -> dict[str, Any]:
    if not target_environment.url:
        raise HTTPException(
            status_code=503,
            detail=f"Environment '{target_environment.id}' is not configured with a target URL.",
        )
    return await _get_json(
        url=f"{target_environment.url}/health",
        params=None,
        settings=settings,
        model=None,
        timeout=10.0,
    )


async def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    settings: Settings,
    model: type[CheckResponse | VerifyResponse],
    timeout: float,
) -> CheckResponse | VerifyResponse:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=_internal_headers(settings))
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach environment backend at {url}: {exc}",
        ) from exc

    try:
        response_body = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Environment backend at {url} returned non-JSON output.",
        ) from exc

    if response.status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail=f"Environment backend at {url} returned {response.status_code}.",
        )
    if response.status_code >= 400:
        detail = response_body
        if isinstance(response_body, dict):
            detail = response_body.get("detail", response_body)
        raise HTTPException(status_code=response.status_code, detail=detail)

    return model.model_validate(response_body)


async def _get_json(
    *,
    url: str,
    params: dict[str, Any] | None,
    settings: Settings,
    model: type[AsyncPollResponse | AsyncQueueMetrics] | None,
    timeout: float,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=_internal_headers(settings))
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach environment backend at {url}: {exc}",
        ) from exc

    try:
        response_body = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Environment backend at {url} returned non-JSON output.",
        ) from exc

    if response.status_code >= 500:
        raise HTTPException(
            status_code=502,
            detail=f"Environment backend at {url} returned {response.status_code}.",
        )
    if response.status_code >= 400:
        detail = response_body
        if isinstance(response_body, dict):
            detail = response_body.get("detail", response_body)
        raise HTTPException(status_code=response.status_code, detail=detail)

    if model is None:
        return response_body
    return model.model_validate(response_body)
