from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.settings import Environment, Settings


def test_prod_startup_requires_api_key() -> None:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = False
    settings.api_key = None

    app = create_app(settings)
    with pytest.raises(RuntimeError, match="LEAN_SERVER_API_KEY"):
        with TestClient(app):
            pass


def test_prod_rejects_missing_authorization_header() -> None:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = False
    settings.api_key = "test-key"

    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        resp = client.post("/api/check", json={})
        assert resp.status_code == 401


def test_prod_accepts_valid_authorization_header() -> None:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = False
    settings.api_key = "test-key"

    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        resp = client.post(
            "/api/check", json={}, headers={"Authorization": "Bearer test-key"}
        )
        assert resp.status_code == 422
