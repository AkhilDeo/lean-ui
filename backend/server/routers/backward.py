from fastapi import APIRouter, Depends
from kimina_client import BackwardResponse, Snippet, VerifyRequestBody, VerifyResponse
from kimina_client.models import extend

from ..auth import require_key
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
    manager: Manager = Depends(get_manager),
    runtime_settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(require_key),
) -> VerifyResponse:
    """Backward compatible endpoint: accepts both 'proof' / 'code' fields."""

    codes = body.codes
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

    check_responses = await run_checks(
        snippets, float(timeout), debug, manager, reuse, infotree
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
