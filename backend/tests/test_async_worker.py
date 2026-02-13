from __future__ import annotations

import pytest
from fastapi import HTTPException
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.async_jobs import InMemoryAsyncJobs
from server.worker import process_task


@pytest.mark.asyncio
async def test_worker_process_task_success(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )

    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [ReplResponse(id="s1", time=0.1, response={"env": 0})]

    monkeypatch.setattr("server.worker.run_checks", fake_run_checks)

    did_work = await process_task(jobs=jobs, manager=object(), task_timeout_sec=1)
    assert did_work is True

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.progress.done == 1
    assert poll.results is not None
    assert poll.results[0]["id"] == "s1"


@pytest.mark.asyncio
async def test_worker_process_task_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )

    calls = {"n": 0}

    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise HTTPException(status_code=429, detail="No available REPLs")

    monkeypatch.setattr("server.worker.run_checks", fake_run_checks)

    did_work = await process_task(jobs=jobs, manager=object(), task_timeout_sec=1)
    assert did_work is True

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.progress.failed == 1
    assert poll.results is not None
    assert "No available REPLs" in poll.results[0]["error"]
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_worker_retries_transient_http_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = InMemoryAsyncJobs(ttl_sec=3600, backlog_limit=10)
    submit = await jobs.submit(
        CheckRequest(snippets=[Snippet(id="s1", code="#check Nat")], timeout=30)
    )

    calls = {"n": 0}

    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(status_code=429, detail="No available REPLs")
        return [ReplResponse(id="s1", time=0.1, response={"env": 0})]

    monkeypatch.setattr("server.worker.run_checks", fake_run_checks)

    did_work = await process_task(jobs=jobs, manager=object(), task_timeout_sec=1)
    assert did_work is True

    poll = await jobs.poll(submit.job_id)
    assert poll is not None
    assert poll.progress.done == 1
    assert poll.progress.failed == 0
    assert poll.results is not None
    assert poll.results[0]["id"] == "s1"
    assert calls["n"] == 2
