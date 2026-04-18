from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


# TODO: add stats endpoint in webapp typescript


@router.get("/health")
@router.get("/health/", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def get_health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    mode = "gateway" if settings.gateway_enabled else "runtime"
    runtime_id = settings.runtime_id
    ready = mode == "gateway" or request.app.state.runtime_ready_event.is_set()
    ready_reason = None if ready else request.app.state.runtime_ready_reason
    payload = {
        "status": "ok",
        "mode": mode,
        "runtime_id": runtime_id,
        "ready": ready,
        "ready_reason": ready_reason,
    }
    if mode == "runtime":
        payload["ready_details"] = getattr(request.app.state, "runtime_ready_details", None)
    return payload
