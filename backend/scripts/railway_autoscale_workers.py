#!/usr/bin/env python3
"""Auto-scale Railway worker replicas based on async queue depth.

Polls the /api/async/metrics endpoint. When tasks are queued or running,
scales workers to --max-replicas. When the queue has been idle for
--cooldown-minutes, scales back to --min-replicas.

Usage:
    # One-shot check (e.g. from cron every 2 minutes):
    python backend/scripts/railway_autoscale_workers.py

    # Continuous loop:
    python backend/scripts/railway_autoscale_workers.py --loop --interval 120
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

from railway_tune_prod_capacity import (
    API_URL,
    ENVIRONMENT_ID,
    REGION,
    RailwayClient,
    extract_replicas,
    get_service_instance_state,
    load_token,
    update_replicas,
)

STATE_FILE = Path(__file__).parent / ".autoscale_state.json"


def get_queue_activity(base_url: str, api_key: str) -> dict[str, int]:
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(f"{base_url}/api/async/metrics", headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        "queue_depth": data.get("queue_depth", 0),
        "running_tasks": data.get("running_tasks", 0),
        "inflight_jobs": data.get("inflight_jobs", 0),
    }


def current_replicas(client: RailwayClient, service_id: str) -> int:
    state = get_service_instance_state(client, service_id)
    return extract_replicas(state) or 1


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def autoscale_once(
    *,
    client: RailwayClient,
    worker_id: str,
    base_url: str,
    api_key: str,
    min_replicas: int,
    max_replicas: int,
    cooldown_sec: int,
) -> str:
    activity = get_queue_activity(base_url, api_key)
    is_active = (
        activity["queue_depth"] > 0
        or activity["running_tasks"] > 0
        or activity["inflight_jobs"] > 0
    )

    state = load_state()
    now = time.time()
    replicas = current_replicas(client, worker_id)

    if is_active:
        state["last_active_at"] = now
        save_state(state)
        if replicas < max_replicas:
            update_replicas(client, worker_id, max_replicas)
            return f"SCALED UP: {replicas} -> {max_replicas} (queue_depth={activity['queue_depth']} running={activity['running_tasks']})"
        return f"ACTIVE: {replicas} replicas (queue_depth={activity['queue_depth']} running={activity['running_tasks']})"

    # Queue is empty
    last_active = state.get("last_active_at", 0)
    idle_sec = now - last_active if last_active else float("inf")

    if replicas > min_replicas and idle_sec >= cooldown_sec:
        update_replicas(client, worker_id, min_replicas)
        return f"SCALED DOWN: {replicas} -> {min_replicas} (idle {idle_sec:.0f}s >= {cooldown_sec}s cooldown)"

    if replicas > min_replicas:
        remaining = cooldown_sec - idle_sec
        return f"IDLE: {replicas} replicas, scale-down in {remaining:.0f}s"

    return f"IDLE: {replicas} replicas (already at min)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-scale Railway workers based on queue depth.")
    parser.add_argument("--base-url", default="https://lean-ui-production.up.railway.app")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--worker-service-id", default="80159ca4-ee4e-4023-92c8-bbaf89c5ea04")
    parser.add_argument("--min-replicas", type=int, default=1)
    parser.add_argument("--max-replicas", type=int, default=12)
    parser.add_argument("--cooldown-minutes", type=int, default=10)
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of one-shot.")
    parser.add_argument("--interval", type=int, default=120, help="Seconds between checks in loop mode.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = load_token()
    client = RailwayClient(token)

    api_key = args.api_key
    if not api_key:
        from check_railway_state import get_variables, API_SERVICE_ID
        api_vars = get_variables(token, API_SERVICE_ID)
        api_key = api_vars.get("LEAN_SERVER_API_KEY", "")
    if not api_key:
        print("ERROR: No API key found. Pass --api-key or set on Railway.", file=sys.stderr)
        return 1

    cooldown_sec = args.cooldown_minutes * 60

    if not args.loop:
        result = autoscale_once(
            client=client,
            worker_id=args.worker_service_id,
            base_url=args.base_url,
            api_key=api_key,
            min_replicas=args.min_replicas,
            max_replicas=args.max_replicas,
            cooldown_sec=cooldown_sec,
        )
        print(result)
        return 0

    print(f"Autoscaler loop: min={args.min_replicas} max={args.max_replicas} cooldown={args.cooldown_minutes}m interval={args.interval}s")
    while True:
        try:
            result = autoscale_once(
                client=client,
                worker_id=args.worker_service_id,
                base_url=args.base_url,
                api_key=api_key,
                min_replicas=args.min_replicas,
                max_replicas=args.max_replicas,
                cooldown_sec=cooldown_sec,
            )
            print(f"[{time.strftime('%H:%M:%S')}] {result}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] ERROR: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
