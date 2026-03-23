from __future__ import annotations

import asyncio
from time import monotonic

from loguru import logger

from .async_jobs import AsyncJobs
from .runtime_gateway import AsyncRailwayClient
from .settings import Settings

_UPDATE_REPLICAS_MUTATION = """
mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!) {
  serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input)
}
"""


class RuntimeIdleScaler:
    def __init__(self, settings: Settings, jobs: AsyncJobs) -> None:
        self._settings = settings
        self._jobs = jobs
        self._task: asyncio.Task[None] | None = None
        self._idle_since: float | None = None
        token = settings.autoscale_railway_token
        self._railway = AsyncRailwayClient(token) if token else None

    async def start(self) -> None:
        if self._railway is None:
            logger.debug("Runtime idle scaler disabled: no Railway token")
            return
        if not self._settings.runtime_service_id or not self._settings.railway_environment_id:
            logger.debug("Runtime idle scaler disabled: missing service/environment ids")
            return
        self._task = asyncio.create_task(self._loop(), name="runtime-idle-scaler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._railway is not None:
            await self._railway.close()
            self._railway = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(15.0)
                metrics = await self._jobs.metrics(runtime_id=self._settings.runtime_id)
                if metrics.queue_depth > 0 or metrics.running_tasks > 0:
                    self._idle_since = None
                    continue
                now = monotonic()
                if self._idle_since is None:
                    self._idle_since = now
                    continue
                if now - self._idle_since < float(self._settings.runtime_idle_ttl_sec):
                    continue
                await self._scale_to_zero()
                self._idle_since = now
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("Runtime idle scaler loop failed")

    async def _scale_to_zero(self) -> None:
        if self._railway is None or not self._settings.runtime_service_id:
            return
        await self._railway.gql(
            _UPDATE_REPLICAS_MUTATION,
            {
                "sid": self._settings.runtime_service_id,
                "eid": self._settings.railway_environment_id,
                "input": {
                    "multiRegionConfig": {
                        self._settings.railway_region: {"numReplicas": 0},
                    },
                },
            },
        )
        logger.info("Requested scale-to-zero for runtime {}", self._settings.runtime_id)
