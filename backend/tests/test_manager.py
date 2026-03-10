import pytest
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from server.errors import NoAvailableReplError
from server.manager import Manager


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

@dataclass(eq=False)
class FakeRepl:
    header: str
    last_check_at: datetime
    uuid: object
    exhausted: bool = False
    is_running: bool = False


def _fake_repl(header: str, age_sec: int) -> FakeRepl:
    return FakeRepl(
        header=header,
        last_check_at=datetime.now(tz=timezone.utc) - timedelta(seconds=age_sec),
        uuid=uuid4(),
    )


@pytest.mark.asyncio
async def test_get_repl_prefers_evicting_non_warm_header_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(
        max_repls=5,
        max_repl_uses=3,
        max_repl_mem=10,
        init_repls={"import Mathlib": 2, "import Mathlib\nimport Aesop": 2},
        min_host_free_mem=4,
    )
    manager._ensure_lock()
    protected_mathlib_a = _fake_repl("import Mathlib", 50)
    protected_mathlib_b = _fake_repl("import Mathlib", 40)
    protected_aesop_a = _fake_repl("import Mathlib\nimport Aesop", 30)
    protected_aesop_b = _fake_repl("import Mathlib\nimport Aesop", 20)
    blank = _fake_repl("", 10)
    manager._free = [
        protected_mathlib_a,
        protected_mathlib_b,
        protected_aesop_a,
        protected_aesop_b,
        blank,
    ]

    closed: list[str] = []
    created = _fake_repl("import Rare.Header", 0)

    async def fake_close_verbose(repl):  # type: ignore[no-untyped-def]
        closed.append(repl.header)

    async def fake_start_new(header: str):  # type: ignore[no-untyped-def]
        created.header = header
        manager._busy.add(created)
        return created

    monkeypatch.setattr("server.manager.close_verbose", fake_close_verbose)
    monkeypatch.setattr(manager, "start_new", fake_start_new)

    repl = await manager.get_repl(header="import Rare.Header")
    await asyncio.sleep(0)

    assert repl is created
    assert closed == [""]
    assert blank not in manager._free
    assert protected_mathlib_a in manager._free
    assert protected_mathlib_b in manager._free
    assert protected_aesop_a in manager._free
    assert protected_aesop_b in manager._free


@pytest.mark.asyncio
async def test_get_repl_falls_back_to_protected_pool_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = Manager(
        max_repls=4,
        max_repl_uses=3,
        max_repl_mem=10,
        init_repls={"import Mathlib": 2, "import Mathlib\nimport Aesop": 2},
        min_host_free_mem=4,
    )
    manager._ensure_lock()
    protected_mathlib_a = _fake_repl("import Mathlib", 40)
    protected_mathlib_b = _fake_repl("import Mathlib", 30)
    protected_aesop_a = _fake_repl("import Mathlib\nimport Aesop", 20)
    protected_aesop_b = _fake_repl("import Mathlib\nimport Aesop", 10)
    manager._free = [
        protected_mathlib_a,
        protected_mathlib_b,
        protected_aesop_a,
        protected_aesop_b,
    ]

    closed: list[str] = []
    created = _fake_repl("import Rare.Header", 0)

    async def fake_close_verbose(repl):  # type: ignore[no-untyped-def]
        closed.append(repl.header)

    async def fake_start_new(header: str):  # type: ignore[no-untyped-def]
        created.header = header
        manager._busy.add(created)
        return created

    monkeypatch.setattr("server.manager.close_verbose", fake_close_verbose)
    monkeypatch.setattr(manager, "start_new", fake_start_new)

    repl = await manager.get_repl(header="import Rare.Header")
    await asyncio.sleep(0)

    assert repl is created
    assert closed == ["import Mathlib"]
    assert len(manager._free) == 3
