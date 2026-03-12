from __future__ import annotations

import asyncio
import time
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from kimina_client import CheckRequest
from loguru import logger

from ..async_jobs import (
    AsyncBacklogFullError,
    AsyncEnvironmentMetrics,
    AsyncJobs,
    AsyncPollResponse,
    AsyncQueueMetrics,
    AsyncSubmitResponse,
)
from ..auth import require_key
from ..environment_registry import (
    build_environment_registry,
    environment_headers,
    find_environment_by_id,
    resolve_environment_selection,
)
from ..gateway_proxy import (
    proxy_async_metrics_request,
    proxy_async_poll_request,
    proxy_async_submit_request,
)
from ..request_policy import normalize_check_request
from ..settings import Settings
from .check import get_runtime_settings

router = APIRouter()
ASYNC_JOB_ID_SEPARATOR = ":"


def _wrap_job_id(environment_id: str, job_id: str) -> str:
    return f"{environment_id}{ASYNC_JOB_ID_SEPARATOR}{job_id}"


def _unwrap_job_id(job_id: str, runtime_settings: Settings) -> tuple[str, str]:
    if ASYNC_JOB_ID_SEPARATOR not in job_id:
        return runtime_settings.environment_id, job_id
    environment_id, raw_job_id = job_id.split(ASYNC_JOB_ID_SEPARATOR, 1)
    if not raw_job_id:
        raise HTTPException(status_code=400, detail="Malformed async job id.")
    environment = find_environment_by_id(
        build_environment_registry(runtime_settings), environment_id
    )
    if environment is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown environment in async job id '{environment_id}'.",
        )
    return environment_id, raw_job_id


def _set_environment_headers(response: Response, settings: Settings, environment_id: str) -> None:
    environment = find_environment_by_id(build_environment_registry(settings), environment_id)
    if environment is None:
        return
    for key, value in environment_headers(environment).items():
        response.headers[key] = value


def _aggregate_metrics(
    metrics_by_environment: dict[str, AsyncQueueMetrics],
    *,
    include_environments: bool,
) -> AsyncQueueMetrics:
    aggregate = AsyncQueueMetrics(
        queue_depth=sum(item.queue_depth for item in metrics_by_environment.values()),
        inflight_jobs=sum(item.inflight_jobs for item in metrics_by_environment.values()),
        running_tasks=sum(item.running_tasks for item in metrics_by_environment.values()),
        oldest_queued_age_sec=max(
            (item.oldest_queued_age_sec for item in metrics_by_environment.values()),
            default=0.0,
        ),
        dequeue_rate=sum(item.dequeue_rate for item in metrics_by_environment.values()),
        enqueue_rate=sum(item.enqueue_rate for item in metrics_by_environment.values()),
        environments=(
            {
                environment_id: AsyncEnvironmentMetrics(
                    queue_depth=item.queue_depth,
                    inflight_jobs=item.inflight_jobs,
                    running_tasks=item.running_tasks,
                    oldest_queued_age_sec=item.oldest_queued_age_sec,
                    dequeue_rate=item.dequeue_rate,
                    enqueue_rate=item.enqueue_rate,
                )
                for environment_id, item in metrics_by_environment.items()
            }
            if include_environments
            else None
        ),
    )
    return aggregate


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
    raw_response: Response,
    jobs: AsyncJobs = Depends(get_async_jobs),
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> AsyncSubmitResponse:
    normalized_payload = normalize_check_request(payload, runtime_settings)
    selection = resolve_environment_selection(
        requested_environment=normalized_payload.environment,
        snippets=normalized_payload.snippets,
        settings=runtime_settings,
    )
    resolved_environment = selection.resolved_environment
    _set_environment_headers(raw_response, runtime_settings, resolved_environment.id)
    settings = getattr(request.app.state, "settings", runtime_settings)
    soft_limit = int(getattr(settings, "async_admission_queue_limit", 0) or 0)
    if soft_limit > 0 and resolved_environment.id == runtime_settings.environment_id:
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
        if resolved_environment.id != runtime_settings.environment_id:
            response = await proxy_async_submit_request(
                request=normalized_payload,
                target_environment=resolved_environment,
                settings=runtime_settings,
            )
        else:
            response = await jobs.submit(normalized_payload)
        response = response.model_copy(
            update={"job_id": _wrap_job_id(resolved_environment.id, response.job_id)}
        )
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
    raw_response: Response,
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
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> AsyncPollResponse:
    environment_id, raw_job_id = _unwrap_job_id(job_id, runtime_settings)
    _set_environment_headers(raw_response, runtime_settings, environment_id)

    if environment_id != runtime_settings.environment_id:
        environment = find_environment_by_id(
            build_environment_registry(runtime_settings), environment_id
        )
        if environment is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown environment in async job id '{environment_id}'.",
            )
        poll = await proxy_async_poll_request(
            job_id=raw_job_id,
            wait_sec=wait_sec,
            target_environment=environment,
            settings=runtime_settings,
        )
        return poll.model_copy(update={"job_id": _wrap_job_id(environment_id, raw_job_id)})

    with logger.contextualize(job_id=raw_job_id, endpoint="api.async.poll"):
        poll = await jobs.poll(raw_job_id)
        if wait_sec > 0 and poll is not None:
            deadline = time.perf_counter() + wait_sec
            while (
                time.perf_counter() < deadline
                and str(getattr(poll.status, "value", poll.status)).lower()
                in {"queued", "running"}
            ):
                await asyncio.sleep(0.5)
                next_poll = await jobs.poll(raw_job_id)
                if next_poll is None:
                    poll = None
                    break
                poll = next_poll
        if poll is None:
            logger.warning("Async poll miss: job_id={}", raw_job_id)
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
        return poll.model_copy(update={"job_id": _wrap_job_id(environment_id, raw_job_id)})


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
    include_environments: bool = Query(
        default=False,
        description="Include a per-environment metrics breakdown when this service is acting as a gateway.",
    ),
    jobs: AsyncJobs = Depends(get_async_jobs),
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> AsyncQueueMetrics:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and not bool(getattr(settings, "async_metrics_enabled", True)):
        raise HTTPException(status_code=404, detail="Async metrics endpoint is disabled")

    registry = build_environment_registry(runtime_settings)
    remote_environments = [
        environment
        for environment in registry
        if environment.id != runtime_settings.environment_id and environment.url
    ]
    if not remote_environments:
        metrics = await jobs.metrics()
        if include_environments:
            metrics.environments = {
                runtime_settings.environment_id: AsyncEnvironmentMetrics(
                    queue_depth=metrics.queue_depth,
                    inflight_jobs=metrics.inflight_jobs,
                    running_tasks=metrics.running_tasks,
                    oldest_queued_age_sec=metrics.oldest_queued_age_sec,
                    dequeue_rate=metrics.dequeue_rate,
                    enqueue_rate=metrics.enqueue_rate,
                )
            }
        return metrics

    metrics_by_environment: dict[str, AsyncQueueMetrics] = {
        runtime_settings.environment_id: await jobs.metrics()
    }
    for environment in remote_environments:
        metrics_by_environment[environment.id] = await proxy_async_metrics_request(
            include_environments=False,
            target_environment=environment,
            settings=runtime_settings,
        )
    metrics = _aggregate_metrics(
        metrics_by_environment,
        include_environments=include_environments,
    )
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
