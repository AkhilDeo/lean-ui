import os
import re
from enum import Enum
import json
from pathlib import Path
from typing import cast

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    dev = "dev"
    prod = "prod"


BASE_DIR = Path(__file__).resolve().parent.parent  # Repository root directory


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    # Railway provides PORT at runtime. Use 8000 as fallback for local dev
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = "INFO"

    api_key: str | None = None

    environment: Environment = Environment.dev

    lean_version: str = "v4.9.0"
    runtime_id: str = "v4.9.0"
    default_runtime_id: str = "v4.9.0"
    gateway_enabled: bool = False
    embedded_worker_enabled: bool = False
    gateway_sync_proxy_timeout_sec: int = 300
    gateway_wake_replicas: int = 1
    runtime_idle_ttl_sec: int = 15 * 60
    runtime_service_id: str | None = None
    runtime_service_name: str = ""
    railway_environment_id: str | None = None
    railway_region: str = "us-east4-eqdc4a"
    repl_path: Path = BASE_DIR / "repl/.lake/build/bin/repl"
    project_dir: Path = BASE_DIR / "mathlib4"

    max_repls: int = max((os.cpu_count() or 1) - 1, 1)
    max_repl_uses: int = -1
    max_repl_mem: int = 8
    max_wait: int = 300

    init_repls: dict[str, int] = {}

    database_url: str | None = None

    # Async queue API / worker
    async_enabled: bool = False
    redis_url: str | None = None
    async_queue_name_light: str = "lean_async_light"
    async_queue_name_heavy: str = "lean_async_heavy"
    async_result_ttl_sec: int = 86400
    async_backlog_limit: int = 100000
    async_max_queue_wait_sec: int = 600
    async_redis_key_prefix: str = "lean_async"
    async_use_in_memory_backend: bool = False
    async_metrics_enabled: bool = True
    async_worker_concurrency: int | None = None
    async_worker_queue_tier: str = "all"
    async_worker_retries: int = 3
    async_admission_queue_limit: int = 0
    async_alert_max_oldest_queued_age_sec: int = 60
    async_light_retry_attempts: int = 5
    async_heavy_retry_attempts: int = 7
    async_retry_backoff_initial_ms: int = 250
    async_retry_backoff_max_ms: int = 5000
    async_heavy_body_bytes: int = 8 * 1024
    async_heavy_line_count: int = 250
    async_startup_concurrency_limit: int | None = None
    async_circuit_breaker_window: int = 10
    async_circuit_breaker_pause_sec: int = 30
    async_circuit_breaker_failure_rate: float = 0.10
    async_light_warm_repls: dict[str, int] = {"import Mathlib": 5}
    async_heavy_warm_repls: dict[str, int] = {
        "import Mathlib": 2,
        "import Mathlib\nimport Aesop": 2,
    }

    # Railway worker autoscaler
    autoscale_enabled: bool = False
    autoscale_railway_token: str | None = None
    autoscale_worker_service_id: str = "80159ca4-ee4e-4023-92c8-bbaf89c5ea04"
    autoscale_min_replicas: int = 1
    autoscale_max_replicas: int = 12
    autoscale_cooldown_sec: int = 600
    autoscale_throttle_sec: int = 60
    autoscale_check_interval_sec: int = 30

    # Request policy hardening
    request_timeout_max_sec: int = 300
    allow_client_debug: bool = False
    allow_client_timeout_override: bool = True

    # Host-level memory guard for REPL creation.
    min_host_free_mem: int = 4

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="LEAN_SERVER_"
    )

    @field_validator("max_repl_mem", mode="before")
    def _parse_max_mem(cls, v: str) -> int:
        if isinstance(v, int):
            return cast(int, v * 1024)
        m = re.fullmatch(r"(\d+)([MmGg])", v)
        if m:
            n, unit = m.groups()
            n = int(n)
            return n if unit.lower() == "m" else n * 1024
        raise ValueError("max_repl_mem must be an int or '<number>[M|G]'")

    @field_validator("max_repls", mode="before")
    @classmethod
    def _parse_max_repls(cls, v: int | str) -> int:
        if isinstance(v, str) and v.strip() == "":
            return os.cpu_count() or 1
        return cast(int, v)

    @field_validator("min_host_free_mem", mode="before")
    @classmethod
    def _parse_min_host_free_mem(cls, v: int | str) -> int:
        if isinstance(v, int):
            return cast(int, v * 1024)
        m = re.fullmatch(r"(\d+)([MmGg])", v)
        if m:
            n, unit = m.groups()
            n = int(n)
            return n if unit.lower() == "m" else n * 1024
        raise ValueError("min_host_free_mem must be an int or '<number>[M|G]'")

    @field_validator("async_worker_retries")
    @classmethod
    def _validate_async_worker_retries(cls, v: int) -> int:
        if v < 1:
            raise ValueError("async_worker_retries must be >= 1")
        return v

    @field_validator("async_worker_concurrency", mode="before")
    @classmethod
    def _parse_async_worker_concurrency(cls, v: int | str | None) -> int | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        parsed = int(v)
        if parsed < 1:
            raise ValueError("async_worker_concurrency must be >= 1 when provided")
        return parsed

    @field_validator("async_admission_queue_limit", "async_alert_max_oldest_queued_age_sec")
    @classmethod
    def _validate_non_negative_async_limits(cls, v: int) -> int:
        if v < 0:
            raise ValueError("async queue limits must be >= 0")
        return v

    @field_validator("request_timeout_max_sec")
    @classmethod
    def _validate_request_timeout_max_sec(cls, v: int) -> int:
        if v < 0:
            raise ValueError("request_timeout_max_sec must be >= 0")
        return v

    @field_validator(
        "async_light_retry_attempts",
        "async_heavy_retry_attempts",
        "async_retry_backoff_initial_ms",
        "async_retry_backoff_max_ms",
        "async_heavy_body_bytes",
        "async_heavy_line_count",
        "async_circuit_breaker_window",
        "async_circuit_breaker_pause_sec",
        "gateway_sync_proxy_timeout_sec",
        "gateway_wake_replicas",
        "runtime_idle_ttl_sec",
    )
    @classmethod
    def _validate_positive_async_ints(cls, v: int) -> int:
        if v < 1:
            raise ValueError("async verifier tuning integers must be >= 1")
        return v

    @field_validator("async_circuit_breaker_failure_rate")
    @classmethod
    def _validate_failure_rate(cls, v: float) -> float:
        if v <= 0 or v > 1:
            raise ValueError("async_circuit_breaker_failure_rate must be in (0, 1]")
        return v

    @field_validator("autoscale_railway_token", mode="before")
    @classmethod
    def _resolve_railway_token(cls, v: str | None) -> str | None:
        if v:
            return v
        return os.getenv("RAILWAY_TOKEN") or os.getenv("RAILWAY_API_TOKEN") or None

    @field_validator("async_worker_queue_tier")
    @classmethod
    def _validate_worker_queue_tier(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in {"all", "light", "heavy"}:
            raise ValueError("async_worker_queue_tier must be one of: all, light, heavy")
        return normalized

    @field_validator("async_startup_concurrency_limit", mode="before")
    @classmethod
    def _parse_async_startup_concurrency_limit(
        cls, v: int | str | None
    ) -> int | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        parsed = int(v)
        if parsed < 1:
            raise ValueError("async_startup_concurrency_limit must be >= 1 when provided")
        return parsed

    @field_validator("async_light_warm_repls", "async_heavy_warm_repls", mode="before")
    @classmethod
    def _parse_warm_repls(cls, v: dict[str, int] | str) -> dict[str, int]:
        if isinstance(v, dict):
            parsed = v
        elif isinstance(v, str):
            if not v.strip():
                return {}
            try:
                raw = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError("warm REPL settings must be valid JSON objects") from exc
            if not isinstance(raw, dict):
                raise ValueError("warm REPL settings must be JSON objects")
            parsed = raw
        else:
            raise ValueError("warm REPL settings must be dicts or JSON object strings")

        normalized: dict[str, int] = {}
        for key, value in parsed.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("warm REPL headers must be non-empty strings")
            count = int(value)
            if count < 0:
                raise ValueError("warm REPL counts must be >= 0")
            normalized[key] = count
        return normalized


settings = Settings()
