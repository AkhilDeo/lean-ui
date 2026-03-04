from __future__ import annotations

import asyncio

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.main import create_app
from server.settings import Environment, Settings


def _build_app(
    backlog_limit: int = 50000,
    async_metrics_enabled: bool = True,
    admission_queue_limit: int = 0,
):
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.api_key = "test-key"
    settings.database_url = None
    settings.async_enabled = True
    settings.async_use_in_memory_backend = True
    settings.async_backlog_limit = backlog_limit
    settings.async_metrics_enabled = async_metrics_enabled
    settings.async_admission_queue_limit = admission_queue_limit
    settings.max_repls = 1
    settings.max_repl_uses = 3
    settings.init_repls = {}
    return create_app(settings)


@pytest.mark.asyncio
async def test_async_submit_poll_lifecycle() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
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
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
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
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
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
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            resp = await client.get("/async/check/not-found")
            assert resp.status_code == 404


@pytest.mark.asyncio
async def test_async_submit_policy_normalizes_timeout_debug_and_reuse() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[Snippet(id="one", code="#check Nat")],
                timeout=999,
                debug=True,
                reuse=False,
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200

            jobs = app.state.async_jobs
            task = await jobs.dequeue_task(timeout_sec=1)
            assert task is not None
            assert task.timeout == float(app.state.settings.request_timeout_max_sec)
            assert task.debug is False
            assert task.reuse is True


@pytest.mark.asyncio
async def test_async_metrics_endpoint_returns_queue_health() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            metrics = await client.get("/async/metrics")
            assert metrics.status_code == 200
            data = metrics.json()
            assert data["queue_depth"] >= 0
            assert data["inflight_jobs"] >= 0
            assert data["running_tasks"] >= 0


@pytest.mark.asyncio
async def test_async_metrics_endpoint_disabled_returns_404() -> None:
    app = _build_app(async_metrics_enabled=False)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            metrics = await client.get("/async/metrics")
            assert metrics.status_code == 404


@pytest.mark.asyncio
async def test_async_admission_soft_limit_returns_429() -> None:
    app = _build_app(admission_queue_limit=1)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
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
async def test_async_poll_wait_sec_long_polling() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[Snippet(id="one", code="#check Nat")], timeout=30
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200
            job_id = submit.json()["job_id"]
            jobs = app.state.async_jobs

            async def complete_job() -> None:
                await asyncio.sleep(0.05)
                task = await jobs.dequeue_task(timeout_sec=1)
                assert task is not None
                await jobs.mark_task_started(task)
                await jobs.mark_task_success(
                    task, ReplResponse(id="one", time=0.1, response={"env": 0})
                )

            worker = asyncio.create_task(complete_job())
            try:
                poll = await client.get(f"/async/check/{job_id}", params={"wait_sec": 1})
            finally:
                await worker

            assert poll.status_code == 200
            assert poll.json()["status"] == "completed"
