from __future__ import annotations

import pytest
from kimina_client import Snippet

from server.async_queue import (
    AsyncTaskPayload,
    InMemoryTaskQueue,
    deserialize_result,
    serialize_result,
)


@pytest.mark.asyncio
async def test_in_memory_queue_roundtrip() -> None:
    queue = InMemoryTaskQueue()
    task = AsyncTaskPayload.create(
        job_id="job-1",
        task_id="task-1",
        index=0,
        snippet=Snippet(id="snippet-1", code="#check Nat"),
        timeout=30.0,
        debug=False,
        reuse=True,
        infotree=None,
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
