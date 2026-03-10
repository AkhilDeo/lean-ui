from __future__ import annotations

import asyncio
import time
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


class AsyncQueueMetrics(BaseModel):
    queue_depth: int
    inflight_jobs: int
    running_tasks: int
    oldest_queued_age_sec: float
    dequeue_rate: float
    enqueue_rate: float


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

    async def metrics(self) -> AsyncQueueMetrics: ...

    async def recover_running_tasks(self) -> int: ...

    async def close(self) -> None: ...


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _expires_iso(ttl_sec: int) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_sec)).isoformat()


def _iso_to_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


METRICS_STARTED_AT_FIELD = "started_at_epoch_s"
METRICS_ENQUEUED_FIELD = "enqueued_tasks"
METRICS_DEQUEUED_FIELD = "dequeued_tasks"
METRICS_INFLIGHT_JOBS_FIELD = "inflight_jobs"
METRICS_RUNNING_TASKS_FIELD = "running_tasks"


def _decode_redis_hash(raw: dict[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in raw.items():
        key_s = k.decode("utf-8") if isinstance(k, bytes) else str(k)
        val_s = v.decode("utf-8") if isinstance(v, bytes) else str(v)
        out[key_s] = val_s
    return out


def _metrics_from_meta_snapshots(metas: list[dict[str, str]]) -> tuple[int, int]:
    inflight_jobs = 0
    running_tasks = 0
    for meta in metas:
        status = meta.get("status", AsyncJobStatus.queued.value)
        total = int(meta.get("total", 0))
        done = int(meta.get("done", 0))
        failed = int(meta.get("failed", 0))
        running = int(meta.get("running", 0))
        running_tasks += max(running, 0)
        is_terminal = status in {
            AsyncJobStatus.completed.value,
            AsyncJobStatus.failed.value,
            AsyncJobStatus.expired.value,
        }
        if not is_terminal and (done + failed) < total:
            inflight_jobs += 1
    return inflight_jobs, running_tasks


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

    def _tasks_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:tasks"

    def _task_states_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:task_states"

    def _metrics_key(self) -> str:
        return f"{self.key_prefix}:queue:{self.queue_name}:metrics"

    async def _record_enqueue_count(self, count: int) -> None:
        if count <= 0:
            return
        key = self._metrics_key()
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        pipe.hincrby(key, METRICS_ENQUEUED_FIELD, count)
        await pipe.execute()

    async def _record_dequeue_count(self, count: int) -> None:
        if count <= 0:
            return
        key = self._metrics_key()
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        pipe.hincrby(key, METRICS_DEQUEUED_FIELD, count)
        await pipe.execute()

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        n = len(request.snippets)
        queue_depth = await self.queue.length()
        logger.debug(
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
        job_logger = logger.bind(job_id=job_id)
        queued_at = _now_iso()
        expires_at = _expires_iso(self.ttl_sec)
        meta_key = self._meta_key(job_id)
        metrics_key = self._metrics_key()
        results_key = self._results_key(job_id)
        tasks_key = self._tasks_key(job_id)
        task_states_key = self._task_states_key(job_id)

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
            pipe.hset(
                tasks_key,
                mapping={str(task.index): task.model_dump_json() for task in tasks},
            )
            pipe.hset(
                task_states_key,
                mapping={str(task.index): AsyncJobStatus.queued.value for task in tasks},
            )
            pipe.hsetnx(metrics_key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
            pipe.hincrby(metrics_key, METRICS_INFLIGHT_JOBS_FIELD, 1)
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(results_key, self.ttl_sec)
        pipe.expire(tasks_key, self.ttl_sec)
        pipe.expire(task_states_key, self.ttl_sec)
        await pipe.execute()
        job_logger.debug(
            "Async job metadata stored (redis): job_id={} total={} meta_key={} results_key={} ttl_sec={}",
            job_id,
            n,
            meta_key,
            results_key,
            self.ttl_sec,
        )

        try:
            await self.queue.enqueue_many(tasks)
            await self._record_enqueue_count(len(tasks))
            job_logger.debug(
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
            job_logger.exception(
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
        return _decode_redis_hash(raw)

    async def poll(self, job_id: str) -> AsyncPollResponse | None:
        job_logger = logger.bind(job_id=job_id)
        meta = await self._read_meta(job_id)
        if meta is None:
            job_logger.warning("Async poll miss (redis): job_id={}", job_id)
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
        job_logger.debug(
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
        task = await self.queue.dequeue(timeout_sec=timeout_sec)
        if task is not None:
            await self._record_dequeue_count(1)
        return task

    async def mark_task_started(self, task: AsyncTaskPayload) -> None:
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=task.snippet.id,
        )
        meta_key = self._meta_key(task.job_id)
        task_states_key = self._task_states_key(task.job_id)
        metrics_key = self._metrics_key()
        if not await self.redis.exists(meta_key):
            task_logger.warning(
                "Async task start ignored (redis, missing job): job_id={} task_id={} index={} snippet_id={}",
                task.job_id,
                task.task_id,
                task.index,
                task.snippet.id,
            )
            return
        state_raw = await self.redis.hget(task_states_key, str(task.index))
        state = (
            state_raw.decode("utf-8")
            if isinstance(state_raw, bytes)
            else str(state_raw or AsyncJobStatus.queued.value)
        )
        if state in {AsyncJobStatus.completed.value, AsyncJobStatus.failed.value}:
            task_logger.debug(
                "Async task start skipped (redis, already terminal): job_id={} task_id={} index={} state={}",
                task.job_id,
                task.task_id,
                task.index,
                state,
            )
            return
        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(
            meta_key,
            mapping={"status": AsyncJobStatus.running.value, "updated_at": _now_iso()},
        )
        if state != AsyncJobStatus.running.value:
            pipe.hset(task_states_key, str(task.index), AsyncJobStatus.running.value)
            pipe.hincrby(meta_key, "running", 1)
            pipe.hincrby(metrics_key, METRICS_RUNNING_TASKS_FIELD, 1)
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(self._results_key(task.job_id), self.ttl_sec)
        pipe.expire(task_states_key, self.ttl_sec)
        await pipe.execute()
        task_logger.debug(
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
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=task.snippet.id,
        )
        meta_key = self._meta_key(task.job_id)
        metrics_key = self._metrics_key()
        results_key = self._results_key(task.job_id)
        task_states_key = self._task_states_key(task.job_id)
        tasks_key = self._tasks_key(task.job_id)
        if not await self.redis.exists(meta_key):
            await self.redis.hincrby(metrics_key, METRICS_RUNNING_TASKS_FIELD, -1)
            task_logger.warning(
                "Async result write ignored (redis, missing job): job_id={} task_id={} index={} failure={}",
                task.job_id,
                task.task_id,
                task.index,
                is_failure,
            )
            return
        state_raw = await self.redis.hget(task_states_key, str(task.index))
        state = (
            state_raw.decode("utf-8")
            if isinstance(state_raw, bytes)
            else str(state_raw or AsyncJobStatus.queued.value)
        )
        if state in {AsyncJobStatus.completed.value, AsyncJobStatus.failed.value}:
            task_logger.debug(
                "Async result ignored (redis, already terminal): job_id={} task_id={} index={} state={}",
                task.job_id,
                task.task_id,
                task.index,
                state,
            )
            return

        pipe = self.redis.pipeline(transaction=True)
        pipe.lset(results_key, task.index, serialize_result(payload))
        pipe.hset(
            task_states_key,
            str(task.index),
            AsyncJobStatus.failed.value if is_failure else AsyncJobStatus.completed.value,
        )
        pipe.hdel(tasks_key, str(task.index))
        if state == AsyncJobStatus.running.value:
            pipe.hincrby(meta_key, "running", -1)
            pipe.hincrby(metrics_key, METRICS_RUNNING_TASKS_FIELD, -1)
        if is_failure:
            pipe.hincrby(meta_key, "failed", 1)
        else:
            pipe.hincrby(meta_key, "done", 1)
        pipe.hset(meta_key, mapping={"updated_at": _now_iso()})
        pipe.expire(meta_key, self.ttl_sec)
        pipe.expire(results_key, self.ttl_sec)
        pipe.expire(task_states_key, self.ttl_sec)
        pipe.expire(tasks_key, self.ttl_sec)
        await pipe.execute()

        done_b, failed_b, total_b = await self.redis.hmget(
            meta_key, ("done", "failed", "total")
        )
        done = int(done_b or 0)
        failed = int(failed_b or 0)
        total = int(total_b or 0)
        task_logger.debug(
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
            pipe = self.redis.pipeline(transaction=True)
            pipe.hset(
                meta_key,
                mapping={
                    "status": AsyncJobStatus.completed.value,
                    "updated_at": _now_iso(),
                },
            )
            pipe.hincrby(metrics_key, METRICS_INFLIGHT_JOBS_FIELD, -1)
            await pipe.execute()
            task_logger.debug(
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

    async def _oldest_queue_age_sec(self) -> float:
        first = await self.redis.lindex(self.queue_name, 0)
        if first is None:
            return 0.0
        payload = first.decode("utf-8") if isinstance(first, bytes) else str(first)
        try:
            task = AsyncTaskPayload.model_validate_json(payload)
        except Exception:
            return 0.0
        enqueued_at = _iso_to_datetime(task.enqueued_at)
        if enqueued_at is None:
            return 0.0
        return max((datetime.now(tz=timezone.utc) - enqueued_at).total_seconds(), 0.0)

    async def _all_meta(self) -> list[dict[str, str]]:
        metas: list[dict[str, str]] = []
        cursor: int | str = 0
        pattern = f"{self.key_prefix}:job:*:meta"
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=200)
            for key in keys:
                raw = await self.redis.hgetall(key)
                if not raw:
                    continue
                metas.append(_decode_redis_hash(raw))
            if cursor in {0, "0"}:
                break
        return metas

    async def metrics(self) -> AsyncQueueMetrics:
        queue_depth = await self.queue.length()
        oldest_queued_age_sec = await self._oldest_queue_age_sec()
        metrics_raw = await self.redis.hgetall(self._metrics_key())
        metrics_map = _decode_redis_hash(metrics_raw)
        inflight_jobs_raw = metrics_map.get(METRICS_INFLIGHT_JOBS_FIELD)
        running_tasks_raw = metrics_map.get(METRICS_RUNNING_TASKS_FIELD)
        if inflight_jobs_raw is None or running_tasks_raw is None:
            inflight_jobs, running_tasks = _metrics_from_meta_snapshots(await self._all_meta())
        else:
            inflight_jobs = max(int(inflight_jobs_raw), 0)
            running_tasks = max(int(running_tasks_raw), 0)
        started_epoch = float(metrics_map.get(METRICS_STARTED_AT_FIELD, f"{time.time():.6f}"))
        enqueued = int(metrics_map.get(METRICS_ENQUEUED_FIELD, 0))
        dequeued = int(metrics_map.get(METRICS_DEQUEUED_FIELD, 0))
        elapsed = max(time.time() - started_epoch, 1e-6)
        return AsyncQueueMetrics(
            queue_depth=queue_depth,
            inflight_jobs=inflight_jobs,
            running_tasks=running_tasks,
            oldest_queued_age_sec=oldest_queued_age_sec,
            dequeue_rate=dequeued / elapsed,
            enqueue_rate=enqueued / elapsed,
        )

    async def recover_running_tasks(self) -> int:
        recovered = 0
        cursor: int | str = 0
        pattern = f"{self.key_prefix}:job:*:meta"
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=200)
            for key in keys:
                key_text = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                raw_meta = await self.redis.hgetall(key)
                if not raw_meta:
                    continue
                meta = _decode_redis_hash(raw_meta)
                status = meta.get("status", AsyncJobStatus.queued.value)
                if status in {
                    AsyncJobStatus.completed.value,
                    AsyncJobStatus.failed.value,
                    AsyncJobStatus.expired.value,
                }:
                    continue
                job_id = key_text.split(":")[-2]
                task_states = _decode_redis_hash(
                    await self.redis.hgetall(self._task_states_key(job_id))
                )
                if not task_states:
                    continue
                raw_tasks = _decode_redis_hash(await self.redis.hgetall(self._tasks_key(job_id)))
                running_indexes = [index for index, state in task_states.items() if state == AsyncJobStatus.running.value]
                if not running_indexes:
                    continue
                payloads = [raw_tasks[index] for index in running_indexes if index in raw_tasks]
                if not payloads:
                    continue
                pipe = self.redis.pipeline(transaction=True)
                for index in running_indexes:
                    pipe.hset(
                        self._task_states_key(job_id),
                        index,
                        AsyncJobStatus.queued.value,
                    )
                pipe.hset(
                    self._meta_key(job_id),
                    mapping={
                        "status": AsyncJobStatus.queued.value,
                        "running": "0",
                        "updated_at": _now_iso(),
                    },
                )
                pipe.hincrby(
                    self._metrics_key(),
                    METRICS_RUNNING_TASKS_FIELD,
                    -len(payloads),
                )
                pipe.rpush(self.queue_name, *payloads)
                pipe.expire(self._meta_key(job_id), self.ttl_sec)
                pipe.expire(self._results_key(job_id), self.ttl_sec)
                pipe.expire(self._tasks_key(job_id), self.ttl_sec)
                pipe.expire(self._task_states_key(job_id), self.ttl_sec)
                await pipe.execute()
                await self._record_enqueue_count(len(payloads))
                recovered += len(payloads)
                logger.warning(
                    "Recovered async running tasks after worker restart: job_id={} recovered_tasks={}",
                    job_id,
                    len(payloads),
                )
            if cursor in {0, "0"}:
                break
        return recovered

    async def close(self) -> None:
        logger.debug("Closing async jobs backend (redis): queue={}", self.queue_name)
        await self.queue.close()


class InMemoryAsyncJobs:
    def __init__(self, *, ttl_sec: int, backlog_limit: int) -> None:
        self.ttl_sec = ttl_sec
        self.backlog_limit = backlog_limit
        self.queue: InMemoryTaskQueue = InMemoryTaskQueue()
        self._meta: dict[str, dict[str, Any]] = {}
        self._results: dict[str, list[dict[str, Any] | None]] = {}
        self._lock = asyncio.Lock()
        self._created_at_monotonic = time.monotonic()
        self._enqueue_count = 0
        self._dequeue_count = 0
        self._inflight_jobs = 0
        self._running_tasks = 0

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        n = len(request.snippets)
        queue_depth = await self.queue.length()
        logger.debug(
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
        job_logger = logger.bind(job_id=job_id)
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
            if n > 0:
                self._inflight_jobs += 1

        await self.queue.enqueue_many(tasks)
        self._enqueue_count += len(tasks)
        job_logger.debug(
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
        job_logger = logger.bind(job_id=job_id)
        async with self._lock:
            meta = self._meta.get(job_id)
            if meta is None:
                job_logger.warning("Async poll miss (in-memory): job_id={}", job_id)
                return None
            results = self._results.get(job_id, [])
            finalized = None
            if meta["status"] in {AsyncJobStatus.completed, AsyncJobStatus.failed}:
                if all(r is not None for r in results):
                    finalized = [r for r in results if r is not None]
            job_logger.debug(
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
        task = await self.queue.dequeue(timeout_sec=timeout_sec)
        if task is not None:
            self._dequeue_count += 1
        return task

    async def mark_task_started(self, task: AsyncTaskPayload) -> None:
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=task.snippet.id,
        )
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                task_logger.warning(
                    "Async task start ignored (in-memory, missing job): job_id={} task_id={} index={} snippet_id={}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                )
                return
            meta["status"] = AsyncJobStatus.running
            meta["running"] += 1
            self._running_tasks += 1
            meta["updated_at"] = _now_iso()
            task_logger.debug(
                "Async task started (in-memory): job_id={} task_id={} index={} snippet_id={}",
                task.job_id,
                task.task_id,
                task.index,
                task.snippet.id,
            )

    async def mark_task_success(
        self, task: AsyncTaskPayload, response: ReplResponse
    ) -> None:
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=task.snippet.id,
        )
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                task_logger.warning(
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
            self._running_tasks = max(self._running_tasks - 1, 0)
            meta["done"] += 1
            meta["updated_at"] = _now_iso()
            task_logger.debug(
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
                self._inflight_jobs = max(self._inflight_jobs - 1, 0)
                task_logger.debug(
                    "Async job completed (in-memory): job_id={} done={} failed={} total={}",
                    task.job_id,
                    meta["done"],
                    meta["failed"],
                    meta["total"],
                )

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None:
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=snippet_id,
        )
        response = ReplResponse(id=snippet_id, error=error, time=0.0)
        async with self._lock:
            meta = self._meta.get(task.job_id)
            if meta is None:
                task_logger.warning(
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
            self._running_tasks = max(self._running_tasks - 1, 0)
            meta["failed"] += 1
            meta["updated_at"] = _now_iso()
            task_logger.warning(
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
                self._inflight_jobs = max(self._inflight_jobs - 1, 0)
                task_logger.debug(
                    "Async job completed (in-memory): job_id={} done={} failed={} total={}",
                    task.job_id,
                    meta["done"],
                    meta["failed"],
                    meta["total"],
                )

    async def close(self) -> None:
        logger.debug("Closing async jobs backend (in-memory)")
        await self.queue.close()

    async def metrics(self) -> AsyncQueueMetrics:
        queue_depth = await self.queue.length()
        oldest_queued_age_sec = 0.0

        # Accessing the queue's head is required for queue-age observability.
        queue_data = list(getattr(self.queue._q, "_queue", []))
        if queue_data:
            first = queue_data[0]
            try:
                task = AsyncTaskPayload.model_validate_json(first)
                enqueued_at = _iso_to_datetime(task.enqueued_at)
                if enqueued_at is not None:
                    oldest_queued_age_sec = max(
                        (datetime.now(tz=timezone.utc) - enqueued_at).total_seconds(), 0.0
                    )
            except Exception:
                oldest_queued_age_sec = 0.0

        async with self._lock:
            inflight_jobs = self._inflight_jobs
            running_tasks = self._running_tasks

        elapsed = max(time.monotonic() - self._created_at_monotonic, 1e-6)
        return AsyncQueueMetrics(
            queue_depth=queue_depth,
            inflight_jobs=inflight_jobs,
            running_tasks=running_tasks,
            oldest_queued_age_sec=oldest_queued_age_sec,
            dequeue_rate=self._dequeue_count / elapsed,
            enqueue_rate=self._enqueue_count / elapsed,
        )

    async def recover_running_tasks(self) -> int:
        return 0


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
