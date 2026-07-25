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
    "Re-read the project context, groups and source index. You ALREADY HAVE this "
    "in your system prompt — call it only to pick up changes made during this "
    "session, such as after a source is indexed or interpreted.",
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
    "One source's semantic map: its summary, every column name, the role recorded "
    "for it, and its quality flags — no statistics. Cheap. This is usually enough "
    "to judge whether a source is relevant, which columns could be keys, and how "
    "two sources might relate. Start here when you need more than the one-line "
    "index in your system prompt.",
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
    "One source's full measured facts: per-column dtype, null rate, distinct "
    "count, min/max, quartiles, sample values and quality flags. Expensive — the "
    "detailed rung. Call it when you need the actual numbers (interpreting a "
    "source, judging whether a key is usable, quantifying a data-quality "
    "problem), not to browse. These facts are unranked; deciding which matter is "
    "your job.",
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
    "Record that several sources belong together, and the context they share — "
    "same vendor and the same quirks, one system's export, the tables that make "
    "up one workflow. Use it when you learn something true of a set of sources "
    "that no single source's entry can hold. The group's context then travels "
    "with all of them.",
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


@tool(
    "join_findings",
    "Measure what joining two sources on given keys would actually do: key overlap "
    "and coverage, the relationship (1:1 / 1:many / many:many), fan-out, how many "
    "rows each join type would produce and drop — plus example unmatched rows, "
    "null-key rows, and worst fan-out keys. Call this BEFORE deciding anything "
    "about a merge. The findings are unranked: whether a dropped row matters is "
    "your call, not the check's.",
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
    "Append a decided step to the spec — the durable, re-runnable record of what "
    "was done to the data and why. The step is a dict: 'id', 'op' ('join' or "
    "'normalize'), the op's fields (join: 'left', 'right', 'keys' or "
    "'left_on'/'right_on', 'how'; normalize: 'input', 'transforms'), an 'expect' "
    "block of provenance values you predict, and a 'rationale' explaining the "
    "decision. Base 'expect' on what the check measured — run_spec will hold you "
    "to it. Note 'keys', not 'on' ('on' is a reserved boolean in YAML).",
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
    "Re-execute a spec and report what each step actually did, plus drift against "
    "its 'expect' block. Use it to check your own work right after recording a "
    "step: if the numbers disagree with what you predicted, say so rather than "
    "quietly adjusting the expectation.",
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
