from __future__ import annotations

from fastapi.testclient import TestClient
from server.main import create_app
from server.settings import Environment, Settings


def _client() -> TestClient:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = True
    settings.async_use_in_memory_backend = True
    app = create_app(settings)
    return TestClient(app, base_url="http://testserver")


def test_sync_routes_still_present() -> None:
    with _client() as client:
        paths = client.get("/api/openapi.json").json()["paths"]
        assert "/api/check" in paths


def test_sync_check_validation_unchanged() -> None:
    with _client() as client:
        resp = client.post("/api/check", json={})
        assert resp.status_code == 422


def test_sync_verify_endpoint_still_exists() -> None:
    with _client() as client:
        resp = client.post("/verify", json={})
        assert resp.status_code == 422


def test_sync_backward_endpoint_still_exists() -> None:
    with _client() as client:
        resp = client.post("/one_pass_verify_batch", json={})
        assert resp.status_code == 422
