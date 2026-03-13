from __future__ import annotations

from enum import Enum

from .settings import Settings
from .split import split_snippet


class AsyncQueueTier(str, Enum):
    all = "all"
    light = "light"
    heavy = "heavy"


def classify_async_queue_tier(code: str, settings: Settings) -> AsyncQueueTier:
    split = split_snippet(code)
    header = split.header.strip()
    total_lines = len(code.splitlines())
    body_bytes = len(split.body.encode("utf-8"))

    if "Aesop" in header:
        return AsyncQueueTier.heavy
    if header != "import Mathlib":
        return AsyncQueueTier.heavy
    if body_bytes > settings.async_heavy_body_bytes:
        return AsyncQueueTier.heavy
    if total_lines > settings.async_heavy_line_count:
        return AsyncQueueTier.heavy
    return AsyncQueueTier.light


def warm_repl_targets_for_tier(settings: Settings, tier: AsyncQueueTier) -> dict[str, int]:
    if tier == AsyncQueueTier.light:
        return dict(settings.async_light_warm_repls)
    if tier == AsyncQueueTier.heavy:
        return dict(settings.async_heavy_warm_repls)
    combined = dict(settings.async_light_warm_repls)
    for header, count in settings.async_heavy_warm_repls.items():
        combined[header] = max(combined.get(header, 0), count)
    return combined
