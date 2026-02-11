#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ID = "0aa8564f-7dab-476a-94d9-0de8fb381c9f"
ENVIRONMENT_ID = "9ac4affd-7f62-415d-9c34-d2748db92462"
API_SERVICE_ID = "d1aa5615-5ffe-47f4-a34e-a3dfe5b348cb"
REGION = "us-east4-eqdc4a"

WORKER_SERVICE_NAME = "lean-ui-worker"
REDIS_SERVICE_NAME = "lean-ui-redis"
REDIS_TEMPLATE_ID = "895cb7c9-8ea9-4407-b4b6-b5013a65145e"
REPO = "AkhilDeo/lean-ui"
API_URL = "https://backboard.railway.com/graphql/v2"


def load_token() -> str:
    token = os.getenv("RAILWAY_TOKEN") or os.getenv("RAILWAY_API_TOKEN")
    if token:
        return token

    cfg = Path.home() / ".railway" / "config.json"
    if not cfg.exists():
        raise RuntimeError("No Railway token found. Set RAILWAY_TOKEN.")
    data = json.loads(cfg.read_text())
    token = data.get("user", {}).get("token")
    if not token:
        raise RuntimeError("No Railway token found in ~/.railway/config.json.")
    return token


class RailwayClient:
    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        response = requests.post(API_URL, headers=self._headers, json=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"GraphQL error: {body['errors']}")
        return body["data"]


def get_services(client: RailwayClient) -> dict[str, str]:
    query = """
    query($pid:String!) {
      project(id:$pid) {
        services { edges { node { id name } } }
      }
    }
    """
    data = client.gql(query, {"pid": PROJECT_ID})
    out: dict[str, str] = {}
    for edge in data["project"]["services"]["edges"]:
        node = edge["node"]
        out[node["name"]] = node["id"]
    return out


def create_worker_if_missing(client: RailwayClient, services: dict[str, str]) -> str:
    if WORKER_SERVICE_NAME in services:
        return services[WORKER_SERVICE_NAME]

    mutation = """
    mutation($input:ServiceCreateInput!) {
      serviceCreate(input:$input) { id name }
    }
    """
    data = client.gql(
        mutation,
        {
            "input": {
                "projectId": PROJECT_ID,
                "environmentId": ENVIRONMENT_ID,
                "name": WORKER_SERVICE_NAME,
                "source": {"repo": REPO},
                "branch": "main",
            }
        },
    )
    return data["serviceCreate"]["id"]


def create_redis_if_missing(client: RailwayClient, services: dict[str, str]) -> str:
    if REDIS_SERVICE_NAME in services:
        return services[REDIS_SERVICE_NAME]

    mutation = """
    mutation($input:ServiceCreateInput!) {
      serviceCreate(input:$input) { id name }
    }
    """
    try:
        data = client.gql(
            mutation,
            {
                "input": {
                    "projectId": PROJECT_ID,
                    "environmentId": ENVIRONMENT_ID,
                    "name": REDIS_SERVICE_NAME,
                    "templateId": REDIS_TEMPLATE_ID,
                }
            },
        )
    except RuntimeError:
        # Some API versions require templateServiceId. Fall back to a direct image service.
        data = client.gql(
            mutation,
            {
                "input": {
                    "projectId": PROJECT_ID,
                    "environmentId": ENVIRONMENT_ID,
                    "name": REDIS_SERVICE_NAME,
                    "source": {"image": "redis:7"},
                }
            },
        )
    return data["serviceCreate"]["id"]


def update_service_instance(
    client: RailwayClient,
    *,
    service_id: str,
    input_payload: dict[str, Any],
) -> None:
    mutation = """
    mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!) {
      serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input)
    }
    """
    client.gql(
        mutation,
        {"sid": service_id, "eid": ENVIRONMENT_ID, "input": input_payload},
    )


def update_limits(
    client: RailwayClient,
    *,
    service_id: str,
    vcpus: float,
    memory_gb: float,
) -> None:
    mutation = """
    mutation($input:ServiceInstanceLimitsUpdateInput!) {
      serviceInstanceLimitsUpdate(input:$input)
    }
    """
    client.gql(
        mutation,
        {
            "input": {
                "serviceId": service_id,
                "environmentId": ENVIRONMENT_ID,
                "vCPUs": vcpus,
                "memoryGB": memory_gb,
            }
        },
    )


def get_service_variables(client: RailwayClient, *, service_id: str) -> dict[str, str]:
    query = """
    query($pid:String!,$eid:String!,$sid:String!) {
      variables(projectId:$pid, environmentId:$eid, serviceId:$sid)
    }
    """
    data = client.gql(
        query,
        {"pid": PROJECT_ID, "eid": ENVIRONMENT_ID, "sid": service_id},
    )
    return data["variables"]


def upsert_variables(
    client: RailwayClient,
    *,
    service_id: str,
    variables: dict[str, str],
) -> None:
    mutation = """
    mutation($input:VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input:$input)
    }
    """
    client.gql(
        mutation,
        {
            "input": {
                "projectId": PROJECT_ID,
                "environmentId": ENVIRONMENT_ID,
                "serviceId": service_id,
                "skipDeploys": True,
                "replace": False,
                "variables": variables,
            }
        },
    )


def redeploy(client: RailwayClient, *, service_id: str) -> None:
    mutation = """
    mutation($sid:String!,$eid:String!) {
      serviceInstanceRedeploy(serviceId:$sid, environmentId:$eid)
    }
    """
    client.gql(mutation, {"sid": service_id, "eid": ENVIRONMENT_ID})


def get_deployment_status(client: RailwayClient, *, service_id: str) -> str:
    query = """
    query($eid:String!,$sid:String!) {
      serviceInstance(environmentId:$eid, serviceId:$sid) {
        latestDeployment { status }
      }
    }
    """
    data = client.gql(query, {"eid": ENVIRONMENT_ID, "sid": service_id})
    latest = data["serviceInstance"]["latestDeployment"]
    if latest is None:
        return "UNKNOWN"
    return latest["status"]


def wait_for_success(client: RailwayClient, *, service_id: str, timeout_sec: int = 1200) -> None:
    started = time.time()
    while True:
        status = get_deployment_status(client, service_id=service_id)
        print(f"service={service_id} status={status}")
        if status == "SUCCESS":
            return
        if status in {"FAILED", "CRASHED"}:
            raise RuntimeError(f"Deployment failed for service {service_id}: {status}")
        if time.time() - started > timeout_sec:
            raise RuntimeError(f"Timed out waiting for deployment success: {service_id}")
        time.sleep(10)


def get_public_domain(client: RailwayClient, *, service_id: str) -> str | None:
    query = """
    query($eid:String!,$sid:String!) {
      serviceInstance(environmentId:$eid, serviceId:$sid) {
        domains { serviceDomains { domain } }
      }
    }
    """
    data = client.gql(query, {"eid": ENVIRONMENT_ID, "sid": service_id})
    domains = data["serviceInstance"]["domains"]["serviceDomains"]
    if not domains:
        return None
    return domains[0]["domain"]


def choose_redis_url(redis_vars: dict[str, str]) -> str:
    for key in ("REDIS_URL", "REDIS_PRIVATE_URL", "REDIS_URI"):
        value = redis_vars.get(key)
        if value:
            return value
    private_domain = redis_vars.get("RAILWAY_PRIVATE_DOMAIN")
    if private_domain:
        return f"redis://{private_domain}:6379/0"
    raise RuntimeError(
        "Could not find REDIS_URL/REDIS_PRIVATE_URL/REDIS_URI on lean-ui-redis service"
    )


def main() -> int:
    token = load_token()
    client = RailwayClient(token)

    services = get_services(client)
    worker_id = create_worker_if_missing(client, services)
    services = get_services(client)
    redis_id = create_redis_if_missing(client, services)
    services = get_services(client)
    worker_id = services[WORKER_SERVICE_NAME]
    redis_id = services[REDIS_SERVICE_NAME]

    update_service_instance(
        client,
        service_id=worker_id,
        input_payload={
            "rootDirectory": "/backend",
            "railwayConfigFile": "/backend/railway.worker.toml",
            "startCommand": "python -m server.worker",
            "sleepApplication": False,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 3,
            "multiRegionConfig": {REGION: {"numReplicas": 3}},
        },
    )
    update_limits(client, service_id=worker_id, vcpus=32, memory_gb=32)

    update_service_instance(
        client,
        service_id=API_SERVICE_ID,
        input_payload={
            "sleepApplication": True,
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
        },
    )
    update_limits(client, service_id=API_SERVICE_ID, vcpus=2, memory_gb=10)

    redis_vars = get_service_variables(client, service_id=redis_id)
    redis_url = choose_redis_url(redis_vars)

    common = {
        "LEAN_SERVER_ENVIRONMENT": "prod",
        "LEAN_SERVER_ASYNC_ENABLED": "true",
        "LEAN_SERVER_REDIS_URL": redis_url,
        "LEAN_SERVER_ASYNC_RESULT_TTL_SEC": "86400",
        "LEAN_SERVER_ASYNC_QUEUE_NAME": "lean_async_check",
        "LEAN_SERVER_ASYNC_BACKLOG_LIMIT": "50000",
        "LEAN_SERVER_ASYNC_MAX_QUEUE_WAIT_SEC": "600",
        "LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND": "false",
        "LEAN_SERVER_MIN_HOST_FREE_MEM": "4G",
    }

    api_vars = {
        **common,
        "LEAN_SERVER_MAX_REPLS": "1",
        "LEAN_SERVER_MAX_REPL_MEM": "10G",
        "LEAN_SERVER_INIT_REPLS": "{}",
    }
    worker_vars = {
        **common,
        "LEAN_SERVER_MAX_REPLS": "3",
        "LEAN_SERVER_MAX_REPL_MEM": "10G",
        "LEAN_SERVER_INIT_REPLS": '{"import Mathlib": 2}',
    }

    upsert_variables(client, service_id=API_SERVICE_ID, variables=api_vars)
    upsert_variables(client, service_id=worker_id, variables=worker_vars)

    redeploy(client, service_id=API_SERVICE_ID)
    redeploy(client, service_id=worker_id)

    wait_for_success(client, service_id=API_SERVICE_ID)
    wait_for_success(client, service_id=worker_id)

    api_domain = get_public_domain(client, service_id=API_SERVICE_ID)
    worker_domain = get_public_domain(client, service_id=worker_id)
    print(f"api_domain={api_domain}")
    print(f"worker_domain={worker_domain}")
    if api_domain != "lean-ui-production.up.railway.app":
        raise RuntimeError(
            "Unexpected API domain after rollout. "
            f"Expected lean-ui-production.up.railway.app, got {api_domain}"
        )
    if worker_domain is not None:
        print("warning: worker has a public domain configured")

    print("Railway async rollout completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
