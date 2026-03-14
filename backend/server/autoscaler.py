"""Railway worker autoscaler — scales replicas based on async queue activity."""
from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

import httpx
from loguru import logger

from .async_jobs import AsyncJobs
from .settings import Settings

ENVIRONMENT_ID = "9ac4affd-7f62-415d-9c34-d2748db92462"
REGION = "us-east4-eqdc4a"
API_URL = "https://backboard.railway.com/graphql/v2"

_UPDATE_REPLICAS_MUTATION = """
mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input)
}
"""

_GET_STATE_QUERY = """
query($eid:String!,$sid:String!) {
  serviceInstance(environmentId:$eid, serviceId:$sid) {
    latestDeployment { meta }
  }
}
"""


def _extract_replicas(data: dict[str, Any]) -> int | None:
    latest = (data.get("serviceInstance") or {}).get("latestDeployment")
    if not latest:
        return None
    meta = latest.get("meta") or {}
    deploy = (meta.get("serviceManifest") or {}).get("deploy") or {}
    region_cfg = (deploy.get("multiRegionConfig") or {}).get(REGION, {})
    value = region_cfg.get("numReplicas", deploy.get("numReplicas"))
    if value is None:
        return None
    return int(value)


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
        resp = await self._client.post("", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]

    async def close(self) -> None:
        await self._client.aclose()


class WorkerAutoscaler:
    def __init__(self, settings: Settings, jobs: AsyncJobs) -> None:
        self._settings = settings
        self._jobs = jobs
        self._client: AsyncRailwayClient | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._last_scale_up_at: float = 0.0
        self._idle_since: float | None = None
        self._current_replicas: int | None = None

    async def start(self) -> None:
        token = self._settings.autoscale_railway_token
        if not token:
            logger.warning("Autoscaler enabled but no Railway token found; autoscaler disabled")
            return

        self._client = AsyncRailwayClient(token)
        self._loop_task = asyncio.create_task(self._idle_check_loop())
        logger.info(
            "Worker autoscaler started: service_id={} min={} max={} cooldown={}s throttle={}s",
            self._settings.autoscale_worker_service_id,
            self._settings.autoscale_min_replicas,
            self._settings.autoscale_max_replicas,
            self._settings.autoscale_cooldown_sec,
            self._settings.autoscale_throttle_sec,
        )

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("Worker autoscaler stopped")

    async def notify_submit(self) -> None:
        now = monotonic()
        if now - self._last_scale_up_at < self._settings.autoscale_throttle_sec:
            return
        self._last_scale_up_at = now
        self._idle_since = None
        asyncio.create_task(self._do_scale_up())

    async def _idle_check_loop(self) -> None:
        interval = self._settings.autoscale_check_interval_sec
        while True:
            try:
                await asyncio.sleep(interval)
                metrics = await self._jobs.metrics()
                busy = metrics.queue_depth > 0 or metrics.running_tasks > 0
                if busy:
                    self._idle_since = None
                else:
                    now = monotonic()
                    if self._idle_since is None:
                        self._idle_since = now
                    elif now - self._idle_since >= self._settings.autoscale_cooldown_sec:
                        await self._do_scale_down()
                        self._idle_since = now
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("Autoscaler idle-check loop error")

    async def _do_scale_up(self) -> None:
        target = self._settings.autoscale_max_replicas
        try:
            if self._current_replicas == target:
                return
            await self._set_replicas(target)
            logger.info("Autoscaler scaled UP workers to {} replicas", target)
        except Exception:
            logger.opt(exception=True).warning("Autoscaler scale-up failed")

    async def _do_scale_down(self) -> None:
        target = self._settings.autoscale_min_replicas
        try:
            if self._current_replicas == target:
                return
            await self._set_replicas(target)
            logger.info("Autoscaler scaled DOWN workers to {} replicas", target)
        except Exception:
            logger.opt(exception=True).warning("Autoscaler scale-down failed")

    async def _set_replicas(self, count: int) -> None:
        if self._client is None:
            return
        sid = self._settings.autoscale_worker_service_id
        await self._client.gql(
            _UPDATE_REPLICAS_MUTATION,
            {
                "sid": sid,
                "eid": ENVIRONMENT_ID,
                "input": {
                    "multiRegionConfig": {REGION: {"numReplicas": count}},
                },
            },
        )
        self._current_replicas = count

    async def _fetch_current_replicas(self) -> int | None:
        if self._client is None:
            return None
        sid = self._settings.autoscale_worker_service_id
        data = await self._client.gql(
            _GET_STATE_QUERY,
            {"eid": ENVIRONMENT_ID, "sid": sid},
        )
        return _extract_replicas(data)
