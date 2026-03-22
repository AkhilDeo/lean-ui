#!/usr/bin/env python3
"""Disable async Railway resources (worker, Redis, autoscaler) to cut costs.

Operates entirely via Railway's GraphQL API — no git push needed.
The API server keeps serving /api/check (sync verification) normally;
/api/async/* endpoints will return 503.

Re-enable with:  python railway_apply_async_rollout.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# Reuse helpers from the async rollout script (same directory).
from railway_apply_async_rollout import (
    API_SERVICE_ID,
    REDIS_SERVICE_NAME,
    WORKER_SERVICE_NAME,
    REGION,
    RailwayClient,
    get_deployment_status,
    get_services,
    load_token,
    redeploy,
    update_service_instance,
    upsert_variables,
    wait_for_success,
)


def sleep_service(client: RailwayClient, service_id: str, *, label: str) -> None:
    """Put a service to sleep (Railway requires numReplicas >= 1, so we set 1 + sleep)."""
    print(f"[{label}] Sleeping service {service_id} …")
    update_service_instance(
        client,
        service_id=service_id,
        input_payload={
            "sleepApplication": True,
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
        },
    )
    print(f"[{label}] Done.")


def disable_async_on_api(client: RailwayClient) -> None:
    """Set ASYNC_ENABLED and AUTOSCALE_ENABLED to false on the API server."""
    print("[api] Disabling async + autoscaler env vars …")
    upsert_variables(
        client,
        service_id=API_SERVICE_ID,
        variables={
            "LEAN_SERVER_ASYNC_ENABLED": "false",
            "LEAN_SERVER_AUTOSCALE_ENABLED": "false",
        },
    )
    print("[api] Redeploying API server …")
    redeploy(client, service_id=API_SERVICE_ID)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Disable async Railway resources to cut costs. Dry-run by default.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply changes. Without this flag, dry-run only.",
    )
    args = parser.parse_args()

    token = load_token()
    client = RailwayClient(token)

    # Discover services
    services = get_services(client)

    worker_id = services.get(WORKER_SERVICE_NAME)
    redis_id = services.get(REDIS_SERVICE_NAME)

    plan = {
        "worker_service": {"id": worker_id, "action": "sleep + 0 replicas"},
        "redis_service": {"id": redis_id, "action": "sleep + 0 replicas"},
        "api_service": {
            "id": API_SERVICE_ID,
            "action": "set ASYNC_ENABLED=false, AUTOSCALE_ENABLED=false, redeploy",
        },
    }

    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        print("\nDry-run only. Re-run with --execute to apply changes.")
        return 0

    # Step 1 — Sleep the worker
    if worker_id:
        sleep_service(client, worker_id, label="worker")
    else:
        print(f"[worker] Service '{WORKER_SERVICE_NAME}' not found — skipping.")

    # Step 2 — Sleep Redis
    if redis_id:
        sleep_service(client, redis_id, label="redis")
    else:
        print(f"[redis] Service '{REDIS_SERVICE_NAME}' not found — skipping.")

    # Step 3 — Disable async + autoscaler on API, redeploy
    disable_async_on_api(client)

    # Step 4 — Wait for API redeploy
    print("[api] Waiting for API redeploy to succeed …")
    wait_for_success(client, service_id=API_SERVICE_ID)

    # Summary
    api_status = get_deployment_status(client, service_id=API_SERVICE_ID)
    print("\n=== Summary ===")
    print(f"Worker ({worker_id}): sleeping, 0 replicas")
    print(f"Redis  ({redis_id}): sleeping, 0 replicas")
    print(f"API    ({API_SERVICE_ID}): redeployed, status={api_status}")
    print("  LEAN_SERVER_ASYNC_ENABLED=false")
    print("  LEAN_SERVER_AUTOSCALE_ENABLED=false")
    print("\nAsync system disabled. /api/check still works.")
    print("To re-enable: python railway_apply_async_rollout.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
