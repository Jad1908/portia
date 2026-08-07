"""Driving a copilot turn from the window.

V0 drives; it is not a viewer. `agent/ask.py` injects ``answer`` and ``confirm``
as callables precisely so something other than stdin can supply them — its
docstring names this panel as the third consumer. So there is no engine change
here: a form-backed ``answer`` and a payload-rendering ``confirm``, and every
question and every write confirmation lands on screen.

The one thing to know about ordering. ``ask.build_can_use_tool`` emits its
question event into a buffer that `session.run` drains from the *outer* loop —
which is blocked on the message stream while the callback waits for an answer.
So the form is rendered from inside the callback, exactly as `cli/chat.py` prints
from inside ``answer_questions``. Waiting for the yielded event instead would
deadlock: the event that renders the form only arrives once the form has been
answered.

Those two callbacks therefore append their own rows, and `_record` drops the
question/answer/approval events when they later arrive, so the transcript keeps
one entry per thing that happened, in the order it happened.
"""

from __future__ import annotations

import asyncio
from typing import Any

from portia import runlog
from portia.agent import events
from portia.ui import engine, state
from portia.ui.state import APP, Decision

#: Events the UI already represents as a `Decision`, having rendered them from
#: inside the callback that produced them.
_OWNED = (events.QUESTION, events.ANSWER, events.APPROVAL)

#: Tool results are where a durable artifact changes on disk, so the panes catch
#: up there — the graph fills in as steps are recorded, not after the turn.
_SYNC_ON = (events.TOOL_RESULT, events.RESULT)


async def start(
    prompt: str,
    *,
    model: str,
    effort: str | None,
    kind: str = state.GOAL,
    label: str = "",
) -> None:
    """Run one exchange to completion, streaming its events into the transcript."""
    from portia.ui import transcript

    if APP.busy:
        return
    stream = APP.start_exchange(prompt, model=model, effort=effort, kind=kind, label=label)
    turn = stream.exchange
    assert turn is not None
    transcript.pane.refresh()

    try:
        from portia.agent import session
    except ImportError as exc:  # the `agent` extra isn't installed
        turn.error = f"{type(exc).__name__}: {exc}"
        turn.running = False
        transcript.pane.refresh()
        return

    # The window's copy of a turn dies with the window; this is the durable one
    # (`portia/runlog.py`). Teed here, at the edge, for the same reason the CLI
    # tees in `run_turn` — the engine must not learn it is being observed.
    log = runlog.start(APP.portia_dir, prompt=prompt, model=model, effort=effort, cwd=str(APP.root))

    try:
        async for event in session.run(
            prompt,
            answer=answer,
            confirm=confirm,
            model=model,
            effort=effort,
            cwd=str(APP.root),
            portia_dir=APP.portia_dir,
        ):
            _record(event, stream, log)
    except asyncio.CancelledError:
        turn.error = "interrupted"
        raise
    except Exception as exc:  # noqa: BLE001 — shown to the operator, not swallowed
        turn.error = f"{type(exc).__name__}: {exc}"
    finally:
        turn.running = False
        _resolve_orphans()
        _sync_artifacts()
        transcript.pane.refresh()


def _record(event: events.Event, stream: state.Stream, log: runlog.Log) -> None:
    from portia.ui import transcript

    # Logged before the panel's own bookkeeping: the events the transcript drops
    # are ones it has *already rendered* from inside the callback that produced
    # them, and a log missing every question and every write confirmation would
    # be missing the decisions the run is worth reading for.
    log.event(event)

    if event.kind in _OWNED:
        return
    if event.kind == events.RESULT and stream.exchange is not None:
        stream.exchange.subtype = event.data.get("subtype")
        stream.exchange.cost_usd = event.data.get("cost_usd")
        totals = runlog.token_totals(event.data.get("usage") or {})
        stream.exchange.input_tokens = totals["input_tokens"]
        stream.exchange.cached_tokens = totals["cached_tokens"]
        stream.exchange.output_tokens = totals["output_tokens"]
    stream.rows.append(event)
    if event.kind in _SYNC_ON:
        _sync_artifacts()
    transcript.pane.refresh()


# --- the two moments the loop stops for the human ---------------------------


async def answer(questions: list[dict]) -> dict[str, Any]:
    """Render the copilot's questions as a form and wait for the human."""
    from portia.ui import transcript

    decision = _stop(events.QUESTION, {"questions": questions})
    answers = await decision.future
    transcript.pane.refresh()
    return answers


async def confirm(tool_name: str, tool_input: dict) -> bool:
    """Lay out a pending write and wait for allow or deny."""
    from portia.ui import transcript

    decision = _stop(events.APPROVAL, {"name": tool_name, "input": tool_input})
    allowed = await decision.future
    transcript.pane.refresh()
    return bool(allowed)


def _stop(kind: str, payload: dict) -> Decision:
    """Park a decision in the running turn's stream, and show it.

    The loop is now blocked on a human. A question sitting behind a tab nobody
    is looking at is indistinguishable from a hung turn, so the pane follows the
    decision rather than waiting to be found — and if the human is still on the
    add-data screen, they are **invited** to come and see it.

    That last part is what makes the opening read safe. It starts as soon as
    profiling finishes, with the add-data screen still up, so the copilot can
    stop and ask while the transcript is nowhere on screen. The popup is the
    bridge: it says what is waiting and offers the way through, and the human
    decides when to take it (`state.prompt_for_decision`).
    """
    from portia.ui import screens, transcript

    decision = Decision(kind, payload, asyncio.get_running_loop().create_future())
    stream = _running_stream()
    stream.rows.append(decision)
    APP.tab = next(tab for tab, s in APP.streams.items() if s is stream)
    if APP.prompt_for_decision(kind):
        screens.offer_workspace()
    transcript.pane.refresh()
    return decision


def _running_stream() -> state.Stream:
    """Whichever stream owns the live exchange. The engine only ever runs one."""
    return next((s for s in APP.streams.values() if s.busy), APP.stream())


def _resolve_orphans() -> None:
    """A turn that ended mid-question leaves a form nobody can answer.

    It happens when the stream errors or the SDK gives up while the callback is
    still waiting. Cancel the future rather than leave the panel showing a
    decision that will never be read.
    """
    for stream in APP.streams.values():
        for row in stream.rows:
            if isinstance(row, Decision) and not row.resolved and not row.future.done():
                row.future.cancel()


# --- keeping the other panes honest -----------------------------------------


def _sync_artifacts() -> None:
    """Re-read what the copilot may have just written, and redraw the panes."""
    from portia.ui import app as app_module
    from portia.ui import artifacts, workflow

    engine.refresh_catalog(APP)
    if APP.spec_path is None:
        specs = engine.specs_in(APP)
        if specs:
            engine.select_spec(specs[0], APP)
    else:
        engine.reload_spec(APP)

    artifacts.pane.refresh()
    workflow.pane.refresh()
    app_module.run_controls.refresh()
