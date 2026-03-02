#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiohttp

RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
TERMINAL_ASYNC_STATES = {"completed", "failed", "expired"}
DEFAULT_CODE_FIELDS = ("prompt", "code", "proof", "lean_code", "snippet", "text")
FAILED_LABEL_POLICIES = {"strict_invalid", "transport_failure", "split_by_error"}
RAMP_HOLD_SECONDS = {25: 120.0, 50: 180.0, 100: 180.0, 250: 300.0}


@dataclass(frozen=True)
class Case:
    case_id: str
    code: str
    expected_valid: bool
    source: str
    label_kind: str = "semantic_valid"
    verification_error: str | None = None


@dataclass
class EvalRow:
    mode: str
    tier_concurrency: int
    case_id: str
    expected_valid: bool
    predicted_valid: bool
    system_error: bool
    latency_ms: float
    http_status: int
    detail: str | None = None
    completed: bool = False
    poll_timeout: bool = False
    queue_stall_detected: bool = False
    label_kind: str = "semantic_valid"


@dataclass
class DatasetStats:
    path: str
    total_lines: int = 0
    parsed_rows: int = 0
    malformed_rows: int = 0
    missing_code_rows: int = 0
    empty_code_rows: int = 0


@dataclass
class Thresholds:
    sync_p99_target_ms: float = 5000.0
    sync_max_system_error_rate: float = 0.01
    sync_max_429_rate: float = 0.01
    async_max_system_error_rate: float = 0.005
    async_poll_timeout_rate: float = 0.0
    async_min_completion_rate: float = 0.995
    min_valid_tpr: float = 0.99
    min_invalid_tnr: float = 0.98
    min_overall_accuracy: float = 0.99
    required_async_concurrency: int = 2000


@dataclass
class SevereThresholds:
    system_error_rate: float = 0.20
    http_429_rate: float = 0.20
    async_poll_timeout_rate: float = 0.05


@dataclass
class AsyncMetricsSnapshot:
    timestamp_epoch_s: float
    queue_depth: int | None
    inflight_jobs: int | None
    running_tasks: int | None
    oldest_queued_age_sec: float | None
    dequeue_rate: float | None
    enqueue_rate: float | None
    global_done: int
    global_running: int
    error: str | None = None


class AsyncProgressTracker:
    def __init__(self) -> None:
        self._state: dict[str, tuple[str, int, int, int]] = {}
        self._lock = asyncio.Lock()

    async def observe(self, *, job_id: str, status: str, done: int, failed: int, running: int) -> None:
        async with self._lock:
            self._state[job_id] = (status, max(done, 0), max(failed, 0), max(running, 0))

    async def snapshot(self) -> tuple[int, int]:
        async with self._lock:
            global_done = sum(done + failed for _, done, failed, _ in self._state.values())
            global_running = sum(running for _, _, _, running in self._state.values())
            return global_done, global_running


def parse_levels(raw: str) -> tuple[int, ...]:
    levels = tuple(int(x.strip()) for x in raw.split(",") if x.strip())
    if not levels:
        raise ValueError("No concurrency levels provided.")
    if any(level <= 0 for level in levels):
        raise ValueError("Concurrency levels must be > 0.")
    return levels


def parse_ramp_schedule(raw: str) -> list[tuple[int, float]]:
    items: list[tuple[int, float]] = []
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(f"Invalid ramp schedule entry '{entry}', expected '<concurrency>:<seconds>'")
        level_raw, hold_raw = entry.split(":", 1)
        level = int(level_raw.strip())
        hold = float(hold_raw.strip())
        if level <= 0 or hold <= 0:
            raise ValueError(f"Invalid ramp entry '{entry}', both values must be > 0")
        items.append((level, hold))
    if not items:
        raise ValueError("Ramp schedule is empty")
    return items


def quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    xs = sorted(values)

    def pct(p: float) -> float:
        if len(xs) == 1:
            return xs[0]
        k = (len(xs) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return xs[int(k)]
        return xs[f] * (c - k) + xs[c] * (k - f)

    return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99)}


def _extract_code(row: Any, code_fields: tuple[str, ...]) -> str | None:
    if isinstance(row, str):
        text = row.strip()
        return text if text else None

    if isinstance(row, dict):
        prompt = row.get("prompt")
        response = row.get("response")
        if isinstance(prompt, str) and isinstance(response, str):
            prompt_text = prompt.strip()
            response_text = response.strip()
            if prompt_text and response_text:
                sorry_idx = prompt_text.rfind("sorry")
                if sorry_idx >= 0:
                    return (
                        prompt_text[:sorry_idx]
                        + response_text
                        + prompt_text[sorry_idx + len("sorry") :]
                    )
                return f"{prompt_text}\n{response_text}"
        for field in code_fields:
            value = row.get(field)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
    return None


def classify_failed_label_kind(
    verification_error: str | None,
    policy: str,
) -> str:
    policy_normalized = policy.strip().lower()
    if policy_normalized not in FAILED_LABEL_POLICIES:
        raise ValueError(f"Unknown failed label policy: {policy}")
    if policy_normalized == "strict_invalid":
        return "semantic_invalid"
    if policy_normalized == "transport_failure":
        return "transport_failure"

    error = (verification_error or "").strip().lower()
    if not error:
        return "semantic_invalid"
    timeout_signals = (
        "timed out",
        "transport_error",
        "connectionerror",
        "header command timed out",
        "timeout",
    )
    if any(signal in error for signal in timeout_signals):
        return "transport_failure"
    return "semantic_invalid"


def load_jsonl_cases(
    path: Path,
    *,
    expected_valid: bool,
    id_prefix: str,
    code_fields: tuple[str, ...],
) -> tuple[list[Case], DatasetStats]:
    stats = DatasetStats(path=str(path))
    cases: list[Case] = []

    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    default_kind = "semantic_valid" if expected_valid else "semantic_invalid"

    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            stats.total_lines += 1
            raw = line.strip()
            if not raw:
                stats.missing_code_rows += 1
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                stats.malformed_rows += 1
                continue

            code = _extract_code(row, code_fields)
            if code is None:
                stats.missing_code_rows += 1
                continue
            if not code.strip():
                stats.empty_code_rows += 1
                continue

            verification_error = None
            if isinstance(row, dict):
                ve = row.get("verification_error")
                if isinstance(ve, str) and ve.strip():
                    verification_error = ve.strip()

            case_id = f"{id_prefix}-{idx}"
            cases.append(
                Case(
                    case_id=case_id,
                    code=code,
                    expected_valid=expected_valid,
                    source=path.name,
                    label_kind=default_kind,
                    verification_error=verification_error,
                )
            )
            stats.parsed_rows += 1

    return cases, stats


def apply_failed_label_policy(
    failed_cases: list[Case],
    *,
    policy: str,
) -> tuple[list[Case], dict[str, Any]]:
    label_counts: Counter[str] = Counter()
    error_counter: Counter[str] = Counter()
    bucket_counter: Counter[str] = Counter()
    relabeled: list[Case] = []

    for case in failed_cases:
        error_text = (case.verification_error or "").strip()
        if error_text:
            error_counter[error_text] += 1
        else:
            error_counter["<missing>"] += 1

        kind = classify_failed_label_kind(case.verification_error, policy)
        label_counts[kind] += 1
        bucket = (
            "transport_timeout_or_header_timeout"
            if kind == "transport_failure"
            else "true_lean_invalid_or_parse_error"
        )
        bucket_counter[bucket] += 1

        relabeled.append(
            Case(
                case_id=case.case_id,
                code=case.code,
                expected_valid=(kind == "semantic_valid"),
                source=case.source,
                label_kind=kind,
                verification_error=case.verification_error,
            )
        )

    return relabeled, {
        "label_policy": policy,
        "label_kind_counts": dict(label_counts),
        "bucket_counts": dict(bucket_counter),
        "verification_error_counts": dict(error_counter),
    }


def build_cases(
    verified_cases: list[Case],
    failed_cases: list[Case],
    *,
    max_verified: int,
    max_failed: int,
    seed: int,
) -> list[Case]:
    rng = random.Random(seed)
    verified = list(verified_cases)
    failed = list(failed_cases)
    rng.shuffle(verified)
    rng.shuffle(failed)

    if max_verified > 0:
        verified = verified[:max_verified]
    if max_failed > 0:
        failed = failed[:max_failed]

    cases = verified + failed
    rng.shuffle(cases)
    return cases


def classify_repl_result(repl_result: dict[str, Any]) -> tuple[bool, bool, str | None]:
    err = repl_result.get("error")
    if err:
        return False, True, f"repl_error:{err}"

    payload = repl_result.get("response") or {}
    messages = payload.get("messages") or []
    sorries = payload.get("sorries") or []

    has_error_msg = any(
        str(m.get("severity", "")).lower() == "error" for m in messages if isinstance(m, dict)
    )
    if has_error_msg:
        return False, False, "lean_error_message"

    if sorries:
        return False, False, "contains_sorry"

    return True, False, None


async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    body: dict[str, Any],
    timeout_s: float,
) -> tuple[int, dict[str, Any] | None, str | None]:
    try:
        async with session.post(url, json=body, timeout=timeout_s) as resp:
            text = await resp.text()
            if resp.status != 200:
                return resp.status, None, text[:1000]
            try:
                return resp.status, json.loads(text), None
            except Exception as exc:
                return resp.status, None, f"invalid_json:{exc}"
    except Exception as exc:
        return -1, None, f"transport_error:{exc}"


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    timeout_s: float,
) -> tuple[int, dict[str, Any] | None, str | None]:
    try:
        async with session.get(url, timeout=timeout_s) as resp:
            text = await resp.text()
            if resp.status != 200:
                return resp.status, None, text[:1000]
            try:
                return resp.status, json.loads(text), None
            except Exception as exc:
                return resp.status, None, f"invalid_json:{exc}"
    except Exception as exc:
        return -1, None, f"transport_error:{exc}"


def backoff_sleep(attempt: int, base: float, max_sleep: float) -> float:
    return min(base * (2 ** max(0, attempt - 1)), max_sleep)


async def fetch_async_metrics(
    *,
    session: aiohttp.ClientSession,
    base_url: str,
    metrics_path: str,
    timeout_s: float,
) -> tuple[int, dict[str, Any] | None, str | None]:
    url = f"{base_url.rstrip('/')}/{metrics_path.lstrip('/')}"
    return await get_json(session, url, timeout_s=timeout_s)


def detect_global_stall(
    snapshots: list[AsyncMetricsSnapshot],
    *,
    required_windows: int,
) -> bool:
    if required_windows <= 0:
        return False
    stagnant_windows = 0
    for idx in range(1, len(snapshots)):
        prev = snapshots[idx - 1]
        curr = snapshots[idx]
        if prev.error or curr.error:
            stagnant_windows = 0
            continue
        if prev.queue_depth is None or curr.queue_depth is None:
            stagnant_windows = 0
            continue
        stagnant = (
            curr.queue_depth > 0
            and curr.queue_depth == prev.queue_depth
            and curr.global_done == prev.global_done
            and curr.global_running == prev.global_running
        )
        stagnant_windows = stagnant_windows + 1 if stagnant else 0
        if stagnant_windows >= required_windows:
            return True
    return False


async def collect_async_metrics_snapshot(
    *,
    session: aiohttp.ClientSession,
    base_url: str,
    metrics_path: str,
    tracker: AsyncProgressTracker,
    timeout_s: float,
) -> AsyncMetricsSnapshot:
    status, body, detail = await fetch_async_metrics(
        session=session,
        base_url=base_url,
        metrics_path=metrics_path,
        timeout_s=timeout_s,
    )
    global_done, global_running = await tracker.snapshot()
    if status != 200 or body is None:
        return AsyncMetricsSnapshot(
            timestamp_epoch_s=time.time(),
            queue_depth=None,
            inflight_jobs=None,
            running_tasks=None,
            oldest_queued_age_sec=None,
            dequeue_rate=None,
            enqueue_rate=None,
            global_done=global_done,
            global_running=global_running,
            error=detail or f"http_{status}",
        )
    return AsyncMetricsSnapshot(
        timestamp_epoch_s=time.time(),
        queue_depth=int(body.get("queue_depth", 0)),
        inflight_jobs=int(body.get("inflight_jobs", 0)),
        running_tasks=int(body.get("running_tasks", 0)),
        oldest_queued_age_sec=float(body.get("oldest_queued_age_sec", 0.0)),
        dequeue_rate=float(body.get("dequeue_rate", 0.0)),
        enqueue_rate=float(body.get("enqueue_rate", 0.0)),
        global_done=global_done,
        global_running=global_running,
        error=None,
    )


async def monitor_async_metrics(
    *,
    session: aiohttp.ClientSession,
    base_url: str,
    metrics_path: str,
    tracker: AsyncProgressTracker,
    snapshots: list[AsyncMetricsSnapshot],
    interval_s: float,
    stop_event: asyncio.Event,
) -> None:
    while True:
        snapshot = await collect_async_metrics_snapshot(
            session=session,
            base_url=base_url,
            metrics_path=metrics_path,
            tracker=tracker,
            timeout_s=max(5.0, interval_s + 5.0),
        )
        snapshots.append(snapshot)
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            return
        except asyncio.TimeoutError:
            continue


def _rows_from_results(
    *,
    mode: str,
    tier_concurrency: int,
    cases: list[Case],
    results: list[dict[str, Any]],
    latency_ms: float,
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    if len(results) != len(cases):
        return [
            EvalRow(
                mode=mode,
                tier_concurrency=tier_concurrency,
                case_id=case.case_id,
                expected_valid=case.expected_valid,
                predicted_valid=False,
                system_error=True,
                latency_ms=latency_ms,
                http_status=200,
                detail=(
                    "missing_results"
                    if len(results) < len(cases)
                    else f"unexpected_results_count:{len(results)}"
                ),
                label_kind=case.label_kind,
            )
            for case in cases
        ]

    for case, result in zip(cases, results):
        pred, sys_err, why = classify_repl_result(result)
        rows.append(
            EvalRow(
                mode=mode,
                tier_concurrency=tier_concurrency,
                case_id=case.case_id,
                expected_valid=case.expected_valid,
                predicted_valid=pred,
                system_error=sys_err,
                latency_ms=latency_ms,
                http_status=200,
                detail=why,
                completed=True,
                label_kind=case.label_kind,
            )
        )
    return rows


async def eval_sync_batch(
    *,
    cases: list[Case],
    tier_concurrency: int,
    base_url: str,
    session: aiohttp.ClientSession,
    lean_timeout: int,
    retries: int,
    retry_backoff_base: float,
    retry_backoff_max: float,
) -> list[EvalRow]:
    payload = {
        "snippets": [{"id": case.case_id, "code": case.code} for case in cases],
        "timeout": lean_timeout,
        "debug": False,
        "reuse": True,
    }
    url = f"{base_url}/api/check"
    started = time.perf_counter()
    last_status = -1
    last_detail: str | None = None

    for attempt in range(1, retries + 1):
        status, body, detail = await post_json(session, url, payload, timeout_s=lean_timeout + 10)
        last_status, last_detail = status, detail
        if status == 200 and body is not None:
            latency_ms = (time.perf_counter() - started) * 1000.0
            results = body.get("results") or []
            return _rows_from_results(
                mode="sync",
                tier_concurrency=tier_concurrency,
                cases=cases,
                results=results,
                latency_ms=latency_ms,
            )

        if status in RETRYABLE_HTTP and attempt < retries:
            await asyncio.sleep(backoff_sleep(attempt, retry_backoff_base, retry_backoff_max))
            continue
        break

    latency_ms = (time.perf_counter() - started) * 1000.0
    return [
        EvalRow(
            mode="sync",
            tier_concurrency=tier_concurrency,
            case_id=case.case_id,
            expected_valid=case.expected_valid,
            predicted_valid=False,
            system_error=True,
            latency_ms=latency_ms,
            http_status=last_status,
            detail=last_detail,
            label_kind=case.label_kind,
        )
        for case in cases
    ]


async def eval_async_batch(
    *,
    cases: list[Case],
    tier_concurrency: int,
    base_url: str,
    session: aiohttp.ClientSession,
    lean_timeout: int,
    retries: int,
    retry_backoff_base: float,
    retry_backoff_max: float,
    poll_interval_s: float,
    poll_timeout_s: float,
    poll_wait_sec: float,
    progress_tracker: AsyncProgressTracker | None = None,
) -> list[EvalRow]:
    submit_payload = {
        "snippets": [{"id": case.case_id, "code": case.code} for case in cases],
        "timeout": lean_timeout,
        "debug": False,
        "reuse": True,
    }
    submit_url = f"{base_url}/api/async/check"
    started = time.perf_counter()

    job_id: str | None = None
    last_status = -1
    last_detail: str | None = None

    for attempt in range(1, retries + 1):
        status, body, detail = await post_json(
            session,
            submit_url,
            submit_payload,
            timeout_s=lean_timeout + 10,
        )
        last_status, last_detail = status, detail
        if status == 200 and body and body.get("job_id"):
            job_id = str(body["job_id"])
            if progress_tracker is not None:
                await progress_tracker.observe(
                    job_id=job_id,
                    status="queued",
                    done=0,
                    failed=0,
                    running=0,
                )
            break
        if status in RETRYABLE_HTTP and attempt < retries:
            await asyncio.sleep(backoff_sleep(attempt, retry_backoff_base, retry_backoff_max))
            continue

        latency_ms = (time.perf_counter() - started) * 1000.0
        return [
            EvalRow(
                mode="async",
                tier_concurrency=tier_concurrency,
                case_id=case.case_id,
                expected_valid=case.expected_valid,
                predicted_valid=False,
                system_error=True,
                latency_ms=latency_ms,
                http_status=status,
                detail=detail,
                label_kind=case.label_kind,
            )
            for case in cases
        ]

    if not job_id:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return [
            EvalRow(
                mode="async",
                tier_concurrency=tier_concurrency,
                case_id=case.case_id,
                expected_valid=case.expected_valid,
                predicted_valid=False,
                system_error=True,
                latency_ms=latency_ms,
                http_status=last_status,
                detail=last_detail,
                label_kind=case.label_kind,
            )
            for case in cases
        ]

    poll_url = f"{base_url}/api/async/check/{job_id}"
    if poll_wait_sec > 0:
        poll_url = f"{poll_url}?wait_sec={poll_wait_sec:.3f}"
    poll_deadline = time.perf_counter() + poll_timeout_s

    while time.perf_counter() < poll_deadline:
        await asyncio.sleep(poll_interval_s)
        status, body, detail = await get_json(
            session,
            poll_url,
            timeout_s=max(5.0, poll_interval_s + poll_wait_sec + 5.0),
        )
        last_status, last_detail = status, detail

        if status == 200 and body is not None:
            st = str(body.get("status", "")).lower()
            progress = body.get("progress") or {}
            done = int(progress.get("done", 0))
            failed = int(progress.get("failed", 0))
            running = int(progress.get("running", 0))
            if progress_tracker is not None:
                await progress_tracker.observe(
                    job_id=job_id,
                    status=st,
                    done=done,
                    failed=failed,
                    running=running,
                )

            if st == "completed":
                latency_ms = (time.perf_counter() - started) * 1000.0
                results = body.get("results") or []
                rows = _rows_from_results(
                    mode="async",
                    tier_concurrency=tier_concurrency,
                    cases=cases,
                    results=results,
                    latency_ms=latency_ms,
                )
                for row in rows:
                    row.completed = True
                return rows

            if st in {"failed", "expired"}:
                latency_ms = (time.perf_counter() - started) * 1000.0
                return [
                    EvalRow(
                        mode="async",
                        tier_concurrency=tier_concurrency,
                        case_id=case.case_id,
                        expected_valid=case.expected_valid,
                        predicted_valid=False,
                        system_error=True,
                        latency_ms=latency_ms,
                        http_status=200,
                        detail=f"job_{st}",
                        label_kind=case.label_kind,
                    )
                    for case in cases
                ]

            if st in {"queued", "running"}:
                continue

            if st in TERMINAL_ASYNC_STATES:
                latency_ms = (time.perf_counter() - started) * 1000.0
                return [
                    EvalRow(
                        mode="async",
                        tier_concurrency=tier_concurrency,
                        case_id=case.case_id,
                        expected_valid=case.expected_valid,
                        predicted_valid=False,
                        system_error=True,
                        latency_ms=latency_ms,
                        http_status=200,
                        detail=f"terminal_without_results:{st}",
                        label_kind=case.label_kind,
                    )
                    for case in cases
                ]

            latency_ms = (time.perf_counter() - started) * 1000.0
            return [
                EvalRow(
                    mode="async",
                    tier_concurrency=tier_concurrency,
                    case_id=case.case_id,
                    expected_valid=case.expected_valid,
                    predicted_valid=False,
                    system_error=True,
                    latency_ms=latency_ms,
                    http_status=200,
                    detail=f"unknown_job_state:{st}",
                    label_kind=case.label_kind,
                )
                for case in cases
            ]

        if status in RETRYABLE_HTTP:
            continue

        if status == 404:
            latency_ms = (time.perf_counter() - started) * 1000.0
            return [
                EvalRow(
                    mode="async",
                    tier_concurrency=tier_concurrency,
                    case_id=case.case_id,
                    expected_valid=case.expected_valid,
                    predicted_valid=False,
                    system_error=True,
                    latency_ms=latency_ms,
                    http_status=status,
                    detail="job_not_found_or_expired",
                    label_kind=case.label_kind,
                )
                for case in cases
            ]

        latency_ms = (time.perf_counter() - started) * 1000.0
        return [
            EvalRow(
                mode="async",
                tier_concurrency=tier_concurrency,
                case_id=case.case_id,
                expected_valid=case.expected_valid,
                predicted_valid=False,
                system_error=True,
                latency_ms=latency_ms,
                http_status=status,
                detail=detail,
                label_kind=case.label_kind,
            )
            for case in cases
        ]

    latency_ms = (time.perf_counter() - started) * 1000.0
    if progress_tracker is not None:
        await progress_tracker.observe(
            job_id=job_id,
            status="poll_timeout",
            done=0,
            failed=0,
            running=0,
        )
    return [
        EvalRow(
            mode="async",
            tier_concurrency=tier_concurrency,
            case_id=case.case_id,
            expected_valid=case.expected_valid,
            predicted_valid=False,
            system_error=True,
            latency_ms=latency_ms,
            http_status=last_status,
            detail="poll_timeout",
            poll_timeout=True,
            label_kind=case.label_kind,
        )
        for case in cases
    ]


def accuracy(rows: list[EvalRow]) -> dict[str, Any]:
    semantic_rows = [
        row for row in rows if row.label_kind in {"semantic_valid", "semantic_invalid"}
    ]

    tp = tn = fp = fn = 0
    for row in semantic_rows:
        if row.expected_valid and row.predicted_valid:
            tp += 1
        elif row.expected_valid and not row.predicted_valid:
            fn += 1
        elif (not row.expected_valid) and row.predicted_valid:
            fp += 1
        else:
            tn += 1

    valid_total = tp + fn
    invalid_total = tn + fp
    total = valid_total + invalid_total

    return {
        "counts": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "sample_sizes": {
            "semantic_total": len(semantic_rows),
            "semantic_valid": valid_total,
            "semantic_invalid": invalid_total,
            "excluded_transport_failure": sum(
                1 for row in rows if row.label_kind == "transport_failure"
            ),
        },
        "rates": {
            "valid_true_positive_rate": (tp / valid_total) if valid_total else None,
            "invalid_true_negative_rate": (tn / invalid_total) if invalid_total else None,
            "overall_accuracy": ((tp + tn) / total) if total else None,
        },
    }


def compute_tier_metrics(
    rows: list[EvalRow],
    wave_latency_ms: list[float],
    *,
    queue_stall_detected: bool = False,
    async_metrics_snapshots: list[AsyncMetricsSnapshot] | None = None,
) -> dict[str, Any]:
    processed = len(rows)
    latencies = [row.latency_ms for row in rows]
    elapsed_s = sum(wave_latency_ms) / 1000.0
    system_errors = sum(1 for row in rows if row.system_error)
    http_429 = sum(1 for row in rows if row.http_status == 429)

    async_rows = [row for row in rows if row.mode == "async"]
    async_poll_timeouts = sum(1 for row in async_rows if row.poll_timeout)
    async_completed = sum(1 for row in async_rows if row.completed)
    snapshots = async_metrics_snapshots or []
    async_metrics_errors = sum(1 for snap in snapshots if snap.error)
    async_metrics_available = any(snap.error is None for snap in snapshots)

    return {
        "processed_attempts": processed,
        "elapsed_seconds": elapsed_s,
        "throughput_attempts_per_sec": (processed / elapsed_s) if elapsed_s > 0 else 0.0,
        "system_errors": system_errors,
        "system_error_rate": (system_errors / processed) if processed else 0.0,
        "http_429_count": http_429,
        "http_429_rate": (http_429 / processed) if processed else 0.0,
        "request_latency_ms": quantiles(latencies),
        "wave_latency_ms": quantiles(wave_latency_ms),
        "accuracy": accuracy(rows),
        "async_poll_timeout_rate": (
            (async_poll_timeouts / len(async_rows)) if async_rows else 0.0
        ),
        "async_job_completion_rate": (
            (async_completed / len(async_rows)) if async_rows else 0.0
        ),
        "queue_stall_detected": queue_stall_detected,
        "async_metrics_available": async_metrics_available,
        "async_metrics_samples": len(snapshots),
        "async_metrics_error_samples": async_metrics_errors,
        "async_metrics_snapshots": [asdict(snap) for snap in snapshots],
    }


def checks_for_tier(
    *,
    mode: str,
    metrics: dict[str, Any],
    thresholds: Thresholds,
    fail_on_accuracy: bool,
    fail_on_performance: bool,
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if fail_on_performance:
        if mode == "sync":
            checks["sync_p99"] = (
                float(metrics["request_latency_ms"]["p99"]) <= thresholds.sync_p99_target_ms
            )
            checks["sync_system_error_rate"] = (
                float(metrics["system_error_rate"]) <= thresholds.sync_max_system_error_rate
            )
            checks["sync_429_rate"] = (
                float(metrics["http_429_rate"]) <= thresholds.sync_max_429_rate
            )
        if mode == "async":
            checks["async_system_error_rate"] = (
                float(metrics["system_error_rate"]) <= thresholds.async_max_system_error_rate
            )
            checks["async_poll_timeout_rate"] = (
                float(metrics["async_poll_timeout_rate"]) <= thresholds.async_poll_timeout_rate
            )
            checks["async_job_completion_rate"] = (
                float(metrics["async_job_completion_rate"]) >= thresholds.async_min_completion_rate
            )
            checks["async_queue_stall"] = not bool(metrics["queue_stall_detected"])
            checks["async_metrics_available"] = bool(metrics["async_metrics_available"])

    if fail_on_accuracy:
        rates = metrics["accuracy"]["rates"]
        vtpr = rates["valid_true_positive_rate"]
        itnr = rates["invalid_true_negative_rate"]
        overall = rates["overall_accuracy"]
        checks["accuracy_valid_tpr"] = vtpr is not None and vtpr >= thresholds.min_valid_tpr
        checks["accuracy_invalid_tnr"] = itnr is not None and itnr >= thresholds.min_invalid_tnr
        checks["accuracy_overall"] = overall is not None and overall >= thresholds.min_overall_accuracy

    return checks


def severe_failure_reasons(
    *,
    mode: str,
    metrics: dict[str, Any],
    severe: SevereThresholds,
) -> list[str]:
    reasons: list[str] = []
    if float(metrics["system_error_rate"]) > severe.system_error_rate:
        reasons.append(
            f"system_error_rate={metrics['system_error_rate']:.4f}>{severe.system_error_rate:.4f}"
        )
    if float(metrics["http_429_rate"]) > severe.http_429_rate:
        reasons.append(f"http_429_rate={metrics['http_429_rate']:.4f}>{severe.http_429_rate:.4f}")
    if mode == "async" and float(metrics["async_poll_timeout_rate"]) > severe.async_poll_timeout_rate:
        reasons.append(
            "async_poll_timeout_rate="
            f"{metrics['async_poll_timeout_rate']:.4f}>{severe.async_poll_timeout_rate:.4f}"
        )
    if mode == "async" and bool(metrics["queue_stall_detected"]):
        reasons.append("queue_stall_detected=true")
    if mode == "async" and not bool(metrics.get("async_metrics_available", False)):
        reasons.append("async_metrics_unavailable=true")
    return reasons


def chunked_cases(cases: list[Case], batch_size: int) -> list[list[Case]]:
    if batch_size <= 1:
        return [[c] for c in cases]
    return [cases[i : i + batch_size] for i in range(0, len(cases), batch_size)]


async def run_tier(
    *,
    mode: str,
    concurrency: int,
    base_cases: list[Case],
    session: aiohttp.ClientSession,
    args: argparse.Namespace,
    duration_s: float | None = None,
) -> tuple[dict[str, Any], list[EvalRow]]:
    if duration_s is None:
        total_cases = max(args.min_total_cases, concurrency * args.waves)
        case_cursor = 0

        def next_wave() -> list[Case] | None:
            nonlocal case_cursor
            if case_cursor >= total_cases:
                return None
            size = min(concurrency, total_cases - case_cursor)
            wave = [base_cases[(case_cursor + i) % len(base_cases)] for i in range(size)]
            case_cursor += size
            return wave

    else:
        total_cases = 0
        deadline = time.perf_counter() + duration_s
        waves_run = 0
        case_cursor = 0

        def next_wave() -> list[Case] | None:
            nonlocal waves_run, total_cases, case_cursor
            if waves_run > 0 and time.perf_counter() >= deadline:
                return None
            waves_run += 1
            wave = [base_cases[(case_cursor + i) % len(base_cases)] for i in range(concurrency)]
            case_cursor += concurrency
            total_cases += len(wave)
            return wave

    rows: list[EvalRow] = []
    wave_lat_ms: list[float] = []
    base_url = args.base_url.rstrip("/")
    progress_tracker = AsyncProgressTracker() if mode == "async" else None
    async_metrics_snapshots: list[AsyncMetricsSnapshot] = []
    monitor_stop = asyncio.Event()
    monitor_task: asyncio.Task[None] | None = None

    if mode == "async":
        if progress_tracker is None:
            raise RuntimeError("Async progress tracker not initialized")
        monitor_task = asyncio.create_task(
            monitor_async_metrics(
                session=session,
                base_url=base_url,
                metrics_path=args.async_metrics_path,
                tracker=progress_tracker,
                snapshots=async_metrics_snapshots,
                interval_s=args.async_metrics_interval_s,
                stop_event=monitor_stop,
            )
        )

    try:
        while True:
            wave_cases = next_wave()
            if wave_cases is None:
                break
            t0 = time.perf_counter()

            case_batches = chunked_cases(wave_cases, max(1, args.batch_size))
            sem_limit = (
                min(len(case_batches), args.client_max_inflight)
                if args.client_max_inflight > 0
                else len(case_batches)
            )
            sem = asyncio.Semaphore(max(1, sem_limit))

            async def run_one(batch: list[Case]) -> list[EvalRow]:
                async with sem:
                    if mode == "sync":
                        return await eval_sync_batch(
                            cases=batch,
                            tier_concurrency=concurrency,
                            base_url=base_url,
                            session=session,
                            lean_timeout=args.lean_timeout,
                            retries=args.retries,
                            retry_backoff_base=args.retry_backoff_base,
                            retry_backoff_max=args.retry_backoff_max,
                        )
                    return await eval_async_batch(
                        cases=batch,
                        tier_concurrency=concurrency,
                        base_url=base_url,
                        session=session,
                        lean_timeout=args.lean_timeout,
                        retries=args.retries,
                        retry_backoff_base=args.retry_backoff_base,
                        retry_backoff_max=args.retry_backoff_max,
                        poll_interval_s=args.poll_interval_s,
                        poll_timeout_s=args.poll_timeout_s,
                        poll_wait_sec=args.async_poll_wait_sec,
                        progress_tracker=progress_tracker,
                    )

            batch_rows = await asyncio.gather(*(run_one(batch) for batch in case_batches))
            for r in batch_rows:
                rows.extend(r)
            wave_elapsed = (time.perf_counter() - t0) * 1000.0
            wave_lat_ms.append(wave_elapsed)
    finally:
        if monitor_task is not None:
            monitor_stop.set()
            await monitor_task

    queue_stall_detected = False
    if mode == "async":
        queue_stall_detected = detect_global_stall(
            async_metrics_snapshots,
            required_windows=args.global_stall_windows,
        )

    metrics = compute_tier_metrics(
        rows,
        wave_latency_ms=wave_lat_ms,
        queue_stall_detected=queue_stall_detected,
        async_metrics_snapshots=async_metrics_snapshots,
    )
    thresholds = Thresholds(
        sync_p99_target_ms=args.sync_p99_target_ms,
        sync_max_system_error_rate=args.sync_max_system_error_rate,
        sync_max_429_rate=args.sync_max_429_rate,
        async_max_system_error_rate=args.async_max_system_error_rate,
        async_poll_timeout_rate=args.async_poll_timeout_rate,
        async_min_completion_rate=args.async_min_completion_rate,
        min_valid_tpr=args.min_valid_tpr,
        min_invalid_tnr=args.min_invalid_tnr,
        min_overall_accuracy=args.min_overall_accuracy,
        required_async_concurrency=args.required_async_concurrency,
    )
    checks = checks_for_tier(
        mode=mode,
        metrics=metrics,
        thresholds=thresholds,
        fail_on_accuracy=args.fail_on_accuracy,
        fail_on_performance=args.fail_on_performance,
    )
    severe = SevereThresholds(
        system_error_rate=args.severe_system_error_rate,
        http_429_rate=args.severe_http_429_rate,
        async_poll_timeout_rate=args.severe_async_poll_timeout_rate,
    )
    severe_reasons = severe_failure_reasons(mode=mode, metrics=metrics, severe=severe)

    tier = {
        "mode": mode,
        "concurrency": concurrency,
        "duration_seconds_target": duration_s,
        "batch_size": args.batch_size,
        **metrics,
        "checks": checks,
        "pass": all(checks.values()) if checks else True,
        "severe_failure": bool(severe_reasons),
        "severe_failure_reasons": severe_reasons,
        "sample_failures": [
            asdict(row) for row in rows if row.system_error
        ][: min(20, len(rows))],
    }
    return tier, rows


def profile_defaults(profile: str) -> tuple[str, str]:
    p = profile.lower()
    if p == "diag":
        return "10", "250"
    if p == "quick":
        return "10,50,100", "250,500"
    if p == "full":
        return "10,50,100", "250,500,1000,2000"
    if p == "accuracy":
        return "10", "20"
    if p == "throughput":
        return "10", "50,100,250"
    raise ValueError(f"Unknown profile: {profile}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Lean production load tester (sync + async + accuracy + incident report)."
    )
    backend_dir = Path(__file__).resolve().parents[2]

    parser.add_argument("--base-url", default="https://lean-ui-production.up.railway.app")
    parser.add_argument(
        "--profile",
        choices=["diag", "quick", "full", "accuracy", "throughput"],
        default="quick",
    )
    parser.add_argument("--mode", choices=["sync", "async", "both"], default="both")
    parser.add_argument("--run-type", choices=["mixed", "accuracy", "throughput"], default="mixed")
    parser.add_argument("--sync-levels", default="")
    parser.add_argument("--async-levels", default="")

    parser.add_argument(
        "--verified-jsonl",
        default=str(backend_dir / "data/loadtest/proof_sft_verified.jsonl"),
    )
    parser.add_argument(
        "--failed-jsonl",
        default=str(backend_dir / "data/loadtest/proof_sft_failed.jsonl"),
    )
    parser.add_argument(
        "--failed-label-policy",
        choices=sorted(FAILED_LABEL_POLICIES),
        default="split_by_error",
    )
    parser.add_argument(
        "--code-field",
        action="append",
        default=[],
        help="Repeatable: code field preference. Defaults to prompt,code,proof,lean_code,snippet,text",
    )
    parser.add_argument("--max-verified", type=int, default=2000)
    parser.add_argument("--max-failed", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)

    parser.add_argument("--api-key", default="")
    parser.add_argument("--waves", type=int, default=1)
    parser.add_argument("--min-total-cases", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--client-max-inflight", type=int, default=0, help="0 means no extra client cap.")
    parser.add_argument("--lean-timeout", type=int, default=300)
    parser.add_argument("--poll-interval-s", type=float, default=2.0)
    parser.add_argument("--poll-timeout-s", type=float, default=180.0)
    parser.add_argument("--async-poll-wait-sec", type=float, default=0.0)
    parser.add_argument("--async-metrics-path", default="/api/async/metrics")
    parser.add_argument("--async-metrics-interval-s", type=float, default=5.0)
    parser.add_argument("--global-stall-windows", type=int, default=12)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-backoff-base", type=float, default=1.0)
    parser.add_argument("--retry-backoff-max", type=float, default=8.0)

    parser.add_argument("--ramp-up", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--ramp-schedule", default="25:120,50:180,100:180,250:300")

    parser.add_argument("--sync-p99-target-ms", type=float, default=5000.0)
    parser.add_argument("--sync-max-system-error-rate", type=float, default=0.01)
    parser.add_argument("--sync-max-429-rate", type=float, default=0.01)
    parser.add_argument("--async-max-system-error-rate", type=float, default=0.005)
    parser.add_argument("--async-poll-timeout-rate", type=float, default=0.0)
    parser.add_argument("--async-min-completion-rate", type=float, default=0.995)

    parser.add_argument("--min-valid-tpr", type=float, default=0.99)
    parser.add_argument("--min-invalid-tnr", type=float, default=0.98)
    parser.add_argument("--min-overall-accuracy", type=float, default=0.99)
    parser.add_argument("--required-async-concurrency", type=int, default=2000)

    parser.add_argument("--severe-system-error-rate", type=float, default=0.20)
    parser.add_argument("--severe-http-429-rate", type=float, default=0.20)
    parser.add_argument("--severe-async-poll-timeout-rate", type=float, default=0.05)

    parser.add_argument("--fail-on-accuracy", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-on-performance", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-on-severe-fail", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--emit-incident-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--output-dir",
        default=str(backend_dir / "outputs/loadtests/verification"),
    )
    return parser


def render_incident_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Incident/Validation Report: Lean UI Load Test")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Base URL: `{summary['base_url']}`")
    lines.append(f"- Mode: `{summary['mode']}`")
    lines.append(f"- Overall pass: `{summary['pass']}`")
    lines.append(
        f"- All tiers pass: `{summary['slo_results']['all_tiers_pass']}` | Required async tier met: `{summary['slo_results']['required_async_concurrency_met']}`"
    )
    lines.append("")

    lines.append("## Dataset")
    ds = summary["dataset"]
    lines.append(f"- Verified cases loaded: `{ds['verified_cases_loaded']}`")
    lines.append(f"- Failed cases loaded: `{ds['failed_cases_loaded']}`")
    lines.append(f"- Total evaluated cases pool: `{ds['pool_size']}`")
    lines.append(f"- Code fields: `{', '.join(ds['code_fields'])}`")
    lines.append(f"- Label policy: `{summary['label_policy']}`")
    lines.append("")

    lines.append("## Tier Results")
    lines.append("| mode | concurrency | pass | throughput req/s | p99 ms | sys err rate | 429 rate | async completion | async poll timeout | queue stall |")
    lines.append("|---|---:|:---:|---:|---:|---:|---:|---:|---:|:---:|")
    for tier in summary["tier_results"]:
        lines.append(
            "| "
            f"{tier['mode']} | {tier['concurrency']} | {tier['pass']} | "
            f"{tier['throughput_attempts_per_sec']:.3f} | {tier['request_latency_ms']['p99']:.1f} | "
            f"{tier['system_error_rate']:.4f} | {tier['http_429_rate']:.4f} | "
            f"{tier['async_job_completion_rate']:.4f} | {tier['async_poll_timeout_rate']:.4f} | "
            f"{tier['queue_stall_detected']} |"
        )
    lines.append("")

    severe = [t for t in summary["tier_results"] if t.get("severe_failure")]
    lines.append("## Severe Failure Triggers")
    if not severe:
        lines.append("- None detected.")
    else:
        for tier in severe:
            reasons = ", ".join(tier.get("severe_failure_reasons", []))
            lines.append(f"- `{tier['mode']}` concurrency `{tier['concurrency']}`: {reasons}")
    lines.append("")

    lines.append("## Notes")
    lines.append("- Async is primary baseline in this suite; sync is validated for near-term readiness.")
    lines.append("- Queue-stall signal uses global progress windows from `/api/async/metrics` plus aggregate poll progress.")
    lines.append("- Railway env parity to verify separately: `LEAN_SERVER_REDIS_URL`, `LEAN_SERVER_ASYNC_QUEUE_NAME`, `LEAN_SERVER_ASYNC_ENABLED=true`.")
    lines.append("")

    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> int:
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if args.async_poll_wait_sec < 0:
        raise ValueError("--async-poll-wait-sec must be >= 0")

    default_sync, default_async = profile_defaults(args.profile)
    sync_levels = parse_levels(args.sync_levels or default_sync)
    async_levels = parse_levels(args.async_levels or default_async)

    if args.run_type == "throughput":
        args.fail_on_accuracy = False
    if args.run_type == "accuracy":
        args.fail_on_performance = False

    code_fields = tuple(args.code_field) if args.code_field else DEFAULT_CODE_FIELDS

    verified_cases, verified_stats = load_jsonl_cases(
        Path(args.verified_jsonl),
        expected_valid=True,
        id_prefix="verified",
        code_fields=code_fields,
    )
    failed_raw_cases, failed_stats = load_jsonl_cases(
        Path(args.failed_jsonl),
        expected_valid=False,
        id_prefix="failed",
        code_fields=code_fields,
    )
    failed_cases, failed_breakdown = apply_failed_label_policy(
        failed_raw_cases,
        policy=args.failed_label_policy,
    )

    base_cases = build_cases(
        verified_cases,
        failed_cases,
        max_verified=args.max_verified,
        max_failed=args.max_failed,
        seed=args.seed,
    )
    if not base_cases:
        raise ValueError("No test cases generated from datasets.")

    timeout = aiohttp.ClientTimeout(total=max(args.lean_timeout + 30, args.poll_timeout_s + 30))
    tier_results: list[dict[str, Any]] = []
    all_rows: list[EvalRow] = []
    stopped_early = False
    api_key = args.api_key.strip() or os.getenv("LEAN_SERVER_API_KEY", "").strip()
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    ramp_schedule: list[tuple[int, float]] = []
    if args.ramp_up:
        ramp_schedule = parse_ramp_schedule(args.ramp_schedule)

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        if args.mode in ("sync", "both"):
            for level in sync_levels:
                tier, rows = await run_tier(
                    mode="sync",
                    concurrency=level,
                    base_cases=base_cases,
                    session=session,
                    args=args,
                )
                tier_results.append(tier)
                all_rows.extend(rows)
                if args.stop_on_severe_fail and tier["severe_failure"]:
                    stopped_early = True
                    break

        if not stopped_early and args.mode in ("async", "both"):
            if args.ramp_up:
                for level, hold_sec in ramp_schedule:
                    tier, rows = await run_tier(
                        mode="async",
                        concurrency=level,
                        base_cases=base_cases,
                        session=session,
                        args=args,
                        duration_s=hold_sec,
                    )
                    tier["ramp_phase"] = {"concurrency": level, "hold_seconds": hold_sec}
                    tier_results.append(tier)
                    all_rows.extend(rows)
                    if args.stop_on_severe_fail and tier["severe_failure"]:
                        stopped_early = True
                        break
            else:
                for level in async_levels:
                    tier, rows = await run_tier(
                        mode="async",
                        concurrency=level,
                        base_cases=base_cases,
                        session=session,
                        args=args,
                    )
                    tier_results.append(tier)
                    all_rows.extend(rows)
                    if args.stop_on_severe_fail and tier["severe_failure"]:
                        stopped_early = True
                        break

    all_tiers_pass = all(bool(tier.get("pass")) for tier in tier_results)
    async_required_ok = True
    if args.mode in ("async", "both"):
        async_candidates = [
            tier
            for tier in tier_results
            if tier["mode"] == "async" and int(tier["concurrency"]) >= args.required_async_concurrency
        ]
        async_required_ok = bool(async_candidates) and any(bool(tier.get("pass")) for tier in async_candidates)

    summary = {
        "timestamp_epoch_s": time.time(),
        "base_url": args.base_url,
        "mode": args.mode,
        "profile": args.profile,
        "run_type": args.run_type,
        "label_policy": args.failed_label_policy,
        "failed_error_breakdown": failed_breakdown,
        "dataset": {
            "verified_jsonl": args.verified_jsonl,
            "failed_jsonl": args.failed_jsonl,
            "code_fields": list(code_fields),
            "verified_stats": asdict(verified_stats),
            "failed_stats": asdict(failed_stats),
            "verified_cases_loaded": len(verified_cases),
            "failed_cases_loaded": len(failed_cases),
            "pool_size": len(base_cases),
            "seed": args.seed,
        },
        "async_observability": {
            "metrics_path": args.async_metrics_path,
            "metrics_interval_s": args.async_metrics_interval_s,
            "global_stall_windows": args.global_stall_windows,
            "api_key_provided": bool(api_key),
            "poll_wait_sec": args.async_poll_wait_sec,
        },
        "batch_size": args.batch_size,
        "ramp_schedule": [
            {"concurrency": level, "hold_seconds": hold}
            for level, hold in ramp_schedule
        ],
        "thresholds": {
            "sync_p99_target_ms": args.sync_p99_target_ms,
            "sync_max_system_error_rate": args.sync_max_system_error_rate,
            "sync_max_429_rate": args.sync_max_429_rate,
            "async_max_system_error_rate": args.async_max_system_error_rate,
            "async_poll_timeout_rate": args.async_poll_timeout_rate,
            "async_min_completion_rate": args.async_min_completion_rate,
            "min_valid_tpr": args.min_valid_tpr,
            "min_invalid_tnr": args.min_invalid_tnr,
            "min_overall_accuracy": args.min_overall_accuracy,
            "required_async_concurrency": args.required_async_concurrency,
        },
        "severe_thresholds": {
            "system_error_rate": args.severe_system_error_rate,
            "http_429_rate": args.severe_http_429_rate,
            "async_poll_timeout_rate": args.severe_async_poll_timeout_rate,
        },
        "slo_results": {
            "all_tiers_pass": all_tiers_pass,
            "required_async_concurrency_met": async_required_ok,
        },
        "stopped_early": stopped_early,
        "pass": all_tiers_pass and async_required_ok,
        "tier_results": tier_results,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "verification_loadtest_summary.json"
    calibrated_summary_path = out_dir / "verification_loadtest_summary_calibrated.json"
    results_path = out_dir / "verification_loadtest_results.jsonl"
    breakdown_path = out_dir / "failed_error_breakdown.json"
    report_path = out_dir / "incident_report.md"

    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    summary_path.write_text(payload, encoding="utf-8")
    calibrated_summary_path.write_text(payload, encoding="utf-8")
    breakdown_path.write_text(
        json.dumps(failed_breakdown, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with results_path.open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")

    if args.emit_incident_report:
        report_path.write_text(render_incident_report(summary), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pass"] else 2


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
