#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
DEFAULT_WORKER_SERVICE_NAME = "lean-ui-worker"
REGION = "us-east4-eqdc4a"
API_URL = "https://backboard.railway.com/graphql/v2"


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


def get_variables(client: RailwayClient, service_id: str) -> dict[str, str]:
    query = """
    query($pid:String!,$eid:String!,$sid:String!) {
      variables(projectId:$pid, environmentId:$eid, serviceId:$sid)
    }
    """
    data = client.gql(query, {"pid": PROJECT_ID, "eid": ENVIRONMENT_ID, "sid": service_id})
    return data["variables"]


def get_service_instance_state(client: RailwayClient, service_id: str) -> dict[str, Any]:
    query = """
    query($eid:String!,$sid:String!) {
      serviceInstance(environmentId:$eid, serviceId:$sid) {
        sleepApplication
        domains { serviceDomains { domain } }
        latestDeployment { id status meta }
      }
      limitOverride: serviceInstanceLimitOverride(environmentId:$eid, serviceId:$sid)
    }
    """
    return client.gql(query, {"eid": ENVIRONMENT_ID, "sid": service_id})


def upsert_variables(client: RailwayClient, service_id: str, variables: dict[str, str]) -> None:
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


def update_limits(client: RailwayClient, service_id: str, vcpus: float, memory_gb: float) -> None:
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


def update_replicas(client: RailwayClient, service_id: str, replicas: int) -> None:
    mutation = """
    mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!) {
      serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input)
    }
    """
    client.gql(
        mutation,
        {
            "sid": service_id,
            "eid": ENVIRONMENT_ID,
            "input": {
                "multiRegionConfig": {REGION: {"numReplicas": replicas}},
            },
        },
    )


def redeploy(client: RailwayClient, service_id: str) -> None:
    mutation = """
    mutation($sid:String!,$eid:String!) {
      serviceInstanceRedeploy(serviceId:$sid, environmentId:$eid)
    }
    """
    client.gql(mutation, {"sid": service_id, "eid": ENVIRONMENT_ID})


def get_deployment_status(client: RailwayClient, service_id: str) -> str:
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


def wait_for_success(client: RailwayClient, service_id: str, timeout_sec: int = 1200) -> None:
    started = time.time()
    while True:
        status = get_deployment_status(client, service_id)
        print(f"service={service_id} status={status}")
        if status == "SUCCESS":
            return
        if status in {"FAILED", "CRASHED"}:
            raise RuntimeError(f"Deployment failed for service {service_id}: {status}")
        if time.time() - started > timeout_sec:
            raise RuntimeError(f"Timed out waiting for deployment success: {service_id}")
        time.sleep(10)


def extract_replicas(service_state: dict[str, Any]) -> int | None:
    latest = service_state["serviceInstance"].get("latestDeployment")
    if not latest:
        return None
    meta = latest.get("meta") or {}
    deploy = (meta.get("serviceManifest") or {}).get("deploy") or {}
    region_cfg = (deploy.get("multiRegionConfig") or {}).get(REGION, {})
    value = region_cfg.get("numReplicas", deploy.get("numReplicas"))
    if value is None:
        return None
    return int(value)


def snapshot(
    *,
    client: RailwayClient,
    api_service_id: str,
    worker_service_id: str,
    worker_service_name: str,
) -> dict[str, Any]:
    api_vars = get_variables(client, api_service_id)
    worker_vars = get_variables(client, worker_service_id)
    api_state = get_service_instance_state(client, api_service_id)
    worker_state = get_service_instance_state(client, worker_service_id)

    return {
        "timestamp_epoch_s": time.time(),
        "project_id": PROJECT_ID,
        "environment_id": ENVIRONMENT_ID,
        "region": REGION,
        "services": {
            "api": {
                "id": api_service_id,
                "name": "lean-ui-production",
                "variables": api_vars,
                "state": api_state,
                "replicas": extract_replicas(api_state),
            },
            "worker": {
                "id": worker_service_id,
                "name": worker_service_name,
                "variables": worker_vars,
                "state": worker_state,
                "replicas": extract_replicas(worker_state),
            },
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Tune Lean UI production env vars/limits for load testing. Defaults to dry-run."
    )
    backend_dir = Path(__file__).resolve().parents[1]

    parser.add_argument("--worker-service-name", default=DEFAULT_WORKER_SERVICE_NAME)
    parser.add_argument("--execute", action="store_true", help="Apply mutations. Without this flag, dry-run only.")
    parser.add_argument("--apply-limits", action="store_true", help="Also apply CPU/memory/replica updates.")

    parser.add_argument("--api-max-repls", type=int, default=1)
    parser.add_argument("--api-max-repl-mem", default="8G")
    parser.add_argument("--worker-max-repls", type=int, default=6)
    parser.add_argument("--worker-max-repl-mem", default="12G")
    parser.add_argument("--min-host-free-mem", default="4G")
    parser.add_argument("--async-enabled", default="true")
    parser.add_argument("--async-metrics-enabled", default="true")
    parser.add_argument("--async-admission-queue-limit", type=int, default=0)
    parser.add_argument("--async-alert-max-oldest-queued-age-sec", type=int, default=60)
    parser.add_argument("--async-queue-name-light", default="lean_async_light")
    parser.add_argument("--async-queue-name-heavy", default="lean_async_heavy")
    parser.add_argument("--worker-async-concurrency", type=int, default=6)
    parser.add_argument("--worker-queue-tier", default="light")
    parser.add_argument(
        "--api-init-repls",
        default="{}",
    )
    parser.add_argument(
        "--worker-init-repls",
        default='{"import Mathlib":1,"import Mathlib\\nimport Aesop":1}',
    )
    parser.add_argument("--async-startup-concurrency-limit", type=int, default=2)
    parser.add_argument("--redis-url", default="")

    parser.add_argument("--api-vcpus", type=float, default=4.0)
    parser.add_argument("--api-memory-gb", type=float, default=8.0)
    parser.add_argument("--worker-vcpus", type=float, default=8.0)
    parser.add_argument("--worker-memory-gb", type=float, default=32.0)
    parser.add_argument("--worker-replicas", type=int, default=12)

    parser.add_argument(
        "--output-dir",
        default=str(backend_dir / "outputs/loadtests/verification"),
    )
    return parser


def parse_json_object(raw: str, *, arg_name: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{arg_name} must be valid JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{arg_name} must be a JSON object")
    return json.dumps(value, separators=(",", ":"))


def main() -> int:
    args = build_parser().parse_args()
    args.api_init_repls = parse_json_object(args.api_init_repls, arg_name="--api-init-repls")
    args.worker_init_repls = parse_json_object(
        args.worker_init_repls,
        arg_name="--worker-init-repls",
    )
    if args.worker_async_concurrency < 1:
        raise ValueError("--worker-async-concurrency must be >= 1")
    if args.async_admission_queue_limit < 0:
        raise ValueError("--async-admission-queue-limit must be >= 0")
    if args.async_alert_max_oldest_queued_age_sec < 0:
        raise ValueError("--async-alert-max-oldest-queued-age-sec must be >= 0")

    token = load_token()
    client = RailwayClient(token)
    services = get_services(client)
    if args.worker_service_name not in services:
        raise RuntimeError(f"Worker service not found: {args.worker_service_name}")

    worker_id = services[args.worker_service_name]

    before = snapshot(
        client=client,
        api_service_id=API_SERVICE_ID,
        worker_service_id=worker_id,
        worker_service_name=args.worker_service_name,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    before_path = output_dir / f"railway_tune_snapshot_before_{ts}.json"
    before_path.write_text(json.dumps(before, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    api_vars = before["services"]["api"]["variables"]
    worker_vars = before["services"]["worker"]["variables"]

    redis_url = args.redis_url.strip() or api_vars.get("LEAN_SERVER_REDIS_URL") or worker_vars.get("LEAN_SERVER_REDIS_URL")
    if not redis_url:
        raise RuntimeError(
            "Could not infer LEAN_SERVER_REDIS_URL from existing vars. Provide --redis-url."
        )

    common_vars = {
        "LEAN_SERVER_ASYNC_ENABLED": args.async_enabled,
        "LEAN_SERVER_ASYNC_METRICS_ENABLED": args.async_metrics_enabled,
        "LEAN_SERVER_ASYNC_ADMISSION_QUEUE_LIMIT": str(args.async_admission_queue_limit),
        "LEAN_SERVER_ASYNC_ALERT_MAX_OLDEST_QUEUED_AGE_SEC": str(
            args.async_alert_max_oldest_queued_age_sec
        ),
        "LEAN_SERVER_REDIS_URL": redis_url,
        "LEAN_SERVER_ASYNC_QUEUE_NAME_LIGHT": args.async_queue_name_light,
        "LEAN_SERVER_ASYNC_QUEUE_NAME_HEAVY": args.async_queue_name_heavy,
        "LEAN_SERVER_MIN_HOST_FREE_MEM": args.min_host_free_mem,
    }
    api_update = {
        **common_vars,
        "LEAN_SERVER_MAX_REPLS": str(args.api_max_repls),
        "LEAN_SERVER_MAX_REPL_MEM": args.api_max_repl_mem,
        "LEAN_SERVER_INIT_REPLS": args.api_init_repls,
    }
    worker_update = {
        **common_vars,
        "LEAN_SERVER_MAX_REPLS": str(args.worker_max_repls),
        "LEAN_SERVER_MAX_REPL_MEM": args.worker_max_repl_mem,
        "LEAN_SERVER_INIT_REPLS": args.worker_init_repls,
        "LEAN_SERVER_ASYNC_WORKER_CONCURRENCY": str(args.worker_async_concurrency),
        "LEAN_SERVER_ASYNC_WORKER_QUEUE_TIER": args.worker_queue_tier,
        "LEAN_SERVER_ASYNC_STARTUP_CONCURRENCY_LIMIT": str(args.async_startup_concurrency_limit),
    }

    planned = {
        "execute": args.execute,
        "apply_limits": args.apply_limits,
        "api_service_id": API_SERVICE_ID,
        "worker_service_id": worker_id,
        "api_var_updates": api_update,
        "worker_var_updates": worker_update,
        "limits": {
            "api": {"vcpus": args.api_vcpus, "memory_gb": args.api_memory_gb},
            "worker": {"vcpus": args.worker_vcpus, "memory_gb": args.worker_memory_gb},
            "worker_replicas": args.worker_replicas,
        },
        "snapshot_before": str(before_path),
    }

    if not args.execute:
        print(json.dumps(planned, indent=2, sort_keys=True))
        print("Dry-run only. Re-run with --execute to apply changes.")
        return 0

    upsert_variables(client, API_SERVICE_ID, api_update)
    upsert_variables(client, worker_id, worker_update)

    if args.apply_limits:
        update_limits(client, API_SERVICE_ID, args.api_vcpus, args.api_memory_gb)
        update_limits(client, worker_id, args.worker_vcpus, args.worker_memory_gb)
        update_replicas(client, worker_id, args.worker_replicas)

    redeploy(client, API_SERVICE_ID)
    redeploy(client, worker_id)

    wait_for_success(client, API_SERVICE_ID)
    wait_for_success(client, worker_id)

    after = snapshot(
        client=client,
        api_service_id=API_SERVICE_ID,
        worker_service_id=worker_id,
        worker_service_name=args.worker_service_name,
    )
    after_path = output_dir / f"railway_tune_snapshot_after_{ts}.json"
    after_path.write_text(json.dumps(after, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "applied": True,
                "snapshot_before": str(before_path),
                "snapshot_after": str(after_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
