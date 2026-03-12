from __future__ import annotations

import os

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from kimina_client import CheckRequest, Snippet

from server.main import create_app
from server.repl import Repl
from server.settings import Environment, Settings
from server.worker import process_task


def _require_real_repl_runtime() -> None:
    if os.getenv("LEAN_SERVER_RUN_REAL_REPL_TESTS") != "1":
        pytest.skip("set LEAN_SERVER_RUN_REAL_REPL_TESTS=1 to run real REPL smoke")


@pytest.mark.asyncio
async def test_direct_repl_import_mathlib_smoke() -> None:
    _require_real_repl_runtime()

    repl = await Repl.create("", 1, 8192)
    try:
        await repl.start()
        response = await repl.send_timeout(
            Snippet(id="mathlib-smoke", code="import Mathlib"),
            timeout=60,
            is_header=True,
        )
        assert response.error is None
        assert response.response is not None
        assert "env" in response.response
    finally:
        await repl.close()


def _real_app(*, async_enabled: bool) -> Settings:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.api_key = "test-key"
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = async_enabled
    settings.async_use_in_memory_backend = async_enabled
    settings.max_repls = 1
    settings.max_repl_uses = 3
    return settings


@pytest.mark.asyncio
async def test_sync_check_mathlib_smoke() -> None:
    _require_real_repl_runtime()

    app = create_app(_real_app(async_enabled=False))
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[
                    Snippet(
                        id="sync-smoke",
                        code="import Mathlib\nexample : 1 = 1 := by\n  rfl\n",
                    )
                ],
                timeout=60,
            ).model_dump()
            response = await client.post("/check", json=payload)
            assert response.status_code == 200
            body = response.json()
            assert body["results"][0].get("error") in (None, "")


@pytest.mark.asyncio
async def test_async_check_mathlib_smoke() -> None:
    _require_real_repl_runtime()

    app = create_app(_real_app(async_enabled=True))
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            payload = CheckRequest(
                snippets=[
                    Snippet(
                        id="async-smoke",
                        code="import Mathlib\nexample : 1 = 1 := by\n  rfl\n",
                    )
                ],
                timeout=60,
            ).model_dump()
            submit = await client.post("/async/check", json=payload)
            assert submit.status_code == 200
            job_id = submit.json()["job_id"]

            processed = await process_task(
                jobs=app.state.async_jobs,
                manager=app.state.manager,
                task_timeout_sec=1,
            )
            assert processed is True

            poll = await client.get(f"/async/check/{job_id}")
            assert poll.status_code == 200
            body = poll.json()
            assert body["status"] == "completed"
            assert body["results"][0].get("error") in (None, "")
