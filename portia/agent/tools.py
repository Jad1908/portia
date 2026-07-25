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

from portia.agent import handlers
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
    "Read the project's context and the list of indexed data sources. "
    "Call this first, before anything else — it carries the human's description "
    "of the project, which is what makes a column's meaning decidable.",
    {},
    annotations=_READ_ONLY,
)
async def get_context(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.get_context(**_dir(args)))
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return _failed(exc)


@tool(
    "profile_source",
    "Get everything the deterministic checks found about one source: per-column "
    "dtype, null rate, distinct count, sample values and quality flags, plus any "
    "interpretation already recorded. Call this before interpreting a source. "
    "These facts are unranked — deciding which of them matter is your job.",
    {"source": str},
    annotations=_READ_ONLY,
)
async def profile_source(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(handlers.profile_source(args["source"], **_dir(args)))
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)


@tool(
    "set_interpretation",
    "Record what a source IS: a short prose summary, and a role for each column "
    "(e.g. identifier, measure, timestamp, category, free_text). This is durable "
    "— it becomes the project's memory and is what a future session reads instead "
    "of re-deriving. Writes judgment only; it never alters a measured fact. "
    "Pass 'summary', 'roles' (a column->role object), or both.",
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


def _dir(args: dict[str, Any]) -> dict[str, str]:
    """Pass ``portia_dir`` through only when the caller set it, so handler defaults win."""
    return {"portia_dir": args["portia_dir"]} if args.get("portia_dir") else {}


#: Read-only checks — safe to auto-approve. Writes are listed separately so the
#: session can route them through the permission flow instead.
READ_TOOLS = [get_context, profile_source]
WRITE_TOOLS = [set_interpretation]

ALL_TOOLS = [*READ_TOOLS, *WRITE_TOOLS]


def qualified(name: str) -> str:
    """The ``mcp__<server>__<tool>`` name the SDK exposes to the model."""
    return f"mcp__{SERVER_NAME}__{name}"


def build_server():
    """The in-process MCP server the agent talks to. Runs inside this process."""
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=ALL_TOOLS)
