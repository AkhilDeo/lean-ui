from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from time import time

from kimina_client import ReplResponse, Snippet
from loguru import logger
import psutil

from .errors import NoAvailableReplError, ReplError
from .repl import Repl, close_verbose
from .settings import settings
from .utils import is_blank


@dataclass(frozen=True)
class WarmTargetStatus:
    header: str
    target: int
    reached: int
    attempts: int
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class WarmPoolStatus:
    success: bool
    targets: list[WarmTargetStatus]
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "reason": self.reason,
            "targets": [
                {
                    "header": target.header,
                    "target": target.target,
                    "reached": target.reached,
                    "attempts": target.attempts,
                    "success": target.success,
                    "error": target.error,
                }
                for target in self.targets
            ],
        }


class Manager:
    def __init__(
        self,
        *,
        max_repls: int = settings.max_repls,
        max_repl_uses: int = settings.max_repl_uses,
        max_repl_mem: int = settings.max_repl_mem,
        init_repls: dict[str, int] = settings.init_repls,
        min_host_free_mem: int = settings.min_host_free_mem,
        startup_concurrency_limit: int | None = settings.async_startup_concurrency_limit,
    ) -> None:
        self.max_repls = max_repls
        self.max_repl_uses = max_repl_uses
        self.max_repl_mem = max_repl_mem
        self.init_repls = init_repls
        self.min_host_free_mem = min_host_free_mem
        self.startup_concurrency_limit = startup_concurrency_limit

        self._lock: asyncio.Lock | None = None
        self._cond: asyncio.Condition | None = None
        self._startup_semaphore: asyncio.Semaphore | None = None
        self._free: list[Repl] = []
        self._busy: set[Repl] = set()
        self._cold_start_count = 0
        self._spawn_failure_count = 0

        logger.info(
            "REPL manager initialized with: MAX_REPLS={}, MAX_REPL_USES={}, MAX_REPL_MEM={} MB, MIN_HOST_FREE_MEM={} MB",
            max_repls,
            max_repl_uses,
            max_repl_mem,
            min_host_free_mem,
        )

    def _has_memory_headroom(self) -> bool:
        """
        Keep host headroom before creating a new REPL process.
        Values are in MB.
        """
        try:
            available_mb = int(psutil.virtual_memory().available / 1024 / 1024)
        except Exception:
            # If metrics are unavailable, avoid blocking all traffic.
            return True
        required_mb = self.max_repl_mem + self.min_host_free_mem
        return available_mb >= required_mb

    def _ensure_lock(self) -> None:
        """Ensure the lock and condition are initialized in an async context."""
        if self._lock is None:
            self._lock = asyncio.Lock()
            self._cond = asyncio.Condition(self._lock)
        if self._startup_semaphore is None and self.startup_concurrency_limit:
            self._startup_semaphore = asyncio.Semaphore(self.startup_concurrency_limit)

    async def initialize_repls(self) -> None:
        if len(self.init_repls) == 0:
            return
        if self.max_repls < sum(self.init_repls.values()):
            raise ValueError(
                f"Cannot initialize REPLs: Σ (INIT_REPLS values) = {sum(self.init_repls.values())} > {self.max_repls} = MAX_REPLS"
            )
        initialized_repls: list[Repl] = []
        for header, count in self.init_repls.items():
            for _ in range(count):
                initialized_repls.append(await self.get_repl(header=header))

        async def _prep_and_release(repl: Repl) -> None:
            # All initialized imports should finish in 60 seconds.
            await self.prep(repl, snippet_id="init", timeout=60, debug=False)
            await self.release_repl(repl)

        await asyncio.gather(*(_prep_and_release(r) for r in initialized_repls))

        logger.info(f"Initialized REPLs with: {json.dumps(self.init_repls, indent=2)}")

    async def get_repl(
        self,
        header: str = "",
        snippet_id: str = "",
        timeout: float = settings.max_wait,
        reuse: bool = True,
    ) -> Repl:
        """
        Async-safe way to get a `Repl` instance for a given header.
        Immediately raises an Exception if not possible.
        """
        self._ensure_lock()
        assert self._cond is not None  # Type narrowing after _ensure_lock
        deadline = time() + timeout
        repl_to_destroy: Repl | None = None
        while True:
            async with self._cond:
                logger.debug(
                    f"# Free = {len(self._free)} | # Busy = {len(self._busy)} | # Max = {self.max_repls}"
                )
                if reuse:
                    for i, r in enumerate(self._free):
                        if (
                            r.header == header
                        ):  # repl shouldn't be exhausted (max uses to check)
                            repl = self._free.pop(i)
                            self._busy.add(repl)

                            logger.debug(
                                f"\\[{repl.uuid.hex[:8]}] Reusing ({'started' if repl.is_running else 'non-started'}) REPL for {snippet_id}"
                            )
                            return repl
                total = len(self._free) + len(self._busy)
                if total < self.max_repls:
                    if not self._has_memory_headroom():
                        remaining = deadline - time()
                        if remaining <= 0:
                            raise NoAvailableReplError(
                                "Insufficient host memory to spawn a new REPL"
                            )
                        if len(self._busy) == 0:
                            raise NoAvailableReplError(
                                "Insufficient host memory to spawn a new REPL"
                            )
                        logger.warning(
                            "Memory headroom unavailable for new REPL, waiting up to {:.2f}s",
                            remaining,
                        )
                        try:
                            await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                        except asyncio.TimeoutError:
                            raise NoAvailableReplError(
                                "Timed out waiting for host memory headroom"
                            ) from None
                        continue
                    break

                if self._free:
                    oldest = min(
                        self._free, key=lambda r: r.last_check_at
                    )  # Use the one that's been around the longest
                    self._free.remove(oldest)
                    repl_to_destroy = oldest
                    break

                remaining = deadline - time()
                if remaining <= 0:
                    raise NoAvailableReplError(f"Timed out after {timeout}s")

                try:
                    logger.debug(
                        f"Waiting for a REPL to become available (timeout in {remaining:.2f}s)"
                    )
                    # Wait for a REPL to be released
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise NoAvailableReplError(
                        f"Timed out after {timeout}s while waiting for a REPL"
                    ) from None

        if repl_to_destroy is not None:
            asyncio.create_task(close_verbose(repl_to_destroy))

        return await self.start_new(header)

    async def destroy_repl(self, repl: Repl) -> None:
        self._ensure_lock()
        assert self._cond is not None  # Type narrowing after _ensure_lock
        async with self._cond:
            self._busy.discard(repl)
            if repl in self._free:
                self._free.remove(repl)
            asyncio.create_task(close_verbose(repl))
            self._cond.notify(1)

    async def release_repl(self, repl: Repl) -> None:
        self._ensure_lock()
        assert self._cond is not None  # Type narrowing after _ensure_lock
        async with self._cond:
            if repl not in self._busy:
                logger.error(
                    f"Attempted to release a REPL that is not busy: {repl.uuid.hex[:8]}"
                )
                return

            if repl.exhausted:
                uuid = repl.uuid
                logger.debug(f"REPL {uuid.hex[:8]} is exhausted, closing it")
                self._busy.discard(repl)

                asyncio.create_task(close_verbose(repl))
                self._cond.notify(1)
                return
            self._busy.remove(repl)
            self._free.append(repl)
            repl.last_check_at = datetime.now()
            logger.debug(f"\\[{repl.uuid.hex[:8]}] Released!")
            self._cond.notify(1)

    async def start_new(self, header: str) -> Repl:
        repl = await Repl.create(
            header, max_repl_uses=self.max_repl_uses, max_repl_mem=self.max_repl_mem
        )
        self._busy.add(repl)
        return repl

    async def cleanup(self) -> None:
        self._ensure_lock()
        assert self._cond is not None  # Type narrowing after _ensure_lock
        async with self._cond:
            logger.info("Cleaning up REPL manager...")
            for repl in self._free:
                asyncio.create_task(close_verbose(repl))
            self._free.clear()

            for repl in self._busy:
                asyncio.create_task(close_verbose(repl))
            self._busy.clear()

            logger.info("REPL manager cleaned up!")
        pass

    async def prep(
        self, repl: Repl, snippet_id: str, timeout: float, debug: bool
    ) -> ReplResponse | None:
        if repl.is_running:
            return None

        try:
            self._ensure_lock()
            if self._startup_semaphore is not None:
                async with self._startup_semaphore:
                    self._cold_start_count += 1
                    await repl.start()
            else:
                self._cold_start_count += 1
                await repl.start()
        except Exception as e:
            self._spawn_failure_count += 1
            logger.exception("Failed to start REPL: {}", e)
            raise ReplError(f"Failed to start REPL: {e}") from e

        if not is_blank(repl.header):
            try:
                cmd_response = await repl.send_timeout(
                    Snippet(id=f"{snippet_id}-header", code=repl.header),
                    timeout=timeout,
                    is_header=True,
                )
            except TimeoutError as e:
                logger.error("Header command timed out")
                raise e
            except Exception as e:
                logger.error("Failed to run header on REPL: {}", e)
                raise ReplError(f"Failed to run header on REPL: {e}") from e

            if not debug:
                cmd_response.diagnostics = None

            if cmd_response.error:
                logger.error(f"Header command failed: {cmd_response.error}")
                await self.destroy_repl(repl)

            repl.header_cmd_response = cmd_response

            return cmd_response
        return repl.header_cmd_response

    async def count_free_started_repls(self, headers: set[str] | None = None) -> int:
        self._ensure_lock()
        assert self._cond is not None
        async with self._cond:
            return sum(
                1
                for repl in self._free
                if repl.is_running and (headers is None or repl.header in headers)
            )

    async def ensure_warm_repls(
        self, targets: dict[str, int], *, timeout: float = 60.0
    ) -> WarmPoolStatus:
        results: list[WarmTargetStatus] = []
        first_failure: str | None = None

        for header, target in targets.items():
            if target <= 0:
                reached = await self.count_free_started_repls({header})
                results.append(
                    WarmTargetStatus(
                        header=header,
                        target=target,
                        reached=reached,
                        attempts=0,
                        success=True,
                        error=None,
                    )
                )
                continue
            attempts = 0
            failure: str | None = None
            while True:
                current = await self.count_free_started_repls({header})
                if current >= target:
                    break
                self._ensure_lock()
                assert self._cond is not None
                async with self._cond:
                    total = len(self._free) + len(self._busy)
                    if total >= self.max_repls:
                        failure = (
                            f"Warm pool target '{header}' blocked by max_repls "
                            f"({current}/{target}, max={self.max_repls})"
                        )
                        logger.warning(failure)
                        break
                attempts += 1
                try:
                    repl = await self.get_repl(
                        header=header,
                        snippet_id="warm-pool",
                        timeout=timeout,
                        reuse=False,
                    )
                except Exception as e:
                    failure = f"Warm pool failed to allocate REPL for '{header}': {e}"
                    logger.warning(failure)
                    break
                try:
                    prep = await self.prep(
                        repl,
                        snippet_id="warm-pool",
                        timeout=timeout,
                        debug=False,
                    )
                    if prep and prep.error:
                        failure = f"Warm pool header failed for '{header}': {prep.error}"
                        logger.warning(failure)
                        await self.destroy_repl(repl)
                        break
                except Exception as e:
                    failure = f"Warm pool prep failed for '{header}': {e}"
                    logger.warning(failure)
                    await self.destroy_repl(repl)
                    break
                await self.release_repl(repl)

            reached = await self.count_free_started_repls({header})
            success = reached >= target and failure is None
            if not success and failure is None:
                failure = f"Warm pool target '{header}' not reached ({reached}/{target})"
            if not success and first_failure is None:
                first_failure = failure
            results.append(
                WarmTargetStatus(
                    header=header,
                    target=target,
                    reached=reached,
                    attempts=attempts,
                    success=success,
                    error=failure,
                )
            )

        success = all(result.success for result in results)
        reason = None if success else first_failure or "Warm pool targets were not reached"
        return WarmPoolStatus(success=success, targets=results, reason=reason)

    def drain_startup_stats(self) -> dict[str, int]:
        snapshot = {
            "cold_starts": self._cold_start_count,
            "spawn_failures": self._spawn_failure_count,
        }
        self._cold_start_count = 0
        self._spawn_failure_count = 0
        return snapshot
