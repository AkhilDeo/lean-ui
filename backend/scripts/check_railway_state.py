#!/usr/bin/env python3
from __future__ import annotations

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


def main() -> int:
    token = load_token()
    worker_id = service_id_by_name(token, WORKER_SERVICE_NAME)

    api_state = get_state(token, API_SERVICE_ID)
    worker_state = get_state(token, worker_id)

    if api_state["serviceInstance"]["latestDeployment"]["status"] != "SUCCESS":
        raise RuntimeError("API service latest deployment is not SUCCESS")
    if worker_state["serviceInstance"]["latestDeployment"]["status"] != "SUCCESS":
        raise RuntimeError("Worker service latest deployment is not SUCCESS")

    assert_limits(api_state, cpu=2, memory_gb=10)
    assert_limits(worker_state, cpu=32, memory_gb=32)
    assert_replicas(api_state, expected=1)
    assert_replicas(worker_state, expected=3)

    if api_state["serviceInstance"]["sleepApplication"] is not True:
        raise RuntimeError("API service should have sleepApplication=true")
    if worker_state["serviceInstance"]["sleepApplication"] is not False:
        raise RuntimeError("Worker service should have sleepApplication=false")

    api_domains = api_state["serviceInstance"]["domains"]["serviceDomains"]
    if not api_domains or api_domains[0]["domain"] != "lean-ui-production.up.railway.app":
        raise RuntimeError("API public domain mismatch")

    worker_domains = worker_state["serviceInstance"]["domains"]["serviceDomains"]
    if worker_domains:
        print("warning: worker service has public domains configured")

    print("Railway state matches expected async rollout configuration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
