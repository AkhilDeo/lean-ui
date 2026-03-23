from fastapi import APIRouter, Request

router = APIRouter()


# TODO: add stats endpoint in webapp typescript


@router.get("/health")
@router.get("/health/", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def get_health(request: Request) -> dict[str, str]:
    settings = getattr(request.app.state, "settings", None)
    mode = "gateway" if getattr(settings, "gateway_enabled", False) else "runtime"
    runtime_id = getattr(settings, "runtime_id", "")
    return {"status": "ok", "mode": mode, "runtime_id": runtime_id}
