from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from pathlib import Path

from fastapi import HTTPException

from .manager import Manager, ReplCapacityPool
from .runtime_registry import RuntimeRegistry
from .settings import Settings


def runtime_slug(runtime_id: str) -> str:
    return runtime_id.lower().replace(".", "_").replace("-", "_")


class RuntimeManagerRegistry:
    def __init__(self, settings: Settings, runtime_registry: RuntimeRegistry) -> None:
        self._settings = settings
        self._runtime_registry = runtime_registry
        self._managers: dict[str, Manager] = {}
        self._manager_lock = threading.Lock()
        self._capacity_pool = ReplCapacityPool(settings.max_total_repls)

    def _paths_for_runtime(self, runtime_id: str) -> tuple[Path, Path]:
        if not self._settings.multi_runtime_enabled:
            return Path(self._settings.repl_path), Path(self._settings.project_dir)
        root = Path(self._settings.runtime_root) / runtime_slug(runtime_id)
        return root / "repl/.lake/build/bin/repl", root / "mathlib4"

    def _install_runtime(self, runtime_id: str) -> None:
        setup_script = Path("/usr/local/bin/setup.sh")
        if not setup_script.exists():
            raise HTTPException(
                status_code=503,
                detail=f"Runtime {runtime_id} is not installed and setup.sh is unavailable",
            )
        env = os.environ.copy()
        env["LEAN_SERVER_RUNTIME_IDS"] = runtime_id
        env["LEAN_SERVER_RUNTIME_ROOT"] = str(self._settings.runtime_root)
        env.setdefault("LEAN_SERVER_LEAN_VERSION", self._settings.lean_version)
        try:
            subprocess.run(
                [str(setup_script)],
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Timed out installing runtime {runtime_id}",
            ) from exc
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "").strip().splitlines()
            tail = "\n".join(output[-20:])
            raise HTTPException(
                status_code=503,
                detail=f"Failed to install runtime {runtime_id}: {tail}",
            ) from exc

    def get(self, runtime_id: str) -> Manager:
        if self._runtime_registry.get(runtime_id) is None:
            raise HTTPException(status_code=400, detail=f"Unknown runtime_id: {runtime_id}")
        manager = self._managers.get(runtime_id)
        if manager is not None:
            return manager
        with self._manager_lock:
            manager = self._managers.get(runtime_id)
            if manager is not None:
                return manager
            repl_path, project_dir = self._paths_for_runtime(runtime_id)
            if not repl_path.exists() or not project_dir.exists():
                self._install_runtime(runtime_id)
            if not repl_path.exists() or not project_dir.exists():
                raise HTTPException(
                    status_code=503,
                    detail=f"Runtime {runtime_id} did not produce expected artifacts",
                )
            manager = Manager(
                max_repls=self._settings.max_repls,
                max_repl_uses=self._settings.max_repl_uses,
                max_repl_mem=self._settings.max_repl_mem,
                init_repls=self._settings.init_repls,
                min_host_free_mem=self._settings.min_host_free_mem,
                startup_concurrency_limit=self._settings.async_startup_concurrency_limit,
                repl_path=repl_path,
                project_dir=project_dir,
                capacity_pool=self._capacity_pool,
            )
            self._managers[runtime_id] = manager
            return manager

    async def get_async(self, runtime_id: str) -> Manager:
        return await asyncio.to_thread(self.get, runtime_id)

    def known_runtime_ids(self) -> list[str]:
        return self._runtime_registry.known_runtime_ids()

    async def initialize_default(self) -> None:
        await self.get(self._settings.default_runtime_id).initialize_repls()

    async def cleanup(self) -> None:
        for manager in list(self._managers.values()):
            await manager.cleanup()
        self._managers.clear()
