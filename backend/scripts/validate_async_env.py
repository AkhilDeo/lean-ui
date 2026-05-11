#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from server.runtime_registry import seeded_runtime_ids  # noqa: E402

COMMON_REQUIRED = {
    "LEAN_SERVER_ENVIRONMENT",
    "LEAN_SERVER_ASYNC_ENABLED",
    "LEAN_SERVER_ASYNC_METRICS_ENABLED",
    "LEAN_SERVER_ASYNC_ADMISSION_QUEUE_LIMIT",
    "LEAN_SERVER_ASYNC_ALERT_MAX_OLDEST_QUEUED_AGE_SEC",
    "LEAN_SERVER_ASYNC_RESULT_TTL_SEC",
    "LEAN_SERVER_ASYNC_QUEUE_NAME_LIGHT",
    "LEAN_SERVER_ASYNC_QUEUE_NAME_HEAVY",
    "LEAN_SERVER_ASYNC_BACKLOG_LIMIT",
    "LEAN_SERVER_ASYNC_MAX_QUEUE_WAIT_SEC",
    "LEAN_SERVER_AUTOSCALE_RAILWAY_TOKEN",
}

GATEWAY_REQUIRED = {
    "LEAN_SERVER_GATEWAY_ENABLED",
    "LEAN_SERVER_DEFAULT_RUNTIME_ID",
    "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID",
}

SINGLE_SERVICE_REQUIRED = {
    "LEAN_SERVER_GATEWAY_ENABLED",
    "LEAN_SERVER_MULTI_RUNTIME_ENABLED",
    "LEAN_SERVER_EMBEDDED_WORKER_ENABLED",
    "LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND",
    "LEAN_SERVER_DEFAULT_RUNTIME_ID",
    "LEAN_SERVER_RUNTIME_ID",
    "LEAN_SERVER_RUNTIME_IDS",
    "LEAN_SERVER_RUNTIME_ROOT",
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_TOTAL_REPLS",
    "LEAN_SERVER_MAX_REPL_MEM",
    "LEAN_SERVER_INIT_REPLS",
    "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY",
    "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER",
}

RUNTIME_REQUIRED = {
    "LEAN_SERVER_RUNTIME_ID",
    "LEAN_SERVER_RUNTIME_SERVICE_ID",
    "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID",
    "LEAN_SERVER_EMBEDDED_WORKER_ENABLED",
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_TOTAL_REPLS",
    "LEAN_SERVER_MAX_REPL_MEM",
    "LEAN_SERVER_INIT_REPLS",
    "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY",
    "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER",
    "LEAN_SERVER_ASYNC_LIGHT_RETRY_ATTEMPTS",
    "LEAN_SERVER_ASYNC_HEAVY_RETRY_ATTEMPTS",
}

API_REQUIRED = {
    "LEAN_SERVER_MAX_REPLS",
    "LEAN_SERVER_MAX_TOTAL_REPLS",
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
    elif normalized_role == "runtime":
        required.update(RUNTIME_REQUIRED)
    elif normalized_role in {"single", "single-service", "api"}:
        required.update(SINGLE_SERVICE_REQUIRED)
        for _runtime_id in seeded_runtime_ids():
            required.add("LEAN_SERVER_RUNTIME_IDS")
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
