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
REGION = "us-east4-eqdc4a"
API_URL = "https://backboard.railway.com/graphql/v2"
FORMAL_CONJECTURES_REF = "c18e2336abba12b96b75c0ea4a894342a64037bb"
REPO = "AkhilDeo/lean-ui"

DEFAULT_GATEWAY_SERVICE = "lean-ui"
DEFAULT_ENV_SERVICE_IDS = {
    "mathlib-v4.15": "lean-ui",
    "mathlib-v4.27": "lean-ui-mathlib427",
    "formal-conjectures-v4.27": "lean-ui-formalconjectures427",
}


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
    return {
        edge["node"]["name"]: edge["node"]["id"]
        for edge in data["project"]["services"]["edges"]
    }


def get_variables(client: RailwayClient, service_id: str) -> dict[str, str]:
    query = """
    query($pid:String!,$eid:String!,$sid:String!) {
      variables(projectId:$pid, environmentId:$eid, serviceId:$sid)
    }
    """
    data = client.gql(query, {"pid": PROJECT_ID, "eid": ENVIRONMENT_ID, "sid": service_id})
    return data["variables"]


def get_service_state(client: RailwayClient, service_id: str) -> dict[str, Any]:
    query = """
    query($eid:String!,$sid:String!) {
      serviceInstance(environmentId:$eid, serviceId:$sid) {
        domains { serviceDomains { domain } }
        latestDeployment { status }
      }
    }
    """
    return client.gql(query, {"eid": ENVIRONMENT_ID, "sid": service_id})["serviceInstance"]


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


def redeploy(client: RailwayClient, service_id: str) -> None:
    mutation = """
    mutation($sid:String!,$eid:String!) {
      serviceInstanceRedeploy(serviceId:$sid, environmentId:$eid)
    }
    """
    client.gql(mutation, {"sid": service_id, "eid": ENVIRONMENT_ID})


def wait_for_success(client: RailwayClient, service_id: str, timeout_sec: int = 1200) -> None:
    started = time.time()
    while True:
        status = get_service_state(client, service_id)["latestDeployment"]["status"]
        if status == "SUCCESS":
            return
        if status in {"FAILED", "CRASHED"}:
            raise RuntimeError(f"Deployment failed for service {service_id}: {status}")
        if time.time() - started > timeout_sec:
            raise RuntimeError(f"Timed out waiting for deployment success: {service_id}")
        time.sleep(10)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure Lean UI Railway services for the multi-environment gateway."
    )
    parser.add_argument("--execute", action="store_true", help="Apply mutations.")
    parser.add_argument("--apply-limits", action="store_true", help="Also apply CPU/memory limits.")
    parser.add_argument("--gateway-service", default=DEFAULT_GATEWAY_SERVICE)
    parser.add_argument("--mathlib-v427-service", default=DEFAULT_ENV_SERVICE_IDS["mathlib-v4.27"])
    parser.add_argument(
        "--formal-conjectures-service",
        default=DEFAULT_ENV_SERVICE_IDS["formal-conjectures-v4.27"],
    )
    parser.add_argument("--gateway-idle-ttl-sec", type=int, default=0)
    parser.add_argument("--mathlib-v427-idle-ttl-sec", type=int, default=900)
    parser.add_argument("--formal-conjectures-idle-ttl-sec", type=int, default=600)
    parser.add_argument("--mathlib-v427-max-repls", type=int, default=2)
    parser.add_argument("--formal-conjectures-max-repls", type=int, default=1)
    parser.add_argument("--gateway-max-repls", type=int, default=10)
    parser.add_argument(
        "--gateway-init-repls",
        default='{"import Mathlib":2,"import Mathlib\\nimport Aesop":2}',
    )
    parser.add_argument("--mathlib-v427-init-repls", default="{}")
    parser.add_argument("--formal-conjectures-init-repls", default="{}")
    parser.add_argument("--gateway-vcpus", type=float, default=16.0)
    parser.add_argument("--gateway-memory-gb", type=float, default=16.0)
    parser.add_argument("--mathlib-v427-vcpus", type=float, default=4.0)
    parser.add_argument("--mathlib-v427-memory-gb", type=float, default=8.0)
    parser.add_argument("--formal-conjectures-vcpus", type=float, default=4.0)
    parser.add_argument("--formal-conjectures-memory-gb", type=float, default=8.0)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "outputs/loadtests/verification"),
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


def service_domain(state: dict[str, Any]) -> str:
    domains = state.get("domains", {}).get("serviceDomains", [])
    if not domains:
        raise RuntimeError("Service does not expose a public domain.")
    return f"https://{domains[0]['domain']}"


def internal_service_url(variables: dict[str, str], state: dict[str, Any]) -> str:
    private_domain = variables.get("RAILWAY_PRIVATE_DOMAIN", "").strip()
    if private_domain:
        return f"http://{private_domain}"
    return service_domain(state)


def build_gateway_registry(
    *,
    gateway_url: str,
    mathlib_v427_url: str,
    formal_conjectures_url: str,
) -> str:
    environments = [
        {
            "id": "mathlib-v4.15",
            "display_name": "Mathlib 4.15",
            "lean_version": "v4.15.0",
            "project_label": "Mathlib",
            "project_type": "mathlib",
            "url": gateway_url,
            "selectable": True,
            "auto_routable": True,
        },
        {
            "id": "mathlib-v4.27",
            "display_name": "Mathlib 4.27",
            "lean_version": "v4.27.0",
            "project_label": "Mathlib",
            "project_type": "mathlib",
            "url": mathlib_v427_url,
            "selectable": True,
            "auto_routable": True,
        },
        {
            "id": "formal-conjectures-v4.27",
            "display_name": "Formal Conjectures 4.27",
            "lean_version": "v4.27.0",
            "project_label": "FormalConjectures",
            "project_type": "formal-conjectures",
            "url": formal_conjectures_url,
            "import_prefixes": ["FormalConjectures"],
            "selectable": True,
            "auto_routable": True,
        },
    ]
    return json.dumps(environments, separators=(",", ":"))


def create_service_if_missing(
    client: RailwayClient,
    services: dict[str, str],
    *,
    service_name: str,
) -> str:
    if service_name in services:
        return services[service_name]

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
                "name": service_name,
                "source": {"repo": REPO},
                "branch": "main",
            }
        },
    )
    return data["serviceCreate"]["id"]


def main() -> int:
    args = build_parser().parse_args()
    args.gateway_init_repls = parse_json_object(
        args.gateway_init_repls,
        arg_name="--gateway-init-repls",
    )
    args.mathlib_v427_init_repls = parse_json_object(
        args.mathlib_v427_init_repls,
        arg_name="--mathlib-v427-init-repls",
    )
    args.formal_conjectures_init_repls = parse_json_object(
        args.formal_conjectures_init_repls,
        arg_name="--formal-conjectures-init-repls",
    )

    token = load_token()
    client = RailwayClient(token)
    services = get_services(client)
    if args.gateway_service not in services:
        raise RuntimeError(f"Missing Railway gateway service: {args.gateway_service}")

    gateway_id = services[args.gateway_service]
    mathlib_v427_id = services.get(args.mathlib_v427_service)
    formal_conjectures_id = services.get(args.formal_conjectures_service)
    services_to_create = [
        service_name
        for service_name, service_id in (
            (args.mathlib_v427_service, mathlib_v427_id),
            (args.formal_conjectures_service, formal_conjectures_id),
        )
        if service_id is None
    ]

    if args.execute:
        if mathlib_v427_id is None:
            mathlib_v427_id = create_service_if_missing(
                client,
                services,
                service_name=args.mathlib_v427_service,
            )
        if formal_conjectures_id is None:
            formal_conjectures_id = create_service_if_missing(
                client,
                services,
                service_name=args.formal_conjectures_service,
            )
        services = get_services(client)
        mathlib_v427_id = services[args.mathlib_v427_service]
        formal_conjectures_id = services[args.formal_conjectures_service]
    else:
        mathlib_v427_id = mathlib_v427_id or f"<create:{args.mathlib_v427_service}>"
        formal_conjectures_id = formal_conjectures_id or f"<create:{args.formal_conjectures_service}>"

    gateway_state = get_service_state(client, gateway_id)
    gateway_vars = get_variables(client, gateway_id)
    gateway_api_key = gateway_vars.get("LEAN_SERVER_API_KEY", "").strip()
    if not gateway_api_key:
        raise RuntimeError("Gateway service must already have LEAN_SERVER_API_KEY configured.")

    if not args.execute and services_to_create:
        mathlib_v427_state = {"domains": {"serviceDomains": []}, "latestDeployment": {"status": "PENDING"}}
        formal_conjectures_state = {"domains": {"serviceDomains": []}, "latestDeployment": {"status": "PENDING"}}
        mathlib_v427_vars = {}
        formal_conjectures_vars = {}
    else:
        mathlib_v427_state = get_service_state(client, str(mathlib_v427_id))
        formal_conjectures_state = get_service_state(client, str(formal_conjectures_id))
        mathlib_v427_vars = get_variables(client, str(mathlib_v427_id))
        formal_conjectures_vars = get_variables(client, str(formal_conjectures_id))

    gateway_public_url = service_domain(gateway_state)
    gateway_registry = build_gateway_registry(
        gateway_url=gateway_public_url,
        mathlib_v427_url=(
            internal_service_url(mathlib_v427_vars, mathlib_v427_state)
            if mathlib_v427_vars or args.execute
            else f"http://{args.mathlib_v427_service}.railway.internal"
        ),
        formal_conjectures_url=internal_service_url(
            formal_conjectures_vars, formal_conjectures_state
        )
        if formal_conjectures_vars or args.execute
        else f"http://{args.formal_conjectures_service}.railway.internal",
    )

    gateway_update = {
        "LEAN_SERVER_ENVIRONMENT_ID": "mathlib-v4.15",
        "LEAN_SERVER_PROJECT_LABEL": "Mathlib",
        "LEAN_SERVER_PROJECT_TYPE": "mathlib",
        "LEAN_SERVER_LEAN_VERSION": "v4.15.0",
        "LEAN_SERVER_MAX_REPLS": str(args.gateway_max_repls),
        "LEAN_SERVER_INIT_REPLS": args.gateway_init_repls,
        "LEAN_SERVER_IDLE_REPL_TTL_SEC": str(args.gateway_idle_ttl_sec),
        "LEAN_SERVER_GATEWAY_DEFAULT_ENVIRONMENT": "mathlib-v4.15",
        "LEAN_SERVER_GATEWAY_INTERNAL_API_KEY": gateway_api_key,
        "LEAN_SERVER_GATEWAY_ENVIRONMENTS": gateway_registry,
    }
    mathlib_v427_update = {
        "LEAN_SERVER_ENVIRONMENT_ID": "mathlib-v4.27",
        "LEAN_SERVER_PROJECT_LABEL": "Mathlib",
        "LEAN_SERVER_PROJECT_TYPE": "mathlib",
        "LEAN_SERVER_LEAN_VERSION": "v4.27.0",
        "LEAN_SERVER_MAX_REPLS": str(args.mathlib_v427_max_repls),
        "LEAN_SERVER_INIT_REPLS": args.mathlib_v427_init_repls,
        "LEAN_SERVER_IDLE_REPL_TTL_SEC": str(args.mathlib_v427_idle_ttl_sec),
        "LEAN_SERVER_GATEWAY_DEFAULT_ENVIRONMENT": "mathlib-v4.27",
        "LEAN_SERVER_GATEWAY_ENVIRONMENTS": "[]",
    }
    formal_conjectures_update = {
        "LEAN_SERVER_ENVIRONMENT_ID": "formal-conjectures-v4.27",
        "LEAN_SERVER_PROJECT_LABEL": "FormalConjectures",
        "LEAN_SERVER_PROJECT_TYPE": "formal-conjectures",
        "LEAN_SERVER_LEAN_VERSION": "v4.27.0",
        "LEAN_SERVER_MAX_REPLS": str(args.formal_conjectures_max_repls),
        "LEAN_SERVER_INIT_REPLS": args.formal_conjectures_init_repls,
        "LEAN_SERVER_IDLE_REPL_TTL_SEC": str(args.formal_conjectures_idle_ttl_sec),
        "LEAN_SERVER_GATEWAY_DEFAULT_ENVIRONMENT": "formal-conjectures-v4.27",
        "LEAN_SERVER_GATEWAY_ENVIRONMENTS": "[]",
    }

    plan = {
        "execute": args.execute,
        "apply_limits": args.apply_limits,
        "services_to_create": services_to_create,
        "gateway_service": {"name": args.gateway_service, "id": gateway_id, "vars": gateway_update},
        "mathlib_v427_service": {
            "name": args.mathlib_v427_service,
            "id": mathlib_v427_id,
            "vars": mathlib_v427_update,
        },
        "formal_conjectures_service": {
            "name": args.formal_conjectures_service,
            "id": formal_conjectures_id,
            "vars": formal_conjectures_update,
        },
        "limits": {
            "gateway": {"vcpus": args.gateway_vcpus, "memory_gb": args.gateway_memory_gb},
            "mathlib_v427": {
                "vcpus": args.mathlib_v427_vcpus,
                "memory_gb": args.mathlib_v427_memory_gb,
            },
            "formal_conjectures": {
                "vcpus": args.formal_conjectures_vcpus,
                "memory_gb": args.formal_conjectures_memory_gb,
            },
        },
        "required_build_args": {
            "mathlib-v4.27": {
                "LEAN_PROJECT_NAME": "mathlib4",
                "LEAN_PROJECT_REPO_URL": "https://github.com/leanprover-community/mathlib4.git",
                "LEAN_PROJECT_REF": "v4.27.0",
                "LEAN_PROJECT_CACHE_CMD": "lake exe cache get",
                "LEAN_PROJECT_UPDATE_MANIFEST": "true",
                "REPL_BRANCH": "v4.27.0",
            },
            "formal-conjectures-v4.27": {
                "LEAN_PROJECT_NAME": "formal-conjectures",
                "LEAN_PROJECT_REPO_URL": "https://github.com/google-deepmind/formal-conjectures.git",
                "LEAN_PROJECT_REF": FORMAL_CONJECTURES_REF,
                "LEAN_PROJECT_CACHE_CMD": "",
                "LEAN_PROJECT_UPDATE_MANIFEST": "false",
                "REPL_BRANCH": "v4.27.0",
            },
        },
        "deploy_validation": {
            "gateway_public_url": gateway_public_url,
            "gateway_health_url": f"{gateway_public_url}/health",
            "gateway_environments_url": f"{gateway_public_url}/api/environments",
            "gateway_environment_health_url": f"{gateway_public_url}/api/environments/health",
            "expected_environments": [
                "mathlib-v4.15",
                "mathlib-v4.27",
                "formal-conjectures-v4.27",
            ],
            "expected_formal_conjectures_ref": FORMAL_CONJECTURES_REF,
        },
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    plan_path = output_dir / f"railway_multi_env_plan_{ts}.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not args.execute:
        print(json.dumps({"plan_path": str(plan_path), **plan}, indent=2, sort_keys=True))
        print("Dry-run only. Re-run with --execute to apply variable and limit updates.")
        return 0

    for service_id in (mathlib_v427_id, formal_conjectures_id):
        update_service_instance(
            client,
            service_id=str(service_id),
            input_payload={
                "rootDirectory": "/backend",
                "railwayConfigFile": "/backend/railway.toml",
                "startCommand": "python -m server",
                "sleepApplication": False,
                "restartPolicyType": "ON_FAILURE",
                "restartPolicyMaxRetries": 3,
            },
        )

    upsert_variables(client, gateway_id, gateway_update)
    upsert_variables(client, str(mathlib_v427_id), mathlib_v427_update)
    upsert_variables(client, str(formal_conjectures_id), formal_conjectures_update)

    if args.apply_limits:
        update_limits(client, gateway_id, args.gateway_vcpus, args.gateway_memory_gb)
        update_limits(
            client,
            str(mathlib_v427_id),
            args.mathlib_v427_vcpus,
            args.mathlib_v427_memory_gb,
        )
        update_limits(
            client,
            str(formal_conjectures_id),
            args.formal_conjectures_vcpus,
            args.formal_conjectures_memory_gb,
        )

    for service_id in (gateway_id, str(mathlib_v427_id), str(formal_conjectures_id)):
        redeploy(client, service_id)

    for service_id in (gateway_id, str(mathlib_v427_id), str(formal_conjectures_id)):
        wait_for_success(client, service_id)

    print(
        json.dumps(
            {
                "applied": True,
                "plan_path": str(plan_path),
                "gateway_registry": json.loads(gateway_registry),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
