from __future__ import annotations

import asyncio

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from kimina_client import CheckRequest, ReplResponse, Snippet

from server.async_jobs import AsyncPollResponse, AsyncProgress, AsyncQueueMetrics, AsyncSubmitResponse
from server.main import create_app
from server.settings import Environment, Settings


def _build_app(
    backlog_limit: int = 50000,
    async_metrics_enabled: bool = True,
    admission_queue_limit: int = 0,
    include_multi_env_registry: bool = False,
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
    settings.environment_id = "mathlib-v4.15"
    settings.project_label = "Mathlib"
    settings.project_type = "mathlib"
    settings.gateway_default_environment = "mathlib-v4.15"
    settings.gateway_environments = (
        [
            {
                "id": "mathlib-v4.15",
                "display_name": "Mathlib 4.15",
                "lean_version": "v4.15.0",
                "project_label": "Mathlib",
                "project_type": "mathlib",
            },
            {
                "id": "mathlib-v4.27",
                "display_name": "Mathlib 4.27",
                "lean_version": "v4.27.0",
                "project_label": "Mathlib",
                "project_type": "mathlib",
                "url": "http://mathlib-v427.internal",
            },
            {
                "id": "formal-conjectures-v4.27",
                "display_name": "Formal Conjectures 4.27",
                "lean_version": "v4.27.0",
                "project_label": "FormalConjectures",
                "project_type": "formal-conjectures",
                "url": "http://formal-conjectures.internal",
                "import_prefixes": ["FormalConjectures"],
            },
        ]
        if include_multi_env_registry
        else []
    )
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
async def test_async_submit_preserves_include_sorry_details() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[Snippet(id="one", code="theorem foo : Nat := by sorry")],
                timeout=30,
                include_sorry_details=True,
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200

            jobs = app.state.async_jobs
            task = await jobs.dequeue_task(timeout_sec=1)
            assert task is not None
            assert task.include_sorry_details is True


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
async def test_async_metrics_track_inflight_jobs_and_running_tasks() -> None:
    app = _build_app()

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[
                    Snippet(id="one", code="#check Nat"),
                    Snippet(id="two", code="#check Int"),
                ],
                timeout=30,
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200

            metrics = await client.get("/async/metrics")
            assert metrics.status_code == 200
            queued = metrics.json()
            assert queued["inflight_jobs"] == 1
            assert queued["running_tasks"] == 0

            jobs = app.state.async_jobs
            task = await jobs.dequeue_task(timeout_sec=1)
            assert task is not None
            await jobs.mark_task_started(task)

            metrics = await client.get("/async/metrics")
            assert metrics.status_code == 200
            running = metrics.json()
            assert running["inflight_jobs"] == 1
            assert running["running_tasks"] == 1

            await jobs.mark_task_success(
                task, ReplResponse(id=task.snippet.id, time=0.1, response={"env": 0})
            )

            second_task = await jobs.dequeue_task(timeout_sec=1)
            assert second_task is not None
            await jobs.mark_task_started(second_task)
            await jobs.mark_task_success(
                second_task,
                ReplResponse(id=second_task.snippet.id, time=0.1, response={"env": 0}),
            )

            metrics = await client.get("/async/metrics")
            assert metrics.status_code == 200
            finished = metrics.json()
            assert finished["inflight_jobs"] == 0
            assert finished["running_tasks"] == 0


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


@pytest.mark.asyncio
async def test_async_submit_routes_remote_environment_and_wraps_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(include_multi_env_registry=True)

    async def fake_proxy_async_submit_request(*, request, target_environment, settings):  # type: ignore[no-untyped-def]
        assert request.environment == "mathlib-v4.27"
        assert target_environment.id == "mathlib-v4.27"
        return AsyncSubmitResponse(
            job_id="remote-job",
            status="queued",
            total_snippets=1,
            queued_at="2026-03-11T00:00:00Z",
            expires_at="2026-03-12T00:00:00Z",
        )

    monkeypatch.setattr(
        "server.routers.async_check.proxy_async_submit_request",
        fake_proxy_async_submit_request,
    )

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[Snippet(id="one", code="#check Nat")],
                timeout=30,
                environment="mathlib-v4.27",
            ).model_dump()
            submit = await client.post("/async/check", json=payload)

    assert submit.status_code == 200
    assert submit.headers["X-Lean-Environment-ID"] == "mathlib-v4.27"
    assert submit.json()["job_id"] == "mathlib-v4.27:remote-job"


@pytest.mark.asyncio
async def test_async_poll_routes_wrapped_remote_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(include_multi_env_registry=True)

    async def fake_proxy_async_poll_request(*, job_id, wait_sec, target_environment, settings):  # type: ignore[no-untyped-def]
        assert job_id == "remote-job"
        assert wait_sec == 1.0
        assert target_environment.id == "mathlib-v4.27"
        return AsyncPollResponse(
            job_id="remote-job",
            status="completed",
            progress=AsyncProgress(total=1, done=1, failed=0, running=0),
            results=[{"id": "one", "response": {"env": 0}}],
            created_at="2026-03-11T00:00:00Z",
            updated_at="2026-03-11T00:00:01Z",
            expires_at="2026-03-12T00:00:00Z",
        )

    monkeypatch.setattr(
        "server.routers.async_check.proxy_async_poll_request",
        fake_proxy_async_poll_request,
    )

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            poll = await client.get(
                "/async/check/mathlib-v4.27:remote-job",
                params={"wait_sec": 1},
            )

    assert poll.status_code == 200
    assert poll.headers["X-Lean-Environment-ID"] == "mathlib-v4.27"
    assert poll.json()["job_id"] == "mathlib-v4.27:remote-job"


@pytest.mark.asyncio
async def test_async_metrics_aggregate_gateway_environments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(include_multi_env_registry=True)

    async def fake_local_metrics() -> AsyncQueueMetrics:
        return AsyncQueueMetrics(
            queue_depth=1,
            inflight_jobs=2,
            running_tasks=3,
            oldest_queued_age_sec=4.0,
            dequeue_rate=5.0,
            enqueue_rate=6.0,
        )

    async def fake_proxy_async_metrics_request(*, include_environments, target_environment, settings):  # type: ignore[no-untyped-def]
        assert include_environments is False
        if target_environment.id == "mathlib-v4.27":
            return AsyncQueueMetrics(
                queue_depth=10,
                inflight_jobs=20,
                running_tasks=30,
                oldest_queued_age_sec=40.0,
                dequeue_rate=50.0,
                enqueue_rate=60.0,
            )
        return AsyncQueueMetrics(
            queue_depth=100,
            inflight_jobs=200,
            running_tasks=300,
            oldest_queued_age_sec=400.0,
            dequeue_rate=500.0,
            enqueue_rate=600.0,
        )

    async with LifespanManager(app):
        monkeypatch.setattr(app.state.async_jobs, "metrics", fake_local_metrics)
        monkeypatch.setattr(
            "server.routers.async_check.proxy_async_metrics_request",
            fake_proxy_async_metrics_request,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            metrics = await client.get("/async/metrics", params={"include_environments": True})

    assert metrics.status_code == 200
    body = metrics.json()
    assert body["queue_depth"] == 111
    assert body["inflight_jobs"] == 222
    assert body["running_tasks"] == 333
    assert body["oldest_queued_age_sec"] == 400.0
    assert set(body["environments"]) == {
        "mathlib-v4.15",
        "mathlib-v4.27",
        "formal-conjectures-v4.27",
    }
