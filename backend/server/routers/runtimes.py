from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request

from ..runtime_registry import RuntimeRegistry, RuntimeRegistryResponse

router = APIRouter()


def get_runtime_registry(request: Request) -> RuntimeRegistry:
    return cast(RuntimeRegistry, request.app.state.runtime_registry)


@router.get("/api/runtimes", response_model=RuntimeRegistryResponse)
@router.get("/api/runtimes/", response_model=RuntimeRegistryResponse, include_in_schema=False)
async def list_runtimes(request: Request) -> RuntimeRegistryResponse:
    return get_runtime_registry(request).as_response()
