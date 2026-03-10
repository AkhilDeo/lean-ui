from __future__ import annotations

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from server.main import create_app
from server.settings import Environment, Settings


@pytest.mark.asyncio
async def test_request_logging_skips_debug_bind_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    settings.environment = Environment.dev
    settings.async_enabled = False
    settings.log_level = "INFO"
    app = create_app(settings)

    def fail_bind(**kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"logger.bind should not run when debug logging is disabled: {kwargs}")

    monkeypatch.setattr("server.main.logger.bind", fail_bind)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
