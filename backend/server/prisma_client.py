from __future__ import annotations

from typing import Any

try:
    from prisma import Prisma  # type: ignore
except Exception:  # pragma: no cover - exercised only when prisma is unavailable
    Prisma = None  # type: ignore[assignment]


class _MissingPrismaClient:
    async def connect(self) -> None:
        raise RuntimeError("prisma dependency is not installed")

    async def disconnect(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            f"prisma dependency is not installed; attempted to access '{name}'"
        )


prisma = Prisma() if Prisma is not None else _MissingPrismaClient()
