# =============================================================================
# File: c6_tools.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/tools/c6_tools.py
# Description: C9 tool adapters wrapping the C6 Validation Pipeline Registry.
#              Three tools: list plugins, trigger a validation run, fetch
#              findings for a run.
#
# @relation implements:R-100-015
# @relation uses:E-700-001
# @relation uses:E-700-002
# =============================================================================

from __future__ import annotations

from typing import Any

from ay_platform_core.c6_validation.models import CodeArtifact, RunTriggerRequest
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c9_mcp.tools.base import Tool, ToolDispatchError


def build_tools(c6: ValidationService) -> list[Tool]:
    """Return the full list of C6-backed tools."""
    return [
        _list_plugins_tool(c6),
        _trigger_validation_tool(c6),
        _list_findings_tool(c6),
    ]


# ---------------------------------------------------------------------------
# c6_list_plugins
# ---------------------------------------------------------------------------


def _list_plugins_tool(c6: ValidationService) -> Tool:
    async def handler(_args: dict[str, Any]) -> dict[str, Any]:
        plugins = await c6.alist_plugins()
        domains = await c6.alist_domains()
        return {
            "plugins": [p.model_dump(mode="json") for p in plugins],
            "domains": domains,
        }

    return Tool(
        name="c6_list_plugins",
        description=(
            "List validation plugins registered with C6 plus the set of "
            "declared production domains."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c6_trigger_validation
# ---------------------------------------------------------------------------


def _trigger_validation_tool(c6: ValidationService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        domain = _required_str(args, "domain")
        project_id = _required_str(args, "project_id")

        check_ids_raw = args.get("check_ids", [])
        if not isinstance(check_ids_raw, list) or not all(
            isinstance(x, str) for x in check_ids_raw
        ):
            raise ToolDispatchError("check_ids must be a list of strings")

        requirements_raw = args.get("requirements", [])
        if not isinstance(requirements_raw, list) or not all(
            isinstance(x, dict) for x in requirements_raw
        ):
            raise ToolDispatchError("requirements must be a list of objects")

        artifacts_raw = args.get("artifacts", [])
        if not isinstance(artifacts_raw, list):
            raise ToolDispatchError("artifacts must be a list")
        try:
            artifacts = [CodeArtifact.model_validate(a) for a in artifacts_raw]
        except Exception as exc:
            raise ToolDispatchError(f"invalid artifact: {exc}") from exc

        payload = RunTriggerRequest(
            domain=domain,
            project_id=project_id,
            check_ids=list(check_ids_raw),
            requirements=requirements_raw,
            artifacts=artifacts,
        )
        response = await c6.trigger_run(
            payload,
            requirements=requirements_raw,
            artifacts=artifacts,
        )
        return response.model_dump(mode="json")

    return Tool(
        name="c6_trigger_validation",
        description=(
            "Trigger a new validation run for a given domain + project. "
            "Returns a 202-style response carrying the new run_id. The run "
            "executes asynchronously; poll c6_list_findings once the run "
            "reports `status=completed`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "project_id": {"type": "string"},
                "check_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "requirements": {"type": "array"},
                "artifacts": {"type": "array"},
            },
            "required": ["domain", "project_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c6_list_findings
# ---------------------------------------------------------------------------


def _list_findings_tool(c6: ValidationService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        run_id = _required_str(args, "run_id")
        limit = _optional_int(args, "limit", default=100, low=1, high=1_000)
        offset = _optional_int(args, "offset", default=0, low=0, high=1_000_000)
        page = await c6.list_findings(run_id, limit=limit, offset=offset)
        return page.model_dump(mode="json")

    return Tool(
        name="c6_list_findings",
        description=(
            "List findings of a previously triggered validation run. "
            "Use `limit` / `offset` to paginate."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["run_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Arg helpers
# ---------------------------------------------------------------------------


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ToolDispatchError(f"missing or invalid required argument: {key}")
    return value


def _optional_int(
    args: dict[str, Any], key: str, *, default: int, low: int, high: int
) -> int:
    raw: Any = args.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolDispatchError(f"argument {key!r} must be an integer")
    value: int = raw
    if value < low or value > high:
        raise ToolDispatchError(
            f"argument {key!r} must be in [{low}, {high}]"
        )
    return value
