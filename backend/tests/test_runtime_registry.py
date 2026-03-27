import pytest

from server.runtime_registry import (
    RuntimeConfigurationError,
    build_runtime_registry,
    runtime_env_key,
    seeded_runtime_ids,
    validate_runtime_configuration,
)
from server.settings import Settings


def test_runtime_registry_seeds_curated_versions_with_415_default() -> None:
    registry = build_runtime_registry("v4.15.0")

    assert registry.default_runtime_id == "v4.15.0"
    assert registry.known_runtime_ids() == [
        "v4.9.0",
        "v4.15.0",
    ]
    assert registry.require("v4.9.0").display_name == "Mathlib 4.9.0"
    assert registry.require("v4.15.0").is_default is True


def test_gateway_runtime_validation_requires_seeded_runtime_wiring() -> None:
    settings = Settings(_env_file=None)
    settings.gateway_enabled = True
    settings.async_enabled = True
    settings.railway_environment_id = "railway-env"

    with pytest.raises(RuntimeConfigurationError, match="BASE_URL"):
        validate_runtime_configuration(settings)


def test_gateway_runtime_validation_accepts_fully_wired_seeded_registry() -> None:
    env = {}
    for runtime_id in seeded_runtime_ids():
        env[runtime_env_key(runtime_id, "SERVICE_ID")] = f"{runtime_id}-service"
        env[runtime_env_key(runtime_id, "BASE_URL")] = f"https://{runtime_id}.internal"

    settings = Settings(_env_file=None)
    settings.gateway_enabled = True
    settings.async_enabled = True
    settings.railway_environment_id = "railway-env"

    registry = build_runtime_registry("v4.15.0", env=env)
    validate_runtime_configuration(settings, registry)


def test_embedded_runtime_validation_requires_runtime_service_wiring() -> None:
    settings = Settings(_env_file=None)
    settings.async_enabled = True
    settings.embedded_worker_enabled = True
    settings.runtime_id = "v4.15.0"
    settings.lean_version = "v4.15.0"
    settings.init_repls = {}

    with pytest.raises(RuntimeConfigurationError, match="LEAN_SERVER_RUNTIME_SERVICE_ID"):
        validate_runtime_configuration(settings)


def test_embedded_runtime_validation_rejects_version_mismatch() -> None:
    env = {
        runtime_env_key("v4.15.0", "SERVICE_ID"): "runtime-service",
        runtime_env_key("v4.15.0", "BASE_URL"): "https://v4.15.0.internal",
    }
    settings = Settings(_env_file=None)
    settings.async_enabled = True
    settings.embedded_worker_enabled = True
    settings.runtime_id = "v4.15.0"
    settings.lean_version = "v4.9.0"
    settings.runtime_service_id = "runtime-service"
    settings.railway_environment_id = "railway-env"
    settings.init_repls = {}

    registry = build_runtime_registry("v4.15.0", env=env)
    with pytest.raises(RuntimeConfigurationError, match="LEAN_SERVER_LEAN_VERSION"):
        validate_runtime_configuration(settings, registry)
