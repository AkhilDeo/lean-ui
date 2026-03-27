from __future__ import annotations

from pathlib import Path

from scripts.check_railway_state import assert_limits, assert_replicas
from scripts.validate_async_env import missing_keys, required_keys_for_role


def test_missing_keys_utility() -> None:
    required = {"A", "B", "C"}
    env = {"A": "1", "C": "3"}
    assert missing_keys(required, env) == ["B"]


def test_gateway_role_requires_seeded_runtime_service_ids_and_base_urls() -> None:
    required = required_keys_for_role("gateway")
    assert "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID" in required
    assert "LEAN_SERVER_RUNTIME_V4_15_0_SERVICE_ID" in required
    assert "LEAN_SERVER_RUNTIME_V4_15_0_BASE_URL" in required


def test_runtime_role_requires_single_runtime_service_wiring() -> None:
    required = required_keys_for_role("runtime")
    assert "LEAN_SERVER_RUNTIME_ID" in required
    assert "LEAN_SERVER_RUNTIME_SERVICE_ID" in required
    assert "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID" in required


def test_assert_limits_and_replicas_helpers() -> None:
    state = {
        "limitOverride": {"containers": {"cpu": 2, "memoryBytes": 10_000_000_000}},
        "serviceInstance": {
            "latestDeployment": {
                "meta": {
                    "serviceManifest": {
                        "deploy": {
                            "numReplicas": 1,
                            "multiRegionConfig": {"us-east4-eqdc4a": {"numReplicas": 1}},
                        }
                    }
                }
            }
        },
    }
    assert_limits(state, cpu=2, memory_gb=10)
    assert_replicas(state, expected=1)


def test_dockerfile_exports_single_runtime_build_args() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "REPL_REPO_URL=${REPL_REPO_URL}" in dockerfile
    assert "REPL_BRANCH=${REPL_BRANCH}" in dockerfile


def test_setup_defaults_single_mathlib_runtime() -> None:
    setup_script = (
        Path(__file__).resolve().parents[1] / "setup.sh"
    ).read_text(encoding="utf-8")
    assert "https://github.com/leanprover-community/repl.git" in setup_script
    assert 'REPL_BRANCH="${REPL_BRANCH:-$LEAN_SERVER_LEAN_VERSION}"' in setup_script
