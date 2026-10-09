"""The in-process MCP server — the only place the SDK meets the engine.

Every tool here is a thin wrapper: validate nothing, decide nothing, just call
the matching function in ``handlers.py`` and hand back its evidence as text.
Compact JSON for most of it, and `core/columnar.py`'s table where the evidence
is one record per column (:func:`_evidence`'s ``encode``), grouped by type when
a table is too wide for that (``coarser``).
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
thread, which is what `core/table.py` requires. No connection is shared across
tools, so there is nothing for two of them to race over; what they share is the
machine's memory, which is why only `DATA_READERS` of them read data at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import Any

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from portia import spec
from portia.agent import ask, chartspec, drawn, handlers, prompts
from portia.core import cancel, columnar, present
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
#:
#: Past it a result is refused (`_too_large`), except a whole table's columns,
#: which come back grouped by type when that fits (`_by_type`).
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


#: How many tool calls may read data at once in this process: **one**.
#:
#: A harness runs every read-only tool of a message together (Claude Code up to
#: ten), and each call opens its own DuckDB database, each sized to 80% of the
#: machine's memory. On a 16 GB laptop with two 1,579-column CSVs, three
#: `query_data` calls at once peaked at 2.5-3.3 GB, took free memory to 16 MB,
#: grew the system's compressed memory by 1.9 GB and swapped in two runs of
#: four, 0.6 GB once; one at a time they peaked at 2.7-2.8 GB, left 73 MB free
#: and did not swap (2026-10-08). The cost is the sum of the calls rather than
#: the longest: none on three profiles, +15% on the three heaviest calls of that
#: job, +40% on three wide queries, whose time was mostly the CSV sniff, which
#: runs on one core. Two at a time was as fast as ungated and grew compressed
#: memory by 0.7-1.1 GB: less, and no bound.
#:
#: Only the tools that open a connection wait (`_evidence`'s ``reads_data``):
#: a catalog read or a write of prose is never held behind a query.
DATA_READERS = 1

_readers = threading.BoundedSemaphore(DATA_READERS)


def _wait_turn(scope: cancel.Scope) -> None:
    """Take one of the `DATA_READERS` places, waiting where a press can reach the wait.

    Nothing is running while a call waits, so there is no query to interrupt:
    the wait checks ``scope`` every `cancel.INTERRUPT_EVERY` instead, and a press
    ends it as a stop rather than leaving a thread queued behind a query that
    nobody wants any more. A press made before the call came is checked
    first, so a free place is not taken by a call already stopped. The caller
    releases the place.
    """
    scope.check()
    while not _readers.acquire(timeout=cancel.INTERRUPT_EVERY):
        scope.check()


@contextlib.contextmanager
def stopping(scope: cancel.Scope | None) -> Iterator[None]:
    """Install ``scope`` as what Stop cancels, for the exchange's duration.

    A press on a data call's card made before that call reached portia is held
    until it does (`interrupt_call`), and no longer than the exchange it was
    made in. What a finished exchange left untaken is the last exchange's.
    """
    global _stop
    previous, _stop = _stop, scope
    with _calls_lock:
        _outcomes.clear()
    try:
        yield
    finally:
        _stop = previous
        with _calls_lock:
            _presses.clear()


# --- one data call, stopped on its own (2026-10-09, `CONVERSATION.md` §16) ---
#
# Stop ends the exchange. *Interrupt query* on a data call's card ends that call
# and nothing else: the chat, or the job reading the sources, goes on, and the
# copilot reads why it has no result (`_interrupted`). Each call that reads data
# runs under a scope of its own, a child of the exchange's, so the exchange's
# Stop still reaches it (`core/cancel.Scope`). The window names a call by its
# card, which carries the harness's id for the call; a harness that sends that
# id with the request (`TOOL_USE_META`) is matched on it, and one that does not
# is matched on the tool and its arguments, in the order the calls arrived.
# No time limit and no planner estimate stop anything here: a correct query can
# run for an hour, and an estimate was measured and found useless for it.

#: Where a data call is, as the window draws it. Kinds, not a ladder.
WAITING = "waiting"
RUNNING = "running"
MEASURING = "measuring"

#: What a press on a card came to (`interrupt_call`).
INTERRUPTING = "interrupting"
SKIPPING = "skipping"
TOO_LATE = "too_late"
PENDING = "pending"

#: How the measuring after an interrupt ended, as the log records it.
MEASURED = "measured"
SKIPPED = "skipped"
FAILED = "failed"
NOT_MEASURED = "not_measured"

#: The key the Claude Code binary names a call by in an MCP request's ``_meta``:
#: the same id its tool-use block carries, which is the id the window's card has.
TOOL_USE_META = "claudecode/toolUseId"

#: The tool whose interruption is a write that did not happen, and says so.
WRITES_A_STEP = "record_step"

#: The tools that read data, by name: the calls `_evidence` runs with
#: ``reads_data``, which take turns among `DATA_READERS` and whose cards carry
#: *Interrupt query*. Named here so the window can ask without importing a
#: decorator; `tests/test_query_interrupt.py` holds it to the call sites.
DATA_TOOLS = frozenset(
    {
        "measure_overlaps",
        "profile_source",
        "query_data",
        "plot_data",
        "join_findings",
        "record_step",
        "run_spec",
    }
)


@dataclass
class Interruption:
    """A press on a data call's card: why, when, and what came of it."""

    #: The reason the user picked, as the menu said it, or ``None`` when no
    #: menu was shown (*Don't ask again*).
    reason: str | None
    #: The sentence typed under *Other*, or empty.
    note: str
    pressed: float
    #: Seconds the call had run when it stopped; ``None`` when it never started.
    ran: float | None = None
    #: Whether the call stopped because of it. A call can finish first, and a
    #: step being written refuses (`cancel.Scope.commit`).
    landed: bool = False
    #: A second press while the measuring runs, or before it starts.
    skip: bool = False
    measure: str = NOT_MEASURED
    measured: dict | None = None
    error: str = ""


@dataclass
class DataCall:
    """One call that reads data, from the moment it reaches portia to its result."""

    tool: str
    args: dict
    #: The arguments as one comparable string, for a card matched without an id.
    key: str
    #: The harness's id for the call, when it sends one (`TOOL_USE_META`).
    use_id: str | None
    scope: cancel.Scope
    #: The scope this call's is a child of: the exchange's in the window, the
    #: request's when a host serves the tools (`cli/serve.py`). Its stop is not
    #: a press on the card.
    parent: cancel.Scope | None
    queued: float
    #: The window's card for it, once one has asked.
    card: str | None = None
    #: When it took its `DATA_READERS` place: the running time starts here, and
    #: time spent waiting in line is not running time.
    started: float | None = None
    phase: str = WAITING
    measuring: cancel.Scope | None = None
    interruption: Interruption | None = None


class _Interrupted(Exception):
    """The call stopped at its own press; `_evidence` answers with `_interrupted`."""


#: Every data call in flight in this process, in the order they arrived.
_calls: list[DataCall] = []
#: Presses on a card whose call had not reached portia yet, by card id, with
#: the tool and arguments to know it by when it does.
_presses: dict[str, tuple[str, str, Interruption]] = {}
#: Interruptions whose call has ended, by card id, until the window takes them
#: for the log (`take_interruption`).
_outcomes: dict[str, dict[str, Any]] = {}
#: The loop presses and reads; the workers move a call's phase.
_calls_lock = threading.Lock()


def _args_key(args: dict) -> str:
    return json.dumps(args, sort_keys=True, default=str)


def _use_id() -> str | None:
    """The harness's id for the call being served, when its request carries one.

    The MCP server sets the request's context for the length of a tool call, and
    `asyncio.to_thread` copies it. ``None`` outside a request (a test calling a
    tool directly) and from a harness that sends no such key.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # pragma: no cover - the agent extra brings mcp
        return None
    try:
        meta = request_ctx.get().meta
    except LookupError:
        return None
    extra = getattr(meta, "model_extra", None) or {}
    value = extra.get(TOOL_USE_META)
    return str(value) if value else None


def _register(tool: str, args: dict) -> DataCall:
    """A data call has reached portia: hold it, and apply a press made before it came."""
    # The exchange's scope in the window; outside one, whatever scope the
    # caller installed around the call (a host's request, `cli/serve._stoppable`),
    # which this call's own must sit inside or the caller's stop reaches nothing.
    parent = _stop if _stop is not None else cancel.CURRENT.get()
    record = DataCall(
        tool=tool,
        args=args,
        key=_args_key(args),
        use_id=_use_id(),
        scope=cancel.Scope(parent=parent),
        parent=parent,
        queued=time.monotonic(),
    )
    with _calls_lock:
        _calls.append(record)
        early = _claim_press(record)
    if early is not None:
        record.scope.interrupt()
    return record


def _claim_press(record: DataCall) -> Interruption | None:
    """A press already waiting for this call, bound to it. Under `_calls_lock`."""
    for card, (name, key, interruption) in list(_presses.items()):
        if card == record.use_id or (name == record.tool and key == record.key):
            del _presses[card]
            record.card = card
            record.interruption = interruption
            return interruption
    return None


def _bound(card: str, tool: str, args: dict) -> DataCall | None:
    """The data call a card is about, binding it on first ask. Under `_calls_lock`.

    By the card already bound, then by the harness's id, then by the tool and
    its arguments among calls no card holds yet, first come first served: two
    identical calls are told apart by the order they arrived in.
    """
    for record in _calls:
        if record.card == card:
            return record
    for record in _calls:
        if record.card is None and record.use_id == card:
            record.card = card
            return record
    key = _args_key(args)
    for record in _calls:
        if record.card is None and record.tool == tool and record.key == key:
            record.card = card
            return record
    return None


def data_call(card: str, tool: str, args: dict) -> dict[str, Any] | None:
    """Where the call behind a card is, for the window: ``None`` once it is not in flight.

    ``waiting`` is how many other data calls are waiting for a place, which is
    what is lined up behind the one running.
    """
    with _calls_lock:
        record = _bound(card, tool, args)
        if record is None:
            return None
        return {
            "phase": record.phase,
            "started": record.started,
            "interrupted": record.interruption is not None,
            "waiting": sum(1 for r in _calls if r.phase == WAITING and r is not record),
        }


def interrupt_call(
    card: str, tool: str, args: dict, *, reason: str | None = None, note: str = ""
) -> str:
    """The press on a data call's card. Stops that call and nothing else.

    A first press interrupts it, waiting or running; one made before the call
    reached portia is held and applied when it does (`PENDING`). A second
    press, while what portia measures after the interrupt runs, skips the
    measuring. A step being written refuses (`TOO_LATE`).
    """
    pressed = Interruption(reason=reason, note=note.strip(), pressed=time.monotonic())
    with _calls_lock:
        record = _bound(card, tool, args)
        if record is None:
            _presses[card] = (tool, _args_key(args), pressed)
            return PENDING
        earlier = record.interruption
        if earlier is None:
            record.interruption = pressed
        else:
            earlier.skip = True
        measuring = record.measuring
    if earlier is not None:
        if measuring is not None:
            measuring.cancel()
        return SKIPPING
    return INTERRUPTING if record.scope.interrupt() else TOO_LATE


def take_interruption(card: str) -> dict[str, Any] | None:
    """The interruption of a call that has ended, once, for the window's log."""
    with _calls_lock:
        return _outcomes.pop(card, None)


def take_interruptions() -> dict[str, dict[str, Any]]:
    """Every interruption not taken yet: an exchange that ended before its results came."""
    with _calls_lock:
        taken = dict(_outcomes)
        _outcomes.clear()
        return taken


def _retire(record: DataCall) -> None:
    """The call has answered: let it go, keeping what its press came to for the log."""
    with _calls_lock:
        if record in _calls:
            _calls.remove(record)
        interruption = record.interruption
        if interruption is not None and record.card is not None:
            _outcomes[record.card] = {
                "id": record.card,
                "name": record.tool,
                "reason": interruption.reason,
                "note": interruption.note or None,
                "ran": None if interruption.ran is None else round(interruption.ran, 1),
                "landed": interruption.landed,
                "measure": interruption.measure,
                "measured": interruption.measured,
            }
    record.scope.close()


def _pressed(record: DataCall) -> bool:
    """Whether a stop came from this call's own press, rather than the exchange's Stop."""
    return record.interruption is not None and not (
        record.parent is not None and record.parent.cancelled
    )


def _run_data(record: DataCall, call: Callable[[], Any], measure: Callable[[], dict]) -> Any:
    """A data call on its worker: its turn, the call, and what is measured if it is interrupted.

    The `DATA_READERS` place is held through the measuring too, because the
    measuring reads data, and released last.
    """
    try:
        with cancel.scope(record.scope):
            _wait_turn(record.scope)
    except cancel.Cancelled:
        if not _pressed(record):
            raise
        assert record.interruption is not None
        record.interruption.landed = True
        raise _Interrupted() from None
    try:
        with _calls_lock:
            record.started = time.monotonic()
            record.phase = RUNNING
        try:
            with cancel.scope(record.scope):
                return call()
        except cancel.Cancelled:
            if not _pressed(record):
                raise
            assert record.interruption is not None
            record.interruption.landed = True
            record.interruption.ran = time.monotonic() - record.started
            _measure(record, measure)
            raise _Interrupted() from None
    finally:
        _readers.release()


def _measure(record: DataCall, measure: Callable[[], dict]) -> None:
    """What portia measures once the call has stopped, unless a second press skips it.

    Under a scope of its own, so the second press stops the measuring and not
    the exchange; a child of the exchange's, so Stop still stops it.
    """
    interruption = record.interruption
    assert interruption is not None
    with _calls_lock:
        if interruption.skip:
            interruption.measure = SKIPPED
            return
        record.phase = MEASURING
        record.measuring = cancel.Scope(parent=record.parent)
    try:
        with cancel.scope(record.measuring):
            interruption.measured = measure()
        interruption.measure = MEASURED
    except cancel.Cancelled:
        if record.parent is not None and record.parent.cancelled:
            raise
        interruption.measure = SKIPPED
    except Exception as exc:  # noqa: BLE001 - told to the copilot, not swallowed
        interruption.measure = FAILED
        interruption.error = f"{type(exc).__name__}: {exc}"
    finally:
        record.measuring.close()


def _interrupted(record: DataCall) -> dict[str, Any]:
    """What the model reads in place of a result its user interrupted.

    Not `_stopped`, whose text tells the copilot to stop and wait: the exchange
    goes on, and what it is told is that the user stopped this call, why if
    they said, how long it had run, that it has no result and may use no
    number from it, whether a step was written, and what portia measured after.
    """
    interruption = record.interruption
    assert interruption is not None
    parts = [prompts.error("interrupted", tool=record.tool)]
    if interruption.reason:
        parts.append(prompts.error("interrupted_reason", reason=interruption.reason))
        if interruption.note:
            parts.append(prompts.error("interrupted_note", note=interruption.note))
    else:
        parts.append(prompts.error("interrupted_no_reason"))
    if interruption.ran is None:
        parts.append(prompts.error("interrupted_never_started"))
    else:
        parts.append(prompts.error("interrupted_ran", ran=present.duration(interruption.ran)))
    if record.tool == WRITES_A_STEP:
        parts.append(prompts.error("interrupted_nothing_written"))
    if interruption.ran is not None:
        parts.append(_measurement(interruption))
    return {"content": [{"type": "text", "text": "\n\n".join(parts)}], "is_error": True}


def _measurement(interruption: Interruption) -> str:
    """The measuring's part of `_interrupted`: the facts, or why there are none."""
    measured = interruption.measured or {}
    if interruption.measure == MEASURED and measured.get("scanned") is False:
        return prompts.error("interrupted_not_scanned", facts=to_json_compact(measured))
    if interruption.measure == MEASURED:
        return prompts.error("interrupted_measured", facts=to_json_compact(measured))
    if interruption.measure == FAILED:
        return prompts.error("interrupted_measure_failed", error=interruption.error)
    return prompts.error("interrupted_measure_skipped")


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


def _by_type(payload: dict, **fields: Any) -> dict:
    """A column-shaped answer grouped by type, saying so before anything else.

    **What the two column rungs send instead of a refusal** when one line per
    column is over `RESULT_BUDGET` (2026-10-08). A table of 1,579 columns was
    56,289 characters described and 211,563 profiled, so both were refused and
    the copilot could read it only by naming columns it had never seen. A
    refusal is right where a narrower question exists to point at; for a whole
    table's columns there is none short of names the copilot has not been
    shown. So the answer comes at a coarser grain, built from the dict the
    handler already returned: no second scan, and every column counted in
    exactly one group (`core/columnar.by_type`). A grouped answer that is itself
    over the budget is refused as before.

    The notice comes first because a grouped answer read as a complete list is
    the failure the budget exists to stop.
    """
    notice = prompts.error(
        "result_grouped",
        n_columns=f"{len(payload['columns']):,}",
        listed=columnar.LISTED_GROUP,
        examples=columnar.GROUP_EXAMPLES,
    )
    return {"grouped": notice, **columnar.by_type(payload, records="columns", **fields)}


#: `describe_source` grouped: roles and flags tallied, and nothing measured,
#: because L2 is the rung of meaning and statistics belong to L3. An unprofiled
#: warehouse table carries a ``dtype`` and no ``inferred``, and groups on that.
_described_by_type = partial(_by_type, typed_by=("inferred", "dtype"), tallied=("role", "flags"))

#: How a measured table's columns are grouped: roles and flags tallied, the
#: spread of the two numbers every column has, and the range of the values a
#: numeric group holds. A built table nobody profiled groups on its ``dtype``.
_MEASURED_GROUPS: dict[str, Any] = {
    "typed_by": ("inferred", "dtype"),
    "tallied": ("role", "flags"),
    "spread": ("null_rate", "n_distinct"),
    "lowest": ("min",),
    "highest": ("max",),
}

#: `profile_source` grouped: what `describe_source` tallies, plus the numbers.
_profiled_by_type = partial(_by_type, **_MEASURED_GROUPS)


def _shortened(payload: dict) -> dict:
    """A report too long to send whole, every long run in it given as its count and ends.

    **For the tools whose answer is a report about a table, not its columns**
    *(2026-10-08)*: `record_step`, `run_spec` and `read_spec`. A step over a
    1,579-column table came back at 80,633 characters, most of it the outcome
    naming every column with a null rate and every column each input put into
    the output; it was refused, and the refusal told the copilot it had none of
    an answer to a write that had already happened. Shortened by
    `core/columnar.shorten`, the same report is a few kilobytes and every run
    in it is still counted. The notice comes first, as `_by_type`'s does.
    """
    notice = prompts.error(
        "result_shortened", listed=columnar.LISTED_GROUP, examples=columnar.GROUP_EXAMPLES
    )
    return {"shortened": notice, **columnar.shorten(payload)}


def _spec_shortened(payload: dict) -> dict:
    """`read_spec` shortened, a built table's columns grouped as `profile_source` groups them.

    ``measured`` is the model's catalog entry, a record per column, so it is
    grouped by type rather than cut into runs; everything else is a report.
    """
    measured = payload.get("measured")
    if isinstance(measured, dict) and isinstance(measured.get("columns"), list):
        grouped = columnar.by_type(measured, records="columns", **_MEASURED_GROUPS)
        payload = {**payload, "measured": grouped}
    return _shortened(payload)


#: The fields of `record_step`'s answer a receipt keeps: where it was written,
#: what it is called, and the outcome's shape and flags, which is what the
#: copilot needs to know the write happened and whether anything is wrong.
_RECEIPT_FIELDS = ("spec", "step_id", "superseded", "n_steps", "layer", "target", "written_to")
_RECEIPT_OUTCOME = ("n_rows", "n_cols", "flags")


def _step_receipt(payload: dict, size: int) -> dict:
    """What `record_step` did, when even its shortened report does not fit.

    **A write is never answered with a refusal** *(2026-10-08)*. `_too_large`
    says *you have none of it, do not call this again*, which after a read is
    true and harmless. After `record_step` the step is already in the spec and
    its model indexed, so the same words read as *it failed*, and a copilot
    that believes it records the step a second time. The receipt says that it
    was recorded, where, and the outcome's shape, and how to read the rest.
    """
    outcome = payload.get("outcome") or {}
    spec_path = str(payload.get("spec") or "")
    step_ref = f"{spec_path}#{payload.get('step_id')}"
    return {
        "receipt": prompts.error(
            "step_receipt", size=f"{size:,}", budget=f"{RESULT_BUDGET:,}", step=step_ref
        ),
        **{key: payload.get(key) for key in _RECEIPT_FIELDS},
        "outcome": {key: outcome.get(key) for key in _RECEIPT_OUTCOME},
    }


async def _evidence(
    call: Callable[[], Any],
    *,
    encode: Callable[[Any], str] = to_json_compact,
    coarser: Callable[[Any], Any] | None = None,
    receipt: Callable[[Any, int], Any] | None = None,
    reads_data: bool = False,
    called: tuple[str, dict[str, Any]] | None = None,
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

    ``coarser`` is the same evidence at a coarser grain, for a tool whose whole
    answer has no narrower question behind it (:func:`_by_type`). It is tried
    only past the budget and sent only within it; otherwise the refusal is the
    one there always was, with the full answer's size.

    ``receipt`` is for a tool that writes: past the budget even coarsened, it
    is sent in the refusal's place, given the size that did not fit, because a
    write that happened must never be answered with *you have none of it*
    (:func:`_step_receipt`).

    **The worker runs under the exchange's cancel scope** (`stopping`), so the
    connection a handler opens through `core/io.connect` registers with it and
    Stop reaches the query — the same mechanism Run, Build and the indexing
    pass use, applied to the work the *agent* starts. The scope is entered on
    the worker rather than here because `cancel.scope` translates whatever the
    interrupted driver raised into `Cancelled`, and that translation has to
    wrap the call, not the await.

    ``reads_data`` is set by the tools whose handler opens a connection, and
    such a call waits its turn among `DATA_READERS` before it starts, inside
    the scope, so a press reaches it there too.

    **A data call can be stopped on its own** *(2026-10-09)*: it runs under a
    scope of its own, a child of the exchange's (`DataCall`), which a press on
    its card cancels (`interrupt_call`). What the model reads then is
    `_interrupted`, with what portia measured after the press, and the
    exchange goes on. ``called`` is the tool's name and its arguments, which
    are how the window's card finds the call and what the measuring reads.
    """
    tool_name, arguments = called or ("", {})
    record = _register(tool_name, arguments) if reads_data else None

    def stoppable() -> Any:
        if record is None:
            with cancel.scope(_stop):
                return call()
        return _run_data(
            record,
            call,
            lambda: handlers.interrupted_facts(tool_name, arguments, **_dir(arguments)),
        )

    try:
        found = await asyncio.to_thread(stoppable)
        text = encode(found)
        if len(text) > RESULT_BUDGET and coarser is not None:
            coarse = to_json_compact(coarser(found))
            if len(coarse) <= RESULT_BUDGET:
                text = coarse
            elif receipt is not None:
                text = to_json_compact(receipt(found, len(coarse)))
        elif len(text) > RESULT_BUDGET and receipt is not None:
            text = to_json_compact(receipt(found, len(text)))
    except _Interrupted:
        assert record is not None
        return _interrupted(record)
    except cancel.Cancelled:
        return _stopped()
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not swallowed
        return _failed(exc)
    finally:
        if record is not None:
            _retire(record)
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
        lambda: handlers.describe_source(args["source"], **_dir(args)),
        encode=_as_columns,
        coarser=_described_by_type,
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
    return await _evidence(
        lambda: handlers.measure_overlaps(args["pairs"], **_dir(args)),
        reads_data=True,
        called=("measure_overlaps", args),
    )


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
        # Named columns are already the narrower question; too many of them is
        # refused, as it always was.
        coarser=_profiled_by_type if args.get("columns") is None else None,
        reads_data=True,
        called=("profile_source", args),
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
        ),
        reads_data=True,
        called=("query_data", args),
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
    return await _evidence(lambda: _draw(args), reads_data=True, called=("plot_data", args))


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
        ),
        reads_data=True,
        called=("join_findings", args),
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
        ),
        coarser=_shortened,
        receipt=_step_receipt,
        reads_data=True,
        called=("record_step", args),
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
        ),
        coarser=_spec_shortened,
    )


@tool(
    "run_spec",
    prompts.tool("run_spec"),
    {"spec_path": str},
    annotations=_READ_ONLY,
)
async def run_spec(args: dict[str, Any]) -> dict[str, Any]:
    return await _evidence(
        lambda: handlers.run_spec(args["spec_path"]),
        coarser=_shortened,
        reads_data=True,
        called=("run_spec", args),
    )


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
