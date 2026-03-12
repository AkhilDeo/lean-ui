from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.async_jobs import (
    AsyncBacklogFullError,
    AsyncJobStatus,
    InMemoryAsyncJobs,
    RedisAsyncJobs,
)
from server.async_queue import RedisTaskQueue


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def _record(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.ops.append((name, args, kwargs))
            return self

        return _record

    async def execute(self) -> list[object]:
        out: list[object] = []
        for name, args, kwargs in self.ops:
            method = getattr(self.redis, name)
            out.append(await method(*args, **kwargs))
        self.ops.clear()
        return out


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self.lists: dict[str, list[str]] = defaultdict(list)

    @staticmethod
    def _norm(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        _ = transaction
        return FakePipeline(self)

    async def hsetnx(self, key: str, field: str, value: str) -> int:
        key = self._norm(key)
        field = self._norm(field)
        if field in self.hashes[key]:
            return 0
        self.hashes[key][field] = value
        return 1

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        key = self._norm(key)
        field = self._norm(field)
        current = int(self.hashes[key].get(field, "0"))
        new_value = current + amount
        self.hashes[key][field] = str(new_value)
        return new_value

    async def hset(self, key: str, mapping: dict[str, str] | None = None, *args: object) -> int:
        key = self._norm(key)
        if isinstance(mapping, dict):
            for field, value in mapping.items():
                self.hashes[key][field] = str(value)
            return len(mapping)
        if mapping is not None and len(args) == 1:
            self.hashes[key][str(mapping)] = str(args[0])
            return 1
        if len(args) == 2:
            field, value = args
            self.hashes[key][str(field)] = str(value)
            return 1
        raise ValueError("Unsupported hset usage")

    async def hget(self, key: str, field: str) -> bytes | None:
        key = self._norm(key)
        field = self._norm(field)
        value = self.hashes.get(key, {}).get(field)
        return value.encode("utf-8") if value is not None else None

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        key = self._norm(key)
        return {
            field.encode("utf-8"): value.encode("utf-8")
            for field, value in self.hashes.get(key, {}).items()
        }

    async def hdel(self, key: str, field: str) -> int:
        key = self._norm(key)
        field = self._norm(field)
        if field in self.hashes.get(key, {}):
            del self.hashes[key][field]
            return 1
        return 0

    async def hmget(self, key: str, fields: tuple[str, ...]) -> list[bytes | None]:
        key = self._norm(key)
        return [
            value.encode("utf-8")
            if (value := self.hashes.get(key, {}).get(self._norm(field))) is not None
            else None
            for field in fields
        ]

    async def expire(self, key: str, ttl_sec: int) -> bool:
        _ = self._norm(key), ttl_sec
        return True

    async def rpush(self, key: str, *values: str) -> int:
        key = self._norm(key)
        self.lists[key].extend(str(v) for v in values)
        return len(self.lists[key])

    async def lset(self, key: str, index: int, value: str) -> bool:
        key = self._norm(key)
        self.lists[key][index] = value
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        key = self._norm(key)
        items = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        return [item.encode("utf-8") for item in items[start:stop]]

    async def lindex(self, key: str, index: int) -> bytes | None:
        key = self._norm(key)
        items = self.lists.get(key, [])
        if not items:
            return None
        try:
            return items[index].encode("utf-8")
        except IndexError:
            return None

    async def llen(self, key: str) -> int:
        key = self._norm(key)
        return len(self.lists.get(key, []))

    async def blpop(self, key: str, timeout: int = 1) -> tuple[bytes, bytes] | None:
        _ = timeout
        key = self._norm(key)
        items = self.lists.get(key, [])
        if not items:
            return None
        value = items.pop(0)
        return key.encode("utf-8"), value.encode("utf-8")

    async def exists(self, key: str) -> int:
        key = self._norm(key)
        return int(key in self.hashes or key in self.lists)

    async def scan(self, cursor: int | str = 0, match: str | None = None, count: int = 200):
        _ = cursor, count
        keys = list(self.hashes)
        if match:
            prefix = match.split("*", 1)[0]
            keys = [key for key in keys if key.startswith(prefix)]
        return 0, [key.encode("utf-8") for key in keys]

    async def aclose(self) -> None:
        return None


def make_redis_jobs() -> RedisAsyncJobs:
    redis = FakeRedis()
    return RedisAsyncJobs(
        redis=redis,  # type: ignore[arg-type]
        queue=RedisTaskQueue(redis=redis, queue_name="lean_async_check"),  # type: ignore[arg-type]
        queue_name="lean_async_check",
        key_prefix="lean_async",
        ttl_sec=3600,
        backlog_limit=100,
    )


@pytest.mark.asyncio
async def test_submit_and_complete_single_job() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )
    assert submit.status == AsyncJobStatus.queued

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.status == AsyncJobStatus.queued
    assert poll.progress.total == 1
    assert poll.results is None

    task = await jobs.dequeue_task(timeout_sec=1)
    assert task is not None
    await jobs.mark_task_started(task)
    await jobs.mark_task_success(
        task, ReplResponse(id="s1", time=0.2, response={"env": 0})
    )

    done = await jobs.poll(submit.job_id)
    assert done is not None
    assert done.status == AsyncJobStatus.completed
    assert done.progress.done == 1
    assert done.results is not None
    assert done.results[0].id == "s1"
    assert done.results[0].status.value == "valid"
    assert done.results[0].passed is True


@pytest.mark.asyncio
async def test_batch_preserves_result_order() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(
            snippets=[
                Snippet(id="a", code="#check Nat"),
                Snippet(id="b", code="#check Int"),
            ],
            timeout=30,
        )
    )

    t1 = await jobs.dequeue_task(timeout_sec=1)
    t2 = await jobs.dequeue_task(timeout_sec=1)
    assert t1 is not None and t2 is not None

    # Complete second first to ensure poll still returns ordered list.
    await jobs.mark_task_started(t2)
    await jobs.mark_task_success(t2, ReplResponse(id="b", time=0.1, response={"env": 0}))
    await jobs.mark_task_started(t1)
    await jobs.mark_task_success(t1, ReplResponse(id="a", time=0.1, response={"env": 0}))

    done = await jobs.poll(submit.job_id)
    assert done is not None
    assert done.results is not None
    assert [item.id for item in done.results] == ["a", "b"]


@pytest.mark.asyncio
async def test_backlog_limit_rejected() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=1)
    await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )
    with pytest.raises(AsyncBacklogFullError):
        await jobs.submit(
            CheckRequest(snippets=[Snippet(id="s2", code="#check Int")], timeout=30)
        )


@pytest.mark.asyncio
async def test_in_memory_metrics_reflect_queue_and_running() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(
            snippets=[
                Snippet(id="s1", code="#check Nat"),
                Snippet(id="s2", code="#check Int"),
            ],
            timeout=30,
        )
    )
    _ = submit

    before = await jobs.metrics()
    assert before.queue_depth == 2
    assert before.inflight_jobs == 1
    assert before.running_tasks == 0
    assert before.enqueue_rate > 0

    task = await jobs.dequeue_task(timeout_sec=1)
    assert task is not None
    await jobs.mark_task_started(task)

    during = await jobs.metrics()
    assert during.queue_depth == 1
    assert during.running_tasks == 1
    assert during.inflight_jobs == 1
    assert during.oldest_queued_age_sec >= 0

    await jobs.mark_task_success(task, ReplResponse(id=task.snippet.id, time=0.1, response={"env": 0}))
    after = await jobs.metrics()
    assert after.dequeue_rate > 0


@pytest.mark.asyncio
async def test_redis_recovery_requeues_running_tasks_only() -> None:
    jobs = make_redis_jobs()
    submit = await jobs.submit(
        CheckRequest(
            snippets=[
                Snippet(id="s1", code="#check Nat"),
                Snippet(id="s2", code="#check Int"),
            ],
            timeout=30,
        )
    )
    _ = submit

    task = await jobs.dequeue_task(timeout_sec=1)
    assert task is not None
    await jobs.mark_task_started(task)

    recovered = await jobs.recover_running_tasks()
    assert recovered == 1

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.status == AsyncJobStatus.queued
    assert poll.progress.running == 0
    metrics = await jobs.metrics()
    assert metrics.running_tasks == 0

    first = await jobs.dequeue_task(timeout_sec=1)
    second = await jobs.dequeue_task(timeout_sec=1)
    assert first is not None and second is not None
    assert {first.snippet.id, second.snippet.id} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_redis_duplicate_completion_after_recovery_is_ignored() -> None:
    jobs = make_redis_jobs()
    submit = await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )

    original = await jobs.dequeue_task(timeout_sec=1)
    assert original is not None
    await jobs.mark_task_started(original)
    recovered = await jobs.recover_running_tasks()
    assert recovered == 1

    await jobs.mark_task_success(
        original, ReplResponse(id="s1", time=0.1, response={"env": 0})
    )
    duplicate = await jobs.dequeue_task(timeout_sec=1)
    assert duplicate is not None
    await jobs.mark_task_started(duplicate)
    await jobs.mark_task_success(
        duplicate, ReplResponse(id="s1", time=0.1, response={"env": 1})
    )

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.progress.done == 1
    assert poll.progress.failed == 0
    assert poll.results is not None
    assert poll.results[0].response == {"env": 0}
