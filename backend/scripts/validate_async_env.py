#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from typing import Iterable

COMMON_REQUIRED = {
    "LEAN_SERVER_ENVIRONMENT",
    "LEAN_SERVER_ASYNC_ENABLED",
    "LEAN_SERVER_REDIS_URL",
    "LEAN_SERVER_ASYNC_RESULT_TTL_SEC",
    "LEAN_SERVER_ASYNC_QUEUE_NAME",
    "LEAN_SERVER_ASYNC_BACKLOG_LIMIT",
    "LEAN_SERVER_ASYNC_MAX_QUEUE_WAIT_SEC",
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
}


def missing_keys(required: Iterable[str], env: dict[str, str]) -> list[str]:
    return sorted([k for k in required if not env.get(k)])


def main() -> int:
    role = (sys.argv[1] if len(sys.argv) > 1 else "api").strip().lower()
    env = dict(os.environ)

    required = set(COMMON_REQUIRED)
    if role == "worker":
        required.update(WORKER_REQUIRED)
    else:
        required.update(API_REQUIRED)

    missing = missing_keys(required, env)
    if missing:
        print("Missing required environment variables:")
        for key in missing:
            print(f"- {key}")
        return 1

    print(f"Environment looks valid for role='{role}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
