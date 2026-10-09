"""*Interrupt query* in the window: the menu, the card, the record (`CONVERSATION.md` §16).

The engine's half is `tests/test_query_interrupt.py`. These pin what the user
agreed the window does: a press asks why and the query keeps running while it
asks; a reason picked interrupts with that reason, and under *Other* only once
the sentence is sent; shutting the menu undoes the press; *Don't ask again*
interrupts at once from then on and Settings turns it back on; an interrupted
card is greyed and said to be the user's doing, never a red error; and the log
says what each press came to.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from portia import runlog
from portia.agent import events
from portia.ui import state
from portia.ui.state import App

pytest.importorskip("nicegui", reason="the app needs the `ui` extra")

from nicegui import context, ui  # noqa: E402

from portia.ui import engine, exchange, interrupt, settings, transcript  # noqa: E402

_ARGS = {"sql": "SELECT 1", "inputs": ["orders"], "question": "q"}


def _call(card: str = "toolu_1", name: str = "query_data", args: dict | None = None):
    return events.Event(
        events.TOOL_CALL,
        {"id": card, "name": f"{events.TOOL_PREFIX}{name}", "input": dict(args or _ARGS)},
    )


@contextlib.contextmanager
def _as_app(app, *modules):
    originals = [(m, m.APP) for m in modules]
    for m in modules:
        m.APP = app
    try:
        yield app
    finally:
        for m, original in originals:
            m.APP = original


@pytest.fixture
def live(monkeypatch):
    """A chat with one `query_data` running, and the engine's half recording presses."""
    app = App()
    chat = app.new_chat()
    app.show_chat(chat)
    chat.exchange = state.Exchange(prompt="x", model="m", effort=None)
    chat.rows.append(_call())
    pressed: list[tuple] = []
    calls: dict[str, dict] = {}

    def interrupt_call(card, tool, args, *, reason=None, note=""):
        pressed.append((card, tool, reason, note))
        calls[card] = {"phase": state.CALL_RUNNING, "interrupted": True, "started": None}
        return "interrupting"

    monkeypatch.setattr(engine, "interrupt_call", interrupt_call)
    monkeypatch.setattr(engine, "data_call", lambda card, tool, args: calls.get(card))
    with _as_app(app, exchange, interrupt, transcript, settings):
        with ui.element("div"):
            interrupt.build_menu()
        yield SimpleNamespace(app=app, chat=chat, pressed=pressed, calls=calls)


def _menu():
    return interrupt._MENUS[context.client.id]


# --- the menu: ask first, then interrupt -------------------------------------------


def test_a_press_asks_why_and_the_query_keeps_running(live):
    interrupt.pressed("toolu_1")

    menu = _menu()
    assert menu.open_for == "toolu_1"
    assert menu.card.visible and menu.card._props["data-anchor"] == "toolu_1"
    assert live.pressed == []


def test_shutting_the_menu_without_a_pick_undoes_the_press(live):
    interrupt.pressed("toolu_1")
    _menu().tick.value = True
    interrupt.closed("toolu_1")

    menu = _menu()
    assert menu.open_for is None and not menu.card.visible
    assert "data-anchor" not in menu.card._props
    assert live.pressed == []
    assert live.app.ask_before_interrupt is True, "the tick goes with the press it was on"


def test_pressing_the_control_again_while_the_menu_is_open_shuts_it(live):
    interrupt.pressed("toolu_1")
    interrupt.pressed("toolu_1")
    assert _menu().open_for is None
    assert live.pressed == []


def test_a_reason_picked_interrupts_with_that_reason(live):
    interrupt.pressed("toolu_1")
    interrupt._picked(context.client.id, "Wrong query")

    assert live.pressed == [("toolu_1", "query_data", "Wrong query", "")]
    assert _menu().open_for is None


def test_other_waits_for_its_sentence(live):
    interrupt.pressed("toolu_1")
    interrupt._other(context.client.id)
    menu = _menu()
    assert menu.other.visible
    assert live.pressed == []

    menu.field.value = "it joins on the meter and not the hour"
    interrupt._send_other(context.client.id)
    assert live.pressed == [
        ("toolu_1", "query_data", "Other", "it joins on the meter and not the hour")
    ]
    assert not menu.other.visible and menu.field.value == ""


def test_dont_ask_again_interrupts_every_later_press_at_once(live):
    interrupt.pressed("toolu_1")
    _menu().tick.value = True
    interrupt._picked(context.client.id, "Takes too long")
    assert live.app.ask_before_interrupt is False

    live.chat.rows.append(_call("toolu_2", "profile_source", {"source": "orders"}))
    interrupt.pressed("toolu_2")
    assert _menu().open_for is None
    assert live.pressed[-1] == ("toolu_2", "profile_source", None, "")


def test_a_press_once_interrupted_skips_the_measuring_and_asks_nothing(live):
    interrupt.pressed("toolu_1")
    interrupt._picked(context.client.id, "Wrong query")
    interrupt.pressed("toolu_1")
    assert _menu().open_for is None
    assert [p[0] for p in live.pressed] == ["toolu_1", "toolu_1"]
    assert live.pressed[-1][2] is None


def test_a_press_on_a_call_that_answered_or_reads_no_data_does_nothing(live):
    live.chat.rows.append(events.Event(events.TOOL_RESULT, {"id": "toolu_1", "text": "{}"}))
    live.chat.rows.append(_call("toolu_3", "describe_source", {"source": "orders"}))
    interrupt.pressed("toolu_1")
    interrupt.pressed("toolu_3")
    assert _menu().open_for is None and live.pressed == []


def test_the_menu_shuts_when_its_call_answers(live):
    interrupt.pressed("toolu_1")
    live.chat.rows.append(events.Event(events.TOOL_RESULT, {"id": "toolu_1", "text": "{}"}))
    interrupt.tick()
    assert _menu().open_for is None


def test_settings_turns_asking_back_on(live):
    live.app.ask_before_interrupt = False
    with ui.element("div") as panel:
        settings._interrupting()
    (switch,) = [e for e in panel.descendants() if isinstance(e, ui.switch)]
    assert switch.value is False
    switch.value = True
    assert live.app.ask_before_interrupt is True


def test_the_long_query_note_cannot_be_set_to_nothing(live):
    with ui.element("div") as panel:
        settings._interrupting()
    (box,) = [e for e in panel.descendants() if isinstance(e, ui.number)]
    box.value = 2
    assert live.app.long_query_minutes == 2.0
    box.value = None
    assert live.app.long_query_minutes == 2.0
    box.value = 0
    assert live.app.long_query_minutes == 2.0


# --- the card ----------------------------------------------------------------------


def _drawn(rows, *, busy):
    with ui.element("div") as slot:
        transcript._rows(rows, busy=busy)
    return slot


def _having(slot, name: str) -> list:
    return [e for e in slot.descendants() if name in getattr(e, "classes", [])]


def test_a_running_data_call_carries_the_control_from_the_first_second(live):
    slot = _drawn(live.chat.rows, busy=True)
    (control,) = _having(slot, "interrupt-control")
    assert control._props["data-interrupt"] == "toolu_1"
    assert control.text == transcript._INTERRUPT_QUERY


def test_a_call_that_reads_no_data_has_no_control(live):
    rows = [_call("toolu_3", "describe_source", {"source": "orders"})]
    assert _having(_drawn(rows, busy=True), "interrupt-control") == []


def test_past_the_set_minutes_the_card_says_so_and_how_many_wait(live, monkeypatch):
    import time

    live.app.long_query_minutes = 0.0
    live.calls["toolu_1"] = {
        "phase": state.CALL_RUNNING,
        "interrupted": False,
        "started": time.monotonic() - 1,
        "waiting": 2,
    }
    slot = _drawn(live.chat.rows, busy=True)
    (note,) = _having(slot, "tool-note")
    assert note.text == "Running for over 0 min. 2 data calls wait behind it."


def test_a_cell_is_redrawn_only_when_what_it_says_moves(live):
    live.calls["toolu_1"] = {"phase": state.CALL_WAITING, "interrupted": False, "started": None}
    slot = _drawn(live.chat.rows, busy=True)
    (control,) = _having(slot, "interrupt-control")
    transcript._tick_live()
    assert _having(slot, "interrupt-control") == [control], "nothing moved, nothing redrawn"

    live.calls["toolu_1"] = {"phase": state.CALL_MEASURING, "interrupted": True, "started": 1.0}
    transcript._tick_live()
    (redrawn,) = _having(slot, "interrupt-control")
    assert redrawn is not control
    assert redrawn.text == transcript._SKIP_MEASURING


def _interrupted_rows(reason="Wrong query", landed=True):
    outcome = {
        "id": "toolu_1",
        "name": "query_data",
        "reason": reason,
        "note": None,
        "ran": 4.2,
        "landed": landed,
        "measure": "measured",
        "measured": {"inputs": {}},
    }
    return [
        _call(),
        events.Event(runlog.INTERRUPTED, outcome),
        events.Event(events.TOOL_RESULT, {"id": "toolu_1", "text": "...", "is_error": True}),
    ]


def test_an_interrupted_card_is_greyed_and_the_users_doing_never_an_error(live):
    rows = _interrupted_rows()
    answers = transcript._results_by_call(rows)
    assert transcript._call_states(rows, answers, busy=False) == {"toolu_1": transcript.INTERRUPTED}
    slot = _drawn(rows, busy=False)
    (card,) = _having(slot, "tool-card")
    assert "tool-card--interrupted" in card.classes
    assert _having(slot, "tool-state--error") == []
    texts = [
        e.text
        for e in _having(slot, "tool-state--interrupted")[0].descendants()
        if isinstance(e, ui.label)
    ]
    assert transcript._BY_YOU in texts and "Wrong query" in texts
    # The record is drawn on its card and is not a row of its own.
    assert len(_having(slot, "transcript-row")) == 0


def test_a_press_that_came_too_late_leaves_the_card_as_the_call_ended(live):
    rows = _interrupted_rows(landed=False)
    rows[-1] = events.Event(events.TOOL_RESULT, {"id": "toolu_1", "text": "{}"})
    assert transcript._call_states(rows, transcript._results_by_call(rows), busy=False) == {
        "toolu_1": transcript.DONE
    }


# --- the record --------------------------------------------------------------------


def test_the_log_says_what_the_press_came_to_just_before_the_result(live, monkeypatch, tmp_path):
    outcome = _interrupted_rows()[1].data
    monkeypatch.setattr(
        engine, "take_interruption", lambda card: outcome if card == "toolu_1" else None
    )
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: False)
    live.chat.log = runlog.start(tmp_path, kind=runlog.CHAT)
    live.app.open = None  # nothing on screen to redraw
    exchange._record(_call(), live.chat)
    exchange._record(
        events.Event(events.TOOL_RESULT, {"id": "toolu_1", "text": "...", "is_error": True}),
        live.chat,
    )

    logged = runlog.read(live.chat.log.path)
    kinds = [e.kind for e in logged.events]
    assert kinds[-3:] == [events.TOOL_CALL, runlog.INTERRUPTED, events.TOOL_RESULT]
    record = logged.events[-2].data
    assert (record["id"], record["reason"], record["ran"]) == ("toolu_1", "Wrong query", 4.2)
    assert record["measured"] == {"inputs": {}}
    assert [r.kind for r in live.chat.rows[-2:]] == [runlog.INTERRUPTED, events.TOOL_RESULT]

    summary = runlog.summary(logged)
    assert summary["interrupted"] == 1
    assert summary["interrupt_reasons"] == {"Wrong query": 1}

    # A replay of the log draws the card the way the live pane did.
    with ui.element("div") as slot:
        transcript.replay(logged)
    (card,) = _having(slot, "tool-card")
    assert "tool-card--interrupted" in card.classes


def test_an_interruption_whose_result_never_came_is_still_on_the_record(live, tmp_path):
    """The exchange was stopped while portia measured: the press is still a fact."""
    live.chat.log = runlog.start(tmp_path, kind=runlog.CHAT)
    exchange._keep_interruption(live.chat, _interrupted_rows(reason=None)[1].data)
    logged = runlog.read(live.chat.log.path)
    assert runlog.summary(logged)["interrupt_reasons"] == {runlog.NO_REASON: 1}


def test_the_window_and_the_engine_spell_a_calls_phase_alike():
    from portia.agent import tools

    assert (state.CALL_WAITING, state.CALL_RUNNING, state.CALL_MEASURING) == (
        tools.WAITING,
        tools.RUNNING,
        tools.MEASURING,
    )
    assert exchange.TOO_LATE == tools.TOO_LATE
    assert engine.reads_data("query_data") and not engine.reads_data("describe_source")
