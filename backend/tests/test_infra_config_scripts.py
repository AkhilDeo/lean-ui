from __future__ import annotations

from scripts.check_railway_state import assert_limits, assert_replicas
from scripts.validate_async_env import missing_keys


def test_missing_keys_utility() -> None:
    required = {"A", "B", "C"}
    env = {"A": "1", "C": "3"}
    assert missing_keys(required, env) == ["B"]


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
