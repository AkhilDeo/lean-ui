from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from server.routers.check import wait_for_task_or_disconnect


class DummyRequest:
    def __init__(self, disconnect_states: list[bool]) -> None:
        self._disconnect_states = disconnect_states
        self._index = 0

    async def is_disconnected(self) -> bool:
        if not self._disconnect_states:
            return False
        idx = min(self._index, len(self._disconnect_states) - 1)
        self._index += 1
        return self._disconnect_states[idx]


@pytest.mark.asyncio
async def test_wait_for_task_or_disconnect_returns_without_poll_delay() -> None:
    async def quick() -> str:
        await asyncio.sleep(0.01)
        return "done"

    task = asyncio.create_task(quick())
    started = time.perf_counter()
    result = await wait_for_task_or_disconnect(
        task,
        DummyRequest([False]),
        disconnect_poll_interval_sec=0.5,
    )

    assert result == "done"
    assert time.perf_counter() - started < 0.2


@pytest.mark.asyncio
async def test_wait_for_task_or_disconnect_cancels_on_disconnect() -> None:
    async def slow() -> str:
        await asyncio.sleep(10)
        return "never"

    task = asyncio.create_task(slow())

    with pytest.raises(HTTPException) as exc_info:
        await wait_for_task_or_disconnect(
            task,
            DummyRequest([True]),
            disconnect_poll_interval_sec=0.01,
        )

    await asyncio.sleep(0)

    assert exc_info.value.status_code == 499
    assert task.cancelled()
