from __future__ import annotations

from fastapi.testclient import TestClient
from kimina_client.models import CheckResponse, ReplResponse

from server.main import create_app
from server.runtime_registry import runtime_env_key, seeded_runtime_ids
from server.settings import Environment, Settings


def _gateway_app() -> Settings:
    settings = Settings(_env_file=None)
    settings.environment = Environment.prod
    settings.api_key = "test-key"
    settings.database_url = None
    settings.async_enabled = True
    settings.async_use_in_memory_backend = True
    settings.gateway_enabled = True
    settings.runtime_id = "gateway"
    settings.default_runtime_id = "v4.28.0"
    settings.railway_environment_id = "railway-env"
    return settings


def _seed_gateway_runtime_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for runtime_id in seeded_runtime_ids():
        monkeypatch.setenv(runtime_env_key(runtime_id, "SERVICE_ID"), f"{runtime_id}-service")
        monkeypatch.setenv(
            runtime_env_key(runtime_id, "BASE_URL"), f"https://{runtime_id}.internal"
        )


def test_gateway_runtimes_endpoint_exposes_registry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _seed_gateway_runtime_env(monkeypatch)
    app = create_app(_gateway_app())

    with TestClient(app, base_url="http://testserver") as client:
        client.headers.update({"Authorization": "Bearer test-key"})
        response = client.get("/api/runtimes")
        assert response.status_code == 200
        body = response.json()
        assert body["default_runtime_id"] == "v4.28.0"
        assert any(runtime["runtime_id"] == "v4.9.0" for runtime in body["runtimes"])


def test_gateway_sync_check_proxies_to_warm_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _seed_gateway_runtime_env(monkeypatch)
    app = create_app(_gateway_app())

    with TestClient(app, base_url="http://testserver") as client:
        client.headers.update({"Authorization": "Bearer test-key"})
        gateway = app.state.runtime_gateway

        async def fake_is_warm(runtime):  # type: ignore[no-untyped-def]
            return runtime.runtime_id == "v4.28.0"

        async def fake_proxy(runtime, payload):  # type: ignore[no-untyped-def]
            assert runtime.runtime_id == "v4.28.0"
            assert payload["runtime_id"] == "v4.28.0"
            return CheckResponse(
                results=[ReplResponse(id="verification", time=0.1, response={"env": 0})]
            )

        async def fake_wake(runtime):  # type: ignore[no-untyped-def]
            raise AssertionError(f"did not expect wake for warm runtime {runtime.runtime_id}")

        monkeypatch.setattr(gateway, "is_runtime_warm", fake_is_warm)
        monkeypatch.setattr(gateway, "proxy_sync_check", fake_proxy)
        monkeypatch.setattr(gateway, "wake_runtime", fake_wake)

        response = client.post(
            "/api/check",
            json={
                "snippets": [{"id": "verification", "code": "#check Nat"}],
                "runtime_id": "v4.28.0",
            },
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "valid"


def test_gateway_sync_check_wakes_cold_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _seed_gateway_runtime_env(monkeypatch)
    app = create_app(_gateway_app())

    with TestClient(app, base_url="http://testserver") as client:
        client.headers.update({"Authorization": "Bearer test-key"})
        gateway = app.state.runtime_gateway
        calls: list[str] = []

        async def fake_is_warm(runtime):  # type: ignore[no-untyped-def]
            return False

        async def fake_proxy(runtime, payload):  # type: ignore[no-untyped-def]
            _ = runtime, payload
            return None

        async def fake_wake(runtime):  # type: ignore[no-untyped-def]
            calls.append(runtime.runtime_id)

        monkeypatch.setattr(gateway, "is_runtime_warm", fake_is_warm)
        monkeypatch.setattr(gateway, "proxy_sync_check", fake_proxy)
        monkeypatch.setattr(gateway, "wake_runtime", fake_wake)

        response = client.post(
            "/api/check",
            json={
                "snippets": [{"id": "verification", "code": "#check Nat"}],
                "runtime_id": "v4.9.0",
            },
        )
        assert response.status_code == 503
        assert calls == ["v4.9.0"]
