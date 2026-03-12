from __future__ import annotations

from fastapi.testclient import TestClient
from server.main import create_app
from server.settings import Environment, Settings


def _client() -> TestClient:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.api_key = "test-key"
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = True
    settings.async_use_in_memory_backend = True
    app = create_app(settings)
    client = TestClient(app, base_url="http://testserver")
    client.headers.update({"Authorization": "Bearer test-key"})
    return client


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


def test_openapi_exposes_explicit_snippet_outcome_fields() -> None:
    with _client() as client:
        schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
        repl_response = schemas["ReplResponse"]
        async_poll = schemas["AsyncPollResponse"]

        assert "status" in repl_response["properties"]
        assert repl_response["properties"]["status"]["$ref"] == "#/components/schemas/SnippetStatus"
        assert repl_response["properties"]["passed"]["type"] == "boolean"
        assert async_poll["properties"]["results"]["anyOf"][0]["items"]["$ref"] == "#/components/schemas/ReplResponse"
