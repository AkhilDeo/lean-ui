from __future__ import annotations

import asyncio
import time
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from kimina_client import CheckRequest
from loguru import logger

from ..async_jobs import (
    AsyncBacklogFullError,
    AsyncJobs,
    AsyncPollResponse,
    AsyncQueueMetrics,
    AsyncSubmitResponse,
)
from ..auth import require_key
from ..request_policy import normalize_check_request
from ..settings import Settings
from .check import get_runtime_settings

router = APIRouter()


def get_async_jobs(request: Request) -> AsyncJobs:
    jobs = getattr(request.app.state, "async_jobs", None)
    if jobs is None:
        logger.warning("Async API requested but async jobs backend is not configured")
        raise HTTPException(
            status_code=503,
            detail="Async queue API is not enabled on this service",
        )
    return cast(AsyncJobs, jobs)


@router.post(
    "/async/check",
    response_model=AsyncSubmitResponse,
    response_model_exclude_none=True,
)
@router.post(
    "/async/check/",
    response_model=AsyncSubmitResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def submit_async_check(
    payload: CheckRequest,
    request: Request,
    jobs: AsyncJobs = Depends(get_async_jobs),
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> AsyncSubmitResponse:
    normalized_payload = normalize_check_request(payload, runtime_settings)
    settings = getattr(request.app.state, "settings", runtime_settings)
    soft_limit = int(getattr(settings, "async_admission_queue_limit", 0) or 0)
    if soft_limit > 0:
        metrics = await jobs.metrics()
        projected_depth = metrics.queue_depth + len(normalized_payload.snippets)
        if projected_depth > soft_limit:
            logger.bind(endpoint="api.async.submit").warning(
                "Async submit rejected (admission queue soft limit): queue_depth={} incoming={} projected={} soft_limit={}",
                metrics.queue_depth,
                len(normalized_payload.snippets),
                projected_depth,
                soft_limit,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    "Async queue admission soft limit exceeded "
                    f"({projected_depth} > {soft_limit})"
                ),
            )

    logger.bind(endpoint="api.async.submit").debug(
        "Async submit received: snippets={} timeout={} debug={} reuse={} infotree={}",
        len(normalized_payload.snippets),
        normalized_payload.timeout,
        normalized_payload.debug,
        normalized_payload.reuse,
        normalized_payload.infotree,
    )
    try:
        response = await jobs.submit(normalized_payload)
        autoscaler = getattr(request.app.state, "autoscaler", None)
        if autoscaler is not None:
            await autoscaler.notify_submit()
        logger.bind(
            endpoint="api.async.submit",
            job_id=response.job_id,
        ).debug(
            "Async submit accepted: job_id={} total_snippets={} expires_at={}",
            response.job_id,
            response.total_snippets,
            response.expires_at,
        )
        return response
    except AsyncBacklogFullError as e:
        logger.bind(endpoint="api.async.submit").warning(
            "Async submit rejected (backlog full): {}",
            e,
        )
        raise HTTPException(status_code=429, detail=str(e)) from e


@router.get(
    "/async/check/{job_id}",
    response_model=AsyncPollResponse,
    response_model_exclude_none=True,
)
@router.get(
    "/async/check/{job_id}/",
    response_model=AsyncPollResponse,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def get_async_check_status(
    job_id: str,
    wait_sec: float = Query(
        default=0.0,
        ge=0.0,
        le=60.0,
        description=(
            "Optional long-poll wait duration in seconds. "
            "When >0, API will poll until terminal state or timeout."
        ),
    ),
    jobs: AsyncJobs = Depends(get_async_jobs),
    _: str = Depends(require_key),
) -> AsyncPollResponse:
    with logger.contextualize(job_id=job_id, endpoint="api.async.poll"):
        poll = await jobs.poll(job_id)
        if wait_sec > 0 and poll is not None:
            deadline = time.perf_counter() + wait_sec
            while (
                time.perf_counter() < deadline
                and str(getattr(poll.status, "value", poll.status)).lower()
                in {"queued", "running"}
            ):
                await asyncio.sleep(0.5)
                next_poll = await jobs.poll(job_id)
                if next_poll is None:
                    poll = None
                    break
                poll = next_poll
        if poll is None:
            logger.warning("Async poll miss: job_id={}", job_id)
            raise HTTPException(status_code=404, detail="Async job not found or expired")
        logger.debug(
            "Async poll: job_id={} status={} done={} failed={} running={} total={} has_results={}",
            poll.job_id,
            poll.status,
            poll.progress.done,
            poll.progress.failed,
            poll.progress.running,
            poll.progress.total,
            poll.results is not None,
        )
        return poll


@router.get(
    "/async/metrics",
    response_model=AsyncQueueMetrics,
    response_model_exclude_none=True,
)
@router.get(
    "/async/metrics/",
    response_model=AsyncQueueMetrics,
    response_model_exclude_none=True,
    include_in_schema=False,
)
async def get_async_metrics(
    request: Request,
    jobs: AsyncJobs = Depends(get_async_jobs),
    _: str = Depends(require_key),
) -> AsyncQueueMetrics:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and not bool(getattr(settings, "async_metrics_enabled", True)):
        raise HTTPException(status_code=404, detail="Async metrics endpoint is disabled")

    metrics = await jobs.metrics()
    alert_max_age = int(getattr(settings, "async_alert_max_oldest_queued_age_sec", 60) or 0)
    if alert_max_age > 0 and metrics.oldest_queued_age_sec > alert_max_age:
        logger.bind(endpoint="api.async.metrics").warning(
            "Async queue age alert: oldest_queued_age_sec={:.3f} threshold_sec={}",
            metrics.oldest_queued_age_sec,
            alert_max_age,
        )
    if metrics.queue_depth > 0 and metrics.running_tasks == 0:
        logger.bind(endpoint="api.async.metrics").warning(
            "Async queue progress alert: queue_depth={} running_tasks=0 inflight_jobs={}",
            metrics.queue_depth,
            metrics.inflight_jobs,
        )
    logger.bind(endpoint="api.async.metrics").debug(
        "Async metrics: queue_depth={} inflight_jobs={} running_tasks={} oldest_queued_age_sec={:.3f} dequeue_rate={:.3f} enqueue_rate={:.3f}",
        metrics.queue_depth,
        metrics.inflight_jobs,
        metrics.running_tasks,
        metrics.oldest_queued_age_sec,
        metrics.dequeue_rate,
        metrics.enqueue_rate,
    )
    return metrics
