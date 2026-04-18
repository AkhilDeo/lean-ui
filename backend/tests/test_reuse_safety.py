from __future__ import annotations

from uuid import uuid4

import pytest
from kimina_client import ReplResponse, Snippet

from server.routers.check import run_checks


class FakeRepl:
    def __init__(self) -> None:
        self.uuid = uuid4()
        self.header = ""
        self.header_cmd_response = None
        self.exhausted = False

    async def send_timeout(self, snippet: Snippet, timeout: float, infotree=None):  # type: ignore[no-untyped-def]
        _ = timeout, infotree
        return ReplResponse(id=snippet.id, time=0.01, response={"env": 0})


class FakeManager:
    def __init__(self) -> None:
        self.get_repl_calls: list[dict[str, object]] = []
        self.release_calls = 0
        self.destroy_calls = 0

    async def get_repl(  # type: ignore[no-untyped-def]
        self,
        header: str = "",
        snippet_id: str = "",
        timeout: float = 60.0,
        reuse: bool = True,
    ) -> FakeRepl:
        _ = timeout
        self.get_repl_calls.append(
            {
                "header": header,
                "snippet_id": snippet_id,
                "reuse": reuse,
            }
        )
        repl = FakeRepl()
        repl.header = header
        return repl

    async def prep(self, repl: FakeRepl, snippet_id: str, timeout: float, debug: bool):  # type: ignore[no-untyped-def]
        _ = repl, snippet_id, timeout, debug
        return None

    async def release_repl(self, repl: FakeRepl) -> None:
        _ = repl
        self.release_calls += 1

    async def destroy_repl(self, repl: FakeRepl) -> None:
        _ = repl
        self.destroy_calls += 1


@pytest.mark.asyncio
async def test_run_checks_disables_reuse_for_headerless_snippet() -> None:
    manager = FakeManager()
    snippets = [Snippet(id="s1", code="theorem t : 1 + 1 = 2 := by\n  rfl\n")]

    result = await run_checks(
        snippets=snippets,
        timeout=30,
        debug=False,
        manager=manager,  # type: ignore[arg-type]
        reuse=True,
        infotree=None,
    )

    assert len(result) == 1
    assert manager.get_repl_calls[0]["reuse"] is False


@pytest.mark.asyncio
async def test_run_checks_keeps_reuse_for_import_header_snippet() -> None:
    manager = FakeManager()
    snippets = [Snippet(id="s1", code="import Mathlib\n#check Nat\n")]

    result = await run_checks(
        snippets=snippets,
        timeout=30,
        debug=False,
        manager=manager,  # type: ignore[arg-type]
        reuse=True,
        infotree=None,
    )

    assert len(result) == 1
    assert manager.get_repl_calls[0]["reuse"] is True
