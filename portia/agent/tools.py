"""The in-process MCP server — the only place the SDK meets the engine.

Every tool here is a thin wrapper: validate nothing, decide nothing, just call
the matching function in ``handlers.py`` and hand back its evidence as text.
Compact JSON for most of it, and `core/columnar.py`'s table where the evidence
is one record per column (:func:`_evidence`'s ``encode``).
Keeping the wrappers this thin is the point — the logic lives in `handlers`
where it can be tested without the SDK, and this file stays a translation layer
we can swap if the harness ever changes.

Tool descriptions matter more than they look: they are what the agent reads to
decide *when* to reach for a check. Say when to call it, not just what it does.

**Every handler runs on a thread, and that is not an optimization.** This server
is in-process (:func:`build_server`), so a tool body executes on whatever event
loop is driving the SDK — which, in the app, is the one serving NiceGUI's
websocket. `handlers` is synchronous and hits DuckDB, so calling it directly from
these coroutines froze the window for the length of every query: measured on the
demo project, 1.20s for `profile_source`, 2.16s for `join_findings`, 2.35s for a
six-pair `measure_overlaps`. NiceGUI gives up on a client that misses its
heartbeat for `reconnect_timeout` (`ui/__main__.py`), so a long enough tool call
put the "trying to reconnect" card over a window whose server was fine and busy.
`ui/engine.py` has always threaded the work the *buttons* start; this is the same
rule applied to the work the *agent* starts, and :func:`_evidence` is the one
place it happens.

Each handler opens its own DuckDB connection inside the call (`core/io.connect`),
so moving the whole call to a worker keeps a connection and its use on one
thread, which is what `core/table.py` requires. Nothing here is shared across
tools, so there is nothing for two of them to race over.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Iterator
from functools import partial
from typing import Any

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from portia import spec
from portia.agent import ask, chartspec, drawn, handlers, prompts
from portia.core import cancel, columnar
from portia.core.serialize import to_json_compact

SERVER_NAME = "portia"

#: The largest result a tool will hand back, in characters.
#:
#: **The SDK refuses an oversized result and tells the model to recover with
#: tools it does not have** — `offset`, `limit`, `jq` and a saved file path,
#: against an agent configured with no filesystem and no shell. It happened
#: three times in the 2026-08-17 AQN runs and the model read an instruction it
#: could not act on and moved on silently, once without its main source's
#: numbers. We cannot intercept that refusal, because it happens after the tool
#: returns; the only way to avoid it is to not return an oversized payload.
#:
#: The window is measured, not chosen: in those logs the largest result the SDK
#: **accepted** was 29,265 characters and the smallest it **refused** was 51,402,
#: so the real ceiling is somewhere between. This sits just above the
#: known-good end, because the limit is on tokens and characters are only a
#: proxy for them — the same length of denser text may not fit.
RESULT_BUDGET = 30_000

_READ_ONLY = ToolAnnotations(readOnlyHint=True)

#: The cancel scope the exchange in flight installed, or none. **A module slot
#: rather than a `ContextVar`**, which is what `core/cancel.py` uses everywhere
#: else: the SDK runs a tool body on a task it created when the client
#: connected, so a scope set on the task that later calls `send` is not in the
#: context the tool inherits. What the process has one of at a time is an
#: exchange — `Conversation.send` refuses to overlap and the window runs one —
#: so this is `core/backend.py`'s argument for a global, applied to a stop.
#: Before it existed, Stop interrupted the SDK and the copilot's tool thread
#: kept running: a BigQuery profile pressed at 204 s finished at 372 s, on the
#: meter, three minutes after the loop had given up on it (2026-09-06).
_stop: cancel.Scope | None = None


@contextlib.contextmanager
def stopping(scope: cancel.Scope | None) -> Iterator[None]:
    """Install ``scope`` as what Stop cancels, for the exchange's duration."""
    global _stop
    previous, _stop = _stop, scope
    try:
        yield
    finally:
        _stop = previous


def _stopped() -> dict[str, Any]:
    """What the model reads instead of a result when the human pressed Stop."""
    return {
        "content": [{"type": "text", "text": prompts.error("tool_stopped")}],
        "is_error": True,
    }


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _failed(exc: Exception) -> dict[str, Any]:
    """Compose the message the agent reads, rather than leaking a bare traceback.

    An uncaught exception would still reach it as ``str(exc)``; going through
    here means we can add the context needed to pick a different move.
    """
    return {
        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
        "is_error": True,
    }


def _too_large(size: int) -> dict[str, Any]:
    """Refuse, and name the smaller question — rather than truncate.

    **Refusing is the same call `profile_source` makes on an unknown column**
    (`handlers._requested`): a silently short answer is the failure this whole
    stream exists to stop, and the AQN build run shipped a deliverable without
    its main source's numbers with nothing in the transcript reading as though
    the copilot knew. A truncated payload is a short answer that looks complete.

    The item this closes asked for the opposite — *degrade instead of refuse* —
    and it is kept that way in `SPRINT.md`, because the argument only changed
    once there were smaller questions to point at. `profile_source` takes
    `columns` and `join_findings` takes `left_columns`/`right_columns`; before
    those existed, refusing would have been a dead end rather than a redirect.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": prompts.error(
                    "result_too_large", size=f"{size:,}", budget=f"{RESULT_BUDGET:,}"
                ),
            }
        ],
        "is_error": True,
    }


#: Per-column evidence as a header and one line per column, not as JSON — the
#: two rungs whose payload *is* a list of columns (`core/columnar.py`).
#:
#: `profile_source` is the one that failed loudly: the AQN build run could not
#: return 83,079 characters and the copilot built its whole table without that
#: source's numbers. It is 28,086 here.
#:
#: **`describe_source` is the one that was quietly larger**, and it took counting
#: a whole session to see, because no single call was ever refused: 31 calls,
#: 101,601 characters, **40% of every tool result in that run** and more than
#: `profile_source` and `measure_overlaps` together. It is 42,353 here. Worth
#: remembering which of the two the sprint went looking for.
#:
#: Only the encoding moved — `handlers` still returns a plain dict, so the
#: "testable without the SDK" seam holds and every number is unchanged.
_as_columns = partial(columnar.render, records="columns")


async def _evidence(
    call: Callable[[], Any], *, encode: Callable[[Any], str] = to_json_compact
) -> dict[str, Any]:
    """Run one handler off the event loop and hand back what it found.

    ``call`` is a thunk rather than ``(fn, *args)`` so that **reading ``args``
    happens inside the try**, on the worker. A tool whose required field is
    missing raises `KeyError` while unpacking, and that has always been an error
    the agent reads and recovers from rather than one that escapes into the SDK;
    building the arguments out here would have quietly moved it.

    ``encode`` is where a handler's dict becomes the text the model reads, and it
    is deliberately *here* rather than in `handlers`: a handler returns a plain
    jsonable dict, which is what makes it testable without the SDK, so the
    encoding belongs at this edge. Encoding stays on the loop because it is
    string work on a dict the worker already built.

    **The size guard is here for the same reason the encoding is**: it is the one
    place every tool's payload becomes text, so one check covers all ten and no
    handler learns that a token limit exists. It measures the encoded string
    rather than estimating from the dict, because the encoders differ —
    `core/columnar.py` and compact JSON do not cost the same per record.

    **The worker runs under the exchange's cancel scope** (`stopping`), so the
    connection a handler opens through `core/io.connect` registers with it and
    Stop reaches the query — the same mechanism Run, Build and the indexing
    pass use, applied to the work the *agent* starts. The scope is entered on
    the worker rather than here because `cancel.scope` translates whatever the
    interrupted driver raised into `Cancelled`, and that translation has to
    wrap the call, not the await.
    """

    def stoppable() -> Any:
        with cancel.scope(_stop):
            return call()

    try:
        text = encode(await asyncio.to_thread(stoppable))
    except cancel.Cancelled:
        return _stopped()
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return _failed(exc)
    return _ok(text) if len(text) <= RESULT_BUDGET else _too_large(len(text))


async def _awaited(
    call: Callable[[], Any], *, encode: Callable[[Any], str] = to_json_compact
) -> dict[str, Any]:
    """`_evidence` for a tool whose work is waiting, not computing.

    The one tool that awaits the human rather than a query (`ask_user`) has
    nothing to put on a thread, and parking a worker for the length of a
    human's think would be a thread held for nothing. Same encoder choice and
    the same size rule as `_evidence`, so no handler encodes for itself.
    """
    try:
        text = encode(await call())
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return _failed(exc)
    return _ok(text) if len(text) <= RESULT_BUDGET else _too_large(len(text))


@tool(
    "get_context",
    prompts.tool("get_context"),
    {},
    annotations=_READ_ONLY,
)
async def get_context(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(lambda: handlers.get_context(**_dir(args)))


@tool(
    "describe_source",
    prompts.tool("describe_source"),
    {"source": str},
    annotations=_READ_ONLY,
)
async def describe_source(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.describe_source(args["source"], **_dir(args)), encode=_as_columns
    )


@tool(
    "graph_lookup",
    prompts.tool("graph_lookup"),
    {
        "type": "object",
        "properties": {
            "table": {"type": "string", "description": "Source name, source path, or model name"},
            "column": {"type": "string", "description": "One column of it, for lineage"},
        },
        "required": ["table"],
    },
    annotations=_READ_ONLY,
)
async def graph_lookup(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.graph_lookup(args["table"], column=args.get("column"), **_dir(args))
    )


@tool(
    "measure_overlaps",
    prompts.tool("measure_overlaps"),
    {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "description": "Column pairs to compare, each with the reason you picked it",
                "items": {
                    "type": "object",
                    "properties": {
                        "left": {"type": "string", "description": "Source or model name"},
                        "left_column": {"type": "string"},
                        "right": {"type": "string", "description": "Source or model name"},
                        "right_column": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "description": "Why these two might be related — required",
                        },
                    },
                    "required": ["left", "left_column", "right", "right_column", "reason"],
                },
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["pairs"],
    },
)
async def measure_overlaps(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(lambda: handlers.measure_overlaps(args["pairs"], **_dir(args)))


@tool(
    "profile_source",
    prompts.tool("profile_source"),
    {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source name, or <spec>#<step id>"},
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column names to return; omit for every column",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["source"],
    },
    annotations=_READ_ONLY,
)
async def profile_source(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.profile_source(args["source"], columns=args.get("columns"), **_dir(args)),
        encode=_as_columns,
    )


@tool(
    "query_data",
    prompts.tool("query_data"),
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "What you want to find out, in one sentence — required",
            },
            "sql": {"type": "string", "description": "One SELECT over the declared inputs"},
            "inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every table the query reads: source, model, or <spec>#<step id>",
            },
            "limit": {
                "type": "integer",
                "description": f"Rows to return (default {handlers.QUERY_ROWS})",
            },
            "offset": {"type": "integer", "description": "Rows to skip, to page through a result"},
            "portia_dir": {"type": "string"},
        },
        "required": ["question", "sql", "inputs"],
    },
    annotations=_READ_ONLY,
)
async def query_data(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.query_data(
            args["sql"],
            args["inputs"],
            args["question"],
            limit=args.get("limit"),
            offset=args.get("offset") or 0,
            **_dir(args),
        )
    )


@tool(
    "plot_data",
    prompts.tool("plot_data"),
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "What the chart is meant to show, in one sentence — required",
            },
            "tab": {
                "type": "string",
                "description": "The tab's name, and its identity — reusing one replaces its chart",
            },
            "sql": {"type": "string", "description": "One SELECT over the declared inputs"},
            "inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Every table the query reads: source, model, or <spec>#<step id>",
            },
            "vega": {
                "type": "object",
                "description": (
                    "A Vega-Lite spec: mark, encoding, layer, scale, config — "
                    "everything except transforms and data, which are yours to "
                    "write in the SQL and portia's to supply"
                ),
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["question", "tab", "sql", "inputs", "vega"],
    },
    annotations=_READ_ONLY,
)
async def plot_data(args: dict[str, Any]) -> dict[str, Any]:
    """Draw a chart, and hand the model a receipt rather than the rows.

    **The split is here and not in the handler** (`docs/VISUALIZATION.md` §2.3).
    `handlers.plot_data` returns everything it measured, which is what makes it
    testable without the SDK; this is the edge where a payload becomes text, and
    the rule that the rows never become text is a rule about what the model
    receives. It is the same argument that puts `RESULT_BUDGET` here: one place,
    covering every tool, with no handler learning that a token limit exists.

    The rows are published before the receipt is built, so a chart is on screen
    by the time the agent is told about it.
    """
    return await _evidence(lambda: _draw(args))


def _draw(args: dict[str, Any]) -> dict:
    """Run the query, put the rows on screen, hand back the receipt.

    Publishing happens **on the worker thread**, inside `_evidence`'s
    `to_thread`, and a sink that needs to be on the event loop hops there itself
    — `ui/engine.py` is the only module in the app that knows there is a thread,
    and it already owns that `call_soon_threadsafe`. Hopping here instead would
    put a second copy of that knowledge in the module that must not have it.
    """
    chart = handlers.plot_data(
        args["sql"],
        args["inputs"],
        args["question"],
        args["tab"],
        args["vega"],
        **_dir(args),
    )
    drawn.publish(chart)
    return _receipt(chart)


def _receipt(chart: dict) -> dict:
    """What the model is told about a chart it drew.

    Enough to say something true in the reply — which tab, how big, and what is
    in each column it drew — and not the dataset, which is the whole point
    (§2.5). Everything here is read off the rows rather than computed: `ui/` may
    not compute and neither may this, and restating what came out of the
    ``SELECT`` is not computing.

    **The first shape summarised each column on its own and paired nothing**, and
    that was a defect rather than a simplification (`VISUALIZATION.md` §2.5.1,
    2026-09-04). A numeric channel became ``{min, max}`` and a categorical one
    became a list of values; both statements were true, and side by side they
    invited the reader to supply the mapping between them. The copilot supplied
    it. Given three risk categories and a range it wrote *"Risk 1: 43.9%, Risk 2:
    25.2%, Risk 3: 38.9%"* — max to the first, min to the second, and a third
    number invented — against a measured 25.2 / 29.5 / 43.9, inverting a real
    finding while the correct chart sat on screen beside it.

    So the rule is that a receipt must be **reasonable-from**, not merely true.
    Under :data:`RECEIPT_ROWS` it carries the encoded columns' values *paired*,
    which is the answer to the question a chart obviously raises. Over it, it
    says :data:`UNPAIRED` **as a field**, because the pairing's absence is
    exactly what the model has to know and a silence gets read as *nothing to
    report* — `SQL_LINEAGE.md` §9's finding, twice now.

    This is not §2.3 reversed. §2.3 is about a 5,000-point scatter costing 30,000
    characters and being read one coordinate at a time; nine bars is not that.
    The receipt was always allowed to contain facts and never allowed to contain
    the dataset, and the line between those is a row count.

    **It summarises the columns the spec *names*, not every column returned**
    *(2026-09-03)*. A free Vega-Lite spec has no fixed set of channels to read
    back, and the useful answer was never "the x channel" — it was what is on the
    axis. A field the spec encodes is a field the user is looking at. The spec
    itself is not echoed: the agent wrote it and it is in the log verbatim.
    """
    receipt = {"drawn": chart["tab"], **_facts(chart)}
    # A chart a surface could not draw, reported after its own call returned
    # (`VISUALIZATION.md` §11.2). It rides here because there is nowhere earlier
    # to put it, and it is worth having late: a reply that draws nine charts can
    # still fix the last six once the third has said it broke.
    if failures := drawn.take_failures():
        receipt["render_failures"] = failures
    # Whether anybody can see it, when the surface is another process
    # (`drawn.audience`). Absent in the app, where the window drew it.
    if (seen := drawn.audience()) is not None:
        receipt["shown"] = seen
    return receipt


def _facts(chart: dict) -> dict:
    """What a chart holds, in the receipt's shape: paired rows, or ``unpaired``.

    Split out of :func:`_receipt` for `view_chart`, which hands the same facts
    back beside a picture. One function, so the numbers a model reads next to
    the pixels are the numbers it read when it drew them.
    """
    encoded = chartspec.fields(chart["vega"])
    rows = chart["rows"]
    facts: dict[str, Any] = {
        "question": chart["question"],
        "n_rows": chart["n_rows"],
        "columns": chart["columns"],
    }
    if not encoded:
        return facts
    if len(rows) <= RECEIPT_ROWS:
        facts["plotted"] = [{col: row.get(col) for col in encoded} for row in rows]
        return facts
    facts[UNPAIRED] = True
    for column in encoded:
        facts[column] = _span(rows, column)
    return facts


#: Distinct values a receipt names before it stops listing them and says how many
#: there are instead. A chart of 400 categories is a legitimate chart (§2.7) and
#: its receipt is not 400 names.
RECEIPT_VALUES = 12

#: How many plotted rows a receipt carries **paired** before it falls back to
#: per-column spans (`VISUALIZATION.md` §2.5.2). Small on purpose: a receipt is
#: for writing one or two true sentences, not for reading the data. The agent
#: that wants the numbers behind a big chart has `query_data`, which is one more
#: SELECT and puts them in the log where `findings.review` can read them back.
#:
#: **Twenty because a real session says twenty is enough.** Across the nine
#: figures kept from the 2026-09-04 inspections run, eight are 19 rows or fewer
#: and come back paired at 462–1,988 characters — against a `RESULT_BUDGET` of
#: 30,000. Only a 26-row time series falls through, and a time series is the one
#: shape whose story survives `{min, max}` intact. So the threshold is not a
#: guess about what is affordable; it is where the charts people actually draw
#: sit, and it costs about 6% of the one measured limit in the system.
RECEIPT_ROWS = 20

#: Said in the payload, as a field, when the rows did not fit. The absence of a
#: pairing is the one thing the model must not have to infer — inferring it is
#: what produced §2.5.1's inverted narration — and an omission reads as *nothing
#: to report* rather than as *this is missing*.
UNPAIRED = "unpaired"


def _span(rows: list[dict], column: str) -> Any:
    """One channel's values, as a range if they are numbers and a list if not."""
    values = [row.get(column) for row in rows]
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return {"min": min(present), "max": max(present)}
    seen = list(dict.fromkeys(str(v) for v in present))
    if len(seen) <= RECEIPT_VALUES:
        return seen
    return {"showing": seen[:RECEIPT_VALUES], "n_distinct": len(seen)}


#: What a surface reports a picture as. One format, named once: the browser
#: encodes it, `ui/charts.pictured` refuses anything else, and this labels it.
PICTURE_MIME = "image/png"


@tool(
    "view_chart",
    prompts.tool("view_chart"),
    {
        "type": "object",
        "properties": {
            "tab": {
                "type": "string",
                "description": "The chart's tab name, as its plot_data receipt spelled it",
            },
        },
        "required": ["tab"],
    },
    annotations=_READ_ONLY,
)
async def view_chart(args: dict[str, Any]) -> dict[str, Any]:
    """Hand the model a picture of a chart, with the measured rows beside it.

    **The one result that is not only text**, so it does not go through
    :func:`_evidence`'s encoder: the picture is an image block, which the SDK
    passes to the model as an image, and the text block beside it is the
    receipt's facts (:func:`_facts`). The threading and the stop scope are
    `_evidence`'s, reused: `handlers.view_chart` may wait a few seconds for a
    paint that is on its way, and that wait must not hold the loop the paint is
    reported through.

    **`RESULT_BUDGET` measures the text and not the picture.** The picture's size
    is capped where it arrives (`ui/charts.pictured`), because a limit on an
    image is a limit on pixels and the browser is what has them.

    Only the text reaches a chat log (`events.tool_result_text` reads text
    blocks), so the log records that the copilot looked and at how many pixels,
    and no picture is written anywhere. Nothing in portia saves automatically.
    """
    seen: dict[str, Any] = {}

    def look() -> dict:
        seen.update(handlers.view_chart(args["tab"]))
        chart = seen.get("chart") or {}
        return {
            "viewed": seen["viewed"],
            "picture": {"width": seen["width"], "height": seen["height"]},
            **(_facts(chart) if chart else {}),
        }

    result = await _evidence(look)
    if result.get("is_error") or "image" not in seen:
        return result
    picture = {"type": "image", "data": seen["image"], "mimeType": PICTURE_MIME}
    return {"content": [picture, *result["content"]]}


@tool(
    "review_queries",
    prompts.tool("review_queries"),
    {
        "type": "object",
        "properties": {
            "chat": {
                "type": "string",
                "description": "An older chat to curate; omit for this one",
            },
            "portia_dir": {"type": "string"},
        },
        "required": [],
    },
    annotations=_READ_ONLY,
)
async def review_queries(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(lambda: handlers.review_queries(args.get("chat"), **_dir(args)))


@tool(
    "record_finding",
    prompts.tool("record_finding"),
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "What you wanted to know, in plain language",
            },
            "answer": {"type": "string", "description": "What you found out, as a sentence"},
            "so": {"type": "string", "description": "What it changed — required"},
            "about": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tables and columns it concerns: 'table.column' or a table name",
            },
            "from": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "The query numbers this rests on, from review_queries",
            },
            "spec_name": {
                "type": "string",
                "description": "The model this was in service of, if any",
            },
            "chat": {"type": "string", "description": "An older chat, if curating one"},
            "portia_dir": {"type": "string"},
        },
        "required": ["question", "answer", "so", "about", "from"],
    },
)
async def record_finding(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.record_finding(
            args["question"],
            args["answer"],
            args["so"],
            args["about"],
            args["from"],
            spec_name=args.get("spec_name"),
            chat=args.get("chat"),
            **_dir(args),
        )
    )


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
    return await _evidence(
        lambda: handlers.set_group(
            args["name"],
            context=args.get("context"),
            sources=args.get("sources"),
            **_dir(args),
        )
    )


@tool(
    "set_interpretation",
    prompts.tool("set_interpretation"),
    {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Indexed source name, or the name of a model you built",
            },
            "summary": {
                "type": "string",
                "description": "Prose read of what this data is, in plain language",
            },
            "roles": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Column name -> role",
            },
            "note": {
                "type": "string",
                "description": "One dated sentence appended to what is known about this table",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["source"],
    },
)
async def set_interpretation(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.set_interpretation(
            args["source"],
            summary=args.get("summary"),
            roles=args.get("roles"),
            note=args.get("note"),
            **_dir(args),
        )
    )


@tool(
    "join_findings",
    prompts.tool("join_findings"),
    {
        "type": "object",
        "properties": {
            "left": {"type": "string", "description": "Source name, or <spec>#<step id>"},
            "right": {"type": "string", "description": "Source name, or <spec>#<step id>"},
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key column(s) present in both sources",
            },
            "left_on": {"type": "array", "items": {"type": "string"}},
            "right_on": {"type": "array", "items": {"type": "string"}},
            "left_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Left columns to show in example rows; keys are always included",
            },
            "right_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Right columns to show in example rows; keys are always included",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["left", "right"],
    },
    annotations=_READ_ONLY,
)
async def join_findings(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.join_findings(
            args["left"],
            args["right"],
            keys=args.get("keys"),
            left_on=args.get("left_on"),
            right_on=args.get("right_on"),
            left_columns=args.get("left_columns"),
            right_columns=args.get("right_columns"),
            **_dir(args),
        )
    )


@tool(
    "record_step",
    prompts.tool("record_step", **handlers.step_vocabulary()),
    {
        "type": "object",
        "properties": {
            "spec_path": {"type": "string", "description": "e.g. specs/orders.yaml"},
            "step": {"type": "object", "description": "The step to append"},
            "layer": {
                "type": "string",
                "enum": list(spec.LAYERS),
                "description": "Layer this table belongs to; omit for a flat project",
            },
            "supersedes": {
                "type": "string",
                "description": "A step in this spec this one corrects and replaces",
            },
            "target": {
                "type": "string",
                "description": "DATABASE.SCHEMA this table is created in, on a warehouse",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["spec_path", "step"],
    },
)
async def record_step(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.record_step(
            args["spec_path"],
            args["step"],
            layer=args.get("layer"),
            supersedes=args.get("supersedes"),
            target=args.get("target"),
            **_dir(args),
        )
    )


@tool(
    "read_spec",
    prompts.tool("read_spec"),
    {
        "type": "object",
        "properties": {
            "spec": {"type": "string", "description": "Model name, or its spec path"},
            "journal": {
                "type": "boolean",
                "description": "Include what was asked on the way to this table",
            },
            "measured": {
                "type": "boolean",
                "description": "Include what the last build measured about its table",
            },
            "portia_dir": {"type": "string"},
        },
        "required": ["spec"],
    },
    annotations=_READ_ONLY,
)
async def read_spec(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.read_spec(
            args["spec"],
            journal=bool(args.get("journal")),
            measured=bool(args.get("measured")),
            **_dir(args),
        )
    )


@tool(
    "run_spec",
    prompts.tool("run_spec"),
    {"spec_path": str},
    annotations=_READ_ONLY,
)
async def run_spec(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(lambda: handlers.run_spec(args["spec_path"]))


def _dir(args: dict[str, Any]) -> dict[str, str]:
    """Pass ``portia_dir`` through only when the caller set it, so handler defaults win."""
    return {"portia_dir": args["portia_dir"]} if args.get("portia_dir") else {}


#: The question, as a tool, for the harness that has no question of its own
#: (`agent/ask.py`, `docs/PROVIDERS.md` §9.2). The Claude harness has
#: ``AskUserQuestion`` built in and never sees this one; Codex is offered it in
#: its place. The schema is ``AskUserQuestion``'s ``questions`` shape, so the
#: window's form and every log reader draw it unchanged. **Read-only on
#: purpose**: it writes nothing, and on Codex the read-only hint is what lets a
#: call run without an approval stopping it, which for a question would be an
#: approval to ask for an approval.
QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": "One to four questions",
            "items": {
                "type": "object",
                "properties": {
                    "header": {"type": "string", "description": "Short label, 12 chars or fewer"},
                    "question": {"type": "string", "description": "The question, one sentence"},
                    "options": {
                        "type": "array",
                        "description": "Two to four answers to pick from",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label", "description"],
                        },
                    },
                    "multiSelect": {
                        "type": "boolean",
                        "description": "More than one may be picked",
                    },
                },
                "required": ["header", "question", "options"],
            },
        }
    },
    "required": ["questions"],
}


@tool(
    "ask_user",
    prompts.tool("ask_user"),
    QUESTION_SCHEMA,
    annotations=_READ_ONLY,
)
async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
    """The human's answers, as text. Waits as long as they take; a cancelled
    request (Stop) unwinds through here as the SDK's own cancellation."""
    return await _awaited(lambda: ask.ask_now(list(args.get("questions") or [])))


#: Auto-approved. Writes are listed separately so the session can route them
#: through the permission flow instead.
#:
#: The line is **"does it change a durable artifact the user reviews in a diff"**,
#: not "does it write anything at all". `measure_overlaps` is here despite storing
#: its results: what it writes is metadata *about* the data, in a store §5.2 is
#: explicit about being re-derivable — losing it costs time, not truth — and the
#: decisions still live in the spec, in git. Confirming each of a dozen pairs
#: while indexing would also make the one tool that has to be used in bulk the
#: most expensive one to use.
READ_TOOLS = [
    get_context,
    graph_lookup,
    measure_overlaps,
    describe_source,
    profile_source,
    join_findings,
    query_data,
    plot_data,
    view_chart,
    review_queries,
    read_spec,
    run_spec,
]
WRITE_TOOLS = [set_interpretation, set_group, record_step, record_finding]

#: The question tool, offered only to the harness that has no question of its
#: own (`ask_user` above). Not a read and not a write: it changes nothing and
#: is never gated, and the Claude harness must not see it beside
#: ``AskUserQuestion`` or the model has two ways to ask and picks at random.
QUESTION_TOOLS = [ask_user]

ALL_TOOLS = [*READ_TOOLS, *WRITE_TOOLS, *QUESTION_TOOLS]

#: The build half: the tool that writes a spec and the tool that runs one.
#: **Withheld from a job that reads** (`offered(builds=False)`, 2026-09-23).
#: An indexing job's whole output is the catalog, and it was offered every
#: tool a build gets; the system prompt says *record as you go* and the task
#: prompt said *build nothing* once, at its end, and on a real warehouse the
#: copilot set about fixing what it read instead of describing it. Indexing is
#: read-only by construction now, the way the agent has no filesystem by
#: construction: a tool it is not offered is a tool it cannot reach for.
BUILD_TOOLS = [record_step, run_spec]

#: Tools whose answer is a picture. **Offered only to a model that can see one**
#: (`providers.Provider.sees_images`): a text-only model handed an image block
#: either errors or, worse, describes a chart it was never shown. Same rule as
#: effort, which is refused on a provider that would ignore it.
VISION_TOOLS = [view_chart]


def offered(*, sees_images: bool = True, asks: bool = False, builds: bool = True) -> list:
    """The tools a session gets, given what its model can take in and which harness drives it.

    ``asks`` is the Codex harness: it gets `ask_user`, because it has no
    question tool of its own. The Claude harness never does.

    ``builds`` is off for a job that reads — indexing, a re-read — which is
    then not offered `BUILD_TOOLS`. Everything else stays: the catalog writes
    are the job's output, a question is a `query_data`, and a chart is how it
    shows a shape.
    """
    tools = [*READ_TOOLS, *WRITE_TOOLS] + (list(QUESTION_TOOLS) if asks else [])
    if not builds:
        tools = [t for t in tools if t not in BUILD_TOOLS]
    if sees_images:
        return tools
    return [t for t in tools if t not in VISION_TOOLS]


def descriptions(
    *, sees_images: bool = True, asks: bool = False, builds: bool = True
) -> dict[str, str]:
    """Every tool description as the model receives it, keyed by tool name.

    Read off the registered tools rather than out of ``prompts/tools/``, because
    the two are not the same text: `record_step`'s is a template filled from
    `handlers.step_vocabulary()`, so the file has ``{expect_sql}`` where the
    model has the actual field list. A run log recording the file would record
    something nobody read.

    Exists for `portia/runlog.py`, which keeps a copy beside each chat: the log
    already records which build of portia it ran on, and a sha is enough to
    *find* the prompts but not to read them without leaving what you are doing.
    Nothing in the loop calls this.
    """
    return {
        t.name: str(t.description or "")
        for t in offered(sees_images=sees_images, asks=asks, builds=builds)
    }


def qualified(name: str) -> str:
    """The ``mcp__<server>__<tool>`` name the SDK exposes to the model."""
    return f"mcp__{SERVER_NAME}__{name}"


def build_server(*, sees_images: bool = True, asks: bool = False, builds: bool = True):
    """The in-process MCP server the agent talks to. Runs inside this process.

    The Claude SDK bridges it to its binary itself; the Codex harness serves the
    same object over a loopback port (`agent/loopback.py`), and asks for
    `ask_user` in the list.
    """
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="0.1.0",
        tools=offered(sees_images=sees_images, asks=asks, builds=builds),
    )
