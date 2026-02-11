from __future__ import annotations

import pytest
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.async_jobs import (
    AsyncBacklogFullError,
    AsyncJobStatus,
    InMemoryAsyncJobs,
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
    assert done.results[0]["id"] == "s1"


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
    assert [item["id"] for item in done.results] == ["a", "b"]


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
