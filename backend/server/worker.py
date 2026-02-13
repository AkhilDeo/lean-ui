from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException
from loguru import logger

from .async_jobs import AsyncJobs, create_async_jobs
from .logger import setup_logging
from .manager import Manager
from .routers.check import run_checks
from .settings import Settings


def _is_transient_http_exception(error: HTTPException) -> bool:
    return error.status_code in {429, 500, 502, 503, 504}


async def process_task(
    jobs: AsyncJobs,
    manager: Manager,
    task_timeout_sec: int,
    worker_retries: int = 3,
) -> bool:
    task = await jobs.dequeue_task(timeout_sec=task_timeout_sec)
    if task is None:
        return False

    with logger.contextualize(
        job_id=task.job_id,
        task_id=task.task_id,
        snippet_id=task.snippet.id,
    ):
        started_at = time.perf_counter()
        logger.info(
            "Worker dequeued async task: job_id={} task_id={} index={} snippet_id={} timeout={} debug={} reuse={}",
            task.job_id,
            task.task_id,
            task.index,
            task.snippet.id,
            task.timeout,
            task.debug,
            task.reuse,
        )
        await jobs.mark_task_started(task)
        for attempt in range(1, worker_retries + 1):
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
                logger.info(
                    "Worker completed async task: job_id={} task_id={} index={} snippet_id={} attempt={} elapsed_sec={:.3f}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    time.perf_counter() - started_at,
                )
                break
            except HTTPException as e:
                if _is_transient_http_exception(e) and attempt < worker_retries:
                    logger.warning(
                        "Worker transient HTTPException, retrying: job_id={} task_id={} index={} snippet_id={} attempt={}/{} status_code={} detail={}",
                        task.job_id,
                        task.task_id,
                        task.index,
                        task.snippet.id,
                        attempt,
                        worker_retries,
                        e.status_code,
                        e.detail,
                    )
                    continue

                await jobs.mark_task_failure(task, str(e.detail), task.snippet.id)
                logger.warning(
                    "Worker task failed with HTTPException: job_id={} task_id={} index={} snippet_id={} attempt={}/{} detail={} elapsed_sec={:.3f}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    worker_retries,
                    e.detail,
                    time.perf_counter() - started_at,
                )
                break
            except Exception as e:
                logger.exception("Worker failed processing async task {}: {}", task.task_id, e)
                await jobs.mark_task_failure(task, f"worker_error: {e}", task.snippet.id)
                logger.error(
                    "Worker task failed with unexpected error: job_id={} task_id={} index={} snippet_id={} attempt={}/{} elapsed_sec={:.3f}",
                    task.job_id,
                    task.task_id,
                    task.index,
                    task.snippet.id,
                    attempt,
                    worker_retries,
                    time.perf_counter() - started_at,
                )
                break
    return True


async def run_worker(settings: Settings | None = None) -> None:
    cfg = settings or Settings()
    if not cfg.async_enabled:
        raise RuntimeError("Worker requires LEAN_SERVER_ASYNC_ENABLED=true")

    jobs = await create_async_jobs(cfg)
    manager = Manager(
        max_repls=cfg.max_repls,
        max_repl_uses=cfg.max_repl_uses,
        max_repl_mem=cfg.max_repl_mem,
        init_repls=cfg.init_repls,
        min_host_free_mem=cfg.min_host_free_mem,
    )

    logger.info(
        "Async worker started. queue={} max_repls={} max_repl_mem_mb={} min_host_free_mem_mb={} max_repl_uses={}",
        cfg.async_queue_name,
        cfg.max_repls,
        cfg.max_repl_mem,
        cfg.min_host_free_mem,
        cfg.max_repl_uses,
    )
    try:
        while True:
            await process_task(
                jobs=jobs,
                manager=manager,
                task_timeout_sec=3,
                worker_retries=cfg.async_worker_retries,
            )
    except asyncio.CancelledError:
        logger.info("Worker cancelled")
        raise
    finally:
        await manager.cleanup()
        await jobs.close()


def main() -> None:
    setup_logging()
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
