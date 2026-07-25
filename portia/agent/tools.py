"""The in-process MCP server — the only place the SDK meets the engine.

Every tool here is a thin wrapper: validate nothing, decide nothing, just call
the matching function in ``handlers.py`` and hand back its evidence as JSON.
Keeping the wrappers this thin is the point — the logic lives in `handlers`
where it can be tested without the SDK, and this file stays a translation layer
we can swap if the harness ever changes.

Tool descriptions matter more than they look: they are what the agent reads to
decide *when* to reach for a check. Say when to call it, not just what it does.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from portia.agent import handlers, prompts
from portia.core.serialize import to_json

SERVER_NAME = "portia"

_READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": to_json(payload)}]}


def _failed(exc: Exception) -> dict[str, Any]:
    """Compose the message the agent reads, rather than leaking a bare traceback.

    An uncaught exception would still reach it as ``str(exc)``; going through
    here means we can add the context needed to pick a different move.
    """
    return {
        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
        "is_error": True,
    }


@tool(
    "get_context",
    prompts.tool("get_context"),
    {},
    annotations=_READ_ONLY,
)
async def get_context(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.get_context(**_dir(args)))
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return _failed(exc)


@tool(
    "describe_source",
    prompts.tool("describe_source"),
    {"source": str},
    annotations=_READ_ONLY,
)
async def describe_source(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.describe_source(args["source"], **_dir(args)))
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "profile_source",
    prompts.tool("profile_source"),
    {"source": str},
    annotations=_READ_ONLY,
)
async def profile_source(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.profile_source(args["source"], **_dir(args)))
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "set_group",
    prompts.tool("set_group"),
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short group name"},
            "context": {"type": "string", "description": "What these share, in prose"},
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Indexed source names in the group",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["name"],
    },
)
async def set_group(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(
            handlers.set_group(
                args["name"],
                context=args.get("context"),
                sources=args.get("sources"),
                **_dir(args),
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "set_interpretation",
    prompts.tool("set_interpretation"),
    {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Indexed source name"},
            "summary": {
                "type": "string",
                "description": "Prose read of what this data is, in plain language",
            },
            "roles": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Column name -> role",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["source"],
    },
)
async def set_interpretation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(
            handlers.set_interpretation(
                args["source"],
                summary=args.get("summary"),
                roles=args.get("roles"),
                **_dir(args),
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "join_findings",
    prompts.tool("join_findings"),
    {
        "type": "object",
        "properties": {
            "left": {"type": "string", "description": "Indexed source name"},
            "right": {"type": "string", "description": "Indexed source name"},
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key column(s) present in both sources",
            },
            "left_on": {"type": "array", "items": {"type": "string"}},
            "right_on": {"type": "array", "items": {"type": "string"}},
            "portia_dir": {"type": "string"},
        },
        "required": ["left", "right"],
    },
    annotations=_READ_ONLY,
)
async def join_findings(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(
            handlers.join_findings(
                args["left"],
                args["right"],
                keys=args.get("keys"),
                left_on=args.get("left_on"),
                right_on=args.get("right_on"),
                **_dir(args),
            )
        )
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "record_step",
    prompts.tool("record_step"),
    {
        "type": "object",
        "properties": {
            "spec_path": {"type": "string", "description": "e.g. specs/orders.yaml"},
            "step": {"type": "object", "description": "The step to append"},
            "portia_dir": {"type": "string"},
        },
        "required": ["spec_path", "step"],
    },
)
async def record_step(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.record_step(args["spec_path"], args["step"], **_dir(args)))
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "run_spec",
    prompts.tool("run_spec"),
    {"spec_path": str},
    annotations=_READ_ONLY,
)
async def run_spec(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.run_spec(args["spec_path"]))
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


def _dir(args: dict[str, Any]) -> dict[str, str]:
    """Pass ``portia_dir`` through only when the caller set it, so handler defaults win."""
    return {"portia_dir": args["portia_dir"]} if args.get("portia_dir") else {}


#: Read-only checks — safe to auto-approve. Writes are listed separately so the
#: session can route them through the permission flow instead.
READ_TOOLS = [get_context, describe_source, profile_source, join_findings, run_spec]
WRITE_TOOLS = [set_interpretation, set_group, record_step]

ALL_TOOLS = [*READ_TOOLS, *WRITE_TOOLS]


def qualified(name: str) -> str:
    """The ``mcp__<server>__<tool>`` name the SDK exposes to the model."""
    return f"mcp__{SERVER_NAME}__{name}"


def build_server():
    """The in-process MCP server the agent talks to. Runs inside this process."""
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=ALL_TOOLS)
