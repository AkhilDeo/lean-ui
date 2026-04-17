import asyncio
import json
from typing import TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from kimina_client import CheckRequest, Infotree, ReplResponse, Snippet
from kimina_client.models import CheckResponse, CommandResponse, Pos
from loguru import logger

from ..auth import require_key
from ..db import db
from ..errors import NoAvailableReplError
from ..manager import Manager
from ..prisma_client import prisma
from ..request_policy import normalize_check_request
from ..repl import Repl
from ..runtime_gateway import RuntimeGateway
from ..settings import Settings, settings as default_settings
from ..split import split_snippet

router = APIRouter()
TaskResultT = TypeVar("TaskResultT")


def get_manager(request: Request) -> Manager:
    """Dependency: retrieve the REPL manager from app state"""
    return cast(Manager, request.app.state.manager)


def get_optional_manager(request: Request) -> Manager | None:
    return cast(Manager | None, getattr(request.app.state, "manager", None))


def get_runtime_settings(request: Request) -> Settings:
    cfg = getattr(request.app.state, "settings", None)
    if cfg is None:
        return default_settings
    return cast(Settings, cfg)


def get_runtime_gateway(request: Request) -> RuntimeGateway | None:
    return cast(RuntimeGateway | None, getattr(request.app.state, "runtime_gateway", None))


def ensure_runtime_request_allowed(runtime_settings: Settings, runtime_id: str) -> None:
    if runtime_settings.gateway_enabled:
        return
    if runtime_id != runtime_settings.runtime_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Runtime service for {runtime_settings.runtime_id} "
                f"cannot serve runtime_id: {runtime_id}"
            ),
        )


def _shift_line(pos: Pos | None, offset: int) -> None:
    if not pos:
        return
    line = pos.get("line")
    pos["line"] = line + offset


def _apply_header_offset(response: ReplResponse, offset: int) -> None:
    if offset <= 0 or response.error is not None:
        return

    payload = response.response
    if not payload:
        return

    command_response = cast(CommandResponse, payload)

    messages = command_response.get("messages")
    if not messages:
        return
    for message in messages:
        pos = message.get("pos")
        _shift_line(pos, offset)
        end_pos = message.get("endPos")
        _shift_line(end_pos, offset)

    sorries = command_response.get("sorries")
    if not sorries:
        return
    for sorry in sorries:
        pos = sorry.get("pos")
        _shift_line(pos, offset)
        end_pos = sorry.get("endPos")
        _shift_line(end_pos, offset)


def _log_body_response(repl: Repl, snippet_id: str, response: ReplResponse) -> None:
    logger.debug(
        "[{}] Response for [bold magenta]{}[/bold magenta] body ->\n{}",
        repl.uuid.hex[:8],
        snippet_id,
        json.dumps(response.model_dump(exclude_none=True), indent=2),
    )


async def wait_for_task_or_disconnect(
    task: asyncio.Task[TaskResultT],
    raw_request: Request,
    *,
    disconnect_poll_interval_sec: float = 0.1,
) -> TaskResultT:
    while True:
        done, _ = await asyncio.wait(
            {task},
            timeout=disconnect_poll_interval_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if done:
            return await task
        if await raw_request.is_disconnected():
            task.cancel()
            raise HTTPException(499, "Client disconnected") from None


async def run_checks(
    snippets: list[Snippet],
    timeout: float,
    debug: bool,
    manager: Manager,
    reuse: bool,
    infotree: Infotree | None = None,
) -> list[ReplResponse]:
    async def run_one(snippet: Snippet) -> ReplResponse:
        repl: Repl | None = None
        try:
            split_result = split_snippet(snippet.code)
            header = split_result.header
            body = split_result.body
            header_line_count = split_result.header_line_count
            try:
                repl = await manager.get_repl(header, snippet.id, reuse=reuse)
            except NoAvailableReplError:
                logger.exception("No available REPLs")
                raise HTTPException(429, "No available REPLs") from None
            except Exception as e:
                logger.exception("Failed to get REPL: %s", e)
                raise HTTPException(500, str(e)) from e

            # if reuse is false we should not run the header separate from body
            try:
                prep = await manager.prep(repl, snippet.id, timeout, debug)
                if prep and prep.error:
                    return prep
            except TimeoutError:
                error = f"Lean REPL header command timed out in {timeout} seconds"
                uuid_hex = repl.uuid.hex
                await manager.destroy_repl(repl)
                if db.connected:
                    await prisma.proof.create(
                        data={
                            "id": snippet.id,
                            "code": header,
                            "time": timeout,
                            "error": error,
                            "repl": {
                                "connect": {"uuid": uuid_hex},
                            },
                        }  # type: ignore
                    )
                return ReplResponse(
                    id=snippet.id,
                    error=error,
                    time=timeout,
                    diagnostics={
                        "repl_uuid": uuid_hex,
                    },
                )
            except Exception as e:
                logger.error("REPL prep failed")
                await manager.destroy_repl(repl)
                raise HTTPException(500, str(e)) from e

            try:
                resp = await repl.send_timeout(
                    Snippet(id=snippet.id, code=body), timeout, infotree=infotree
                )
                _apply_header_offset(resp, header_line_count)
            except TimeoutError:
                error = f"Lean REPL command timed out in {timeout} seconds"
                uuid_hex = repl.uuid.hex
                await manager.destroy_repl(repl)
                if db.connected:
                    await prisma.proof.create(
                        data={
                            "id": snippet.id,
                            "code": body,
                            "time": timeout,
                            "error": error,
                            "repl": {
                                "connect": {"uuid": uuid_hex},
                            },
                        }  # type: ignore
                    )
                resp = ReplResponse(
                    id=snippet.id,
                    error=error,
                    time=timeout,
                    diagnostics={
                        "repl_uuid": uuid_hex,
                    },
                )
                _log_body_response(repl, snippet.id, resp)
                return resp
            except Exception as e:
                logger.exception("Snippet execution failed")
                await manager.destroy_repl(repl)
                raise HTTPException(500, str(e)) from e
            else:
                _log_body_response(repl, snippet.id, resp)
                await manager.release_repl(repl)
                # TODO: Try catch everything DB related
                if db.connected:
                    await prisma.proof.create(
                        data={
                            "id": snippet.id,
                            "code": body,
                            "diagnostics": json.dumps(
                                resp.diagnostics if resp.diagnostics else None
                            ),
                            "response": json.dumps(
                                resp.response if resp.response else None
                            ),
                            "time": resp.time,
                            "error": resp.error,
                            "repl": {
                                "connect": {"uuid": repl.uuid.hex},
                            },
                        }  # type: ignore
                    )
                if not debug:
                    resp.diagnostics = None
                return resp
        except asyncio.CancelledError:
            if repl:
                await manager.destroy_repl(repl)  # Kill REPL on cancel
            raise

    results = await asyncio.gather(*(run_one(s) for s in snippets))
    return list(results)


@router.post(
    "/check",
    response_model=CheckResponse,
    response_model_exclude_none=True,
)
@router.post(
    "/check/",
    response_model=CheckResponse,
    response_model_exclude_none=True,
    include_in_schema=False,  # To not clutter OpenAPI spec.
)
async def check(
    request: CheckRequest,
    raw_request: Request,
    manager: Manager | None = Depends(get_optional_manager),
    runtime_settings: Settings = Depends(get_runtime_settings),
    runtime_gateway: RuntimeGateway | None = Depends(get_runtime_gateway),
    _: str = Depends(require_key),
) -> CheckResponse:
    normalized_request = normalize_check_request(request, runtime_settings)
    ensure_runtime_request_allowed(runtime_settings, normalized_request.runtime_id or "")

    if runtime_settings.gateway_enabled:
        if runtime_gateway is None:
            raise HTTPException(status_code=503, detail="Runtime gateway is not configured")
        runtime = runtime_gateway.require_runtime(normalized_request.runtime_id or "")
        payload = normalized_request.model_dump(mode="json", exclude_none=True)
        if await runtime_gateway.is_runtime_warm(runtime):
            proxied = await runtime_gateway.proxy_sync_check(runtime, payload)
            if proxied is not None:
                return proxied
        await runtime_gateway.wake_runtime(runtime)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Runtime {runtime.runtime_id} is cold and is starting up. "
                "Retry asynchronously via /api/async/check."
            ),
        )

    if manager is None:
        raise HTTPException(status_code=503, detail="REPL manager is not configured")

    task = asyncio.create_task(
        run_checks(
            normalized_request.snippets,
            float(normalized_request.timeout),
            normalized_request.debug,
            manager,
            normalized_request.reuse,
            normalized_request.infotree,
        )
    )
    results = await wait_for_task_or_disconnect(task, raw_request)
    return CheckResponse(results=results)
