from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from kimina_client import GatewayEnvironmentHealthResponse, GatewayEnvironmentsResponse

from ..auth import require_key
from ..environment_registry import build_environment_registry, list_public_environments
from ..gateway_proxy import proxy_health_request
from ..settings import Settings, settings as default_settings

router = APIRouter()


def get_runtime_settings(request: Request) -> Settings:
    cfg = getattr(request.app.state, "settings", None)
    if cfg is None:
        return default_settings
    return cfg


@router.get(
    "/environments",
    response_model=GatewayEnvironmentsResponse,
    response_model_exclude_none=True,
)
async def list_environments(
    settings: Settings = Depends(get_runtime_settings),
) -> GatewayEnvironmentsResponse:
    default_environment, environments = list_public_environments(settings)
    return GatewayEnvironmentsResponse(
        default_environment=default_environment,
        environments=environments,
    )


@router.get(
    "/environments/health",
    response_model=GatewayEnvironmentHealthResponse,
    response_model_exclude_none=True,
)
async def environment_health(
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> GatewayEnvironmentHealthResponse:
    environments: list[dict[str, object]] = []
    for environment in build_environment_registry(settings):
        if environment.id == settings.environment_id:
            environments.append(
                {
                    "id": environment.id,
                    "healthy": True,
                    "status": "ok",
                    "environment_id": settings.environment_id,
                    "lean_version": settings.lean_version,
                    "project_label": settings.project_label,
                    "project_type": settings.project_type,
                }
            )
            continue

        try:
            health = await proxy_health_request(
                target_environment=environment,
                settings=settings,
            )
        except HTTPException as exc:
            environments.append(
                {
                    "id": environment.id,
                    "healthy": False,
                    "status": "error",
                    "error": str(exc.detail),
                }
            )
            continue
        except Exception as exc:
            environments.append(
                {
                    "id": environment.id,
                    "healthy": False,
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue

        environments.append(
            {
                "id": environment.id,
                "healthy": True,
                "status": str(health.get("status", "ok")),
                "environment_id": health.get("environment_id"),
                "lean_version": health.get("lean_version"),
                "project_label": health.get("project_label"),
                "project_type": health.get("project_type"),
            }
        )

    return GatewayEnvironmentHealthResponse(environments=environments)
