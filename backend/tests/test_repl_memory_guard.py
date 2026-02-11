from __future__ import annotations

import pytest

from server.errors import NoAvailableReplError
from server.manager import Manager


@pytest.mark.asyncio
async def test_memory_guard_rejects_when_no_busy_repls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(max_repls=1, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    monkeypatch.setattr(manager, "_has_memory_headroom", lambda: False)

    with pytest.raises(NoAvailableReplError, match="Insufficient host memory"):
        await manager.get_repl(timeout=1)


@pytest.mark.asyncio
async def test_memory_guard_waits_when_busy_then_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(max_repls=5, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    monkeypatch.setattr(manager, "_has_memory_headroom", lambda: False)
    manager._busy.add(object())  # type: ignore[arg-type]

    with pytest.raises(NoAvailableReplError, match="Timed out waiting for host memory"):
        await manager.get_repl(timeout=0.1)
