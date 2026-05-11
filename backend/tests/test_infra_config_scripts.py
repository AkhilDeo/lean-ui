from __future__ import annotations

from pathlib import Path

from scripts.check_railway_state import assert_limits, assert_replicas
from scripts.validate_async_env import missing_keys, required_keys_for_role


def test_missing_keys_utility() -> None:
    required = {"A", "B", "C"}
    env = {"A": "1", "C": "3"}
    assert missing_keys(required, env) == ["B"]


def test_single_service_role_requires_multi_runtime_wiring() -> None:
    required = required_keys_for_role("single-service")
    assert "LEAN_SERVER_MULTI_RUNTIME_ENABLED" in required
    assert "LEAN_SERVER_RUNTIME_IDS" in required
    assert "LEAN_SERVER_RUNTIME_ROOT" in required
    assert "LEAN_SERVER_EMBEDDED_WORKER_ENABLED" in required


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


def test_dockerfile_exports_multi_runtime_build_args() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "REPL_REPO_URL=${REPL_REPO_URL}" in dockerfile
    assert "REPL_BRANCH=${REPL_BRANCH}" in dockerfile
    assert "LEAN_SERVER_RUNTIME_IDS=${LEAN_SERVER_RUNTIME_IDS}" in dockerfile
    assert "LEAN_SERVER_MULTI_RUNTIME_ENABLED=true" in dockerfile


def test_setup_supports_multi_runtime_install() -> None:
    setup_script = (
        Path(__file__).resolve().parents[1] / "setup.sh"
    ).read_text(encoding="utf-8")
    assert "https://github.com/leanprover-community/repl.git" in setup_script
    assert 'REPL_BRANCH="${REPL_BRANCH:-$LEAN_SERVER_LEAN_VERSION}"' in setup_script
    assert "install_runtime()" in setup_script
