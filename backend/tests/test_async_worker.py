from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.async_jobs import InMemoryAsyncJobs
from server.settings import Settings
from server.worker import process_task, run_worker


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


@pytest.mark.asyncio
async def test_run_worker_starts_multiple_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    started: set[int] = set()

    class FakeJobs:
        async def recover_running_tasks(self) -> int:
            return 0

        async def close(self) -> None:
            return None

    class FakeManager:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs

        async def cleanup(self) -> None:
            return None

    async def fake_create_async_jobs(cfg):  # type: ignore[no-untyped-def]
        _ = cfg
        return FakeJobs()

    async def fake_consumer_loop(  # type: ignore[no-untyped-def]
        *,
        consumer_id: int,
        jobs,
        manager,
        task_timeout_sec: int,
        worker_retries: int,
    ) -> None:
        _ = jobs, manager, task_timeout_sec, worker_retries
        started.add(consumer_id)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr("server.worker.create_async_jobs", fake_create_async_jobs)
    monkeypatch.setattr("server.worker.Manager", FakeManager)
    monkeypatch.setattr("server.worker._consumer_loop", fake_consumer_loop)

    settings = Settings(_env_file=None)
    settings.async_enabled = True
    settings.max_repls = 4
    settings.async_worker_concurrency = 3

    task = asyncio.create_task(run_worker(settings))
    await asyncio.sleep(0.05)
    assert started == {1, 2, 3}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_run_worker_uses_configured_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inflight = 0
    max_inflight = 0
    reached_target = asyncio.Event()

    class DummyJobs:
        async def recover_running_tasks(self) -> int:
            return 0

        async def close(self) -> None:
            return None

    class DummyManager:
        async def cleanup(self) -> None:
            return None

    async def fake_create_async_jobs(_cfg: Settings) -> DummyJobs:
        return DummyJobs()

    async def fake_process_task(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        if max_inflight >= 3:
            reached_target.set()
        await asyncio.sleep(0.05)
        inflight -= 1
        return True

    monkeypatch.setattr("server.worker.create_async_jobs", fake_create_async_jobs)
    monkeypatch.setattr("server.worker.Manager", lambda **kwargs: DummyManager())
    monkeypatch.setattr("server.worker.process_task", fake_process_task)

    cfg = Settings(_env_file=None)
    cfg.async_enabled = True
    cfg.max_repls = 5
    cfg.async_worker_concurrency = 3

    task = asyncio.create_task(run_worker(cfg))
    await asyncio.wait_for(reached_target.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert max_inflight >= 3
