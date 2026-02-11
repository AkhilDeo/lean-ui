from __future__ import annotations

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.main import create_app
from server.settings import Environment, Settings


def _build_app(backlog_limit: int = 50000):
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.database_url = None
    settings.async_enabled = True
    settings.async_use_in_memory_backend = True
    settings.async_backlog_limit = backlog_limit
    settings.max_repls = 1
    settings.max_repl_uses = 3
    settings.init_repls = {}
    return create_app(settings)


@pytest.mark.asyncio
async def test_async_submit_poll_lifecycle() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver/api"
        ) as client:
            payload = CheckRequest(
                snippets=[Snippet(id="one", code="#check Nat")], timeout=30
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200
            body = submit.json()
            job_id = body["job_id"]
            assert body["status"] == "queued"

            poll = await client.get(f"/async/check/{job_id}")
            assert poll.status_code == 200
            assert poll.json()["status"] == "queued"

            jobs = app.state.async_jobs
            task = await jobs.dequeue_task(timeout_sec=1)
            assert task is not None
            await jobs.mark_task_started(task)
            await jobs.mark_task_success(
                task, ReplResponse(id="one", time=0.1, response={"env": 0})
            )

            done = await client.get(f"/async/check/{job_id}")
            assert done.status_code == 200
            data = done.json()
            assert data["status"] == "completed"
            assert data["results"][0]["id"] == "one"


@pytest.mark.asyncio
async def test_async_submit_batch_final_results_ordered() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver/api"
        ) as client:
            payload = CheckRequest(
                snippets=[
                    Snippet(id="a", code="#check Nat"),
                    Snippet(id="b", code="#check Int"),
                ],
                timeout=30,
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200
            job_id = submit.json()["job_id"]

            jobs = app.state.async_jobs
            task1 = await jobs.dequeue_task(timeout_sec=1)
            task2 = await jobs.dequeue_task(timeout_sec=1)
            assert task1 is not None and task2 is not None
            await jobs.mark_task_started(task2)
            await jobs.mark_task_success(
                task2, ReplResponse(id="b", time=0.1, response={"env": 0})
            )
            await jobs.mark_task_started(task1)
            await jobs.mark_task_success(
                task1, ReplResponse(id="a", time=0.1, response={"env": 0})
            )

            done = await client.get(f"/async/check/{job_id}")
            assert done.status_code == 200
            data = done.json()
            assert [item["id"] for item in data["results"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_async_backlog_limit_returns_429() -> None:
    app = _build_app(backlog_limit=1)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver/api"
        ) as client:
            payload1 = CheckRequest(
                snippets=[Snippet(id="one", code="#check Nat")], timeout=30
            ).model_dump()
            payload2 = CheckRequest(
                snippets=[Snippet(id="two", code="#check Int")], timeout=30
            ).model_dump()
            first = await client.post("/async/check", json=payload1)
            assert first.status_code == 200
            second = await client.post("/async/check", json=payload2)
            assert second.status_code == 429


@pytest.mark.asyncio
async def test_async_poll_unknown_job_returns_404() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver/api"
        ) as client:
            resp = await client.get("/async/check/not-found")
            assert resp.status_code == 404
