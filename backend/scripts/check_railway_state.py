#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

PROJECT_ID = "0aa8564f-7dab-476a-94d9-0de8fb381c9f"
ENVIRONMENT_ID = "9ac4affd-7f62-415d-9c34-d2748db92462"
REGION = "us-east4-eqdc4a"
API_SERVICE_ID = "d1aa5615-5ffe-47f4-a34e-a3dfe5b348cb"
WORKER_SERVICE_NAME = "lean-ui-worker"
REDIS_SERVICE_NAME = "lean-ui-redis"
API_URL = "https://backboard.railway.com/graphql/v2"


def load_token() -> str:
    token = os.getenv("RAILWAY_TOKEN") or os.getenv("RAILWAY_API_TOKEN")
    if token:
        return token
    cfg = Path.home() / ".railway" / "config.json"
    if not cfg.exists():
        raise RuntimeError("No Railway token found.")
    data = json.loads(cfg.read_text())
    token = data.get("user", {}).get("token")
    if not token:
        raise RuntimeError("No Railway token found in ~/.railway/config.json.")
    return token


def gql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL error: {body['errors']}")
    return body["data"]


def service_id_by_name(token: str, name: str) -> str:
    query = """
    query($pid:String!) {
      project(id:$pid) {
        services { edges { node { id name } } }
      }
    }
    """
    data = gql(token, query, {"pid": PROJECT_ID})
    for edge in data["project"]["services"]["edges"]:
        node = edge["node"]
        if node["name"] == name:
            return node["id"]
    raise RuntimeError(f"Service not found: {name}")


def get_state(token: str, service_id: str) -> dict[str, Any]:
    query = """
    query($eid:String!,$sid:String!) {
      serviceInstance(environmentId:$eid, serviceId:$sid) {
        sleepApplication
        domains { serviceDomains { domain } }
        latestDeployment { status meta }
      }
      limitOverride: serviceInstanceLimitOverride(environmentId:$eid, serviceId:$sid)
    }
    """
    return gql(token, query, {"eid": ENVIRONMENT_ID, "sid": service_id})


def get_variables(token: str, service_id: str) -> dict[str, str]:
    query = """
    query($pid:String!,$eid:String!,$sid:String!) {
      variables(projectId:$pid, environmentId:$eid, serviceId:$sid)
    }
    """
    data = gql(
        token,
        query,
        {"pid": PROJECT_ID, "eid": ENVIRONMENT_ID, "sid": service_id},
    )
    return data["variables"]


def assert_limits(state: dict[str, Any], *, cpu: int, memory_gb: int) -> None:
    override = state["limitOverride"]["containers"]
    if int(override["cpu"]) != cpu:
        raise RuntimeError(f"Expected cpu={cpu}, got {override['cpu']}")
    actual_mem_gb = int(override["memoryBytes"]) // 1_000_000_000
    if actual_mem_gb != memory_gb:
        raise RuntimeError(f"Expected memory={memory_gb}GB, got {actual_mem_gb}GB")


def assert_replicas(state: dict[str, Any], *, expected: int) -> None:
    meta = state["serviceInstance"]["latestDeployment"]["meta"]
    deploy = meta["serviceManifest"]["deploy"]
    cfg = deploy.get("multiRegionConfig") or {}
    actual = int(cfg.get(REGION, {}).get("numReplicas", deploy.get("numReplicas", 0)))
    if actual != expected:
        raise RuntimeError(f"Expected replicas={expected} for {REGION}, got {actual}")


def assert_single_service_vars(api_vars: dict[str, str]) -> None:
    expected = {
        "LEAN_SERVER_GATEWAY_ENABLED": "false",
        "LEAN_SERVER_MULTI_RUNTIME_ENABLED": "true",
        "LEAN_SERVER_EMBEDDED_WORKER_ENABLED": "false",
        "LEAN_SERVER_ASYNC_ENABLED": "true",
        "LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND": "false",
    }
    for key, value in expected.items():
        actual = api_vars.get(key, "").strip().lower()
        if actual != value:
            raise RuntimeError(f"{key} expected {value}, got {actual or '<missing>'}")
    if not api_vars.get("LEAN_SERVER_REDIS_URL"):
        raise RuntimeError("LEAN_SERVER_REDIS_URL expected for Redis-backed async")
    if not api_vars.get("LEAN_SERVER_MAX_TOTAL_REPLS"):
        raise RuntimeError("LEAN_SERVER_MAX_TOTAL_REPLS expected for bounded multi-runtime capacity")


def assert_worker_vars(worker_vars: dict[str, str]) -> None:
    expected = {
        "LEAN_SERVER_GATEWAY_ENABLED": "false",
        "LEAN_SERVER_MULTI_RUNTIME_ENABLED": "true",
        "LEAN_SERVER_ASYNC_ENABLED": "true",
        "LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND": "false",
        "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER": "all",
    }
    for key, value in expected.items():
        actual = worker_vars.get(key, "").strip().lower()
        if actual != value:
            raise RuntimeError(f"worker {key} expected {value}, got {actual or '<missing>'}")
    if not worker_vars.get("LEAN_SERVER_REDIS_URL"):
        raise RuntimeError("worker LEAN_SERVER_REDIS_URL expected")
    if not worker_vars.get("LEAN_SERVER_MAX_TOTAL_REPLS"):
        raise RuntimeError("worker LEAN_SERVER_MAX_TOTAL_REPLS expected")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Railway production state for the single sleeping service."
    )
    parser.add_argument("--api-cpu", type=int, default=4)
    parser.add_argument("--api-memory-gb", type=int, default=8)
    parser.add_argument("--api-replicas", type=int, default=1)
    parser.add_argument("--worker-memory-gb", type=int, default=32)
    parser.add_argument("--worker-replicas", type=int, default=1)
    parser.add_argument("--api-sleep", choices=["any", "true", "false"], default="true")
    parser.add_argument("--skip-domain-check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = load_token()

    api_state = get_state(token, API_SERVICE_ID)
    api_vars = get_variables(token, API_SERVICE_ID)
    worker_id = service_id_by_name(token, WORKER_SERVICE_NAME)
    redis_id = service_id_by_name(token, REDIS_SERVICE_NAME)
    worker_state = get_state(token, worker_id)
    worker_vars = get_variables(token, worker_id)

    if api_state["serviceInstance"]["latestDeployment"]["status"] != "SUCCESS":
        raise RuntimeError("API service latest deployment is not SUCCESS")

    assert_limits(api_state, cpu=args.api_cpu, memory_gb=args.api_memory_gb)
    assert_replicas(api_state, expected=args.api_replicas)
    assert_single_service_vars(api_vars)
    assert_limits(worker_state, cpu=4, memory_gb=args.worker_memory_gb)
    assert_replicas(worker_state, expected=args.worker_replicas)
    assert_worker_vars(worker_vars)
    redis_state = get_state(token, redis_id)
    if redis_state["serviceInstance"]["latestDeployment"]["status"] != "SUCCESS":
        raise RuntimeError("Redis service latest deployment is not SUCCESS")

    api_sleep = bool(api_state["serviceInstance"]["sleepApplication"])
    if args.api_sleep != "any" and api_sleep is not (args.api_sleep == "true"):
        raise RuntimeError(
            f"API sleepApplication expected {args.api_sleep}, got {str(api_sleep).lower()}"
        )

    if not args.skip_domain_check:
        api_domains = api_state["serviceInstance"]["domains"]["serviceDomains"]
        if not api_domains or api_domains[0]["domain"] != "lean-ui-production.up.railway.app":
            raise RuntimeError("API public domain mismatch")

    print("Railway state matches expected Redis-backed worker configuration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
