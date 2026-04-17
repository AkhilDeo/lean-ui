from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request

from ..runtime_registry import RuntimeRegistry, RuntimeRegistryResponse
from ..settings import Settings

router = APIRouter()


def get_runtime_registry(request: Request) -> RuntimeRegistry:
    return cast(RuntimeRegistry, request.app.state.runtime_registry)


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.get("/api/runtimes", response_model=RuntimeRegistryResponse)
@router.get("/api/runtimes/", response_model=RuntimeRegistryResponse, include_in_schema=False)
async def list_runtimes(request: Request) -> RuntimeRegistryResponse:
    registry = get_runtime_registry(request)
    settings = get_runtime_settings(request)

    if settings.gateway_enabled:
        return registry.as_response()

    runtime = registry.require(settings.runtime_id).model_copy(update={"is_default": True})
    return RuntimeRegistryResponse(
        default_runtime_id=settings.runtime_id,
        runtimes=[runtime],
    )
