from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from kimina_client import Infotree, Snippet
from pydantic import BaseModel

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - exercised only when redis is unavailable
    Redis = object  # type: ignore[misc,assignment]


class AsyncTaskPayload(BaseModel):
    job_id: str
    task_id: str
    index: int
    snippet: Snippet
    timeout: float
    debug: bool
    reuse: bool
    infotree: Infotree | None = None
    enqueued_at: str

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        task_id: str,
        index: int,
        snippet: Snippet,
        timeout: float,
        debug: bool,
        reuse: bool,
        infotree: Infotree | None,
    ) -> "AsyncTaskPayload":
        return cls(
            job_id=job_id,
            task_id=task_id,
            index=index,
            snippet=snippet,
            timeout=timeout,
            debug=debug,
            reuse=reuse,
            infotree=infotree,
            enqueued_at=datetime.now(tz=timezone.utc).isoformat(),
        )


class TaskQueue(Protocol):
    async def length(self) -> int: ...

    async def enqueue_many(self, tasks: list[AsyncTaskPayload]) -> None: ...

    async def dequeue(self, timeout_sec: int = 1) -> AsyncTaskPayload | None: ...

    async def close(self) -> None: ...


@dataclass
class RedisTaskQueue:
    redis: Redis
    queue_name: str

    async def length(self) -> int:
        return int(await self.redis.llen(self.queue_name))

    async def enqueue_many(self, tasks: list[AsyncTaskPayload]) -> None:
        if not tasks:
            return
        payloads = [t.model_dump_json() for t in tasks]
        await self.redis.rpush(self.queue_name, *payloads)

    async def dequeue(self, timeout_sec: int = 1) -> AsyncTaskPayload | None:
        item = await self.redis.blpop(self.queue_name, timeout=timeout_sec)
        if item is None:
            return None
        _, payload = item
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return AsyncTaskPayload.model_validate_json(payload)

    async def close(self) -> None:
        await self.redis.aclose()


class InMemoryTaskQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def length(self) -> int:
        return self._q.qsize()

    async def enqueue_many(self, tasks: list[AsyncTaskPayload]) -> None:
        for task in tasks:
            await self._q.put(task.model_dump_json())

    async def dequeue(self, timeout_sec: int = 1) -> AsyncTaskPayload | None:
        try:
            payload = await asyncio.wait_for(self._q.get(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return None
        return AsyncTaskPayload.model_validate_json(payload)

    async def close(self) -> None:
        return None


def serialize_result(data: dict[str, object]) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def deserialize_result(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Stored result payload must be a JSON object")
    return parsed
