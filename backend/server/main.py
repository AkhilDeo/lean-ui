import asyncio
import textwrap
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Awaitable, Callable

from fastapi import FastAPI, Request, Response
from loguru import logger
from pydantic.json_schema import GenerateJsonSchema

from .__version__ import __version__
from .async_jobs import create_async_jobs
from .db import db
from .manager import Manager
from .routers.async_check import router as async_check_router
from .routers.backward import router as backward_router
from .routers.check import router as check_router
from .routers.health import router as health_router
from .routers.runtimes import router as runtimes_router
from .runtime_gateway import RuntimeGateway
from .runtime_registry import build_runtime_registry, validate_runtime_configuration
from .runtime_scaler import RuntimeIdleScaler
from .settings import Environment, Settings
from .worker import run_worker

SYNC_READY_HEADER = "import Mathlib"


def no_sort(self: GenerateJsonSchema, value: Any, parent_key: Any = None) -> Any:
    return value


setattr(GenerateJsonSchema, "sort", no_sort)

def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info(
            "Running Kimina Lean Server [bold]'v{}'[/bold] in [bold]{}[/bold] mode on {}:{} with Lean version: '{}'",
            __version__,
            settings.environment.value,
            settings.host,
            settings.port,
            settings.lean_version,
        )
        if settings.environment == Environment.prod and settings.api_key is None:
            raise RuntimeError(
                "LEAN_SERVER_API_KEY must be configured when LEAN_SERVER_ENVIRONMENT=prod"
            )
        if settings.database_url:
            logger.info(f"Database URL = '{settings.database_url}'")
            try:
                await asyncio.wait_for(db.connect(), timeout=10.0)
                logger.info("DB connected: {}", db.connected)
            except asyncio.TimeoutError:
                logger.warning("Database connection timed out after 10 seconds")
            except Exception as e:
                logger.exception("Failed to connect to database: %s", e)

        runtime_registry = build_runtime_registry(settings.default_runtime_id)
        validate_runtime_configuration(settings, runtime_registry)
        app.state.settings = settings
        app.state.runtime_registry = runtime_registry
        app.state.runtime_ready_event = asyncio.Event()
        app.state.runtime_ready_reason = None

        def set_runtime_ready(*, ready: bool, reason: str | None) -> None:
            if ready:
                app.state.runtime_ready_event.set()
            else:
                app.state.runtime_ready_event.clear()
            app.state.runtime_ready_reason = reason

        if settings.gateway_enabled:
            set_runtime_ready(ready=True, reason=None)
        else:
            set_runtime_ready(
                ready=False,
                reason=f"Runtime {settings.runtime_id} verifier warmup is still in progress.",
            )
        app.state.runtime_gateway = (
            RuntimeGateway(settings, runtime_registry) if settings.gateway_enabled else None
        )
        runtime_readiness_task: asyncio.Task[None] | None = None

        if settings.async_enabled:
            app.state.async_jobs = await create_async_jobs(settings)
            logger.info(
                "Async queue API enabled: queues=['{}','{}'] metrics_enabled={}",
                settings.async_queue_name_light,
                settings.async_queue_name_heavy,
                settings.async_metrics_enabled,
            )
        else:
            app.state.async_jobs = None

        if not settings.gateway_enabled:
            manager = Manager(
                max_repls=settings.max_repls,
                max_repl_uses=settings.max_repl_uses,
                max_repl_mem=settings.max_repl_mem,
                init_repls=settings.init_repls,
                min_host_free_mem=settings.min_host_free_mem,
            )
            app.state.manager = manager

            async def _warm_runtime_readiness() -> None:
                try:
                    logger.info(
                        "Starting runtime readiness warmup for {} with header '{}'",
                        settings.runtime_id,
                        SYNC_READY_HEADER,
                    )
                    await manager.ensure_warm_repls({SYNC_READY_HEADER: 1}, timeout=60.0)
                    set_runtime_ready(ready=True, reason=None)
                    logger.info(
                        "Runtime readiness warmup completed for {}",
                        settings.runtime_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    set_runtime_ready(
                        ready=False,
                        reason=f"Runtime {settings.runtime_id} verifier warmup failed: {e}",
                    )
                    logger.exception("Runtime readiness warmup failed: {}", e)

            async def _init_repls_background() -> None:
                try:
                    logger.info("Starting background REPL initialization...")
                    await manager.initialize_repls()
                    logger.info("Background REPL initialization completed")
                except Exception as e:
                    logger.exception("Failed to initialize REPLs in background: %s", e)

            runtime_readiness_task = asyncio.create_task(
                _warm_runtime_readiness(),
                name="runtime-readiness-warmup",
            )
            asyncio.create_task(_init_repls_background())

            if settings.async_enabled and settings.embedded_worker_enabled:
                app.state.embedded_worker_task = asyncio.create_task(
                    run_worker(
                        settings,
                        jobs=app.state.async_jobs,
                        manager=manager,
                        manage_resources=False,
                    ),
                    name="embedded-runtime-worker",
                )
            else:
                app.state.embedded_worker_task = None

            if settings.async_enabled:
                runtime_scaler = RuntimeIdleScaler(settings, app.state.async_jobs)
                await runtime_scaler.start()
                app.state.runtime_scaler = runtime_scaler
            else:
                app.state.runtime_scaler = None
        else:
            app.state.manager = None
            app.state.embedded_worker_task = None
            app.state.runtime_scaler = None

        if settings.environment == Environment.dev:
            threading.Timer(
                0.1,
                lambda: logger.info(
                    "Try me with:\n"
                    + textwrap.indent(
                        "curl --request POST \\\n"
                        "  --url http://localhost:8000/api/check \\\n"
                        "  --header 'Content-Type: application/json' \\\n"
                        "  --data '{"
                        '"snippets":[{"id":"check-nat-test","code":"#check Nat"}]'
                        "}' | jq\n",
                        "  ",
                    )
                ),
            ).start()

        yield

        runtime_scaler = getattr(app.state, "runtime_scaler", None)
        if runtime_scaler is not None:
            await runtime_scaler.stop()

        if runtime_readiness_task is not None:
            runtime_readiness_task.cancel()
            await asyncio.gather(runtime_readiness_task, return_exceptions=True)

        embedded_worker_task = getattr(app.state, "embedded_worker_task", None)
        if embedded_worker_task is not None:
            embedded_worker_task.cancel()
            await asyncio.gather(embedded_worker_task, return_exceptions=True)

        async_jobs = getattr(app.state, "async_jobs", None)
        if async_jobs is not None:
            await async_jobs.close()
        runtime_gateway = getattr(app.state, "runtime_gateway", None)
        if runtime_gateway is not None:
            await runtime_gateway.close()
        manager = getattr(app.state, "manager", None)
        if manager is not None:
            await manager.cleanup()
        await db.disconnect()

        logger.info("Disconnected from database")

    app = FastAPI(
        lifespan=lifespan,
        title="Kimina Lean Server API",
        description="Check Lean 4 snippets at scale via REPL",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        logger=logger,
    )

    app.include_router(
        check_router,
        prefix="/api",
        tags=["check"],
    )
    app.include_router(
        async_check_router,
        prefix="/api",
        tags=["async-check"],
    )
    app.include_router(
        health_router,
        tags=["health"],
    )
    app.include_router(
        runtimes_router,
        tags=["runtimes"],
    )
    app.include_router(
        backward_router,
        tags=["backward"],
    )

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        logger.bind(path=request.url.path, method=request.method).debug("-> request")
        response = await call_next(request)
        logger.bind(status_code=response.status_code).debug("<- response")
        return response

    return app
