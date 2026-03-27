from __future__ import annotations

import pytest
from kimina_client import Snippet

from server.async_queue import (
    AsyncTaskPayload,
    InMemoryTaskQueue,
    deserialize_result,
    serialize_result,
)
from server.async_jobs import RedisAsyncJobs, create_async_jobs
from server.async_tiering import AsyncQueueTier
from server.settings import Settings


@pytest.mark.asyncio
async def test_in_memory_queue_roundtrip() -> None:
    queue = InMemoryTaskQueue()
    task = AsyncTaskPayload.create(
        job_id="job-1",
        task_id="task-1",
        index=0,
        snippet=Snippet(id="snippet-1", code="#check Nat"),
        runtime_id="v4.15.0",
        queue_tier=AsyncQueueTier.light,
        timeout=30.0,
        debug=False,
        reuse=True,
        infotree=None,
        include_sorry_details=False,
    )
    await queue.enqueue_many([task])
    assert await queue.length() == 1

    got = await queue.dequeue(timeout_sec=1)
    assert got is not None
    assert got.task_id == "task-1"
    assert got.snippet.id == "snippet-1"
    assert await queue.length() == 0


def test_result_serialization_roundtrip() -> None:
    payload = {"id": "x", "time": 1.2, "response": {"env": 0}}
    raw = serialize_result(payload)
    assert deserialize_result(raw) == payload


@pytest.mark.asyncio
async def test_create_async_jobs_pings_redis_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    settings.redis_url = "redis://example.internal:6379/0"

    captured_kwargs: dict[str, object] = {}

    class FakeRedis:
        ping_called = False
        closed = False

        async def ping(self) -> bool:
            self.ping_called = True
            return True

        async def aclose(self) -> None:
            self.closed = True

    fake_redis = FakeRedis()

    def fake_from_url(url: str, **kwargs):  # type: ignore[no-untyped-def]
        assert url == settings.redis_url
        captured_kwargs.update(kwargs)
        return fake_redis

    monkeypatch.setattr("server.async_jobs.redis_from_url", fake_from_url)

    jobs = await create_async_jobs(settings)
    assert isinstance(jobs, RedisAsyncJobs)
    assert fake_redis.ping_called is True
    assert captured_kwargs["socket_connect_timeout"] == 5
    assert captured_kwargs["socket_timeout"] == 5
    await jobs.close()
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_create_async_jobs_fails_fast_when_redis_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    settings.redis_url = "redis://example.internal:6379/0"

    class FakeRedis:
        closed = False

        async def ping(self) -> bool:
            raise RuntimeError("boom")

        async def aclose(self) -> None:
            self.closed = True

    fake_redis = FakeRedis()

    def fake_from_url(url: str, **kwargs):  # type: ignore[no-untyped-def]
        assert url == settings.redis_url
        return fake_redis

    monkeypatch.setattr("server.async_jobs.redis_from_url", fake_from_url)

    with pytest.raises(RuntimeError, match="Async jobs redis backend is unhealthy"):
        await create_async_jobs(settings)

    assert fake_redis.closed is True
