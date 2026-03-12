from __future__ import annotations

import pytest

from kimina_client import AsyncKiminaClient, KiminaClient, Snippet


def test_sync_client_includes_environment_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {"results": [{"id": "one", "time": 0.1, "response": {"env": 0}}]}

    monkeypatch.setattr(KiminaClient, "_query", fake_query)

    client = KiminaClient(api_url="http://testserver")
    client.check("#check Nat", environment="mathlib-v4.27", show_progress=False)

    assert captured["url"] == "http://testserver/api/check"
    assert captured["payload"]["environment"] == "mathlib-v4.27"  # type: ignore[index]


def test_sync_client_includes_sorry_details_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {"results": [{"id": "one", "time": 0.1, "response": {"env": 0}}]}

    monkeypatch.setattr(KiminaClient, "_query", fake_query)

    client = KiminaClient(api_url="http://testserver")
    client.check("#check Nat", include_sorry_details=True, show_progress=False)

    assert captured["url"] == "http://testserver/api/check"
    assert captured["payload"]["include_sorry_details"] is True  # type: ignore[index]


def test_sync_client_async_check_uses_gateway_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {
            "job_id": "mathlib-v4.15:abc123",
            "status": "queued",
            "total_snippets": 1,
            "queued_at": "2026-03-11T00:00:00Z",
            "expires_at": "2026-03-12T00:00:00Z",
        }

    monkeypatch.setattr(KiminaClient, "_query", fake_query)

    client = KiminaClient(api_url="http://testserver")
    response = client.async_check(
        [Snippet(id="one", code="#check Nat")],
        environment="formal-conjectures-v4.27",
    )

    assert response.job_id == "mathlib-v4.15:abc123"
    assert captured["url"] == "http://testserver/api/async/check"
    assert captured["payload"]["environment"] == "formal-conjectures-v4.27"  # type: ignore[index]


def test_sync_client_async_check_includes_sorry_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {
            "job_id": "mathlib-v4.15:abc123",
            "status": "queued",
            "total_snippets": 1,
            "queued_at": "2026-03-11T00:00:00Z",
            "expires_at": "2026-03-12T00:00:00Z",
        }

    monkeypatch.setattr(KiminaClient, "_query", fake_query)

    client = KiminaClient(api_url="http://testserver")
    client.async_check(
        [Snippet(id="one", code="theorem foo : Nat := by sorry")],
        include_sorry_details=True,
    )

    assert captured["url"] == "http://testserver/api/async/check"
    assert captured["payload"]["include_sorry_details"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_async_client_environments_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        assert url == "http://testserver/api/environments"
        assert method == "GET"
        return {
            "default_environment": "mathlib-v4.15",
            "environments": [
                {
                    "id": "mathlib-v4.15",
                    "display_name": "Mathlib 4.15",
                    "lean_version": "v4.15.0",
                    "project_label": "Mathlib",
                    "project_type": "mathlib",
                    "selectable": True,
                    "auto_routable": True,
                    "is_default": True,
                }
            ],
        }

    monkeypatch.setattr(AsyncKiminaClient, "_query", fake_query)

    client = AsyncKiminaClient(api_url="http://testserver")
    response = await client.environments()
    assert response.default_environment == "mathlib-v4.15"
    await client.session.aclose()


@pytest.mark.asyncio
async def test_async_client_environment_health_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        assert url == "http://testserver/api/environments/health"
        assert method == "GET"
        return {
            "environments": [
                {
                    "id": "mathlib-v4.15",
                    "healthy": True,
                    "status": "ok",
                    "environment_id": "mathlib-v4.15",
                    "lean_version": "v4.15.0",
                    "project_label": "Mathlib",
                    "project_type": "mathlib",
                }
            ]
        }

    monkeypatch.setattr(AsyncKiminaClient, "_query", fake_query)

    client = AsyncKiminaClient(api_url="http://testserver")
    response = await client.environment_health()
    assert response.environments[0].environment_id == "mathlib-v4.15"
    await client.session.aclose()


@pytest.mark.asyncio
async def test_async_client_includes_sorry_details_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {"results": [{"id": "one", "time": 0.1, "response": {"env": 0}}]}

    monkeypatch.setattr(AsyncKiminaClient, "_query", fake_query)

    client = AsyncKiminaClient(api_url="http://testserver")
    await client.check("#check Nat", include_sorry_details=True, show_progress=False)

    assert captured["url"] == "http://testserver/api/check"
    assert captured["payload"]["include_sorry_details"] is True  # type: ignore[index]
    await client.session.aclose()


@pytest.mark.asyncio
async def test_async_client_async_check_includes_sorry_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_query(self, url, payload=None, method="POST"):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["payload"] = payload
        return {
            "job_id": "mathlib-v4.15:abc123",
            "status": "queued",
            "total_snippets": 1,
            "queued_at": "2026-03-11T00:00:00Z",
            "expires_at": "2026-03-12T00:00:00Z",
        }

    monkeypatch.setattr(AsyncKiminaClient, "_query", fake_query)

    client = AsyncKiminaClient(api_url="http://testserver")
    await client.async_check(
        [Snippet(id="one", code="theorem foo : Nat := by sorry")],
        include_sorry_details=True,
    )

    assert captured["url"] == "http://testserver/api/async/check"
    assert captured["payload"]["include_sorry_details"] is True  # type: ignore[index]
    await client.session.aclose()
