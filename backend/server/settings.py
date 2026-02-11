import os
import re
from enum import Enum
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

    lean_version: str = "v4.15.0"
    repl_path: Path = BASE_DIR / "repl/.lake/build/bin/repl"
    project_dir: Path = BASE_DIR / "mathlib4"

    max_repls: int = max((os.cpu_count() or 1) - 1, 1)
    max_repl_uses: int = -1
    max_repl_mem: int = 8
    max_wait: int = 60

    init_repls: dict[str, int] = {}

    database_url: str | None = None

    # Async queue API / worker
    async_enabled: bool = False
    redis_url: str | None = None
    async_queue_name: str = "lean_async_check"
    async_result_ttl_sec: int = 86400
    async_backlog_limit: int = 50000
    async_max_queue_wait_sec: int = 600
    async_redis_key_prefix: str = "lean_async"
    async_use_in_memory_backend: bool = False

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


settings = Settings()
