from fastapi import APIRouter, Request

from ..settings import Settings, settings as default_settings

router = APIRouter()


# TODO: add stats endpoint in webapp typescript


@router.get("/health")
@router.get("/health/", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def get_health(request: Request) -> dict[str, str]:
    cfg = getattr(request.app.state, "settings", None)
    settings = cfg if isinstance(cfg, Settings) else default_settings
    return {
        "status": "ok",
        "environment_id": settings.environment_id,
        "lean_version": settings.lean_version,
        "project_label": settings.project_label,
        "project_type": settings.project_type,
    }
