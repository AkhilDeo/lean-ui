from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from kimina_client import CheckRequest
from loguru import logger

from ..async_jobs import (
    AsyncBacklogFullError,
    AsyncJobs,
    AsyncPollResponse,
    AsyncSubmitResponse,
)
from ..auth import require_key

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
    request: CheckRequest,
    jobs: AsyncJobs = Depends(get_async_jobs),
    _: str = Depends(require_key),
) -> AsyncSubmitResponse:
    logger.info(
        "Async submit received: snippets={} timeout={} debug={} reuse={} infotree={}",
        len(request.snippets),
        request.timeout,
        request.debug,
        request.reuse,
        request.infotree,
    )
    try:
        response = await jobs.submit(request)
        logger.info(
            "Async submit accepted: job_id={} total_snippets={} expires_at={}",
            response.job_id,
            response.total_snippets,
            response.expires_at,
        )
        return response
    except AsyncBacklogFullError as e:
        logger.warning("Async submit rejected (backlog full): {}", e)
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
    jobs: AsyncJobs = Depends(get_async_jobs),
    _: str = Depends(require_key),
) -> AsyncPollResponse:
    poll = await jobs.poll(job_id)
    if poll is None:
        logger.warning("Async poll miss: job_id={}", job_id)
        raise HTTPException(status_code=404, detail="Async job not found or expired")
    logger.info(
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
