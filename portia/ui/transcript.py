"""Right pane — the copilot.

A goal box and a **Go**, then the event stream live over the websocket as
`session.run` yields it. Model and effort are pickable here, and the turn states
which it is spending before it starts: an expensive run must never be silent.

Two of these event kinds are not rows in a log — they are the loop stopping for
the human, and they are the whole reason the UI exists:

- **`question-form`.** The evidence panels stay visible and interactive while it
  is pending. Never a modal — the reason to answer here instead of in a terminal
  is that the profile you need is still on screen. Options render in the order
  the agent gave them and are never re-ordered or recommended-badged: which one
  is best is the human's call, and the screen is not a participant.
- **`write-confirm`.** The payload is laid out, not dumped, and an `acknowledge`
  becomes a banner above everything else. Allow is a quiet button and Deny is
  always present; approving a write is not the primary action of the app, and
  making it the loudest thing on screen trains the reflex this component exists
  to prevent (docs/EVALUATION.md, Run 5).

**When the turn ends, it ends.** The panel says so and offers a new turn. There
is no chat box: the engine is single-turn, and an input implying a conversation
it cannot hold is a lie about the system.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from portia.agent import events
from portia.ui import components as c
from portia.ui import turn as turn_driver
from portia.ui.state import APP, Decision

#: Free text always goes through verbatim, and typing an objection is a
#: first-class action rather than a fallback.
FREE_TEXT = "text"
CHOSEN = "chosen"

TOOL_PREFIX = "mcp__portia__"
ARG_CHARS = 160

#: How the copilot's prose is rendered. ``code-friendly`` is not cosmetic: it
#: stops `_` from starting emphasis, and without it `customer_id and name` came
#: out as "customer" followed by italics with the underscores eaten. Column names
#: are the identifiers this whole product is about; a renderer that silently
#: rewrites them is worse than one that shows raw asterisks.
MARKDOWN_EXTRAS = ["fenced-code-blocks", "tables", "code-friendly"]


@ui.refreshable
def pane() -> None:
    _goal_input()
    with ui.element("div").classes("p-scroll p-pad stack-md") as scroll:
        if not APP.rows:
            c.empty_note(_IDLE)
        for row in APP.rows:
            _row(row)
        if APP.turn and APP.turn.ended:
            _turn_ended()
    if APP.turn is not None:
        _stay_at_the_bottom(scroll)


def _stay_at_the_bottom(scroll: ui.element) -> None:
    """Keep the newest row in view as the turn streams.

    A refresh rebuilds the rows, which would otherwise jump the panel back to
    the top on every event — and the two things worth reading, a question and a
    write confirmation, arrive at the bottom.
    """
    try:
        ui.run_javascript(f"getHtmlElement({scroll.id}).scrollTop = 1e9")
    except Exception:  # noqa: BLE001 — a scroll position is never worth an error
        pass


# --- the goal box -----------------------------------------------------------


def _goal_input() -> None:
    from portia.agent.session import DEFAULT_MODEL, EFFORTS, MODELS

    with ui.element("div").classes("p-pad stack-md"):
        with ui.element("div").classes("row-gap-sm"):
            c.pane_title("Copilot")
            if APP.busy:
                c.caption(_spend())
        if APP.turn is not None:
            _running_state()
            return

        APP.model = APP.model or DEFAULT_MODEL
        ui.textarea(placeholder=_GOAL_PLACEHOLDER).classes("p-field w-full p-editor").props(
            "borderless autogrow"
        ).bind_value(APP, "goal")
        with ui.element("div").classes("row-gap-sm"):
            ui.select(list(MODELS), value=APP.model).props(
                "borderless dense options-dense new-value-mode=add-unique use-input"
            ).classes("p-field p-field-mono").bind_value(APP, "model")
        c.segmented(EFFORTS, APP.effort, _set_effort)
        with ui.element("div").classes("row-gap-sm"):
            c.button("Go", _go, kind=_go_kind(), icon="play_arrow")
            c.caption(_spend())
    c.rule()


def _running_state() -> None:
    if APP.busy:
        with ui.element("div").classes("row-gap-sm"):
            ui.spinner(size="sm")
            c.caption("the copilot is working")
    c.rule()


def _set_effort(effort: str) -> None:
    APP.effort = effort
    pane.refresh()


def _spend() -> str:
    effort = f" · effort {APP.effort}" if APP.effort else ""
    return f"{APP.model}{effort}"


def _go_kind() -> str:
    """Go carries the accent until there is a spec to run; then Run does.

    At most one solid accent fill per view (DESIGN.md), and the two candidates
    are Go and Run. Which one is live follows the state the project is in: with
    no steps recorded the copilot is the way forward; once a spec has steps, the
    thing to do is run it.
    """
    return "tertiary" if APP.spec_has_steps else "primary"


async def _go() -> None:
    goal = (APP.goal or "").strip()
    if not goal or APP.busy:
        return
    await turn_driver.start(goal, model=APP.model, effort=APP.effort)


# --- one row per event ------------------------------------------------------


def _row(row: Any) -> None:
    if isinstance(row, Decision):
        _decision(row)
    elif isinstance(row, events.Event):
        _event(row)


def _event(event: events.Event) -> None:
    kind = event.kind
    with ui.element("div").classes("transcript-row"):
        if kind == events.TEXT:
            # The copilot writes markdown, so it is rendered as markdown. Showing
            # `**sales_orders**` literally is the panel failing to read what it
            # was handed.
            ui.markdown(event.data.get("text", ""), extras=MARKDOWN_EXTRAS).classes(
                "p-markdown t-body c-body"
            )
        elif kind == events.THINKING:
            c.collapsed("thinking", lambda: c.text(event.data.get("text", ""), color="c-mute"))
        elif kind == events.TOOL_CALL:
            _tool_call(event.data)
        elif kind == events.TOOL_RESULT:
            _tool_result(event.data)
        elif kind == events.ERROR:
            c.text(str(event.data.get("message", "")), color="c-error")
        elif kind == events.RESULT:
            return  # `turn-ended` states how it finished


def _tool_call(data: dict) -> None:
    name = str(data.get("name", "")).replace(TOOL_PREFIX, "")
    arguments = {k: v for k, v in (data.get("input") or {}).items() if k != "portia_dir"}
    rendered = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    with ui.element("div").classes("tr-tool"):
        ui.html(f"→ <span class='tool-name'>{name}</span>({_clip(rendered)})")


def _tool_result(data: dict) -> None:
    """The evidence the copilot acted on — the reason the event exists at all."""
    label = "result (error)" if data.get("is_error") else "result"
    text = data.get("text") or ""
    c.collapsed(label, lambda: c.code_block(text))


def _clip(value: str) -> str:
    return value if len(value) <= ARG_CHARS else f"{value[:ARG_CHARS]}…"


# --- the decisions ----------------------------------------------------------


def _decision(decision: Decision) -> None:
    if decision.kind == events.QUESTION:
        _question_form(decision) if not decision.resolved else _answered(decision)
    else:
        _write_confirm(decision) if not decision.resolved else _resolved_write(decision)


def _question_form(decision: Decision) -> None:
    with ui.element("div").classes("question-form"):
        for question in decision.payload["questions"]:
            _one_question(decision, question)
        with ui.element("div").classes("row-gap-sm"):
            c.button("Answer", lambda: _submit_answers(decision))


def _one_question(decision: Decision, question: dict) -> None:
    key = question["question"]
    draft = decision.draft.setdefault(key, {CHOSEN: [], FREE_TEXT: ""})
    multi = bool(question.get("multiSelect"))

    rows: dict[str, ui.element] = {}
    with ui.element("div").classes("stack-sm"):
        if question.get("header"):
            c.caption(str(question["header"]).upper())
        ui.label(key).classes("t-heading-sm pre-wrap")
        # Rendered in the order the agent gave them. Never re-ordered, never
        # recommended-badged: which option is best is the human's call, and the
        # screen is not a participant.
        for option in question.get("options") or []:
            label = str(option.get("label", ""))
            rows[label] = _option_row(option, draft, rows, multi=multi)
        ui.textarea(placeholder=_ANSWER_PLACEHOLDER).classes("p-field w-full p-editor").props(
            "borderless autogrow"
        ).bind_value(draft, FREE_TEXT)


def _option_row(option: dict, draft: dict, rows: dict, *, multi: bool) -> ui.element:
    label = str(option.get("label", ""))
    selected = label in draft[CHOSEN]
    classes = "option-row" + (" option-row--selected" if selected else "")
    with ui.element("div").classes(classes) as row:
        ui.label(label).classes("t-body-strong c-body pre-wrap")
        if option.get("description"):
            ui.label(str(option["description"])).classes("t-body c-mute pre-wrap")
    row.on("click", lambda: _pick(draft, rows, label, multi=multi))
    return row


def _pick(draft: dict, rows: dict, label: str, *, multi: bool) -> None:
    """Repaint the rows in place rather than rebuilding the pane.

    A refresh here would scroll the transcript back to the top, throwing away
    the evidence the human is reading in order to answer — which is the one
    thing this panel exists to keep on screen.
    """
    chosen = draft[CHOSEN]
    if label in chosen:
        chosen.remove(label)
    elif multi:
        chosen.append(label)
    else:
        draft[CHOSEN] = [label]

    for name, row in rows.items():
        row.classes(remove="option-row--selected")
        if name in draft[CHOSEN]:
            row.classes(add="option-row--selected")


def _submit_answers(decision: Decision) -> None:
    """Free text wins and goes through verbatim; otherwise the labels picked."""
    answers = {}
    for question in decision.payload["questions"]:
        key = question["question"]
        draft = decision.draft.get(key) or {}
        typed = (draft.get(FREE_TEXT) or "").strip()
        chosen = draft.get(CHOSEN) or []
        if typed:
            answers[key] = typed
        elif len(chosen) == 1 and not question.get("multiSelect"):
            answers[key] = chosen[0]
        elif chosen:
            answers[key] = list(chosen)
        else:
            return  # nothing said yet — an empty answer is worse than no answer
    decision.resolve(answers)
    pane.refresh()


def _answered(decision: Decision) -> None:
    for question, answer in (decision.outcome or {}).items():
        with ui.element("div").classes("transcript-row"):
            ui.label(str(question)).classes("t-body c-ink pre-wrap")
            ui.label(_as_text(answer)).classes("t-body c-accent pre-wrap")


#: Where each write names what it is writing to. Shown once, in the header.
TARGET_FIELDS = ("spec_path", "source", "name")


def _write_confirm(decision: Decision) -> None:
    name = str(decision.payload["name"]).replace(TOOL_PREFIX, "")
    payload = decision.payload["input"]
    step = payload.get("step") or {}
    acknowledge = list(step.get("acknowledge") or [])
    target = next((f for f in TARGET_FIELDS if payload.get(f)), None)

    # Said once each: the target is in the header, and an acknowledged step's
    # rationale is in the banner. Repeating either buries the parts that differ.
    skip = c.HIDDEN_FIELDS + tuple(f for f in (target,) if f)
    if acknowledge:
        skip += ("rationale",)

    with ui.element("div").classes("write-confirm"):
        if acknowledge:
            c.acknowledged_banner(acknowledge, rationale=step.get("rationale"))
        with ui.element("div").classes("row-gap-sm"):
            ui.label(name).classes("t-mono c-ink")
            if target:
                c.mono(str(payload[target]), color="c-mute")
        c.payload_view(payload, skip=skip)
        with ui.element("div").classes("row-gap-sm"):
            c.button("Allow", lambda: _resolve_write(decision, True))
            c.button("Deny", lambda: _resolve_write(decision, False), kind="secondary")


def _resolve_write(decision: Decision, allowed: bool) -> None:
    decision.resolve(allowed)
    pane.refresh()


def _resolved_write(decision: Decision) -> None:
    name = str(decision.payload["name"]).replace(TOOL_PREFIX, "")
    outcome = "allowed" if decision.outcome else "declined"
    with ui.element("div").classes("transcript-row"):
        with ui.element("div").classes("row-gap-sm"):
            c.mono(name, color="c-ink")
            c.caption(outcome, color="c-accent" if decision.outcome else "c-mute")
        c.collapsed("payload", lambda: c.payload_view(decision.payload["input"]))


# --- the end of a turn ------------------------------------------------------


def _turn_ended() -> None:
    turn = APP.turn
    assert turn is not None
    with ui.element("div").classes("turn-ended"):
        if turn.error:
            c.text(turn.error, color="c-error")
        c.caption(_ended_line(turn))
        c.caption(_NO_FOLLOW_UP)
        with ui.element("div").classes("row-gap-sm"):
            c.button("New turn", _new_turn, icon="refresh")


def _ended_line(turn: Any) -> str:
    cost = f" · ~${turn.cost_usd:.4f}" if turn.cost_usd else ""
    return f"turn ended ({turn.subtype or 'stopped'}) · {turn.model}{cost}"


def _new_turn() -> None:
    APP.turn = None
    APP.rows = []
    pane.refresh()


def _as_text(answer: Any) -> str:
    return ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)


_IDLE = "Nothing yet. Describe the goal above and press Go."
_GOAL_PLACEHOLDER = "What do you want from this data?"
_ANSWER_PLACEHOLDER = "…or answer in your own words"
_NO_FOLLOW_UP = (
    "A turn is one shot. A new one starts fresh, with the catalog and spec on disk as its memory."
)
