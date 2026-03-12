from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from kimina_client import CheckResponse, ReplResponse

from server.main import create_app
from server.manager import Manager
from server.settings import Environment, Settings


def _gateway_settings() -> Settings:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.api_key = "test-key"
    settings.database_url = None
    settings.init_repls = {}
    settings.async_enabled = False
    settings.environment_id = "mathlib-v4.15"
    settings.project_label = "Mathlib"
    settings.project_type = "mathlib"
    settings.gateway_default_environment = "mathlib-v4.15"
    settings.gateway_environments = [
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
    return settings


def _client() -> TestClient:
    app = create_app(_gateway_settings())
    client = TestClient(app, base_url="http://testserver")
    client.headers.update({"Authorization": "Bearer test-key"})
    return client


def test_api_check_defaults_to_mathlib_415_and_sets_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [ReplResponse(id="one", time=0.1, response={"env": 0})]

    monkeypatch.setattr("server.routers.check.run_checks", fake_run_checks)

    with _client() as client:
        resp = client.post(
            "/api/check",
            json={"snippets": [{"id": "one", "code": "#check Nat"}]},
        )

    assert resp.status_code == 200
    assert resp.headers["X-Lean-Environment-ID"] == "mathlib-v4.15"
    body = resp.json()
    assert body["results"][0]["diagnostics"]["environment_id"] == "mathlib-v4.15"
    assert body["results"][0]["diagnostics"]["lean_version"] == "v4.15.0"
    assert body["results"][0]["diagnostics"]["project_label"] == "Mathlib"


def test_api_check_auto_routes_formal_conjectures_to_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_proxy_check_request(*, request, target_environment, settings):  # type: ignore[no-untyped-def]
        captured["environment"] = target_environment.id
        return CheckResponse(
            results=[ReplResponse(id="fc", time=0.1, response={"env": 0})]
        )

    monkeypatch.setattr(
        "server.routers.check.proxy_check_request",
        fake_proxy_check_request,
    )

    with _client() as client:
        resp = client.post(
            "/api/check",
            json={
                "snippets": [
                    {
                        "id": "fc",
                        "code": "import FormalConjectures.Util.ProblemImports\n#check Nat",
                    }
                ],
                "environment": "auto",
            },
        )

    assert resp.status_code == 200
    assert captured["environment"] == "formal-conjectures-v4.27"
    assert resp.headers["X-Lean-Environment-ID"] == "formal-conjectures-v4.27"
    assert resp.json()["results"][0]["diagnostics"]["environment_id"] == (
        "formal-conjectures-v4.27"
    )


def test_api_check_rejects_incompatible_explicit_environment() -> None:
    with _client() as client:
        resp = client.post(
            "/api/check",
            json={
                "snippets": [
                    {
                        "id": "fc",
                        "code": "import FormalConjectures.Util.ProblemImports\n#check Nat",
                    }
                ],
                "environment": "mathlib-v4.27",
            },
    )

    assert resp.status_code == 400
    assert "require 'formal-conjectures-v4.27'" in resp.json()["detail"]


def test_api_environments_lists_supported_registry() -> None:
    with _client() as client:
        resp = client.get("/api/environments")

    assert resp.status_code == 200
    data = resp.json()
    assert data["default_environment"] == "mathlib-v4.15"
    assert {environment["id"] for environment in data["environments"]} == {
        "mathlib-v4.15",
        "mathlib-v4.27",
        "formal-conjectures-v4.27",
    }


def test_api_environment_health_reports_gateway_and_remote_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_proxy_health_request(*, target_environment, settings):  # type: ignore[no-untyped-def]
        return {
            "status": "ok",
            "environment_id": target_environment.id,
            "lean_version": target_environment.lean_version,
            "project_label": target_environment.project_label,
            "project_type": target_environment.project_type,
        }

    monkeypatch.setattr(
        "server.routers.environments.proxy_health_request",
        fake_proxy_health_request,
    )

    with _client() as client:
        resp = client.get("/api/environments/health")

    assert resp.status_code == 200
    data = resp.json()
    assert {environment["id"] for environment in data["environments"]} == {
        "mathlib-v4.15",
        "mathlib-v4.27",
        "formal-conjectures-v4.27",
    }
    assert all(environment["healthy"] for environment in data["environments"])


def test_health_reports_environment_metadata() -> None:
    with _client() as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["environment_id"] == "mathlib-v4.15"
    assert resp.json()["lean_version"] == "v4.15.0"
    assert resp.json()["project_label"] == "Mathlib"


@dataclass
class _FakeRepl:
    last_check_at: datetime


@pytest.mark.asyncio
async def test_manager_reaps_idle_repls(monkeypatch: pytest.MonkeyPatch) -> None:
    reaped: list[_FakeRepl] = []

    async def fake_close_verbose(repl):  # type: ignore[no-untyped-def]
        reaped.append(repl)

    monkeypatch.setattr("server.manager.close_verbose", fake_close_verbose)

    manager = Manager(max_repls=1, idle_repl_ttl_sec=1)
    manager._free = [_FakeRepl(last_check_at=datetime.now() - timedelta(seconds=5))]  # type: ignore[list-item]

    count = await manager.reap_idle_repls()

    assert count == 1
    assert len(manager._free) == 0
