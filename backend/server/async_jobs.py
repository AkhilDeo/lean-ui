from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from kimina_client import CheckRequest, ReplResponse
from loguru import logger
from pydantic import BaseModel, Field

from .async_queue import (
    AsyncTaskPayload,
    InMemoryTaskQueue,
    RedisTaskQueue,
    TaskQueue,
    deserialize_result,
    serialize_result,
)
from .async_tiering import AsyncQueueTier, classify_async_queue_tier
from .runtime_registry import build_runtime_registry
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
    results: list[ReplResponse] | None = None
    created_at: str
    updated_at: str
    expires_at: str
    error: str | None = None


class AsyncQueueTierMetrics(BaseModel):
    queue_depth: int
    running_tasks: int
    oldest_queued_age_sec: float
    dequeue_rate: float
    enqueue_rate: float
    warm_repls: int = 0
    cold_starts: int = 0
    spawn_failures: int = 0
    retries: int = 0
    exhausted_retries: int = 0
    failure_reasons: dict[str, int] = Field(default_factory=dict)


class AsyncQueueMetrics(BaseModel):
    queue_depth: int
    inflight_jobs: int
    running_tasks: int
    oldest_queued_age_sec: float
    dequeue_rate: float
    enqueue_rate: float
    tiers: dict[str, AsyncQueueTierMetrics] | None = None


class AsyncBacklogFullError(Exception):
    pass


class AsyncJobs(Protocol):
    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse: ...

    async def poll(self, job_id: str) -> AsyncPollResponse | None: ...

    async def dequeue_task(
        self,
        timeout_sec: int = 1,
        queue_tier: str | AsyncQueueTier | None = None,
        runtime_id: str | None = None,
    ) -> AsyncTaskPayload | None: ...

    async def mark_task_started(self, task: AsyncTaskPayload) -> None: ...

    async def mark_task_success(
        self, task: AsyncTaskPayload, response: ReplResponse
    ) -> None: ...

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None: ...

    async def metrics(self, runtime_id: str | None = None) -> AsyncQueueMetrics: ...

    async def recover_running_tasks(self) -> int: ...

    async def record_worker_metrics(
        self,
        *,
        queue_tier: str | AsyncQueueTier,
        runtime_id: str | None = None,
        warm_repls: int | None = None,
        cold_starts: int = 0,
        spawn_failures: int = 0,
        retries: int = 0,
        exhausted_retries: int = 0,
        failure_reason: str | None = None,
    ) -> None: ...

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
METRICS_WARM_REPLS_FIELD = "warm_repls"
METRICS_COLD_STARTS_FIELD = "cold_starts"
METRICS_SPAWN_FAILURES_FIELD = "spawn_failures"
METRICS_RETRIES_FIELD = "retries"
METRICS_EXHAUSTED_RETRIES_FIELD = "exhausted_retries"


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


def _queue_name(base_name: str, runtime_id: str) -> str:
    suffix = runtime_id.lower().replace(".", "_").replace("-", "_")
    return f"{base_name}:{suffix}"


def _known_runtime_ids(settings: Settings) -> list[str]:
    if settings.gateway_enabled:
        return build_runtime_registry(settings.default_runtime_id).known_runtime_ids()
    return [settings.runtime_id]


@dataclass
class RedisAsyncJobs:
    redis: Redis
    base_queue_names: dict[AsyncQueueTier, str]
    runtime_ids: list[str]
    key_prefix: str
    ttl_sec: int
    backlog_limit: int
    settings: Settings
    _dequeue_turn: int = 0

    def _meta_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:meta"

    def _results_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:results"

    def _tasks_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:tasks"

    def _task_states_key(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}:task_states"

    def _normalize_tier(self, queue_tier: str | AsyncQueueTier) -> AsyncQueueTier:
        if isinstance(queue_tier, AsyncQueueTier):
            return queue_tier
        return AsyncQueueTier(queue_tier)

    def _queue_name(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> str:
        tier = self._normalize_tier(queue_tier)
        return _queue_name(self.base_queue_names[tier], runtime_id)

    def _task_queue(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> RedisTaskQueue:
        return RedisTaskQueue(self.redis, self._queue_name(runtime_id, queue_tier))

    def _metrics_key(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> str:
        return f"{self.key_prefix}:queue:{self._queue_name(runtime_id, queue_tier)}:metrics"

    def _failure_reasons_key(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> str:
        return (
            f"{self.key_prefix}:queue:{self._queue_name(runtime_id, queue_tier)}:failure_reasons"
        )

    async def _record_enqueue_count(
        self, runtime_id: str, queue_tier: str | AsyncQueueTier, count: int
    ) -> None:
        if count <= 0:
            return
        key = self._metrics_key(runtime_id, queue_tier)
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        pipe.hincrby(key, METRICS_ENQUEUED_FIELD, count)
        await pipe.execute()

    async def _record_dequeue_count(
        self, runtime_id: str, queue_tier: str | AsyncQueueTier, count: int
    ) -> None:
        if count <= 0:
            return
        key = self._metrics_key(runtime_id, queue_tier)
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        pipe.hincrby(key, METRICS_DEQUEUED_FIELD, count)
        await pipe.execute()

    async def _record_task_running_delta(
        self, runtime_id: str, queue_tier: str | AsyncQueueTier, delta: int
    ) -> None:
        key = self._metrics_key(runtime_id, queue_tier)
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        pipe.hincrby(key, METRICS_RUNNING_TASKS_FIELD, delta)
        await pipe.execute()

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        runtime_id = request.runtime_id or self.settings.default_runtime_id
        n = len(request.snippets)
        queue_depth = sum(
            await asyncio.gather(
                *(
                    self._task_queue(runtime_id, tier).length()
                    for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy)
                )
            )
        )
        logger.debug(
            "Async submit preflight (redis): queues={} depth={} incoming={} backlog_limit={}",
            [
                self._queue_name(runtime_id, AsyncQueueTier.light),
                self._queue_name(runtime_id, AsyncQueueTier.heavy),
            ],
            queue_depth,
            n,
            self.backlog_limit,
        )
        if queue_depth + n > self.backlog_limit:
            logger.warning(
                "Async submit rejected (redis): queues={} depth={} incoming={} backlog_limit={}",
                [
                    self._queue_name(runtime_id, AsyncQueueTier.light),
                    self._queue_name(runtime_id, AsyncQueueTier.heavy),
                ],
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
        results_key = self._results_key(job_id)
        tasks_key = self._tasks_key(job_id)
        task_states_key = self._task_states_key(job_id)

        tasks: list[AsyncTaskPayload] = []
        tasks_by_tier: dict[AsyncQueueTier, list[AsyncTaskPayload]] = defaultdict(list)
        for i, snippet in enumerate(request.snippets):
            queue_tier = classify_async_queue_tier(snippet.code, self.settings)
            task = AsyncTaskPayload.create(
                job_id=job_id,
                task_id=uuid4().hex,
                index=i,
                runtime_id=runtime_id,
                snippet=snippet,
                queue_tier=queue_tier.value,
                timeout=float(request.timeout),
                debug=request.debug,
                reuse=request.reuse,
                infotree=request.infotree,
            )
            tasks.append(task)
            tasks_by_tier[queue_tier].append(task)

        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(
            meta_key,
            mapping={
                "status": AsyncJobStatus.queued.value,
                "total": str(n),
                "done": "0",
                "failed": "0",
                "running": "0",
                "queue_tiers": ",".join(sorted(tier.value for tier in tasks_by_tier)),
                "runtime_id": runtime_id,
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
            for queue_tier in tasks_by_tier:
                metrics_key = self._metrics_key(runtime_id, queue_tier)
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
            for queue_tier, tier_tasks in tasks_by_tier.items():
                await self._task_queue(runtime_id, queue_tier).enqueue_many(tier_tasks)
                await self._record_enqueue_count(runtime_id, queue_tier, len(tier_tasks))
                job_logger.debug(
                    "Async job enqueued (redis): job_id={} tasks={} queue={} tier={}",
                    job_id,
                    len(tier_tasks),
                    self._queue_name(runtime_id, queue_tier),
                    queue_tier.value,
                )
        except Exception as e:
            await self.redis.hset(
                meta_key,
                mapping={"status": AsyncJobStatus.failed.value, "error": "enqueue_failed"},
            )
            job_logger.exception(
                "Async job enqueue failed (redis): job_id={} queues={} error={}",
                job_id,
                [
                    self._queue_name(runtime_id, AsyncQueueTier.light),
                    self._queue_name(runtime_id, AsyncQueueTier.heavy),
                ],
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

        results: list[ReplResponse] | None = None
        if status in {AsyncJobStatus.completed, AsyncJobStatus.failed}:
            raw = await self.redis.lrange(self._results_key(job_id), 0, -1)
            parsed: list[ReplResponse] = []
            for item in raw:
                value = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                if not value:
                    continue
                parsed.append(ReplResponse.model_validate(deserialize_result(value)))
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

    async def dequeue_task(
        self,
        timeout_sec: int = 1,
        queue_tier: str | AsyncQueueTier | None = None,
        runtime_id: str | None = None,
    ) -> AsyncTaskPayload | None:
        effective_runtime_id = runtime_id or self.settings.runtime_id
        requested = (
            AsyncQueueTier.all
            if queue_tier is None
            else self._normalize_tier(queue_tier)
        )
        if requested != AsyncQueueTier.all:
            task = await self._task_queue(effective_runtime_id, requested).dequeue(
                timeout_sec=timeout_sec
            )
            if task is not None:
                await self._record_dequeue_count(effective_runtime_id, requested, 1)
            return task

        order = [AsyncQueueTier.light, AsyncQueueTier.heavy]
        if self._dequeue_turn % 2 == 1:
            order.reverse()
        self._dequeue_turn += 1
        for idx, tier in enumerate(order):
            if idx < len(order) - 1:
                if await self._task_queue(effective_runtime_id, tier).length() <= 0:
                    continue
                wait = 1
            else:
                wait = timeout_sec
            task = await self._task_queue(effective_runtime_id, tier).dequeue(
                timeout_sec=wait
            )
            if task is not None:
                await self._record_dequeue_count(effective_runtime_id, tier, 1)
                return task
        return None

    async def mark_task_started(self, task: AsyncTaskPayload) -> None:
        task_logger = logger.bind(
            job_id=task.job_id,
            task_id=task.task_id,
            snippet_id=task.snippet.id,
        )
        meta_key = self._meta_key(task.job_id)
        task_states_key = self._task_states_key(task.job_id)
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
            pipe.hincrby(
                self._metrics_key(task.runtime_id, task.queue_tier),
                METRICS_RUNNING_TASKS_FIELD,
                1,
            )
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
        results_key = self._results_key(task.job_id)
        task_states_key = self._task_states_key(task.job_id)
        tasks_key = self._tasks_key(task.job_id)
        if not await self.redis.exists(meta_key):
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
            pipe.hincrby(
                self._metrics_key(task.runtime_id, task.queue_tier),
                METRICS_RUNNING_TASKS_FIELD,
                -1,
            )
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
        raw_tasks = _decode_redis_hash(await self.redis.hgetall(tasks_key))
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
            queue_tiers_raw = (await self._read_meta(task.job_id) or {}).get("queue_tiers", "")
            seen_tiers = {
                tier.strip()
                for tier in queue_tiers_raw.split(",")
                if tier.strip()
            }
            seen_tiers.update(
                AsyncTaskPayload.model_validate_json(payload).queue_tier
                for payload in raw_tasks.values()
            )
            seen_tiers.add(task.queue_tier)
            for queue_tier in seen_tiers:
                pipe.hincrby(
                    self._metrics_key(task.runtime_id, queue_tier),
                    METRICS_INFLIGHT_JOBS_FIELD,
                    -1,
                )
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
        payload = response.model_dump(exclude_none=True)
        payload.update(
            {
                "runtime_id": task.runtime_id,
                "queue_tier": task.queue_tier,
                "retry_count": task.retry_count,
                "failure_reason": task.failure_reason,
            }
        )
        await self._mark_result(
            task=task,
            payload=payload,
            is_failure=False,
        )

    async def mark_task_failure(
        self, task: AsyncTaskPayload, error: str, snippet_id: str
    ) -> None:
        response = ReplResponse(id=snippet_id, error=error, time=0.0)
        payload = response.model_dump(exclude_none=True)
        payload.update(
            {
                "runtime_id": task.runtime_id,
                "queue_tier": task.queue_tier,
                "retry_count": task.retry_count,
                "failure_reason": task.failure_reason,
            }
        )
        await self._mark_result(
            task=task,
            payload=payload,
            is_failure=True,
        )

    async def _oldest_queue_age_sec(
        self, runtime_id: str, queue_tier: str | AsyncQueueTier
    ) -> float:
        first = await self.redis.lindex(self._queue_name(runtime_id, queue_tier), 0)
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

    async def _tier_metrics(
        self, runtime_id: str, queue_tier: AsyncQueueTier
    ) -> AsyncQueueTierMetrics:
        queue_depth = await self._task_queue(runtime_id, queue_tier).length()
        oldest_queued_age_sec = await self._oldest_queue_age_sec(runtime_id, queue_tier)
        metrics_raw = await self.redis.hgetall(self._metrics_key(runtime_id, queue_tier))
        metrics_map = _decode_redis_hash(metrics_raw)
        started_epoch = float(
            metrics_map.get(METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        )
        elapsed = max(time.time() - started_epoch, 1e-6)
        reasons_raw = await self.redis.hgetall(
            self._failure_reasons_key(runtime_id, queue_tier)
        )
        reasons = {
            key: int(value) for key, value in _decode_redis_hash(reasons_raw).items()
        }
        return AsyncQueueTierMetrics(
            queue_depth=queue_depth,
            running_tasks=max(int(metrics_map.get(METRICS_RUNNING_TASKS_FIELD, 0)), 0),
            oldest_queued_age_sec=oldest_queued_age_sec,
            dequeue_rate=int(metrics_map.get(METRICS_DEQUEUED_FIELD, 0)) / elapsed,
            enqueue_rate=int(metrics_map.get(METRICS_ENQUEUED_FIELD, 0)) / elapsed,
            warm_repls=max(int(metrics_map.get(METRICS_WARM_REPLS_FIELD, 0)), 0),
            cold_starts=max(int(metrics_map.get(METRICS_COLD_STARTS_FIELD, 0)), 0),
            spawn_failures=max(int(metrics_map.get(METRICS_SPAWN_FAILURES_FIELD, 0)), 0),
            retries=max(int(metrics_map.get(METRICS_RETRIES_FIELD, 0)), 0),
            exhausted_retries=max(
                int(metrics_map.get(METRICS_EXHAUSTED_RETRIES_FIELD, 0)), 0
            ),
            failure_reasons=reasons,
        )

    async def _all_meta(self, runtime_id: str | None = None) -> list[dict[str, str]]:
        metas: list[dict[str, str]] = []
        cursor: int | str = 0
        pattern = f"{self.key_prefix}:job:*:meta"
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=200)
            for key in keys:
                raw = await self.redis.hgetall(key)
                if not raw:
                    continue
                decoded = _decode_redis_hash(raw)
                if runtime_id is not None and decoded.get("runtime_id") != runtime_id:
                    continue
                metas.append(decoded)
            if cursor in {0, "0"}:
                break
        return metas

    async def metrics(self, runtime_id: str | None = None) -> AsyncQueueMetrics:
        effective_runtime_ids = [runtime_id] if runtime_id else self.runtime_ids
        tier_metrics = {
            tier.value: AsyncQueueTierMetrics(
                queue_depth=0,
                running_tasks=0,
                oldest_queued_age_sec=0.0,
                dequeue_rate=0.0,
                enqueue_rate=0.0,
            )
            for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy)
        }
        for selected_runtime_id in effective_runtime_ids:
            for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy):
                tier_metric = await self._tier_metrics(selected_runtime_id, tier)
                existing = tier_metrics[tier.value]
                existing.queue_depth += tier_metric.queue_depth
                existing.running_tasks += tier_metric.running_tasks
                existing.oldest_queued_age_sec = max(
                    existing.oldest_queued_age_sec, tier_metric.oldest_queued_age_sec
                )
                existing.dequeue_rate += tier_metric.dequeue_rate
                existing.enqueue_rate += tier_metric.enqueue_rate
                existing.warm_repls += tier_metric.warm_repls
                existing.cold_starts += tier_metric.cold_starts
                existing.spawn_failures += tier_metric.spawn_failures
                existing.retries += tier_metric.retries
                existing.exhausted_retries += tier_metric.exhausted_retries
                for reason, count in tier_metric.failure_reasons.items():
                    existing.failure_reasons[reason] = (
                        existing.failure_reasons.get(reason, 0) + count
                    )
        inflight_candidates: list[int] = []
        for selected_runtime_id in effective_runtime_ids:
            for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy):
                metrics_map = _decode_redis_hash(
                    await self.redis.hgetall(self._metrics_key(selected_runtime_id, tier))
                )
                value = metrics_map.get(METRICS_INFLIGHT_JOBS_FIELD)
                if value is not None:
                    inflight_candidates.append(max(int(value), 0))
        if not inflight_candidates:
            inflight_jobs, running_tasks = _metrics_from_meta_snapshots(
                await self._all_meta(runtime_id=runtime_id)
            )
        else:
            inflight_jobs = max(inflight_candidates)
            running_tasks = sum(metric.running_tasks for metric in tier_metrics.values())
        return AsyncQueueMetrics(
            queue_depth=sum(metric.queue_depth for metric in tier_metrics.values()),
            inflight_jobs=inflight_jobs,
            running_tasks=running_tasks,
            oldest_queued_age_sec=max(
                (metric.oldest_queued_age_sec for metric in tier_metrics.values()),
                default=0.0,
            ),
            dequeue_rate=sum(metric.dequeue_rate for metric in tier_metrics.values()),
            enqueue_rate=sum(metric.enqueue_rate for metric in tier_metrics.values()),
            tiers=tier_metrics,
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
                payloads_by_tier: dict[AsyncQueueTier, list[str]] = defaultdict(list)
                for payload in payloads:
                    task = AsyncTaskPayload.model_validate_json(payload)
                    payloads_by_tier[AsyncQueueTier(task.queue_tier)].append(payload)
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
                for queue_tier, tier_payloads in payloads_by_tier.items():
                    runtime_id = AsyncTaskPayload.model_validate_json(tier_payloads[0]).runtime_id
                    pipe.hincrby(
                        self._metrics_key(runtime_id, queue_tier),
                        METRICS_RUNNING_TASKS_FIELD,
                        -len(tier_payloads),
                    )
                    pipe.rpush(self._queue_name(runtime_id, queue_tier), *tier_payloads)
                pipe.expire(self._meta_key(job_id), self.ttl_sec)
                pipe.expire(self._results_key(job_id), self.ttl_sec)
                pipe.expire(self._tasks_key(job_id), self.ttl_sec)
                pipe.expire(self._task_states_key(job_id), self.ttl_sec)
                await pipe.execute()
                for queue_tier, tier_payloads in payloads_by_tier.items():
                    runtime_id = AsyncTaskPayload.model_validate_json(tier_payloads[0]).runtime_id
                    await self._record_enqueue_count(runtime_id, queue_tier, len(tier_payloads))
                recovered += len(payloads)
                logger.warning(
                    "Recovered async running tasks after worker restart: job_id={} recovered_tasks={}",
                    job_id,
                    len(payloads),
                )
            if cursor in {0, "0"}:
                break
        return recovered

    async def record_worker_metrics(
        self,
        *,
        queue_tier: str | AsyncQueueTier,
        runtime_id: str | None = None,
        warm_repls: int | None = None,
        cold_starts: int = 0,
        spawn_failures: int = 0,
        retries: int = 0,
        exhausted_retries: int = 0,
        failure_reason: str | None = None,
    ) -> None:
        tier = self._normalize_tier(queue_tier)
        effective_runtime_id = runtime_id or self.settings.runtime_id
        key = self._metrics_key(effective_runtime_id, tier)
        pipe = self.redis.pipeline(transaction=True)
        pipe.hsetnx(key, METRICS_STARTED_AT_FIELD, f"{time.time():.6f}")
        if warm_repls is not None:
            pipe.hset(key, METRICS_WARM_REPLS_FIELD, max(warm_repls, 0))
        if cold_starts:
            pipe.hincrby(key, METRICS_COLD_STARTS_FIELD, cold_starts)
        if spawn_failures:
            pipe.hincrby(key, METRICS_SPAWN_FAILURES_FIELD, spawn_failures)
        if retries:
            pipe.hincrby(key, METRICS_RETRIES_FIELD, retries)
        if exhausted_retries:
            pipe.hincrby(key, METRICS_EXHAUSTED_RETRIES_FIELD, exhausted_retries)
        if failure_reason:
            pipe.hincrby(
                self._failure_reasons_key(effective_runtime_id, tier), failure_reason, 1
            )
        await pipe.execute()

    async def close(self) -> None:
        logger.debug(
            "Closing async jobs backend (redis): queues={}",
            [
                self._queue_name(runtime_id, tier)
                for runtime_id in self.runtime_ids
                for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy)
            ],
        )
        await self.redis.aclose()


class InMemoryAsyncJobs:
    def __init__(
        self,
        *,
        ttl_sec: int,
        backlog_limit: int,
        settings: Settings | None = None,
    ) -> None:
        self.ttl_sec = ttl_sec
        self.backlog_limit = backlog_limit
        self.settings = settings or Settings(_env_file=None)
        self.runtime_ids = _known_runtime_ids(self.settings)
        self.queues: dict[str, InMemoryTaskQueue] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._results: dict[str, list[dict[str, Any] | None]] = {}
        self._lock = asyncio.Lock()
        self._created_at_monotonic = time.monotonic()
        self._inflight_jobs = 0
        self._running_tasks_by_bucket: dict[str, int] = {}
        self._enqueue_count_by_bucket: dict[str, int] = {}
        self._dequeue_count_by_bucket: dict[str, int] = {}
        self._worker_metrics_by_bucket: dict[str, dict[str, Any]] = {}
        self._dequeue_turn = 0

    def _normalize_tier(self, queue_tier: str | AsyncQueueTier) -> AsyncQueueTier:
        if isinstance(queue_tier, AsyncQueueTier):
            return queue_tier
        return AsyncQueueTier(queue_tier)

    def _bucket(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> str:
        tier = self._normalize_tier(queue_tier)
        return f"{runtime_id}:{tier.value}"

    def _get_queue(self, runtime_id: str, queue_tier: str | AsyncQueueTier) -> InMemoryTaskQueue:
        bucket = self._bucket(runtime_id, queue_tier)
        if bucket not in self.queues:
            self.queues[bucket] = InMemoryTaskQueue()
            self._running_tasks_by_bucket.setdefault(bucket, 0)
            self._enqueue_count_by_bucket.setdefault(bucket, 0)
            self._dequeue_count_by_bucket.setdefault(bucket, 0)
            self._worker_metrics_by_bucket.setdefault(
                bucket,
                {
                    "warm_repls": 0,
                    "cold_starts": 0,
                    "spawn_failures": 0,
                    "retries": 0,
                    "exhausted_retries": 0,
                    "failure_reasons": {},
                },
            )
        return self.queues[bucket]

    async def submit(self, request: CheckRequest) -> AsyncSubmitResponse:
        runtime_id = request.runtime_id or self.settings.default_runtime_id
        n = len(request.snippets)
        queue_depth = sum(
            await asyncio.gather(
                *(
                    self._get_queue(runtime_id, tier).length()
                    for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy)
                )
            )
        )
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
        tasks: list[AsyncTaskPayload] = []
        tasks_by_tier: dict[AsyncQueueTier, list[AsyncTaskPayload]] = defaultdict(list)
        for i, snippet in enumerate(request.snippets):
            queue_tier = classify_async_queue_tier(snippet.code, self.settings)
            task = AsyncTaskPayload.create(
                job_id=job_id,
                task_id=uuid4().hex,
                index=i,
                runtime_id=runtime_id,
                snippet=snippet,
                queue_tier=queue_tier.value,
                timeout=float(request.timeout),
                debug=request.debug,
                reuse=request.reuse,
                infotree=request.infotree,
            )
            tasks.append(task)
            tasks_by_tier[queue_tier].append(task)

        async with self._lock:
            self._meta[job_id] = {
                "status": AsyncJobStatus.queued,
                "total": n,
                "done": 0,
                "failed": 0,
                "running": 0,
                "runtime_id": runtime_id,
                "created_at": queued_at,
                "updated_at": queued_at,
                "expires_at": expires_at,
                "error": None,
            }
            self._results[job_id] = [None] * n
            if n > 0:
                self._inflight_jobs += 1

        for queue_tier, tier_tasks in tasks_by_tier.items():
            bucket = self._bucket(runtime_id, queue_tier)
            await self._get_queue(runtime_id, queue_tier).enqueue_many(tier_tasks)
            self._enqueue_count_by_bucket[bucket] += len(tier_tasks)
            job_logger.debug(
                "Async job enqueued (in-memory): job_id={} tasks={} runtime_id={} tier={}",
                job_id,
                len(tier_tasks),
                runtime_id,
                queue_tier.value,
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
            finalized: list[ReplResponse] | None = None
            if meta["status"] in {AsyncJobStatus.completed, AsyncJobStatus.failed}:
                if all(r is not None for r in results):
                    finalized = [
                        ReplResponse.model_validate(r) for r in results if r is not None
                    ]
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

    async def dequeue_task(
        self,
        timeout_sec: int = 1,
        queue_tier: str | AsyncQueueTier | None = None,
        runtime_id: str | None = None,
    ) -> AsyncTaskPayload | None:
        effective_runtime_id = runtime_id or self.settings.runtime_id
        requested = (
            AsyncQueueTier.all
            if queue_tier is None
            else self._normalize_tier(queue_tier)
        )
        if requested != AsyncQueueTier.all:
            task = await self._get_queue(effective_runtime_id, requested).dequeue(
                timeout_sec=timeout_sec
            )
            if task is not None:
                self._dequeue_count_by_bucket[
                    self._bucket(effective_runtime_id, requested)
                ] += 1
            return task

        order = [AsyncQueueTier.light, AsyncQueueTier.heavy]
        if self._dequeue_turn % 2 == 1:
            order.reverse()
        self._dequeue_turn += 1
        for idx, tier in enumerate(order):
            if idx < len(order) - 1:
                if await self._get_queue(effective_runtime_id, tier).length() <= 0:
                    continue
                wait = 1
            else:
                wait = timeout_sec
            task = await self._get_queue(effective_runtime_id, tier).dequeue(
                timeout_sec=wait
            )
            if task is not None:
                self._dequeue_count_by_bucket[self._bucket(effective_runtime_id, tier)] += 1
                return task
        return None

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
            self._running_tasks_by_bucket[self._bucket(task.runtime_id, task.queue_tier)] += 1
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
            payload = response.model_dump(exclude_none=True)
            payload.update(
                {
                    "runtime_id": task.runtime_id,
                    "queue_tier": task.queue_tier,
                    "retry_count": task.retry_count,
                    "failure_reason": task.failure_reason,
                }
            )
            results[task.index] = payload
            meta["running"] = max(meta["running"] - 1, 0)
            bucket = self._bucket(task.runtime_id, task.queue_tier)
            self._running_tasks_by_bucket[bucket] = max(
                self._running_tasks_by_bucket[bucket] - 1,
                0,
            )
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
            payload = response.model_dump(exclude_none=True)
            payload.update(
                {
                    "runtime_id": task.runtime_id,
                    "queue_tier": task.queue_tier,
                    "retry_count": task.retry_count,
                    "failure_reason": task.failure_reason,
                }
            )
            results[task.index] = payload
            meta["running"] = max(meta["running"] - 1, 0)
            bucket = self._bucket(task.runtime_id, task.queue_tier)
            self._running_tasks_by_bucket[bucket] = max(
                self._running_tasks_by_bucket[bucket] - 1,
                0,
            )
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
        await asyncio.gather(*(queue.close() for queue in self.queues.values()))

    async def metrics(self, runtime_id: str | None = None) -> AsyncQueueMetrics:
        tier_metrics: dict[str, AsyncQueueTierMetrics] = {}
        oldest_queued_age_sec = 0.0
        elapsed = max(time.monotonic() - self._created_at_monotonic, 1e-6)
        async with self._lock:
            if runtime_id is None:
                inflight_jobs = self._inflight_jobs
                running_tasks = sum(self._running_tasks_by_bucket.values())
            else:
                inflight_jobs = sum(
                    1
                    for meta in self._meta.values()
                    if meta.get("runtime_id") == runtime_id
                    and meta["status"] not in {AsyncJobStatus.completed, AsyncJobStatus.failed}
                )
                running_tasks = sum(
                    count
                    for bucket, count in self._running_tasks_by_bucket.items()
                    if bucket.startswith(f"{runtime_id}:")
                )
            for tier in (AsyncQueueTier.light, AsyncQueueTier.heavy):
                selected_runtime_ids = self.runtime_ids if runtime_id is None else [runtime_id]
                queue_depth = 0
                tier_oldest = 0.0
                running = 0
                dequeue_rate = 0.0
                enqueue_rate = 0.0
                warm_repls = 0
                cold_starts = 0
                spawn_failures = 0
                retries = 0
                exhausted_retries = 0
                failure_reasons: dict[str, int] = {}
                for selected_runtime_id in selected_runtime_ids:
                    queue = self._get_queue(selected_runtime_id, tier)
                    bucket = self._bucket(selected_runtime_id, tier)
                    queue_depth += await queue.length()
                    queue_data = list(getattr(queue._q, "_queue", []))
                    bucket_oldest = 0.0
                    if queue_data:
                        first = queue_data[0]
                        try:
                            task = AsyncTaskPayload.model_validate_json(first)
                            enqueued_at = _iso_to_datetime(task.enqueued_at)
                            if enqueued_at is not None:
                                bucket_oldest = max(
                                    (
                                        datetime.now(tz=timezone.utc) - enqueued_at
                                    ).total_seconds(),
                                    0.0,
                                )
                        except Exception:
                            bucket_oldest = 0.0
                    tier_oldest = max(tier_oldest, bucket_oldest)
                    oldest_queued_age_sec = max(oldest_queued_age_sec, bucket_oldest)
                    worker_metrics = self._worker_metrics_by_bucket[bucket]
                    running += self._running_tasks_by_bucket[bucket]
                    dequeue_rate += self._dequeue_count_by_bucket[bucket] / elapsed
                    enqueue_rate += self._enqueue_count_by_bucket[bucket] / elapsed
                    warm_repls += worker_metrics["warm_repls"]
                    cold_starts += worker_metrics["cold_starts"]
                    spawn_failures += worker_metrics["spawn_failures"]
                    retries += worker_metrics["retries"]
                    exhausted_retries += worker_metrics["exhausted_retries"]
                    for reason, count in worker_metrics["failure_reasons"].items():
                        failure_reasons[reason] = failure_reasons.get(reason, 0) + count
                tier_metrics[tier.value] = AsyncQueueTierMetrics(
                    queue_depth=queue_depth,
                    running_tasks=running,
                    oldest_queued_age_sec=tier_oldest,
                    dequeue_rate=dequeue_rate,
                    enqueue_rate=enqueue_rate,
                    warm_repls=warm_repls,
                    cold_starts=cold_starts,
                    spawn_failures=spawn_failures,
                    retries=retries,
                    exhausted_retries=exhausted_retries,
                    failure_reasons=failure_reasons,
                )
        return AsyncQueueMetrics(
            queue_depth=sum(metric.queue_depth for metric in tier_metrics.values()),
            inflight_jobs=inflight_jobs,
            running_tasks=running_tasks,
            oldest_queued_age_sec=oldest_queued_age_sec,
            dequeue_rate=sum(metric.dequeue_rate for metric in tier_metrics.values()),
            enqueue_rate=sum(metric.enqueue_rate for metric in tier_metrics.values()),
            tiers=tier_metrics,
        )

    async def recover_running_tasks(self) -> int:
        return 0

    async def record_worker_metrics(
        self,
        *,
        queue_tier: str | AsyncQueueTier,
        runtime_id: str | None = None,
        warm_repls: int | None = None,
        cold_starts: int = 0,
        spawn_failures: int = 0,
        retries: int = 0,
        exhausted_retries: int = 0,
        failure_reason: str | None = None,
    ) -> None:
        tier = self._normalize_tier(queue_tier)
        effective_runtime_id = runtime_id or self.settings.runtime_id
        self._get_queue(effective_runtime_id, tier)
        async with self._lock:
            bucket = self._worker_metrics_by_bucket[
                self._bucket(effective_runtime_id, tier)
            ]
            if warm_repls is not None:
                bucket["warm_repls"] = max(warm_repls, 0)
            bucket["cold_starts"] += cold_starts
            bucket["spawn_failures"] += spawn_failures
            bucket["retries"] += retries
            bucket["exhausted_retries"] += exhausted_retries
            if failure_reason:
                reasons = bucket["failure_reasons"]
                reasons[failure_reason] = int(reasons.get(failure_reason, 0)) + 1


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
            settings=settings,
        )

    if settings.redis_url is None:
        raise RuntimeError(
            "LEAN_SERVER_REDIS_URL must be configured when async backend is enabled"
        )
    if redis_from_url is None:
        raise RuntimeError(
            "redis dependency is not installed; install 'redis' to use async backend"
        )

    redis = redis_from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=False,
        health_check_interval=30,
    )
    try:
        await redis.ping()
    except Exception as exc:
        await redis.aclose()
        raise RuntimeError(
            "Async jobs redis backend is unhealthy; failed Redis startup ping"
        ) from exc
    logger.info(
        "Async jobs configured with redis backend: queues=[{}, {}] key_prefix={} ttl_sec={} backlog_limit={}",
        settings.async_queue_name_light,
        settings.async_queue_name_heavy,
        settings.async_redis_key_prefix,
        settings.async_result_ttl_sec,
        settings.async_backlog_limit,
    )
    return RedisAsyncJobs(
        redis=redis,
        base_queue_names={
            AsyncQueueTier.light: settings.async_queue_name_light,
            AsyncQueueTier.heavy: settings.async_queue_name_heavy,
        },
        runtime_ids=_known_runtime_ids(settings),
        key_prefix=settings.async_redis_key_prefix,
        ttl_sec=settings.async_result_ttl_sec,
        backlog_limit=settings.async_backlog_limit,
        settings=settings,
    )
