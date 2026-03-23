from __future__ import annotations

import pytest
from kimina_client import CheckRequest, Snippet

from server.async_jobs import InMemoryAsyncJobs


@pytest.mark.asyncio
async def test_in_memory_async_jobs_dequeue_is_runtime_scoped() -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)

    await jobs.submit(
        CheckRequest(
            snippets=[Snippet(id="old", code="import Mathlib\n#check Nat")],
            timeout=30,
            runtime_id="v4.9.0",
        )
    )
    await jobs.submit(
        CheckRequest(
            snippets=[Snippet(id="new", code="import Mathlib\n#check Int")],
            timeout=30,
            runtime_id="v4.28.0",
        )
    )

    old_task = await jobs.dequeue_task(timeout_sec=1, runtime_id="v4.9.0")
    new_task = await jobs.dequeue_task(timeout_sec=1, runtime_id="v4.28.0")

    assert old_task is not None
    assert new_task is not None
    assert old_task.runtime_id == "v4.9.0"
    assert new_task.runtime_id == "v4.28.0"
    assert old_task.snippet.id == "old"
    assert new_task.snippet.id == "new"
