from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from kimina_client import CheckRequest, ReplResponse, VerifyRequestBody
from pydantic import ValidationError

from server.request_policy import normalize_request_policy
from server.settings import Settings


def test_normalize_request_policy_applies_server_locks() -> None:
    settings = Settings(_env_file=None)
    settings.request_timeout_max_sec = 60
    settings.allow_client_timeout_override = True
    settings.allow_client_debug = False
    settings.allow_client_reuse_override = False

    policy = normalize_request_policy(
        timeout=999,
        debug=True,
        reuse=False,
        settings=settings,
    )
    assert policy.timeout == 60
    assert policy.debug is False
    assert policy.reuse is True


def test_normalize_request_policy_can_disable_timeout_override() -> None:
    settings = Settings(_env_file=None)
    settings.request_timeout_max_sec = 45
    settings.allow_client_timeout_override = False
    settings.allow_client_debug = True
    settings.allow_client_reuse_override = True

    policy = normalize_request_policy(
        timeout=3,
        debug=True,
        reuse=False,
        settings=settings,
    )
    assert policy.timeout == 45
    assert policy.debug is True
    assert policy.reuse is False


def test_check_request_forbids_unknown_fields() -> None:
    payload = {
        "snippets": [{"id": "one", "code": "#check Nat"}],
        "timeout": 30,
        "debug": False,
        "reuse": True,
        "LEAN_SERVER_MAX_REPLS": 999,
    }
    with pytest.raises(ValidationError):
        CheckRequest.model_validate(payload)


def test_verify_request_forbids_unknown_fields() -> None:
    payload = {
        "codes": [{"custom_id": "1", "code": "#check Nat"}],
        "timeout": 30,
        "disable_cache": False,
        "LEAN_SERVER_MAX_REPLS": 999,
    }
    with pytest.raises(ValidationError):
        VerifyRequestBody.model_validate(payload)


def test_verify_request_accepts_environment_field() -> None:
    payload = {
        "codes": [{"custom_id": "1", "code": "#check Nat"}],
        "timeout": 30,
        "disable_cache": False,
        "environment": "formal-conjectures-v4.27",
    }
    request = VerifyRequestBody.model_validate(payload)
    assert request.environment == "formal-conjectures-v4.27"


def test_check_request_accepts_include_sorry_details_field() -> None:
    payload = {
        "snippets": [{"id": "one", "code": "#check Nat"}],
        "timeout": 30,
        "include_sorry_details": True,
    }
    request = CheckRequest.model_validate(payload)
    assert request.include_sorry_details is True


def test_verify_request_accepts_include_sorry_details_field() -> None:
    payload = {
        "codes": [{"custom_id": "1", "code": "#check Nat"}],
        "timeout": 30,
        "disable_cache": False,
        "include_sorry_details": True,
    }
    request = VerifyRequestBody.model_validate(payload)
    assert request.include_sorry_details is True


def test_api_check_rejects_unknown_fields(root_client: TestClient) -> None:
    payload = {
        "snippets": [{"id": "one", "code": "#check Nat"}],
        "timeout": 30,
        "LEAN_SERVER_MAX_REPLS": 999,
    }
    resp = root_client.post("/api/check", json=payload)
    assert resp.status_code == 422


def test_api_check_applies_normalized_policy(
    root_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["timeout"] = args[1]
        captured["debug"] = args[2]
        captured["reuse"] = args[4]
        captured["include_sorry_details"] = args[6]
        return [ReplResponse(id="one", time=0.1, response={"env": 0})]

    monkeypatch.setattr("server.routers.check.run_checks", fake_run_checks)

    payload = {
        "snippets": [{"id": "one", "code": "#check Nat"}],
        "timeout": 999,
        "debug": True,
        "reuse": False,
        "include_sorry_details": True,
    }
    resp = root_client.post("/api/check", json=payload)
    assert resp.status_code == 200
    assert captured["timeout"] == 60.0
    assert captured["debug"] is False
    assert captured["reuse"] is True
    assert captured["include_sorry_details"] is True


def test_check_request_accepts_environment_field() -> None:
    payload = {
        "snippets": [{"id": "one", "code": "#check Nat"}],
        "timeout": 30,
        "environment": "mathlib-v4.27",
    }
    request = CheckRequest.model_validate(payload)
    assert request.environment == "mathlib-v4.27"


def test_api_verify_applies_normalized_policy(
    root_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["timeout"] = args[1]
        captured["debug"] = args[2]
        captured["reuse"] = args[4]
        captured["include_sorry_details"] = args[6]
        return [ReplResponse(id="one", time=0.1, response={"env": 0})]

    monkeypatch.setattr("server.routers.backward.run_checks", fake_run_checks)

    payload = {
        "codes": [{"custom_id": "one", "code": "#check Nat"}],
        "timeout": 999,
        "disable_cache": True,
        "include_sorry_details": True,
    }
    resp = root_client.post("/verify", json=payload)
    assert resp.status_code == 200
    assert captured["timeout"] == 60.0
    assert captured["debug"] is False
    assert captured["reuse"] is True
    assert captured["include_sorry_details"] is True


def test_api_verify_returns_rich_sorry_details_when_requested(
    root_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_checks(*args, **kwargs):  # type: ignore[no-untyped-def]
        return [
            ReplResponse(
                id="one",
                time=0.1,
                response={
                    "env": 0,
                    "sorries": [
                        {
                            "line": 3,
                            "column": 2,
                            "endLine": 3,
                            "endColumn": 7,
                            "pos": {"line": 3, "column": 2},
                            "endPos": {"line": 3, "column": 7},
                            "goal": "⊢ x = x",
                            "localContext": "x : Int",
                            "proofState": "x : Int\n⊢ x = x",
                            "proofStateId": 4,
                        }
                    ],
                },
            )
        ]

    monkeypatch.setattr("server.routers.backward.run_checks", fake_run_checks)

    payload = {
        "codes": [{"custom_id": "one", "code": "theorem foo (x : Int) : x = x := by sorry"}],
        "include_sorry_details": True,
    }
    resp = root_client.post("/verify", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    sorry = body["results"][0]["response"]["sorries"][0]
    assert sorry["goal"] == "⊢ x = x"
    assert sorry["localContext"] == "x : Int"
    assert sorry["proofState"] == "x : Int\n⊢ x = x"
    assert sorry["proofStateId"] == 4
