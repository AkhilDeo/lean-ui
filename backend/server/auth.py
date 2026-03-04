from fastapi import HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader

from .settings import Environment, settings

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

# TODO: Implement key in db once ready
# async def seed_key():
#     if db.connected:
#     existing = await db.client.api_key.find_first()
#     if not existing:
#         await db.client.api_key.create(data={"key": API_KEY})


async def require_key(request: Request, auth: str = Security(api_key_header)) -> str | None:
    cfg = getattr(request.app.state, "settings", settings)

    if cfg.api_key is None and cfg.environment == Environment.prod:
        raise HTTPException(
            status_code=503,
            detail="API key is not configured for production",
        )

    if cfg.api_key is None:
        return None

    if not auth:
        raise HTTPException(401, "Missing API key")

    token = auth.removeprefix("Bearer ").strip()
    # found = await db.client.api_key.find_unique(where={"key": token})
    if token != cfg.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token
