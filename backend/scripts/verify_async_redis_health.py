#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from redis.asyncio import Redis, from_url as redis_from_url
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "redis dependency is required for verify_async_redis_health.py"
    ) from exc


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: float


async def _close_redis(conn: Redis) -> None:
    close_fn = getattr(conn, "aclose", None)
    if close_fn is not None:
        await close_fn()
        return
    close_fn = getattr(conn, "close", None)
    if close_fn is None:
        return
    result = close_fn()
    if asyncio.iscoroutine(result):
        await result


async def run_checks(
    *,
    redis_url: str,
    queue_name: str,
    key_prefix: str,
    timeout_sec: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    results: list[CheckResult] = []
    conn: Redis = redis_from_url(redis_url, decode_responses=True)

    suffix = uuid.uuid4().hex
    temp_key = f"{key_prefix}:health:key:{suffix}"
    temp_queue = f"{queue_name}:health:{suffix}"

    async def _record(name: str, coro) -> None:  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        try:
            detail = await coro
            results.append(
                CheckResult(
                    name=name,
                    ok=True,
                    detail=str(detail),
                    elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    name=name,
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                )
            )

    async def _check_ping() -> str:
        pong = await conn.ping()
        if pong is not True:
            raise RuntimeError(f"Unexpected PING response: {pong}")
        return "PING ok"

    async def _check_ttl_roundtrip() -> str:
        await conn.set(temp_key, "ok", ex=30)
        value = await conn.get(temp_key)
        if value != "ok":
            raise RuntimeError(f"Expected key value 'ok', got '{value}'")
        ttl = await conn.ttl(temp_key)
        if ttl <= 0 or ttl > 30:
            raise RuntimeError(f"Expected ttl in range 1..30, got {ttl}")
        return f"TTL ok ({ttl}s)"

    async def _check_queue_roundtrip() -> str:
        base_len = int(await conn.llen(temp_queue))
        payload = json.dumps({"check": "queue_roundtrip", "ts": time.time()})
        await conn.rpush(temp_queue, payload)
        after_push = int(await conn.llen(temp_queue))
        if after_push != base_len + 1:
            raise RuntimeError(
                f"Queue length mismatch after push: expected {base_len + 1}, got {after_push}"
            )

        popped = await conn.blpop(temp_queue, timeout=timeout_sec)
        if popped is None:
            raise RuntimeError("BLPOP timed out waiting for test payload")
        queue_key, raw_payload = popped
        if queue_key != temp_queue:
            raise RuntimeError(f"Popped from unexpected queue: {queue_key}")
        if raw_payload != payload:
            raise RuntimeError("Popped payload mismatch")

        final_len = int(await conn.llen(temp_queue))
        if final_len != base_len:
            raise RuntimeError(
                f"Queue length mismatch after pop: expected {base_len}, got {final_len}"
            )
        return "Queue enqueue/dequeue and length checks ok"

    await _record("redis_ping", _check_ping())
    await _record("ttl_roundtrip", _check_ttl_roundtrip())
    await _record("queue_roundtrip", _check_queue_roundtrip())

    # Cleanup best-effort.
    try:
        await conn.delete(temp_key)
        await conn.delete(temp_queue)
    except Exception:
        pass
    finally:
        try:
            await _close_redis(conn)
        except Exception:
            pass

    all_ok = all(item.ok for item in results)
    return {
        "timestamp_epoch_s": time.time(),
        "redis_url": redis_url,
        "queue_name": queue_name,
        "key_prefix": key_prefix,
        "checks": [asdict(item) for item in results],
        "pass": all_ok,
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate async Redis path used by Lean UI worker/API."
    )
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--queue-name", default="lean_async_light")
    parser.add_argument("--key-prefix", default="lean_async")
    parser.add_argument("--timeout-sec", type=int, default=5)
    parser.add_argument("--output-json", default="")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    redis_url = args.redis_url.strip() or ""
    if not redis_url:
        # Late import to avoid hard dependency for env-only parsing
        import os

        redis_url = (os.getenv("LEAN_SERVER_REDIS_URL") or "").strip()

    if not redis_url:
        raise SystemExit(
            "Missing Redis URL. Provide --redis-url or set LEAN_SERVER_REDIS_URL."
        )

    summary = asyncio.run(
        run_checks(
            redis_url=redis_url,
            queue_name=args.queue_name,
            key_prefix=args.key_prefix,
            timeout_sec=args.timeout_sec,
        )
    )

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(0 if summary["pass"] else 2)


if __name__ == "__main__":
    main()
