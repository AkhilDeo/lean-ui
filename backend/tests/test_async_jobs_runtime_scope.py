from __future__ import annotations

import pytest
from kimina_client import CheckRequest, Snippet

from server.async_jobs import InMemoryAsyncJobs
from server.settings import Settings


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


def test_in_memory_async_jobs_scope_runtime_ids_by_service_role() -> None:
    gateway_settings = Settings(_env_file=None)
    gateway_settings.gateway_enabled = True
    gateway_settings.default_runtime_id = "v4.9.0"
    gateway_jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10, settings=gateway_settings)
    assert gateway_jobs.runtime_ids == [
        "v4.9.0",
        "v4.15.0",
        "v4.24.0",
        "v4.27.0",
        "v4.28.0",
    ]

    runtime_settings = Settings(_env_file=None)
    runtime_settings.gateway_enabled = False
    runtime_settings.runtime_id = "v4.27.0"
    runtime_jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10, settings=runtime_settings)
    assert runtime_jobs.runtime_ids == ["v4.27.0"]
