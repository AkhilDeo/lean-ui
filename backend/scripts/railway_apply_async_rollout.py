#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from server.runtime_registry import runtime_env_key, seeded_runtime_ids

PROJECT_ID = "0aa8564f-7dab-476a-94d9-0de8fb381c9f"
ENVIRONMENT_ID = "9ac4affd-7f62-415d-9c34-d2748db92462"
GATEWAY_SERVICE_ID = "d1aa5615-5ffe-47f4-a34e-a3dfe5b348cb"
GATEWAY_SERVICE_NAME = "lean-ui"
REGION = "us-east4-eqdc4a"
REPO = "AkhilDeo/lean-ui"
REDIS_SERVICE_NAME = "lean-ui-redis"
REDIS_TEMPLATE_ID = "895cb7c9-8ea9-4407-b4b6-b5013a65145e"
DEFAULT_RUNTIME_ID = "v4.15.0"
API_URL = "https://backboard.railway.com/graphql/v2"

RUNTIME_SERVICE_NAMES = {
    "v4.9.0": "lean-ui-v490",
    "v4.15.0": "lean-ui-v4150",
}


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
    return {
        edge["node"]["name"]: edge["node"]["id"]
        for edge in data["project"]["services"]["edges"]
    }


def create_service_if_missing(client: RailwayClient, services: dict[str, str], name: str) -> str:
    if name in services:
        return services[name]

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
                "name": name,
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


def wait_for_success(client: RailwayClient, *, service_id: str, timeout_sec: int = 1800) -> None:
    started = time.time()
    while True:
        status = get_deployment_status(client, service_id=service_id)
        print(f"service={service_id} status={status}")
        if status == "SUCCESS":
            return
        if status in {"FAILED", "CRASHED", "REMOVED"}:
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
    if REDIS_SERVICE_NAME:
        return f"redis://{REDIS_SERVICE_NAME}.railway.internal:6379/0"
    raise RuntimeError(
        "Could not find REDIS_URL/REDIS_PRIVATE_URL/REDIS_URI on lean-ui-redis service"
    )


def build_common_async_vars(*, redis_url: str, api_key: str) -> dict[str, str]:
    return {
        "LEAN_SERVER_ENVIRONMENT": "prod",
        "LEAN_SERVER_ASYNC_ENABLED": "true",
        "LEAN_SERVER_ASYNC_METRICS_ENABLED": "true",
        "LEAN_SERVER_ASYNC_ADMISSION_QUEUE_LIMIT": "0",
        "LEAN_SERVER_ASYNC_ALERT_MAX_OLDEST_QUEUED_AGE_SEC": "60",
        "LEAN_SERVER_REDIS_URL": redis_url,
        "LEAN_SERVER_API_KEY": api_key,
        "LEAN_SERVER_ASYNC_RESULT_TTL_SEC": "86400",
        "LEAN_SERVER_ASYNC_QUEUE_NAME_LIGHT": "lean_async_light",
        "LEAN_SERVER_ASYNC_QUEUE_NAME_HEAVY": "lean_async_heavy",
        "LEAN_SERVER_ASYNC_BACKLOG_LIMIT": "100000",
        "LEAN_SERVER_ASYNC_MAX_QUEUE_WAIT_SEC": "600",
        "LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND": "false",
        "LEAN_SERVER_REQUEST_TIMEOUT_MAX_SEC": "300",
        "LEAN_SERVER_MAX_WAIT": "300",
        "LEAN_SERVER_MIN_HOST_FREE_MEM": "4G",
    }


def configure_gateway_service(
    client: RailwayClient,
    *,
    runtime_service_urls: dict[str, str],
    runtime_service_ids: dict[str, str],
    redis_url: str,
    api_key: str,
) -> None:
    update_service_instance(
        client,
        service_id=GATEWAY_SERVICE_ID,
        input_payload={
            "rootDirectory": "/backend",
            "railwayConfigFile": "/backend/railway.toml",
            "startCommand": "python -m server",
            "sleepApplication": False,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 3,
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
        },
    )
    update_limits(client, service_id=GATEWAY_SERVICE_ID, vcpus=4, memory_gb=8)

    gateway_vars = build_common_async_vars(redis_url=redis_url, api_key=api_key)
    gateway_vars.update(
        {
            "LEAN_SERVER_GATEWAY_ENABLED": "true",
            "LEAN_SERVER_EMBEDDED_WORKER_ENABLED": "false",
            "LEAN_SERVER_DEFAULT_RUNTIME_ID": DEFAULT_RUNTIME_ID,
            "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "LEAN_SERVER_GATEWAY_SYNC_PROXY_TIMEOUT_SEC": "300",
        }
    )
    for runtime_id in seeded_runtime_ids():
        gateway_vars[runtime_env_key(runtime_id, "SERVICE_ID")] = runtime_service_ids[runtime_id]
        gateway_vars[runtime_env_key(runtime_id, "BASE_URL")] = runtime_service_urls[runtime_id]

    upsert_variables(client, service_id=GATEWAY_SERVICE_ID, variables=gateway_vars)


def configure_runtime_service(
    client: RailwayClient,
    *,
    runtime_id: str,
    service_id: str,
    redis_url: str,
    api_key: str,
) -> None:
    update_service_instance(
        client,
        service_id=service_id,
        input_payload={
            "rootDirectory": "/backend",
            "railwayConfigFile": "/backend/railway.toml",
            "startCommand": "python -m server",
            "sleepApplication": False,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 3,
            "multiRegionConfig": {REGION: {"numReplicas": 1}},
        },
    )
    update_limits(client, service_id=service_id, vcpus=4, memory_gb=8)

    runtime_vars = build_common_async_vars(redis_url=redis_url, api_key=api_key)
    runtime_vars.update(
        {
            "LEAN_SERVER_GATEWAY_ENABLED": "false",
            "LEAN_SERVER_EMBEDDED_WORKER_ENABLED": "true",
            "LEAN_SERVER_DEFAULT_RUNTIME_ID": DEFAULT_RUNTIME_ID,
            "LEAN_SERVER_RUNTIME_ID": runtime_id,
            "LEAN_SERVER_LEAN_VERSION": runtime_id,
            "LEAN_SERVER_RUNTIME_SERVICE_ID": service_id,
            "LEAN_SERVER_RAILWAY_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "LEAN_SERVER_MAX_REPLS": "1",
            "LEAN_SERVER_MAX_REPL_MEM": "8G",
            "LEAN_SERVER_INIT_REPLS": "{}",
            "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY": "1",
            "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER": "all",
            "LEAN_SERVER_ASYNC_LIGHT_RETRY_ATTEMPTS": "5",
            "LEAN_SERVER_ASYNC_HEAVY_RETRY_ATTEMPTS": "7",
        }
    )
    upsert_variables(client, service_id=service_id, variables=runtime_vars)


def main() -> int:
    token = load_token()
    client = RailwayClient(token)

    services = get_services(client)
    redis_id = create_redis_if_missing(client, services)

    runtime_service_ids: dict[str, str] = {}
    services = get_services(client)
    for runtime_id in seeded_runtime_ids():
        name = RUNTIME_SERVICE_NAMES[runtime_id]
        runtime_service_ids[runtime_id] = create_service_if_missing(client, services, name)
        services = get_services(client)

    gateway_vars = get_service_variables(client, service_id=GATEWAY_SERVICE_ID)
    api_key = gateway_vars.get("LEAN_SERVER_API_KEY")
    if not api_key:
        raise RuntimeError("Gateway service is missing LEAN_SERVER_API_KEY")

    redis_vars = get_service_variables(client, service_id=redis_id)
    redis_url = choose_redis_url(redis_vars)

    for runtime_id in seeded_runtime_ids():
        configure_runtime_service(
            client,
            runtime_id=runtime_id,
            service_id=runtime_service_ids[runtime_id],
            redis_url=redis_url,
            api_key=api_key,
        )

    for runtime_id in seeded_runtime_ids():
        redeploy(client, service_id=runtime_service_ids[runtime_id])

    for runtime_id in seeded_runtime_ids():
        wait_for_success(client, service_id=runtime_service_ids[runtime_id])

    runtime_service_urls: dict[str, str] = {}
    for runtime_id in seeded_runtime_ids():
        domain = get_public_domain(client, service_id=runtime_service_ids[runtime_id])
        if not domain:
            raise RuntimeError(f"Runtime service {runtime_id} has no public domain")
        runtime_service_urls[runtime_id] = f"https://{domain}"

    configure_gateway_service(
        client,
        runtime_service_urls=runtime_service_urls,
        runtime_service_ids=runtime_service_ids,
        redis_url=redis_url,
        api_key=api_key,
    )
    redeploy(client, service_id=GATEWAY_SERVICE_ID)
    wait_for_success(client, service_id=GATEWAY_SERVICE_ID)

    print(f"gateway_domain={get_public_domain(client, service_id=GATEWAY_SERVICE_ID)}")
    print(f"redis_service_id={redis_id}")
    for runtime_id in seeded_runtime_ids():
        print(
            f"runtime={runtime_id} service_id={runtime_service_ids[runtime_id]} "
            f"base_url={runtime_service_urls[runtime_id]}"
        )
    print("Railway async rollout completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
