"""The app's non-visual rules — the ones a screenshot would never catch.

Rendering is checked by looking at it; these are the invariants underneath. Two
of them are the product's own rules applied to pixels (docs/DESIGN.md): colour
communicates *kind* and never *rank*, and the UI never computes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from portia import catalog
from portia.checks.outcome import BLOCKING_FLAGS
from portia.ui import state
from portia.ui.state import App, Decision

pytest.importorskip("nicegui", reason="the app needs the `ui` extra")

from portia.ui import components as c  # noqa: E402  (after the extra is confirmed)

# --- flags: named by the engine, coloured by kind ----------------------------


def test_every_blocking_flag_has_a_plain_language_line():
    """An acknowledged override that shows a bare flag name explains nothing."""
    for flag in BLOCKING_FLAGS:
        assert c.flag_meaning(flag) is not c._UNKNOWN_FLAG, flag


def test_an_unknown_flag_still_says_something_true():
    assert c.flag_meaning("invented_flag") == c._UNKNOWN_FLAG


def test_a_blocking_flag_is_coloured_as_blocking():
    flag = sorted(BLOCKING_FLAGS)[0]
    assert c.flag_variant(flag, acknowledged=[]) == c.BLOCKING


def test_an_acknowledged_flag_is_coloured_as_an_override_not_an_alarm():
    flag = sorted(BLOCKING_FLAGS)[0]
    assert c.flag_variant(flag, acknowledged=[flag]) == c.ACKNOWLEDGED


def test_a_non_blocking_flag_is_not_coloured_at_all():
    """Visible, uncoloured, not ranked — a rate is reported and never blocks."""
    assert c.flag_variant("high_null", acknowledged=[]) == ""


def test_there_are_exactly_three_badge_variants():
    variants = {c.BLOCKING, c.DRIFT, c.ACKNOWLEDGED}
    assert len(variants) == 3


# --- app state ---------------------------------------------------------------


def test_a_fresh_app_has_no_project_open():
    """`root` always has a value, so it can't be what says a project is open."""
    assert App().opened is False


def test_the_accent_follows_the_state_the_project_is_in():
    """Go carries it until there are steps; then Run does. Never both."""
    app = App()
    assert app.spec_has_steps is False
    app.spec = {"steps": [{"id": "joined", "op": "join"}]}
    assert app.spec_has_steps is True


def test_a_turn_in_flight_makes_the_app_busy():
    app = App()
    assert app.busy is False
    stream = app.start_turn("merge these", model="claude-haiku-4-5", effort="low")
    assert app.busy is True
    stream.turn.running = False
    assert app.busy is False and stream.turn.ended is True


def test_starting_a_turn_clears_the_last_one():
    """A turn is one shot; a new one starts fresh, per the engine it drives."""
    app = App()
    app.stream().rows = ["something from before"]
    app.start_turn("again", model="m", effort=None)
    assert app.rows == []


def test_indexing_and_chat_keep_separate_transcripts():
    """Two jobs with different rhythms; interleaving them made both hard to read."""
    app = App()
    app.stream(state.CHAT).rows = ["a goal I typed"]
    app.start_turn("index these", model="m", effort=None, kind=state.INDEXING)
    assert app.tab == state.INDEX, "the pane follows the turn that just started"
    assert app.stream(state.CHAT).rows == ["a goal I typed"], "the chat is untouched"
    assert app.stream(state.INDEX).rows == []


def test_a_turn_anywhere_makes_the_app_busy():
    """The engine is single-turn: a chat turn must block an indexing one."""
    app = App()
    app.start_turn("index these", model="m", effort=None, kind=state.INDEXING)
    assert app.busy is True


@pytest.fixture
def loop():
    import asyncio

    made = asyncio.new_event_loop()
    yield made
    made.close()


def test_the_pending_decision_is_the_unresolved_one(loop):
    """The loop is blocked on the most recent unanswered one."""
    app = App()
    stream = app.stream()
    first = Decision("approval", {}, loop.create_future())
    second = Decision("approval", {}, loop.create_future())
    stream.rows = [first, second]
    assert stream.pending is second
    second.resolve(True)
    assert stream.pending is first
    first.resolve(True)
    assert stream.pending is None


def test_resolving_a_decision_hands_the_answer_to_the_waiting_callback(loop):
    """`answer`/`confirm` are blocked on this future; the form is what completes it."""
    decision = Decision("question", {}, loop.create_future())
    decision.resolve({"which key?": "customer_id"})
    assert decision.resolved is True
    assert decision.future.result() == {"which key?": "customer_id"}


# --- selection ---------------------------------------------------------------


def test_selecting_nothing_returns_to_the_workflow():
    app = App()
    app.select("source", "orders")
    assert app.is_selected("source", "orders")
    app.select(None)
    assert app.selection is None


# --- choosing a folder -------------------------------------------------------


def test_a_cancelled_folder_chooser_is_an_answer_of_no_not_an_error(monkeypatch):
    """Cancel exits non-zero with "User canceled." — nothing worth surfacing."""
    from types import SimpleNamespace

    from portia.ui import engine

    monkeypatch.setattr(
        engine.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert engine._choose_folder() is None


def test_a_chosen_folder_comes_back_as_a_path(monkeypatch):
    from types import SimpleNamespace

    from portia.ui import engine

    monkeypatch.setattr(
        engine.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="/Users/x/project/\n"),
    )
    assert engine._choose_folder() == Path("/Users/x/project")


def test_no_chooser_means_the_path_field_is_the_way_in(monkeypatch):
    from portia.ui import engine

    monkeypatch.setattr(engine.sys, "platform", "linux")
    assert engine.can_browse() is False


# --- adding data: say what is happening, and where to go next ----------------


def test_indexing_reports_each_file_as_it_goes(tmp_path, monkeypatch):
    """A window that says nothing for a minute reads as broken.

    Twenty real extracts take that long, so `index` reports per file rather than
    handing the whole list to one thread and returning when it is done.
    """
    import asyncio

    from portia.ui import engine
    from portia.ui.state import App

    for i in range(3):
        pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(tmp_path / f"s{i}.csv", index=False)

    # `open_project` chdirs into the project; the catalog resolves source paths
    # relative to the working directory, so a test that skips that lies.
    monkeypatch.chdir(tmp_path)
    app = App()
    app.root = tmp_path
    app.portia_dir = ".portia"
    catalog.init_project("test", portia_dir=app.portia_dir)

    seen: list[tuple[int, int, str]] = []
    names = asyncio.run(
        engine.index(sorted(tmp_path.glob("*.csv")), app, on_progress=lambda *a: seen.append(a))
    )

    assert names == ["s0", "s1", "s2"]
    assert seen == [(0, 3, "s0"), (1, 3, "s1"), (2, 3, "s2")]
    assert len(app.sources) == 3  # and the catalog really was refreshed


def test_the_add_data_copy_is_read_off_the_loader(monkeypatch):
    """This screen said "CSV" in four places and stopped being true the day
    Parquet landed. A label that can go stale is a label that will."""
    from portia.ui import screens

    monkeypatch.setattr(screens, "_suffixes", lambda: (".csv",))
    assert screens._formats() == "CSV"

    monkeypatch.setattr(screens, "_suffixes", lambda: (".csv", ".parquet"))
    assert screens._formats() == "CSV or PARQUET"

    monkeypatch.setattr(screens, "_suffixes", lambda: (".csv", ".json", ".parquet"))
    assert screens._formats() == "CSV, JSON or PARQUET"


def test_the_continue_button_says_whether_it_spends_money():
    """The turn is deferred to this button, so the button has to admit it."""
    from portia.ui import screens
    from portia.ui.state import APP

    APP.interpret = True
    assert "read" in screens._continue_label()
    APP.interpret = False
    assert screens._continue_label() == "Continue to the project"
