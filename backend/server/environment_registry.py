from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException
from kimina_client import Code, Snippet

from .settings import Settings


@dataclass(frozen=True)
class LeanEnvironmentInfo:
    id: str
    display_name: str
    lean_version: str
    project_label: str
    project_type: str
    url: str | None
    import_prefixes: tuple[str, ...]
    selectable: bool
    auto_routable: bool
    is_default: bool


@dataclass(frozen=True)
class ResolvedEnvironmentSelection:
    requested_environment: str
    resolved_environment: LeanEnvironmentInfo


def _line_requires_prefix(line: str, prefix: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("import "):
        return False
    modules = stripped[len("import ") :].split()
    return any(module == prefix or module.startswith(prefix + ".") for module in modules)


def _extract_codes(snippets: Iterable[Snippet | Code | str]) -> list[str]:
    codes: list[str] = []
    for snippet in snippets:
        if isinstance(snippet, str):
            codes.append(snippet)
        elif isinstance(snippet, Snippet):
            codes.append(snippet.code)
        else:
            codes.append(snippet.get_proof_content())
    return codes


def build_environment_registry(settings: Settings) -> list[LeanEnvironmentInfo]:
    registry: list[LeanEnvironmentInfo] = []
    for entry in settings.gateway_environments:
        env_id = str(entry["id"])
        registry.append(
            LeanEnvironmentInfo(
                id=env_id,
                display_name=str(entry.get("display_name") or entry.get("label") or env_id),
                lean_version=str(entry.get("lean_version") or settings.lean_version),
                project_label=str(entry.get("project_label") or settings.project_label),
                project_type=str(entry.get("project_type") or settings.project_type),
                url=str(entry["url"]).rstrip("/") if entry.get("url") else None,
                import_prefixes=tuple(
                    str(prefix) for prefix in cast_list(entry.get("import_prefixes"))
                ),
                selectable=bool(entry.get("selectable", True)),
                auto_routable=bool(entry.get("auto_routable", True)),
                is_default=env_id == settings.gateway_default_environment,
            )
        )

    if not any(env.id == settings.environment_id for env in registry):
        registry.append(
            LeanEnvironmentInfo(
                id=settings.environment_id,
                display_name=settings.project_label,
                lean_version=settings.lean_version,
                project_label=settings.project_label,
                project_type=settings.project_type,
                url=None,
                import_prefixes=(),
                selectable=True,
                auto_routable=True,
                is_default=settings.environment_id == settings.gateway_default_environment,
            )
        )

    if not any(env.is_default for env in registry):
        registry = [
            LeanEnvironmentInfo(
                **{
                    **env.__dict__,
                    "is_default": env.id == settings.environment_id,
                }
            )
            for env in registry
        ]

    return registry


def cast_list(value: object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError("Expected a list")


def environment_metadata_payload(environment: LeanEnvironmentInfo) -> dict[str, str]:
    return {
        "environment_id": environment.id,
        "lean_version": environment.lean_version,
        "project_label": environment.project_label,
    }


def find_environment_by_id(
    registry: Iterable[LeanEnvironmentInfo], environment_id: str
) -> LeanEnvironmentInfo | None:
    for environment in registry:
        if environment.id == environment_id:
            return environment
    return None


def detect_required_environment_id(
    codes: Iterable[str], registry: Iterable[LeanEnvironmentInfo]
) -> str | None:
    matches: set[str] = set()
    specific_envs = [
        environment
        for environment in registry
        if environment.import_prefixes and environment.auto_routable
    ]

    for code in codes:
        for line in code.splitlines():
            for environment in specific_envs:
                if any(
                    _line_requires_prefix(line, prefix)
                    for prefix in environment.import_prefixes
                ):
                    matches.add(environment.id)

    if not matches:
        return None
    if len(matches) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Request mixes snippets that require different Lean environments. "
                "Submit one environment per batch."
            ),
        )
    return next(iter(matches))


def resolve_environment_selection(
    *,
    requested_environment: str | None,
    snippets: Iterable[Snippet | Code | str],
    settings: Settings,
) -> ResolvedEnvironmentSelection:
    registry = build_environment_registry(settings)
    codes = _extract_codes(snippets)
    required_environment_id = detect_required_environment_id(codes, registry)

    if requested_environment is None:
        target_id = settings.gateway_default_environment
        requested_label = settings.gateway_default_environment
    elif requested_environment == "auto":
        target_id = required_environment_id or settings.gateway_default_environment
        requested_label = "auto"
    else:
        target_id = requested_environment
        requested_label = requested_environment

    target_environment = find_environment_by_id(registry, target_id)
    if target_environment is None and requested_environment is None:
        target_id = settings.environment_id
        requested_label = settings.environment_id
        target_environment = find_environment_by_id(registry, target_id)
    if target_environment is None:
        supported = ", ".join(environment.id for environment in registry)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown environment '{target_id}'. Supported environments: {supported}.",
        )

    if required_environment_id and target_environment.id != required_environment_id:
        required_environment = find_environment_by_id(registry, required_environment_id)
        required_label = (
            required_environment.project_label
            if required_environment is not None
            else required_environment_id
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested environment '{target_environment.id}' is incompatible with "
                f"imports that require '{required_environment_id}' ({required_label})."
            ),
        )

    return ResolvedEnvironmentSelection(
        requested_environment=requested_label,
        resolved_environment=target_environment,
    )


def list_public_environments(settings: Settings) -> tuple[str, list[dict[str, object]]]:
    registry = build_environment_registry(settings)
    return (
        settings.gateway_default_environment,
        [
            {
                "id": environment.id,
                "display_name": environment.display_name,
                "lean_version": environment.lean_version,
                "project_label": environment.project_label,
                "project_type": environment.project_type,
                "selectable": environment.selectable,
                "auto_routable": environment.auto_routable,
                "is_default": environment.is_default,
            }
            for environment in registry
            if environment.selectable
        ],
    )


def add_environment_metadata_to_diagnostics(
    diagnostics: dict[str, object] | None,
    environment: LeanEnvironmentInfo,
) -> dict[str, object]:
    metadata = environment_metadata_payload(environment)
    if diagnostics is None:
        return dict(metadata)
    updated = dict(diagnostics)
    updated.update(metadata)
    return updated


def environment_headers(environment: LeanEnvironmentInfo) -> dict[str, str]:
    return {
        "X-Lean-Environment-ID": environment.id,
        "X-Lean-Version": environment.lean_version,
        "X-Lean-Project-Label": environment.project_label,
    }
