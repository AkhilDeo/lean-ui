import asyncio
from datetime import datetime
from uuid import uuid4

import pytest

from server.errors import NoAvailableReplError
from server.manager import Manager


class DummyRepl:
    def __init__(self, header: str) -> None:
        self.header = header
        self.uuid = uuid4()
        self.exhausted = False
        self.last_check_at = datetime.now()
        self.is_running = False


@pytest.mark.asyncio
async def test_lazy_lock_initialization() -> None:
    """Test that Lock and Condition are initialized lazily in async context."""
    manager = Manager(max_repls=1, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    
    # Initially, lock and condition should be None
    assert manager._lock is None
    assert manager._cond is None
    
    # After calling an async method, they should be initialized
    repl = await manager.get_repl()
    
    assert manager._lock is not None
    assert manager._cond is not None
    assert repl is not None
    
    await manager.release_repl(repl)


@pytest.mark.asyncio
async def test_get_repl() -> None:
    manager = Manager(max_repls=1, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)

    repl = await manager.get_repl()

    assert repl is not None

    await manager.release_repl(repl)


@pytest.mark.asyncio
async def test_exhausted() -> None:
    manager = Manager(max_repls=0, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)

    with pytest.raises(NoAvailableReplError):
        await manager.get_repl(timeout=3)


@pytest.mark.asyncio
async def test_get_repl_with_reuse() -> None:
    manager = Manager(max_repls=1, max_repl_uses=3, max_repl_mem=10, min_host_free_mem=4)

    repl1 = await manager.get_repl()
    assert repl1 is not None

    await manager.release_repl(repl1)

    repl2 = await manager.get_repl()
    assert repl2.uuid == repl1.uuid

    await manager.release_repl(repl2)

    repl3 = await manager.get_repl(reuse=False)

    assert repl3.uuid != repl1.uuid

    assert manager._busy == {repl3}
    assert manager._free == []


@pytest.mark.asyncio
async def test_get_repl_reserves_capacity_while_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(max_repls=1, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    allow_start = asyncio.Event()
    start_calls = 0

    async def fake_start_new(header: str):  # type: ignore[no-untyped-def]
        nonlocal start_calls
        start_calls += 1
        await allow_start.wait()
        return DummyRepl(header)

    monkeypatch.setattr(manager, "start_new", fake_start_new)

    first = asyncio.create_task(manager.get_repl(timeout=1, reuse=False))
    await asyncio.sleep(0.01)

    with pytest.raises(NoAvailableReplError, match="Timed out after 0.05s"):
        await manager.get_repl(timeout=0.05, reuse=False)

    assert start_calls == 1
    allow_start.set()
    repl = await asyncio.wait_for(first, timeout=1.0)
    assert repl in manager._busy
    assert manager._starting == 0


@pytest.mark.asyncio
async def test_get_repl_clears_starting_reservation_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(max_repls=1, max_repl_uses=1, max_repl_mem=10, min_host_free_mem=4)
    calls = 0

    async def fake_start_new(_header: str):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return DummyRepl("")

    monkeypatch.setattr(manager, "start_new", fake_start_new)

    with pytest.raises(RuntimeError, match="boom"):
        await manager.get_repl(timeout=0.1, reuse=False)

    repl = await manager.get_repl(timeout=0.1, reuse=False)
    assert repl in manager._busy
    assert manager._starting == 0
