from fastapi import APIRouter, Depends, Response
from kimina_client import BackwardResponse, Snippet, VerifyRequestBody, VerifyResponse
from kimina_client.models import extend

from ..auth import require_key
from ..environment_registry import environment_headers, resolve_environment_selection
from ..gateway_proxy import proxy_verify_request
from ..manager import Manager
from ..request_policy import normalize_request_policy
from ..settings import Settings
from .check import get_manager, get_runtime_settings, run_checks

router = APIRouter()


@router.post(
    "/one_pass_verify_batch",
    response_model=VerifyResponse,
    response_model_exclude_none=True,
)
@router.post("/verify", response_model=VerifyResponse, response_model_exclude_none=True)
async def one_pass_verify_batch(
    body: VerifyRequestBody,
    raw_response: Response,
    manager: Manager = Depends(get_manager),
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> VerifyResponse:
    """Backward compatible endpoint: accepts both 'proof' / 'code' fields."""

    codes = body.codes
    selection = resolve_environment_selection(
        requested_environment=body.environment,
        snippets=codes,
        settings=runtime_settings,
    )
    resolved_environment = selection.resolved_environment
    for key, value in environment_headers(resolved_environment).items():
        raw_response.headers[key] = value

    snippets = [
        Snippet(id=str(code.custom_id), code=code.get_proof_content()) for code in codes
    ]

    policy = normalize_request_policy(
        timeout=float(body.timeout),
        debug=False,
        reuse=not body.disable_cache,
        settings=runtime_settings,
    )
    timeout = policy.timeout
    debug = policy.debug
    reuse = policy.reuse
    infotree = body.infotree_type

    normalized_body = body.model_copy(
        update={
            "timeout": int(timeout),
            "disable_cache": not reuse,
            "environment": resolved_environment.id,
        }
    )

    if resolved_environment.id != runtime_settings.environment_id:
        return await proxy_verify_request(
            request=normalized_body,
            target_environment=resolved_environment,
            settings=runtime_settings,
        )

    check_responses = await run_checks(
        snippets,
        float(timeout),
        debug,
        manager,
        reuse,
        infotree,
        body.include_sorry_details,
    )

    results: list[BackwardResponse] = []

    for check_response in check_responses:
        extended_response = extend(check_response.response, time=check_response.time)

        result = BackwardResponse(
            custom_id=check_response.id,
            error=check_response.error,
            response=extended_response,
        )
        results.append(result)

    return VerifyResponse(results=results)
