#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from typing import Iterable

from server.runtime_registry import runtime_env_key, seeded_runtime_ids

COMMON_REQUIRED = {
    "LEAN_SERVER_ENVIRONMENT",
    "LEAN_SERVER_ASYNC_ENABLED",
    "LEAN_SERVER_ASYNC_METRICS_ENABLED",
    "LEAN_SERVER_ASYNC_ADMISSION_QUEUE_LIMIT",
    "LEAN_SERVER_ASYNC_ALERT_MAX_OLDEST_QUEUED_AGE_SEC",
    "LEAN_SERVER_REDIS_URL",
    "LEAN_SERVER_ASYNC_RESULT_TTL_SEC",
    "LEAN_SERVER_ASYNC_QUEUE_NAME_LIGHT",
    "LEAN_SERVER_ASYNC_QUEUE_NAME_HEAVY",
    "LEAN_SERVER_ASYNC_BACKLOG_LIMIT",
    "LEAN_SERVER_ASYNC_MAX_QUEUE_WAIT_SEC",
}

GATEWAY_REQUIRED = {
    "LEAN_SERVER_GATEWAY_ENABLED",
    "LEAN_SERVER_DEFAULT_RUNTIME_ID",
    "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID",
}

RUNTIME_REQUIRED = {
    "LEAN_SERVER_RUNTIME_ID",
    "LEAN_SERVER_RUNTIME_SERVICE_ID",
    "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID",
    "LEAN_SERVER_EMBEDDED_WORKER_ENABLED",
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_REPL_MEM",
    "LEAN_SERVER_INIT_REPLS",
    "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY",
    "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER",
    "LEAN_SERVER_ASYNC_LIGHT_RETRY_ATTEMPTS",
    "LEAN_SERVER_ASYNC_HEAVY_RETRY_ATTEMPTS",
}

API_REQUIRED = {
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_REPL_MEM",
    "LEAN_SERVER_INIT_REPLS",
}

WORKER_REQUIRED = {
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_REPL_MEM",
    "LEAN_SERVER_INIT_REPLS",
    "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY",
    "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER",
    "LEAN_SERVER_ASYNC_LIGHT_RETRY_ATTEMPTS",
    "LEAN_SERVER_ASYNC_HEAVY_RETRY_ATTEMPTS",
}


def missing_keys(required: Iterable[str], env: dict[str, str]) -> list[str]:
    return sorted([k for k in required if not env.get(k)])


def required_keys_for_role(role: str) -> set[str]:
    normalized_role = role.strip().lower()
    required = set(COMMON_REQUIRED)
    if normalized_role == "worker":
        required.update(WORKER_REQUIRED)
    elif normalized_role == "gateway":
        required.update(GATEWAY_REQUIRED)
        for runtime_id in seeded_runtime_ids():
            required.add(runtime_env_key(runtime_id, "SERVICE_ID"))
            required.add(runtime_env_key(runtime_id, "BASE_URL"))
    elif normalized_role == "runtime":
        required.update(RUNTIME_REQUIRED)
    else:
        required.update(API_REQUIRED)
    return required


def main() -> int:
    role = (sys.argv[1] if len(sys.argv) > 1 else "api").strip().lower()
    env = dict(os.environ)

    missing = missing_keys(required_keys_for_role(role), env)
    if missing:
        print("Missing required environment variables:")
        for key in missing:
            print(f"- {key}")
        return 1

    print(f"Environment looks valid for role='{role}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
