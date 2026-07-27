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
    """Run one turn to completion, streaming its events into the transcript."""
    from portia.ui import transcript

    if APP.busy:
        return
    turn = APP.start_turn(prompt, model=model, effort=effort, kind=kind, label=label)
    transcript.pane.refresh()

    try:
        from portia.agent import session
    except ImportError as exc:  # the `agent` extra isn't installed
        turn.error = f"{type(exc).__name__}: {exc}"
        turn.running = False
        transcript.pane.refresh()
        return

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
            _record(event, turn)
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


def _record(event: events.Event, turn: Any) -> None:
    from portia.ui import transcript

    if event.kind in _OWNED:
        return
    if event.kind == events.RESULT:
        turn.subtype = event.data.get("subtype")
        turn.cost_usd = event.data.get("cost_usd")
    APP.rows.append(event)
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
    from portia.ui import transcript

    decision = Decision(kind, payload, asyncio.get_running_loop().create_future())
    APP.rows.append(decision)
    transcript.pane.refresh()
    return decision


def _resolve_orphans() -> None:
    """A turn that ended mid-question leaves a form nobody can answer.

    It happens when the stream errors or the SDK gives up while the callback is
    still waiting. Cancel the future rather than leave the panel showing a
    decision that will never be read.
    """
    for row in APP.rows:
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
    app_module.toolbar.refresh()
