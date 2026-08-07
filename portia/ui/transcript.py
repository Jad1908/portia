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
from portia.ui import engine, state
from portia.ui import exchange as exchange_driver
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
        if stream.exchange and stream.exchange.ended:
            _turn_ended(stream)
    if stream.exchange is not None:
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
    """What this tab is for, what is happening in it, and what is left to do.

    **The list is the point of the tab now.** It used to be a transcript and
    nothing else, so "which sources has the copilot actually read" was a
    question you answered by clicking through the left pane one row at a time —
    and the state that matters most, *profiled but never read*, is invisible in
    a list that only knows indexed from not.

    While a turn runs the list is replaced by the banner: the turn is about
    these sources, and offering to start a second one on top of it is an
    invitation to a race.
    """
    with ui.element("div").classes("p-pad stack-md"):
        if stream.exchange is not None:
            _exchange_banner(stream.exchange)
            if stream.busy:
                with ui.element("div").classes("row-gap-sm"):
                    ui.spinner(size="sm")
                    c.caption("the copilot is working")
        else:
            c.caption(_INDEX_WHAT)
            _source_states()
    c.rule()


#: What each state is called on screen, and the icon that repeats down the rows.
#: Words once at the top would be a legend; these are short enough to repeat.
_STATE_LABEL = {
    engine.UNINDEXED: "not indexed",
    engine.UNREAD: "not read",
    engine.INTERPRETED: "read",
}
_STATE_ICON = {
    engine.UNINDEXED: "radio_button_unchecked",
    engine.UNREAD: "pending",
    engine.INTERPRETED: "check_circle",
}


def _source_states() -> None:
    """Every source, what portia knows about it, and what you can do next."""
    states = engine.source_states(APP)
    if not states:
        c.empty_note(_NO_SOURCES)
        return

    with ui.element("div").classes("index-list"):
        for source in states:
            _source_state_row(source)
    _index_actions()


def _source_state_row(source) -> None:
    ticked = source.name in APP.index_ticks
    with ui.element("div").classes(f"index-row is-{source.state}"):
        (
            ui.checkbox(value=ticked)
            .classes("p-check")
            .props("dense")
            .on_value_change(lambda e, n=source.name: _tick_source(n, bool(e.value)))
        )
        ui.icon(_STATE_ICON[source.state]).classes("index-row-icon")
        ui.label(source.name).classes("index-row-name")
        ui.label(_STATE_LABEL[source.state]).classes("index-row-state")
        if source.stale:
            ui.label("changed").classes("index-row-stale")


def _tick_source(name: str, on: bool) -> None:
    """Only the buttons redraw, **not** the list.

    Refreshing the whole pane rebuilt the rows, which deletes the checkbox you
    are in the middle of clicking — so ticking two boxes quickly registered one.
    The checkbox holds its own value; the only thing a tick changes on screen is
    what the two buttons say they will do.
    """
    APP.index_ticks = (APP.index_ticks | {name}) if on else (APP.index_ticks - {name})
    _index_actions.refresh()


@ui.refreshable
def _index_actions() -> None:
    """Two actions, because they cost different things.

    Profiling is deterministic and free; reading costs a model turn. A single
    button doing both would hide which of the two you were about to spend.
    """
    ticked = [s for s in engine.source_states(APP) if s.name in APP.index_ticks]
    to_index = [s for s in ticked if not s.indexed]
    to_read = [s for s in ticked if s.indexed]
    with ui.element("div").classes("index-actions"):
        c.button(
            f"Index {c.count(len(to_index), 'source')}",
            _index_ticked,
            kind="secondary",
            enabled=bool(to_index),
            icon=c.INDEX_ICON,
        )
        c.button(
            f"Interpret {c.count(len(to_read), 'source')}",
            _interpret_ticked,
            kind="primary",
            enabled=bool(to_read),
            icon="auto_awesome",
        )
    c.caption(_INDEX_COST)


async def _index_ticked() -> None:
    """Profile the ticked files. Free, deterministic, no model exchange."""
    from portia.ui import artifacts

    paths = [
        APP.root / s.rel
        for s in engine.source_states(APP)
        if s.name in APP.index_ticks and not s.indexed
    ]
    if not paths:
        return
    await engine.index(paths, APP)
    artifacts.pane.refresh()
    pane.refresh()
    ui.notify(f"profiled {c.count(len(paths), 'source')}")


async def _interpret_ticked() -> None:
    """Spend a turn reading the ticked sources — including ones already read.

    Re-reading is the same act as reading: `set_interpretation` writes judgment
    and never touches a measured fact, so running it again over a source whose
    context has changed is a correction, not a conflict.
    """
    from portia.agent import prompts
    from portia.ui import exchange as exchange_module
    from portia.ui.screens import _default_model

    names = [s.name for s in engine.source_states(APP) if s.name in APP.index_ticks and s.indexed]
    if not names:
        return
    APP.index_ticks = frozenset()
    await exchange_module.start(
        prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
        model=APP.model or _default_model(),
        effort=APP.effort,
        kind=state.INDEXING,
        label=", ".join(names),
    )


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
    with ui.element("div").classes("p-pad stack-md"):
        if stream.exchange is not None:
            _running_state(stream)
            return

        ui.textarea(placeholder=_GOAL_PLACEHOLDER).classes("p-field w-full p-editor").props(
            "borderless autogrow"
        ).bind_value(APP, "goal")
        c.model_effort(APP, _set_effort)
        with ui.element("div").classes("row-gap-sm"):
            c.button("Go", _go, kind=_go_kind(), icon="play_arrow")
            c.caption(_spend())
    c.rule()


def _running_state(stream) -> None:
    if stream.busy:
        with ui.element("div").classes("row-gap-sm"):
            ui.spinner(size="sm")
            c.caption(f"the copilot is working · {_spend()}")


def _exchange_banner(turn) -> None:
    """What this turn is, and which half of indexing is actually running.

    Profiling already happened and was free; what costs a turn is the
    interpretation. A panel that merges the two is the "one merged spinner"
    docs/DESIGN.md forbids.
    """
    if turn.kind == state.GOAL:
        return
    with ui.element("div").classes("exchange-banner"):
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
    await exchange_driver.start(goal, model=APP.model, effort=APP.effort)


# --- one row per event ------------------------------------------------------


def _row(row: Any) -> None:
    if isinstance(row, Decision):
        _decision(row)
    elif isinstance(row, events.Event):
        _event(row)


def _event(event: events.Event) -> None:
    kind = event.kind
    if kind == events.PROMPT:
        _prompt_row(event.data)
        return
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
            return  # `chat-ended` states how it finished


def _prompt_row(data: dict) -> None:
    """What the human said, opening an exchange.

    Its own row kind rather than a `transcript-row`, because in a chat of six
    messages this is the only thing separating one exchange from the next. It
    states the model it ran on: a chat can change model mid-way, and the banner
    above only says what the *next* one will use.

    Uncoloured beyond the kind — `DESIGN.md`: prominence communicates kind, never
    rank. A human message is not more important than the copilot's reply.
    """
    with ui.element("div").classes("prompt-row"):
        c.text(str(data.get("text", "")), color="c-ink")
        model = str(data.get("model") or "")
        if model:
            effort = f" · {data['effort']}" if data.get("effort") else ""
            c.caption(f"{model.replace('claude-', '')}{effort}")


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
    _answers_view(decision.outcome or {})


def _answers_view(answers: dict) -> None:
    """What was asked and what was said, live or replayed from a log."""
    for question, answer in answers.items():
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
    _resolved_write_view(
        str(decision.payload["name"]), decision.payload["input"], bool(decision.outcome)
    )


def _resolved_write_view(name: str, payload: dict, allowed: bool | None) -> None:
    """A write and what the human did with it — live, or replayed from a log.

    One renderer for both, because a replay that laid a write out differently
    from the panel it happened in would be a second opinion about the same
    moment. ``allowed`` is None when the log has the request and not the
    outcome: a turn killed at the confirmation prompt, which is a real thing
    that happens and is not the same as a refusal.
    """
    outcome = "unanswered" if allowed is None else ("allowed" if allowed else "declined")
    with ui.element("div").classes("transcript-row"):
        with ui.element("div").classes("row-gap-sm"):
            c.mono(events.tool_label(name), color="c-ink")
            c.caption(outcome, color="c-accent" if allowed else "c-mute")
        c.collapsed("payload", lambda: c.payload_view(payload))


# --- a turn that already happened -------------------------------------------


def replay(run: Any) -> None:
    """A logged turn, rendered where a live one would be (`portia/runlog.py`).

    Same renderers as the live panel, deliberately: the log stores the events
    the panel was drawing, so replaying them through anything else would be a
    second opinion about a turn that is already written down. What differs is
    that nothing here is answerable — the questions were answered months ago,
    and a form you can fill in on a dead turn is a lie about what it would do.

    Questions and writes each arrive as two events — asked, then answered — and
    each becomes **one** row, because that is what the live panel shows once a
    decision resolves: the answer, not the form that collected it. Drawing both
    halves listed the same question twice, which reads as the copilot having
    asked it twice.
    """
    for index, event in enumerate(run.events):
        if event.kind == events.APPROVAL:
            _resolved_write_view(
                str(event.data.get("name", "")),
                event.data.get("input") or {},
                _outcome_after(run.events, index),
            )
        elif event.kind == events.QUESTION:
            if _answered_after(run.events, index):
                continue  # the ANSWER draws it, exactly as the live panel does
            # No answer under it: the turn ended with the question on screen.
            # That is the shape of an interrupted run and `EVALUATION.md` cares
            # about it, so the options it was offering stay visible.
            _asked_view(event.data.get("questions") or [])
            c.empty_note(_UNANSWERED)
        elif event.kind == events.ANSWER:
            _answers_view(event.data.get("answers") or {})
        elif event.kind == events.APPROVAL_RESULT:
            continue  # drawn with the request it resolves
        else:
            _event(event)


def _answered_after(rows: list, index: int) -> bool:
    """Whether the question at ``index`` ever got an answer."""
    for event in rows[index + 1 :]:
        if event.kind == events.ANSWER:
            return True
        if event.kind == events.QUESTION:
            break  # the next question — this one was never answered
    return False


def _outcome_after(rows: list, index: int) -> bool | None:
    """The allow/deny that resolved the write at ``index``, if the log has one."""
    for event in rows[index + 1 :]:
        if event.kind == events.APPROVAL_RESULT:
            return bool(event.data.get("allowed"))
        if event.kind == events.APPROVAL:
            break  # the next write's request — this one was never resolved
    return None


def _asked_view(questions: list[dict]) -> None:
    """The question as it was put, with its options in the order given.

    Never re-ordered and never badged, replayed or live: which option was best
    is what the human was being asked, and the screen is not a participant.
    """
    for question in questions:
        with ui.element("div").classes("transcript-row"):
            ui.label(str(question.get("question", ""))).classes("t-body c-ink pre-wrap")
            for option in question.get("options") or []:
                c.caption(str(option.get("label", "")))


# --- the end of a turn ------------------------------------------------------


def _turn_ended(stream) -> None:
    turn = stream.exchange
    assert turn is not None
    with ui.element("div").classes("chat-ended"):
        if turn.error:
            c.text(turn.error, color="c-error")
        c.caption(_ended_line(turn))
        spend = _spend_line(turn)
        if spend:
            c.caption(spend)
        c.caption(_NO_FOLLOW_UP)
        with ui.element("div").classes("row-gap-sm"):
            c.button("New chat", lambda: _new_chat(stream), icon="refresh")


def _ended_line(turn: Any) -> str:
    cost = f" · ~${turn.cost_usd:.4f}" if turn.cost_usd else ""
    return f"turn ended ({turn.subtype or 'stopped'}) · {turn.model}{cost}"


def _spend_line(turn: Any) -> str:
    """What the turn cost, in tokens — a **count**, next to the cost in money.

    The numbers are `runlog.token_totals`', not this panel's, so the window and
    `cli.history` cannot disagree about one turn. `in` is the whole input including
    what came from cache, which on a portia turn is nearly all of it: the L0
    prompt and the L1 brief go on every request, and the SDK's raw
    `input_tokens` counts only the part that was not cached — one real run
    reported 17 for a turn that sent 14,651.

    Reported, never judged. Whether a turn was expensive needs a goal, and this
    panel has no way to know one.
    """
    if turn.input_tokens is None:
        return ""
    cached = f" ({c.count(turn.cached_tokens or 0, 'cached')})" if turn.cached_tokens else ""
    return (
        f"{c.count(turn.input_tokens, 'token')} in{cached}"
        f" · {c.count(turn.output_tokens or 0, 'token')} out"
    )


def _new_chat(stream) -> None:
    stream.exchange = None
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
_RUNNING = "something is running here"
_WAITING = "this is waiting on you"

_UNANSWERED = "It ended with this question unanswered."

_IDLE = "Nothing yet. Describe the goal above and press Go."
_IDLE_INDEX = "Nothing indexed in this session."
_NO_SOURCES = "No data in this project's folder yet. Add some from the left pane."
_INDEX_COST = "profiling is free and deterministic · interpreting spends a model exchange"
_INDEX_WHAT = "Reading a source lands here — from Add data, or from Ask the copilot on a source."
_GOAL_PLACEHOLDER = "What do you want from this data?"
_ANSWER_PLACEHOLDER = "…or answer in your own words"
_NO_FOLLOW_UP = (
    "A chat is one shot for now. A new one starts fresh, with the catalog and spec on disk as\n"
    "its memory."
)
