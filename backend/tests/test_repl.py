import asyncio
import os
from typing import AsyncGenerator

import psutil
import pytest
from kimina_client import Snippet

from server.errors import ReplError
from server.repl import Repl


def _real_repl_smoke_enabled() -> bool:
    return os.getenv("LEAN_SERVER_RUN_REAL_REPL_TESTS") == "1"


@pytest.fixture
async def repl() -> AsyncGenerator[Repl, None]:
    repl_instance = await Repl.create("", 1, 8192)
    yield repl_instance
    await repl_instance.close()


@pytest.mark.asyncio
async def test_start(repl: Repl) -> None:
    assert repl.proc is None

    await repl.start()

    assert repl.proc is not None


@pytest.mark.asyncio
async def test_create_close_multiple() -> None:
    if not _real_repl_smoke_enabled():
        pytest.skip("set LEAN_SERVER_RUN_REAL_REPL_TESTS=1 to run real REPL smoke")

    for _ in range(3):
        repl = await Repl.create("", 1, 8192)

        await repl.start()
        assert repl.proc is not None
        pid = repl.proc.pid
        assert pid is not None

        # Run a simple command
        response = await repl.send_timeout(
            Snippet(id="test", code="def f := 2"), timeout=10
        )

        assert response.error is None

        # Close the REPL
        await repl.close()

        # Verify the process has terminated
        assert not psutil.pid_exists(pid)


@pytest.mark.asyncio
async def test_send_reports_process_exit_details() -> None:
    repl = await Repl.create("", 1, 8192)
    loop = asyncio.get_running_loop()

    class FakeStdIn:
        def write(self, payload: bytes) -> None:
            _ = payload

        async def drain(self) -> None:
            return None

    class FakeProc:
        def __init__(self) -> None:
            self.returncode = 1
            self.stdin = FakeStdIn()
            self.stdout = object()
            self.stderr = object()
            self.pid = 123

    repl.proc = FakeProc()  # type: ignore[assignment]
    repl._loop = loop
    repl._stderr_chunks = [b"uncaught exception: resource exhausted"]

    async def fake_read_response() -> bytes:
        return b""

    repl._read_response = fake_read_response  # type: ignore[method-assign]

    with pytest.raises(ReplError, match="resource exhausted"):
        await repl.send(Snippet(id="test", code="#check Nat"))


# @pytest.mark.asyncio
# @pytest.mark.skip
# async def test_del_calls_close(repl: Repl) -> None:
#     await repl.start()

#     assert repl.proc is not None
#     pid = repl.proc.pid

#     # Verify the process is running
#     assert psutil.pid_exists(pid)

#     # Delete the repl instance
#     del repl

#     # Give it 1 second to terminate
#     await asyncio.sleep(10)

#     # Verify the process has terminated
#     assert not psutil.pid_exists(pid), "Process did not terminate after __del__"
