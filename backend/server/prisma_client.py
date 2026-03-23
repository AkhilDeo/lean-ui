from __future__ import annotations

import importlib
from typing import Any

class LazyPrismaClient:
    def __init__(self) -> None:
        self._client: Any | None = None
        self._import_error: Exception | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._import_error is not None:
            raise RuntimeError("prisma dependency is not installed") from self._import_error
        try:
            prisma_module = importlib.import_module("prisma")
            prisma_cls = getattr(prisma_module, "Prisma")
        except Exception as exc:  # pragma: no cover - exercised only when prisma is unavailable
            self._import_error = exc
            raise RuntimeError("prisma dependency is not installed") from exc
        self._client = prisma_cls()
        return self._client

    async def connect(self) -> None:
        client = self._ensure_client()
        await client.connect()

    async def disconnect(self) -> None:
        if self._client is None:
            return None
        await self._client.disconnect()

    def __getattr__(self, name: str) -> Any:
        client = self._ensure_client()
        return getattr(client, name)


prisma = LazyPrismaClient()
