from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from kimina_client import CheckRequest, ReplResponse
from loguru import logger
from pydantic import BaseModel

from .async_queue import (
    AsyncTaskPayload,
    InMemoryTaskQueue,
    RedisTaskQueue,
    TaskQueue,
    deserialize_result,
    serialize_result,
)
from .settings import Settings

try:
    from redis.asyncio import Redis, from_url as redis_from_url
except Exception:  # pragma: no cover - exercised only when redis is unavailable
    Redis = object  # type: ignore[misc,assignment]
    redis_from_url = None  # type: ignore[assignment]


class AsyncJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    expired = "expired"


class AsyncProgress(BaseModel):
    total: int
    done: int
    failed: int
    running: int


class AsyncSubmitResponse(BaseModel):
    job_id: str
    status: AsyncJobStatus
    total_snippets: int
    queued_at: str
    expires_at: str


class AsyncPollResponse(BaseModel):
    job_id: str
    status: AsyncJobStatus
    progress: AsyncProgress
    results: list[dict[str, Any]] | None = None
    created_at: str
    updated_at: str
    expires_at: str
    error: str | None = None


class AsyncBacklogFullError(Exception):
    pass


class AsyncJobs(Protocol):
    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse: ...

    async def poll(self, job_id: str) -> AsyncPollResponse | None: ...

    async def dequeue_task(self, timeout_sec: int = 1) -> AsyncTaskPayload | None: ...

    async def mark_task_started(self, task: AsyncTaskPayload) -> None: ...

    async def mark_task_success(
        self, task: AsyncTaskPayload, response: ReplResponse
    ) -> None: ...

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None: ...

    async def close(self) -> None: ...


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _expires_iso(ttl_sec: int) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_sec)).isoformat()


@dataclass
class RedisAsyncJobs:
    redis: Redis
    queue: RedisTaskQueue
    queue_name: str
    key_prefix: str
    ttl_sec: int
    backlog_limit: int

    def _meta_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:meta"

    def _results_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:results"

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        n = len(request.snippets)
        queue_depth = await self.queue.length()
        logger.info(
            "Async submit preflight (redis): queue={} depth={} incoming={} backlog_limit={}",
            self.queue_name,
            queue_depth,
            n,
            self.backlog_limit,
        )
        if queue_depth + n > self.backlog_limit:
            logger.warning(
                "Async submit rejected (redis): queue={} depth={} incoming={} backlog_limit={}",
                self.queue_name,
                queue_depth,
                n,
                self.backlog_limit,
            )
            raise AsyncBacklogFullError(
                f"Backlog limit exceeded ({queue_depth + n} > {self.backlog_limit})"
            )

        job_id = uuid4().hex
        queued_at = _now_iso()
        expires_at = _expires_iso(self.ttl_sec)
        meta_key = self._meta_key(job_id)
        results_key = self._results_key(job_id)

        tasks = [
            AsyncTaskPayload.create(
                job_id=job_id,
                task_id=uuid4().hex,
                index=i,
                snippet=snippet,
                timeout=float(request.timeout),
                debug=request.debug,
                reuse=request.reuse,
                infotree=request.infotree,
            )
            for i, snippet in enumerate(request.snippets)
        ]

        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(
            meta_key,
            mapping={
                "status": AsyncJobStatus.queued.value,
                "total": str(n),
                "done": "0",
                "failed": "0",
                "running": "0",
                "created_at": queued_at,
                "updated_at": queued_at,
                "expires_at": expires_at,
            },
        )
        if n > 0:
            pipe.rpush(results_key, *([""] * n))
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(results_key, self.ttl_sec)
        await pipe.execute()
        logger.info(
            "Async job metadata stored (redis): job_id={} total={} meta_key={} results_key={} ttl_sec={}",
            job_id,
            n,
            meta_key,
            results_key,
            self.ttl_sec,
        )

        try:
            await self.queue.enqueue_many(tasks)
            logger.info(
                "Async job enqueued (redis): job_id={} tasks={} queue={}",
                job_id,
                len(tasks),
                self.queue_name,
            )
        except Exception as e:
            await self.redis.hset(
                meta_key,
                mapping={"status": AsyncJobStatus.failed.value, "error": "enqueue_failed"},
            )
            logger.exception(
                "Async job enqueue failed (redis): job_id={} queue={} error={}",
                job_id,
                self.queue_name,
                e,
            )
            raise

        return AsyncSubmitResponse(
            job_id=job_id,
            status=AsyncJobStatus.queued,
            total_snippets=n,
            queued_at=queued_at,
            expires_at=expires_at,
        )

    async def _read_meta(self, job_id: str) -> dict[str, str] | None:
        key = self._meta_key(job_id)
        raw = await self.redis.hgetall(key)
        if not raw:
            return None
        out: dict[str, str] = {}
        for k, v in raw.items():
            key_s = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            val_s = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            out[key_s] = val_s
        return out

    async def poll(self, job_id: str) -> AsyncPollResponse | None:
        meta = await self._read_meta(job_id)
        if meta is None:
            logger.warning("Async poll miss (redis): job_id={}", job_id)
            return None

        status = AsyncJobStatus(meta.get("status", AsyncJobStatus.queued.value))
        total = int(meta.get("total", 0))
        done = int(meta.get("done", 0))
        failed = int(meta.get("failed", 0))
        running = int(meta.get("running", 0))

        results: list[dict[str, Any]] | None = None
        if status in {AsyncJobStatus.completed, AsyncJobStatus.failed}:
            raw = await self.redis.lrange(self._results_key(job_id), 0, -1)
            parsed: list[dict[str, Any]] = []
            for item in raw:
                value = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                if not value:
                    continue
                parsed.append(deserialize_result(value))
            if len(parsed) == total:
                results = parsed
        logger.debug(
            "Async poll hit (redis): job_id={} status={} done={} failed={} running={} total={} has_results={}",
            job_id,
            status.value,
            done,
            failed,
            running,
            total,
            results is not None,
        )

        return AsyncPollResponse(
            job_id=job_id,
            status=status,
            progress=AsyncProgress(total=total, done=done, failed=failed, running=running),
            results=results,
            created_at=meta.get("created_at", _now_iso()),
            updated_at=meta.get("updated_at", _now_iso()),
            expires_at=meta.get("expires_at", _expires_iso(self.ttl_sec)),
            error=meta.get("error"),
        )

    async def dequeue_task(self, timeout_sec: int = 1) -> AsyncTaskPayload | None:
        return await self.queue.dequeue(timeout_sec=timeout_sec)

    async def mark_task_started(self, task: AsyncTaskPayload) -> None:
        meta_key = self._meta_key(task.job_id)
        if not await self.redis.exists(meta_key):
            logger.warning(
                "Async task start ignored (redis, missing job): job_id={} task_id={} index={} snippet_id={}",
                task.job_id,
                task.task_id,
                task.index,
                task.snippet.id,
            )
            return
        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(
            meta_key,
            mapping={"status": AsyncJobStatus.running.value, "updated_at": _now_iso()},
        )
        pipe.hincrby(meta_key, "running", 1)
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(self._results_key(task.job_id), self.ttl_sec)
        await pipe.execute()
        logger.info(
            "Async task started (redis): job_id={} task_id={} index={} snippet_id={}",
            task.job_id,
            task.task_id,
            task.index,
            task.snippet.id,
        )

    async def _mark_result(
        self,
        *,
        task: AsyncTaskPayload,
        payload: dict[str, Any],
        is_failure: bool,
    ) -> None:
        meta_key = self._meta_key(task.job_id)
        results_key = self._results_key(task.job_id)
        if not await self.redis.exists(meta_key):
            logger.warning(
                "Async result write ignored (redis, missing job): job_id={} task_id={} index={} failure={}",
                task.job_id,
                task.task_id,
                task.index,
                is_failure,
            )
            return

        pipe = self.redis.pipeline(transaction=True)
        pipe.lset(results_key, task.index, serialize_result(payload))
        pipe.hincrby(meta_key, "running", -1)
        if is_failure:
            pipe.hincrby(meta_key, "failed", 1)
        else:
            pipe.hincrby(meta_key, "done", 1)
        pipe.hset(meta_key, mapping={"updated_at": _now_iso()})
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(results_key, self.ttl_sec)
        await pipe.execute()

        done_b, failed_b, total_b = await self.redis.hmget(
            meta_key, ("done", "failed", "total")
        )
        done = int(done_b or 0)
        failed = int(failed_b or 0)
        total = int(total_b or 0)
        logger.info(
            "Async result stored (redis): job_id={} task_id={} index={} snippet_id={} failure={} done={} failed={} total={}",
            task.job_id,
            task.task_id,
            task.index,
            task.snippet.id,
            is_failure,
            done,
            failed,
            total,
        )
        if done + failed >= total:
            await self.redis.hset(
                meta_key,
                mapping={
                    "status": AsyncJobStatus.completed.value,
                    "updated_at": _now_iso(),
                },
            )
            logger.info(
                "Async job completed (redis): job_id={} done={} failed={} total={}",
                task.job_id,
                done,
                failed,
                total,
            )

    async def mark_task_success(
        self, task: AsyncTaskPayload, response: ReplResponse
    ) -> None:
        await self._mark_result(
            task=task,
            payload=response.model_dump(exclude_none=True),
            is_failure=False,
        )

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None:
        response = ReplResponse(id=snippet_id, error=error, time=0.0)
        await self._mark_result(
            task=task,
            payload=response.model_dump(exclude_none=True),
            is_failure=True,
        )

    async def close(self) -> None:
        logger.info("Closing async jobs backend (redis): queue={}", self.queue_name)
        await self.queue.close()


class InMemoryAsyncJobs:
    def __init__(self, *, ttl_sec: int, backlog_limit: int) -> None:
        self.ttl_sec = ttl_sec
        self.backlog_limit = backlog_limit
        self.queue: InMemoryTaskQueue = InMemoryTaskQueue()
        self._meta: dict[str, dict[str, Any]] = {}
        self._results: dict[str, list[dict[str, Any] | None]] = {}
        self._lock = asyncio.Lock()

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        n = len(request.snippets)
        queue_depth = await self.queue.length()
        logger.info(
            "Async submit preflight (in-memory): queue_depth={} incoming={} backlog_limit={}",
            queue_depth,
            n,
            self.backlog_limit,
        )
        if queue_depth + n > self.backlog_limit:
            logger.warning(
                "Async submit rejected (in-memory): queue_depth={} incoming={} backlog_limit={}",
                queue_depth,
                n,
                self.backlog_limit,
            )
            raise AsyncBacklogFullError(
                f"Backlog limit exceeded ({queue_depth + n} > {self.backlog_limit})"
            )

        job_id = uuid4().hex
        queued_at = _now_iso()
        expires_at = _expires_iso(self.ttl_sec)
        tasks = [
            AsyncTaskPayload.create(
                job_id=job_id,
                task_id=uuid4().hex,
                index=i,
                snippet=snippet,
                timeout=float(request.timeout),
                debug=request.debug,
                reuse=request.reuse,
                infotree=request.infotree,
            )
            for i, snippet in enumerate(request.snippets)
        ]

        async with self._lock:
            self._meta[job_id] = {
                "status": AsyncJobStatus.queued,
                "total": n,
                "done": 0,
                "failed": 0,
                "running": 0,
                "created_at": queued_at,
                "updated_at": queued_at,
                "expires_at": expires_at,
                "error": None,
            }
            self._results[job_id] = [None] * n

        await self.queue.enqueue_many(tasks)
        logger.info(
            "Async job enqueued (in-memory): job_id={} tasks={} ttl_sec={}",
            job_id,
            len(tasks),
            self.ttl_sec,
        )
        return AsyncSubmitResponse(
            job_id=job_id,
            status=AsyncJobStatus.queued,
            total_snippets=n,
            queued_at=queued_at,
            expires_at=expires_at,
        )

    async def poll(self, job_id: str) -> AsyncPollResponse | None:
        async with self._lock:
            meta = self._meta.get(job_id)
            if meta is None:
                logger.warning("Async poll miss (in-memory): job_id={}", job_id)
                return None
            results = self._results.get(job_id, [])
            finalized = None
            if meta["status"] in {AsyncJobStatus.completed, AsyncJobStatus.failed}:
                if all(r is not None for r in results):
                    finalized = [r for r in results if r is not None]
            logger.debug(
                "Async poll hit (in-memory): job_id={} status={} done={} failed={} running={} total={} has_results={}",
                job_id,
                meta["status"].value,
                meta["done"],
                meta["failed"],
                meta["running"],
                meta["total"],
                finalized is not None,
            )
            return AsyncPollResponse(
                job_id=job_id,
                status=meta["status"],
                progress=AsyncProgress(
                    total=meta["total"],
                    done=meta["done"],
                    failed=meta["failed"],
                    running=meta["running"],
                ),
                results=finalized,
                created_at=meta["created_at"],
                updated_at=meta["updated_at"],
                expires_at=meta["expires_at"],
                error=meta["error"],
            )

    async def dequeue_task(self, timeout_sec: int = 1) -> AsyncTaskPayload | None:
        return await self.queue.dequeue(timeout_sec=timeout_sec)

    async def mark_task_started(self, task: AsyncTaskPayload) -> None:
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                logger.warning(
                    "Async task start ignored (in-memory, missing job): job_id={} task_id={} index={} snippet_id={}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                )
                return
            meta["status"] = AsyncJobStatus.running
            meta["running"] += 1
            meta["updated_at"] = _now_iso()
            logger.info(
                "Async task started (in-memory): job_id={} task_id={} index={} snippet_id={}",
                task.job_id,
                task.task_id,
                task.index,
                task.snippet.id,
            )

    async def mark_task_success(
        self, task: AsyncTaskPayload, response: ReplResponse
    ) -> None:
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                logger.warning(
                    "Async success write ignored (in-memory, missing job): job_id={} task_id={} index={} snippet_id={}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                )
                return
            results = self._results[task.job_id]
            results[task.index] = response.model_dump(exclude_none=True)
            meta["running"] = max(meta["running"] - 1, 0)
            meta["done"] += 1
            meta["updated_at"] = _now_iso()
            logger.info(
                "Async result stored (in-memory): job_id={} task_id={} index={} snippet_id={} failure=false done={} failed={} total={}",
                task.job_id,
                task.task_id,
                task.index,
                task.snippet.id,
                meta["done"],
                meta["failed"],
                meta["total"],
            )
            if meta["done"] + meta["failed"] >= meta["total"]:
                meta["status"] = AsyncJobStatus.completed
                logger.info(
                    "Async job completed (in-memory): job_id={} done={} failed={} total={}",
                    task.job_id,
                    meta["done"],
                    meta["failed"],
                    meta["total"],
                )

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None:
        response = ReplResponse(id=snippet_id, error=error, time=0.0)
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                logger.warning(
                    "Async failure write ignored (in-memory, missing job): job_id={} task_id={} index={} snippet_id={}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    snippet_id,
                )
                return
            results = self._results[task.job_id]
            results[task.index] = response.model_dump(exclude_none=True)
            meta["running"] = max(meta["running"] - 1, 0)
            meta["failed"] += 1
            meta["updated_at"] = _now_iso()
            logger.warning(
                "Async result stored (in-memory): job_id={} task_id={} index={} snippet_id={} failure=true done={} failed={} total={} error={}",
                task.job_id,
                task.task_id,
                task.index,
                snippet_id,
                meta["done"],
                meta["failed"],
                meta["total"],
                error,
            )
            if meta["done"] + meta["failed"] >= meta["total"]:
                meta["status"] = AsyncJobStatus.completed
                logger.info(
                    "Async job completed (in-memory): job_id={} done={} failed={} total={}",
                    task.job_id,
                    meta["done"],
                    meta["failed"],
                    meta["total"],
                )

    async def close(self) -> None:
        logger.info("Closing async jobs backend (in-memory)")
        await self.queue.close()


async def create_async_jobs(settings: Settings) -> AsyncJobs:
    if settings.async_use_in_memory_backend:
        logger.warning(
            "Async jobs configured with in-memory backend (non-durable): ttl_sec={} backlog_limit={}",
            settings.async_result_ttl_sec,
            settings.async_backlog_limit,
        )
        return InMemoryAsyncJobs(
            ttl_sec=settings.async_result_ttl_sec,
            backlog_limit=settings.async_backlog_limit,
        )

    if settings.redis_url is None:
        raise RuntimeError(
            "LEAN_SERVER_REDIS_URL must be configured when async backend is enabled"
        )
    if redis_from_url is None:
        raise RuntimeError(
            "redis dependency is not installed; install 'redis' to use async backend"
        )

    redis = redis_from_url(settings.redis_url, decode_responses=False)
    queue = RedisTaskQueue(redis=redis, queue_name=settings.async_queue_name)
    logger.info(
        "Async jobs configured with redis backend: queue={} key_prefix={} ttl_sec={} backlog_limit={}",
        settings.async_queue_name,
        settings.async_redis_key_prefix,
        settings.async_result_ttl_sec,
        settings.async_backlog_limit,
    )
    return RedisAsyncJobs(
        redis=redis,
        queue=queue,
        queue_name=settings.async_queue_name,
        key_prefix=settings.async_redis_key_prefix,
        ttl_sec=settings.async_result_ttl_sec,
        backlog_limit=settings.async_backlog_limit,
    )
