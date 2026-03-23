from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException
from loguru import logger

from .async_jobs import AsyncJobs, create_async_jobs
from .async_tiering import AsyncQueueTier, warm_repl_targets_for_tier
from .logger import setup_logging
from .manager import Manager
from .routers.check import run_checks
from .settings import Settings


TRANSIENT_FAILURE_REASONS: tuple[tuple[str, str], ...] = (
    ("resource temporarily unavailable", "resource_unavailable"),
    ("std::bad_alloc", "bad_alloc"),
    ("repl returned empty stdout", "empty_stdout"),
    ("broken pipe", "broken_pipe"),
    ("failed to read from repl stdout", "stdout_read_failure"),
    ("failed to start repl", "repl_start_failure"),
    ("no available repls", "no_available_repls"),
)

SPAWN_FAILURE_REASONS = {"resource_unavailable", "bad_alloc", "repl_start_failure"}


@dataclass(frozen=True)
class AsyncWorkerPolicy:
    worker_queue_tier: AsyncQueueTier
    light_retry_attempts: int
    heavy_retry_attempts: int
    retry_backoff_initial_ms: int
    retry_backoff_max_ms: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "AsyncWorkerPolicy":
        return cls(
            worker_queue_tier=AsyncQueueTier(settings.async_worker_queue_tier),
            light_retry_attempts=settings.async_light_retry_attempts,
            heavy_retry_attempts=settings.async_heavy_retry_attempts,
            retry_backoff_initial_ms=settings.async_retry_backoff_initial_ms,
            retry_backoff_max_ms=settings.async_retry_backoff_max_ms,
        )

    def attempts_for_tier(self, queue_tier: str) -> int:
        if queue_tier == AsyncQueueTier.heavy.value:
            return self.heavy_retry_attempts
        return self.light_retry_attempts


class AsyncCircuitBreaker:
    def __init__(self, *, window: int, failure_rate: float, pause_sec: int) -> None:
        self.window = window
        self.failure_rate = failure_rate
        self.pause_sec = pause_sec
        self._history: deque[bool] = deque(maxlen=window)
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    async def note_attempt(self, transient_failure: bool) -> None:
        async with self._lock:
            self._history.append(transient_failure)
            if len(self._history) < self.window:
                return
            ratio = sum(1 for item in self._history if item) / len(self._history)
            if ratio > self.failure_rate:
                self._paused_until = max(
                    self._paused_until,
                    time.monotonic() + self.pause_sec,
                )

    async def pause_remaining_sec(self) -> float:
        async with self._lock:
            remaining = self._paused_until - time.monotonic()
            return max(remaining, 0.0)


def _normalize_failure_reason(message: str) -> str | None:
    text = message.strip().lower()
    for needle, reason in TRANSIENT_FAILURE_REASONS:
        if needle in text:
            return reason
    return None


def _is_retryable_http_exception(error: HTTPException) -> bool:
    return error.status_code in {429, 500, 502, 503, 504}


async def _record_manager_metrics(
    jobs: AsyncJobs,
    manager: Manager,
    queue_tier: AsyncQueueTier,
    warm_targets: dict[str, int],
    runtime_id: str,
) -> None:
    stats = manager.drain_startup_stats()
    warm_repls = await manager.count_free_started_repls(set(warm_targets))
    await jobs.record_worker_metrics(
        queue_tier=queue_tier,
        runtime_id=runtime_id,
        warm_repls=warm_repls,
        cold_starts=stats["cold_starts"],
        spawn_failures=stats["spawn_failures"],
    )


async def _maybe_pause_for_circuit_breaker(
    *,
    breaker: AsyncCircuitBreaker | None,
    jobs: AsyncJobs,
    manager: Manager,
    queue_tier: AsyncQueueTier,
    warm_targets: dict[str, int],
    runtime_id: str,
) -> None:
    if breaker is None:
        return
    remaining = await breaker.pause_remaining_sec()
    if remaining <= 0:
        return
    logger.warning(
        "Async worker circuit breaker active: tier={} pause_sec={:.3f}",
        queue_tier.value,
        remaining,
    )
    await manager.ensure_warm_repls(warm_targets, timeout=60.0)
    await _record_manager_metrics(jobs, manager, queue_tier, warm_targets, runtime_id)
    await asyncio.sleep(remaining)


async def process_task(
    jobs: AsyncJobs,
    manager: Manager,
    task_timeout_sec: int,
    worker_retries: int = 3,
    consumer_id: int = 0,
    policy: AsyncWorkerPolicy | None = None,
    circuit_breaker: AsyncCircuitBreaker | None = None,
    runtime_id: str | None = None,
) -> bool:
    effective_policy = policy or AsyncWorkerPolicy.from_settings(Settings())
    task = await jobs.dequeue_task(
        timeout_sec=task_timeout_sec,
        queue_tier=effective_policy.worker_queue_tier,
        runtime_id=runtime_id,
    )
    if task is None:
        return False

    queue_tier = AsyncQueueTier(task.queue_tier)
    attempts = (
        worker_retries
        if worker_retries != 3
        else effective_policy.attempts_for_tier(task.queue_tier)
    )

    with logger.contextualize(
        job_id=task.job_id,
        task_id=task.task_id,
        snippet_id=task.snippet.id,
        consumer_id=consumer_id,
        queue_tier=queue_tier.value,
    ):
        started_at = time.perf_counter()
        logger.debug(
            "Worker dequeued async task: consumer_id={} job_id={} task_id={} index={} snippet_id={} timeout={} debug={} reuse={} queue_tier={}",
            consumer_id,
            task.job_id,
            task.task_id,
            task.index,
            task.snippet.id,
            task.timeout,
            task.debug,
            task.reuse,
            queue_tier.value,
        )
        await jobs.mark_task_started(task)
        for attempt in range(1, attempts + 1):
            task.retry_count = attempt - 1
            task.failure_reason = None
            try:
                responses = await run_checks(
                    [task.snippet],
                    timeout=task.timeout,
                    debug=task.debug,
                    manager=manager,
                    reuse=task.reuse,
                    infotree=task.infotree,
                )
                await jobs.mark_task_success(task, responses[0])
                if circuit_breaker is not None:
                    await circuit_breaker.note_attempt(False)
                logger.debug(
                    "Worker completed async task: consumer_id={} job_id={} task_id={} index={} snippet_id={} attempt={} elapsed_sec={:.3f}",
                    consumer_id,
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    time.perf_counter() - started_at,
                )
                return True
            except HTTPException as e:
                failure_reason = _normalize_failure_reason(str(e.detail))
                is_retryable = _is_retryable_http_exception(e) and failure_reason is not None
                if is_retryable and attempt < attempts:
                    # Spawn failures are systemic; stop retrying early
                    if failure_reason in SPAWN_FAILURE_REASONS and attempt >= 2:
                        logger.warning(
                            "Worker spawn failure early exit: consumer_id={} task_id={} failure_reason={} attempt={}",
                            consumer_id,
                            task.task_id,
                            failure_reason,
                            attempt,
                        )
                        task.failure_reason = failure_reason
                        task.retry_count = attempt
                        await jobs.record_worker_metrics(
                            queue_tier=queue_tier,
                            runtime_id=task.runtime_id,
                            exhausted_retries=1,
                            failure_reason=failure_reason,
                        )
                        if circuit_breaker is not None:
                            await circuit_breaker.note_attempt(True)
                        await jobs.mark_task_failure(task, str(e.detail), task.snippet.id)
                        return True

                    task.failure_reason = failure_reason
                    task.retry_count = attempt
                    await jobs.record_worker_metrics(
                        queue_tier=queue_tier,
                        runtime_id=task.runtime_id,
                        retries=1,
                        failure_reason=failure_reason,
                    )
                    if circuit_breaker is not None:
                        await circuit_breaker.note_attempt(True)
                    if failure_reason in SPAWN_FAILURE_REASONS:
                        cooldown = 5.0
                    else:
                        backoff = min(
                            effective_policy.retry_backoff_initial_ms * (2 ** (attempt - 1)),
                            effective_policy.retry_backoff_max_ms,
                        )
                        cooldown = random.uniform(0.0, backoff / 1000.0)
                    logger.warning(
                        "Worker transient HTTPException, retrying: consumer_id={} job_id={} task_id={} index={} snippet_id={} attempt={}/{} status_code={} detail={} failure_reason={} backoff_sec={:.3f}",
                        consumer_id,
                        task.job_id,
                        task.task_id,
                        task.index,
                        task.snippet.id,
                        attempt,
                        attempts,
                        e.status_code,
                        e.detail,
                        failure_reason,
                        cooldown,
                    )
                    await asyncio.sleep(cooldown)
                    continue

                task.failure_reason = failure_reason
                task.retry_count = attempt - 1 if failure_reason is None else attempt
                await jobs.record_worker_metrics(
                    queue_tier=queue_tier,
                    runtime_id=task.runtime_id,
                    exhausted_retries=1 if failure_reason is not None else 0,
                    failure_reason=failure_reason,
                )
                if circuit_breaker is not None:
                    await circuit_breaker.note_attempt(failure_reason is not None)
                await jobs.mark_task_failure(task, str(e.detail), task.snippet.id)
                logger.warning(
                    "Worker task failed with HTTPException: consumer_id={} job_id={} task_id={} index={} snippet_id={} attempt={}/{} detail={} failure_reason={} elapsed_sec={:.3f}",
                    consumer_id,
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    attempts,
                    e.detail,
                    failure_reason,
                    time.perf_counter() - started_at,
                )
                return True
            except Exception as e:
                detail = f"worker_error: {e}"
                failure_reason = _normalize_failure_reason(detail)
                if failure_reason is not None and attempt < attempts:
                    # Spawn failures are systemic; stop retrying early
                    if failure_reason in SPAWN_FAILURE_REASONS and attempt >= 2:
                        logger.warning(
                            "Worker spawn failure early exit: consumer_id={} task_id={} failure_reason={} attempt={}",
                            consumer_id,
                            task.task_id,
                            failure_reason,
                            attempt,
                        )
                        task.failure_reason = failure_reason
                        task.retry_count = attempt
                        await jobs.record_worker_metrics(
                            queue_tier=queue_tier,
                            runtime_id=task.runtime_id,
                            exhausted_retries=1,
                            failure_reason=failure_reason,
                        )
                        if circuit_breaker is not None:
                            await circuit_breaker.note_attempt(True)
                        await jobs.mark_task_failure(task, detail, task.snippet.id)
                        return True

                    task.failure_reason = failure_reason
                    task.retry_count = attempt
                    await jobs.record_worker_metrics(
                        queue_tier=queue_tier,
                        runtime_id=task.runtime_id,
                        retries=1,
                        failure_reason=failure_reason,
                    )
                    if circuit_breaker is not None:
                        await circuit_breaker.note_attempt(True)
                    if failure_reason in SPAWN_FAILURE_REASONS:
                        cooldown = 5.0
                    else:
                        backoff = min(
                            effective_policy.retry_backoff_initial_ms * (2 ** (attempt - 1)),
                            effective_policy.retry_backoff_max_ms,
                        )
                        cooldown = random.uniform(0.0, backoff / 1000.0)
                    logger.warning(
                        "Worker transient exception, retrying: consumer_id={} task_id={} attempt={}/{} failure_reason={} error={} backoff_sec={:.3f}",
                        consumer_id,
                        task.task_id,
                        attempt,
                        attempts,
                        failure_reason,
                        e,
                        cooldown,
                    )
                    await asyncio.sleep(cooldown)
                    continue

                logger.exception(
                    "Worker failed processing async task: consumer_id={} task_id={} error={}",
                    consumer_id,
                    task.task_id,
                    e,
                )
                task.failure_reason = failure_reason
                task.retry_count = attempt - 1 if failure_reason is None else attempt
                await jobs.record_worker_metrics(
                    queue_tier=queue_tier,
                    runtime_id=task.runtime_id,
                    exhausted_retries=1 if failure_reason is not None else 0,
                    failure_reason=failure_reason,
                )
                if circuit_breaker is not None:
                    await circuit_breaker.note_attempt(failure_reason is not None)
                await jobs.mark_task_failure(task, detail, task.snippet.id)
                logger.error(
                    "Worker task failed with unexpected error: consumer_id={} job_id={} task_id={} index={} snippet_id={} attempt={}/{} failure_reason={} elapsed_sec={:.3f}",
                    consumer_id,
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    attempts,
                    failure_reason,
                    time.perf_counter() - started_at,
                )
                return True
    return True


async def _warm_pool_loop(
    *,
    jobs: AsyncJobs,
    manager: Manager,
    policy: AsyncWorkerPolicy,
    warm_targets: dict[str, int],
    runtime_id: str,
) -> None:
    queue_tier = (
        policy.worker_queue_tier
        if policy.worker_queue_tier != AsyncQueueTier.all
        else AsyncQueueTier.light
    )
    while True:
        try:
            if warm_targets:
                await manager.ensure_warm_repls(warm_targets, timeout=60.0)
            await _record_manager_metrics(
                jobs, manager, queue_tier, warm_targets, runtime_id
            )
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Warm pool loop error: {}", exc)
            await asyncio.sleep(1.0)


async def _consumer_loop(
    *,
    consumer_id: int,
    jobs: AsyncJobs,
    manager: Manager,
    task_timeout_sec: int,
    worker_retries: int,
    policy: AsyncWorkerPolicy,
    circuit_breaker: AsyncCircuitBreaker | None,
    warm_targets: dict[str, int],
    runtime_id: str,
) -> None:
    queue_tier = (
        policy.worker_queue_tier
        if policy.worker_queue_tier != AsyncQueueTier.all
        else AsyncQueueTier.light
    )
    while True:
        try:
            await _maybe_pause_for_circuit_breaker(
                breaker=circuit_breaker,
                jobs=jobs,
                manager=manager,
                queue_tier=queue_tier,
                warm_targets=warm_targets,
                runtime_id=runtime_id,
            )
            did_work = await process_task(
                jobs=jobs,
                manager=manager,
                task_timeout_sec=task_timeout_sec,
                worker_retries=worker_retries,
                consumer_id=consumer_id,
                policy=policy,
                circuit_breaker=circuit_breaker,
                runtime_id=runtime_id,
            )
            await _record_manager_metrics(
                jobs, manager, queue_tier, warm_targets, runtime_id
            )
            if not did_work:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            logger.debug("Worker consumer cancelled: consumer_id={}", consumer_id)
            raise
        except Exception as exc:
            logger.exception(
                "Worker consumer loop error: consumer_id={} error={}",
                consumer_id,
                exc,
            )
            await asyncio.sleep(0.25)


async def run_worker(
    settings: Settings | None = None,
    *,
    jobs: AsyncJobs | None = None,
    manager: Manager | None = None,
    manage_resources: bool = True,
) -> None:
    cfg = settings or Settings()
    if not cfg.async_enabled:
        raise RuntimeError("Worker requires LEAN_SERVER_ASYNC_ENABLED=true")

    owned_jobs = jobs is None
    jobs = jobs or await create_async_jobs(cfg)
    recovered_tasks = await jobs.recover_running_tasks()
    if recovered_tasks:
        logger.warning("Recovered async tasks before worker start: {}", recovered_tasks)
    owned_manager = manager is None
    manager = manager or Manager(
        max_repls=cfg.max_repls,
        max_repl_uses=cfg.max_repl_uses,
        max_repl_mem=cfg.max_repl_mem,
        init_repls=cfg.init_repls,
        min_host_free_mem=cfg.min_host_free_mem,
        startup_concurrency_limit=cfg.async_startup_concurrency_limit,
    )
    configured_concurrency = cfg.async_worker_concurrency or cfg.max_repls
    worker_concurrency = max(1, min(configured_concurrency, cfg.max_repls))
    policy = AsyncWorkerPolicy.from_settings(cfg)
    circuit_breaker = AsyncCircuitBreaker(
        window=cfg.async_circuit_breaker_window,
        failure_rate=cfg.async_circuit_breaker_failure_rate,
        pause_sec=cfg.async_circuit_breaker_pause_sec,
    )
    warm_targets = warm_repl_targets_for_tier(cfg, policy.worker_queue_tier)

    logger.info(
        "Async worker started. queue_tier={} max_repls={} worker_concurrency={} configured_worker_concurrency={} max_repl_mem_mb={} min_host_free_mem_mb={} max_repl_uses={} light_retry_attempts={} heavy_retry_attempts={}",
        policy.worker_queue_tier.value,
        cfg.max_repls,
        worker_concurrency,
        cfg.async_worker_concurrency,
        cfg.max_repl_mem,
        cfg.min_host_free_mem,
        cfg.max_repl_uses,
        cfg.async_light_retry_attempts,
        cfg.async_heavy_retry_attempts,
    )
    warm_task = asyncio.create_task(
        _warm_pool_loop(
            jobs=jobs,
            manager=manager,
            policy=policy,
            warm_targets=warm_targets,
            runtime_id=cfg.runtime_id,
        ),
        name="async-worker-warm-pool",
    )
    consumers = [
        asyncio.create_task(
            _consumer_loop(
                consumer_id=i + 1,
                jobs=jobs,
                manager=manager,
                task_timeout_sec=3,
                worker_retries=cfg.async_worker_retries,
                policy=policy,
                circuit_breaker=circuit_breaker,
                warm_targets=warm_targets,
                runtime_id=cfg.runtime_id,
            ),
            name=f"async-worker-consumer-{i + 1}",
        )
        for i in range(worker_concurrency)
    ]
    try:
        await asyncio.gather(*consumers)
    except asyncio.CancelledError:
        logger.info("Worker cancelled")
        raise
    finally:
        warm_task.cancel()
        await asyncio.gather(warm_task, return_exceptions=True)
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        if manage_resources and owned_manager:
            await manager.cleanup()
        if manage_resources and owned_jobs:
            await jobs.close()


def main() -> None:
    cfg = Settings()
    setup_logging(cfg)
    asyncio.run(run_worker(cfg))


if __name__ == "__main__":
    main()
