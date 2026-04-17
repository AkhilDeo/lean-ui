from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException
from kimina_client.models import CheckResponse
from loguru import logger

from .runtime_registry import RuntimeDescriptor, RuntimeRegistry
from .settings import Settings

API_URL = "https://backboard.railway.com/graphql/v2"
WAKE_PING_TIMEOUT_SEC = 5.0

_UPDATE_REPLICAS_MUTATION = """
mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input)
}
"""


class AsyncRailwayClient:
    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        response = await self._client.post("", json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]

    async def close(self) -> None:
        await self._client.aclose()


def _build_auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


class RuntimeGateway:
    def __init__(self, settings: Settings, registry: RuntimeRegistry) -> None:
        self._settings = settings
        self._registry = registry
        self._http = httpx.AsyncClient(timeout=30.0)
        token = settings.autoscale_railway_token
        self._railway = AsyncRailwayClient(token) if token else None

    async def close(self) -> None:
        await self._http.aclose()
        if self._railway is not None:
            await self._railway.close()

    def registry(self) -> RuntimeRegistry:
        return self._registry

    def require_runtime(self, runtime_id: str) -> RuntimeDescriptor:
        runtime = self._registry.get(runtime_id)
        if runtime is None:
            raise HTTPException(status_code=400, detail=f"Unknown runtime_id: {runtime_id}")
        return runtime

    async def is_runtime_warm(self, runtime: RuntimeDescriptor) -> bool:
        if not runtime.base_url:
            return False
        try:
            response = await self._http.get(
                f"{runtime.base_url.rstrip('/')}/health",
                headers=_build_auth_headers(self._settings.api_key),
                timeout=float(self._settings.gateway_sync_proxy_timeout_sec),
            )
        except Exception:
            return False
        if not response.is_success:
            return False
        try:
            payload = response.json()
        except Exception:
            return False
        return payload.get("ready") is True

    async def wake_runtime(self, runtime: RuntimeDescriptor) -> None:
        if runtime.base_url:
            try:
                await self._http.get(
                    f"{runtime.base_url.rstrip('/')}/health",
                    headers=_build_auth_headers(self._settings.api_key),
                    timeout=WAKE_PING_TIMEOUT_SEC,
                )
                logger.info("Issued runtime wake ping for {}", runtime.runtime_id)
                return
            except Exception:
                logger.debug("Runtime wake ping failed for {}", runtime.runtime_id)
        if self._railway is None:
            logger.debug("Skipping runtime wake for {}: no Railway token", runtime.runtime_id)
            return
        if not runtime.service_id or not self._settings.railway_environment_id:
            logger.debug(
                "Skipping runtime wake for {}: missing service/environment ids",
                runtime.runtime_id,
            )
            return
        await self._railway.gql(
            _UPDATE_REPLICAS_MUTATION,
            {
                "sid": runtime.service_id,
                "eid": self._settings.railway_environment_id,
                "input": {
                    "multiRegionConfig": {
                        self._settings.railway_region: {
                            "numReplicas": self._settings.gateway_wake_replicas,
                        }
                    },
                },
            },
        )
        logger.info("Requested wake for runtime {}", runtime.runtime_id)

    async def proxy_sync_check(self, runtime: RuntimeDescriptor, payload: dict[str, Any]) -> CheckResponse | None:
        if not runtime.base_url:
            return None
        response = await self._http.post(
            f"{runtime.base_url.rstrip('/')}/api/check",
            headers=_build_auth_headers(self._settings.api_key),
            json=payload,
            timeout=float(self._settings.gateway_sync_proxy_timeout_sec),
        )
        if not response.is_success:
            return None
        return CheckResponse.model_validate(response.json())
