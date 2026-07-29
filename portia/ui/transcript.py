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
from portia.ui import state
from portia.ui import turn as turn_driver
from portia.ui.state import APP, Decision

#: Free text always goes through verbatim, and typing an objection is a
#: first-class action rather than a fallback.
FREE_TEXT = "text"
CHOSEN = "chosen"

ARG_CHARS = 160


@ui.refreshable
def pane() -> None:
    _tabs()
    stream = APP.stream()
    if APP.tab == state.CHAT:
        _goal_input(stream)
    else:
        _index_header(stream)
    with ui.element("div").classes("p-scroll p-pad stack-md") as scroll:
        if not stream.rows:
            c.empty_note(_IDLE if APP.tab == state.CHAT else _IDLE_INDEX)
        for row in stream.rows:
            _row(row)
        if stream.turn and stream.turn.ended:
            _turn_ended(stream)
    if stream.turn is not None:
        _stay_at_the_bottom(scroll)


# --- the two tabs -----------------------------------------------------------


def _tabs() -> None:
    """Copilot and Indexing, side by side.

    They are different jobs with different rhythms — one you drive, one the app
    runs on your behalf — and interleaving them in a single scroll made each
    harder to read than it is alone. The marks matter as much as the split: a
    turn is live or a decision is waiting on a tab you cannot see, and the pane
    has to say so or a blocked loop looks like a hung one.
    """
    with ui.element("div").classes("pane-tabs"):
        for tab in state.TABS:
            _tab(tab)


def _tab(tab: str) -> None:
    stream = APP.stream(tab)
    classes = "pane-tab" + (" pane-tab--active" if tab == APP.tab else "")
    with ui.element("div").classes(classes) as element:
        ui.label(_TAB_LABEL[tab])
        if stream.pending is not None:
            ui.element("div").classes("pane-tab-dot pane-tab-dot--waiting").tooltip(_WAITING)
        elif stream.busy:
            ui.element("div").classes("pane-tab-dot").tooltip(_RUNNING)
    element.on("click", lambda t=tab: _show_tab(t))


def _show_tab(tab: str) -> None:
    APP.tab = tab
    pane.refresh()


def _index_header(stream) -> None:
    """What this tab is for, and what is happening in it."""
    with ui.element("div").classes("p-pad stack-md"):
        if stream.turn is not None:
            _turn_banner(stream.turn)
            if stream.busy:
                with ui.element("div").classes("row-gap-sm"):
                    ui.spinner(size="sm")
                    c.caption("the copilot is working")
        else:
            c.caption(_INDEX_WHAT)
    c.rule()


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


def _goal_input(stream) -> None:
    from portia.agent.session import DEFAULT_MODEL, EFFORTS, MODELS

    with ui.element("div").classes("p-pad stack-md"):
        if stream.turn is not None:
            _running_state(stream)
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


def _running_state(stream) -> None:
    if stream.busy:
        with ui.element("div").classes("row-gap-sm"):
            ui.spinner(size="sm")
            c.caption(f"the copilot is working · {_spend()}")


def _turn_banner(turn) -> None:
    """What this turn is, and which half of indexing is actually running.

    Profiling already happened and was free; what costs a turn is the
    interpretation. A panel that merges the two is the "one merged spinner"
    docs/DESIGN.md forbids.
    """
    if turn.kind == state.GOAL:
        return
    with ui.element("div").classes("turn-banner"):
        with ui.element("div").classes("row-gap-sm"):
            ui.icon(_BANNER_ICON[turn.kind]).classes("fact-icon")
            ui.label(_BANNER_TITLE[turn.kind]).classes("t-body-strong c-ink")
            if turn.label:
                c.mono(turn.label, color="c-mute", small=True)
        c.caption(_BANNER_WHY[turn.kind])


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
            c.markdown(event.data.get("text", ""))
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
    name = events.tool_label(str(data.get("name", "")))
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
    name = events.tool_label(str(decision.payload["name"]))
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
    name = events.tool_label(str(decision.payload["name"]))
    outcome = "allowed" if decision.outcome else "declined"
    with ui.element("div").classes("transcript-row"):
        with ui.element("div").classes("row-gap-sm"):
            c.mono(name, color="c-ink")
            c.caption(outcome, color="c-accent" if decision.outcome else "c-mute")
        c.collapsed("payload", lambda: c.payload_view(decision.payload["input"]))


# --- the end of a turn ------------------------------------------------------


def _turn_ended(stream) -> None:
    turn = stream.turn
    assert turn is not None
    with ui.element("div").classes("turn-ended"):
        if turn.error:
            c.text(turn.error, color="c-error")
        c.caption(_ended_line(turn))
        c.caption(_NO_FOLLOW_UP)
        with ui.element("div").classes("row-gap-sm"):
            c.button("New turn", lambda: _new_turn(stream), icon="refresh")


def _ended_line(turn: Any) -> str:
    cost = f" · ~${turn.cost_usd:.4f}" if turn.cost_usd else ""
    return f"turn ended ({turn.subtype or 'stopped'}) · {turn.model}{cost}"


def _new_turn(stream) -> None:
    stream.turn = None
    stream.rows = []
    pane.refresh()


def _as_text(answer: Any) -> str:
    return ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)


#: What each non-goal turn is, in the panel it shares with the chat.
_BANNER_ICON = {state.INDEXING: "inventory_2", state.REREAD: "autorenew"}
_BANNER_TITLE = {state.INDEXING: "Indexing", state.REREAD: "Re-reading"}
_BANNER_WHY = {
    state.INDEXING: "Profiled already — free and deterministic. The copilot is reading them now.",
    state.REREAD: "The copilot is re-reading this source with your note in hand.",
}

_TAB_LABEL = {state.CHAT: "Copilot", state.INDEX: "Indexing"}
_RUNNING = "a turn is running here"
_WAITING = "this turn is waiting on you"

_IDLE = "Nothing yet. Describe the goal above and press Go."
_IDLE_INDEX = "Nothing indexed in this session."
_INDEX_WHAT = "Reading a source lands here — from Add data, or from Ask the copilot on a source."
_GOAL_PLACEHOLDER = "What do you want from this data?"
_ANSWER_PLACEHOLDER = "…or answer in your own words"
_NO_FOLLOW_UP = (
    "A turn is one shot. A new one starts fresh, with the catalog and spec on disk as its memory."
)
