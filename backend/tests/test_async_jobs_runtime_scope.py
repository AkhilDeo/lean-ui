from __future__ import annotations

import pytest
from kimina_client import CheckRequest, Snippet

from server.async_jobs import InMemoryAsyncJobs


@pytest.mark.asyncio
async def test_in_memory_async_jobs_dequeue_preserves_v415_runtime_identity() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)

    await jobs.submit(
        CheckRequest(
            snippets=[Snippet(id="first", code="import Mathlib\n#check Nat")],
            timeout=30,
            runtime_id="v4.15.0",
        )
    )
    await jobs.submit(
        CheckRequest(
            snippets=[Snippet(id="second", code="import Mathlib\n#check Int")],
            timeout=30,
            runtime_id="v4.15.0",
        )
    )

    first_task = await jobs.dequeue_task(timeout_sec=1, runtime_id="v4.15.0")
    second_task = await jobs.dequeue_task(timeout_sec=1, runtime_id="v4.15.0")

    assert first_task is not None
    assert second_task is not None
    assert first_task.runtime_id == "v4.15.0"
    assert second_task.runtime_id == "v4.15.0"
    assert first_task.snippet.id == "first"
    assert second_task.snippet.id == "second"
