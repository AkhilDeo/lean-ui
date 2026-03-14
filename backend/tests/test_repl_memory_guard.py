from __future__ import annotations

import asyncio
import os
import signal
from collections import namedtuple
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from server.errors import NoAvailableReplError
from server.manager import Manager
from server.repl import Repl


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


@pytest.mark.asyncio
async def test_startup_semaphore_limits_concurrent_cold_starts() -> None:
    manager = Manager(
        max_repls=2,
        max_repl_uses=1,
        max_repl_mem=10,
        min_host_free_mem=4,
        startup_concurrency_limit=1,
    )

    started = 0
    max_started = 0

    class FakeRepl:
        def __init__(self, header: str = "") -> None:
            self.header = header
            self.header_cmd_response = None
            self._is_running = False

        @property
        def is_running(self) -> bool:
            return self._is_running

        async def start(self) -> None:
            nonlocal started, max_started
            started += 1
            max_started = max(max_started, started)
            await asyncio.sleep(0.05)
            self._is_running = True
            started -= 1

    repl1 = FakeRepl()
    repl2 = FakeRepl()
    await asyncio.gather(
        manager.prep(repl1, "a", timeout=1.0, debug=False),  # type: ignore[arg-type]
        manager.prep(repl2, "b", timeout=1.0, debug=False),  # type: ignore[arg-type]
    )

    assert max_started == 1


@pytest.mark.asyncio
async def test_ensure_warm_repls_refills_missing_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(max_repls=3, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    created: list[str] = []

    class FakeRepl:
        def __init__(self, header: str) -> None:
            self.header = header
            self.header_cmd_response = None
            self._is_running = True

        @property
        def is_running(self) -> bool:
            return self._is_running

    async def fake_get_repl(header: str = "", **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        created.append(header)
        repl = FakeRepl(header)
        manager._busy.add(repl)  # type: ignore[arg-type]
        return repl

    async def fake_prep(repl, snippet_id: str, timeout: float, debug: bool):  # type: ignore[no-untyped-def]
        _ = repl, snippet_id, timeout, debug
        return None

    async def fake_release_repl(repl) -> None:  # type: ignore[no-untyped-def]
        manager._busy.discard(repl)
        manager._free.append(repl)

    monkeypatch.setattr(manager, "get_repl", fake_get_repl)
    monkeypatch.setattr(manager, "prep", fake_prep)
    monkeypatch.setattr(manager, "release_repl", fake_release_repl)

    await manager.ensure_warm_repls({"import Mathlib": 2})

    assert created == ["import Mathlib", "import Mathlib"]
    assert await manager.count_free_started_repls({"import Mathlib"}) == 2


@pytest.mark.asyncio
async def test_mem_monitor_kills_repl_when_rss_exceeds_limit() -> None:
    """RSS enforcement kills the REPL process group when memory exceeds limit."""
    from datetime import datetime

    repl = Repl(
        uuid=uuid4(),
        created_at=datetime.now(),
        header="",
        max_repl_mem=100,  # 100 MB
        max_repl_uses=-1,
    )

    MemInfo = namedtuple("MemInfo", ["rss"])
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.returncode = None
    repl.proc = fake_proc

    fake_ps = MagicMock()
    # Return RSS of 200 MB (exceeds 100 MB limit)
    fake_ps.memory_info.return_value = MemInfo(rss=200 * 1024 * 1024)
    fake_ps.children.return_value = []
    repl._ps_proc = fake_ps

    with patch("server.repl.os.killpg") as mock_killpg, \
         patch("server.repl.os.getpgid", return_value=12345):
        # Run one iteration of the monitor
        await asyncio.sleep(0)  # ensure event loop is available
        # Directly call the monitor; it will sleep 1s then check
        monitor_task = asyncio.create_task(repl._mem_monitor())
        await asyncio.sleep(1.2)
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
