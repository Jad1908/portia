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


# --- replaying a logged turn -------------------------------------------------


def _events(*pairs):
    from portia.agent import events

    return [events.Event(kind, data) for kind, data in pairs]


def test_a_write_is_paired_with_the_answer_that_resolved_it():
    """The log records a write request and its allow/deny as two events. Drawn
    as two rows they read as two separate things happening."""
    from portia.agent import events
    from portia.ui import transcript

    rows = _events(
        (events.APPROVAL, {"name": "record_step", "input": {}}),
        (events.APPROVAL_RESULT, {"name": "record_step", "allowed": True}),
        (events.APPROVAL, {"name": "record_step", "input": {}}),
        (events.APPROVAL_RESULT, {"name": "record_step", "allowed": False}),
    )
    assert transcript._outcome_after(rows, 0) is True
    assert transcript._outcome_after(rows, 2) is False


def test_a_write_the_turn_died_on_is_unanswered_not_refused():
    """A turn killed at the confirmation prompt leaves a request with no
    outcome, and that is a different fact from a denial."""
    from portia.agent import events
    from portia.ui import transcript

    rows = _events((events.APPROVAL, {"name": "record_step", "input": {}}))
    assert transcript._outcome_after(rows, 0) is None


def test_a_writes_outcome_is_never_read_off_the_next_write():
    """Two requests in a row: the first was never resolved, and the second's
    answer does not belong to it."""
    from portia.agent import events
    from portia.ui import transcript

    rows = _events(
        (events.APPROVAL, {"name": "a", "input": {}}),
        (events.APPROVAL, {"name": "b", "input": {}}),
        (events.APPROVAL_RESULT, {"name": "b", "allowed": True}),
    )
    assert transcript._outcome_after(rows, 0) is None
    assert transcript._outcome_after(rows, 1) is True


def test_turns_and_saved_runs_are_two_different_lists(tmp_path):
    """Same word, two artifacts: a *run* executed a spec, a *turn* was the
    copilot deciding what the spec should say. The pane must not merge them."""
    from portia import runlog
    from portia.ui import engine

    app = App(root=tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "2026-07-29T09-00-00.md").write_text("# a spec run")
    runlog.start(app.catalog_dir, prompt="a goal", model="m")

    assert [p.suffix for p in engine.runs_in(app)] == [".md"]
    assert [p.suffix for p in engine.turns_in(app)] == [".jsonl"]


def test_a_turns_counts_come_from_the_engine_not_the_panel(tmp_path):
    """`DESIGN.md`: nothing in `ui/` computes. The window and `cli.runs` have to
    quote the same number for how often the copilot asked."""
    from portia import runlog
    from portia.agent import events
    from portia.ui import engine

    app = App(root=tmp_path)
    log = runlog.start(app.catalog_dir, prompt="a goal", model="m")
    log.event(events.question_event([{"question": "which grain?"}]))

    run = runlog.read(engine.turn_path(app, log.path.name))
    assert engine.turn_summary(run) == runlog.summary(run)
    assert engine.turn_summary(run)["questions"] == 1


def test_an_answered_question_is_one_row_not_two():
    """The live panel replaces a resolved question with its answer. A replay
    that drew the form *and* the answer listed the same question twice, which
    reads as the copilot having asked it twice."""
    from portia.agent import events
    from portia.ui import transcript

    rows = _events(
        (events.QUESTION, {"questions": [{"question": "which grain?"}]}),
        (events.ANSWER, {"answers": {"which grain?": "city-date"}}),
    )
    assert transcript._answered_after(rows, 0) is True


def test_a_question_the_turn_died_on_keeps_its_options_on_screen():
    """An unanswered question is the shape of an interrupted run."""
    from portia.agent import events
    from portia.ui import transcript

    rows = _events((events.QUESTION, {"questions": [{"question": "which grain?"}]}))
    assert transcript._answered_after(rows, 0) is False


def test_an_answer_is_never_read_off_the_next_question():
    from portia.agent import events
    from portia.ui import transcript

    rows = _events(
        (events.QUESTION, {"questions": [{"question": "a"}]}),
        (events.QUESTION, {"questions": [{"question": "b"}]}),
        (events.ANSWER, {"answers": {"b": "yes"}}),
    )
    assert transcript._answered_after(rows, 0) is False
    assert transcript._answered_after(rows, 1) is True


# --- width behaviour --------------------------------------------------------


def test_the_three_bands_are_the_ones_the_design_specifies():
    assert state.band_for(1600) == state.WIDE_BAND
    assert state.band_for(state.WIDE) == state.WIDE_BAND
    assert state.band_for(1200) == state.MEDIUM_BAND
    assert state.band_for(state.MEDIUM) == state.MEDIUM_BAND
    assert state.band_for(900) == state.NARROW_BAND


def test_crossing_a_band_sets_that_bands_defaults():
    app = state.App(width=1600, band=state.WIDE_BAND)

    assert app.resize(1200) is True
    assert (app.show_files, app.show_transcript) == (True, False)

    assert app.resize(900) is True
    assert (app.show_files, app.show_transcript) == (False, False)

    assert app.resize(1600) is True
    assert (app.show_files, app.show_transcript) == (True, True)


def test_resizing_inside_a_band_leaves_the_panes_alone():
    """A layout that keeps reopening a pane you just closed is worse than one
    that never adapts."""
    app = state.App(width=1600, band=state.WIDE_BAND)
    app.show_transcript = False

    assert app.resize(1500) is False
    assert app.show_transcript is False
    assert app.width == 1500


def test_whatever_is_showing_fits_the_window_it_is_showing_in():
    """The workflow pane never gives way (DESIGN.md → Width behaviour), so every
    other pane's ceiling is computed against its floor — and the band defaults
    close a pane outright when three of them could not fit at any size.

    Measured before this: at 820px the workflow splitter panel was 158px wide
    holding a pane with a 320px `min-width`, so most of the middle pane was
    clipped away behind the transcript.
    """
    from portia.ui import app as app_module
    from portia.ui.state import APP

    for width in (1600, 1400, 1200, 1024, 900, 820, 700):
        APP.resize(width)
        showing = app_module.WORKFLOW_MIN
        if APP.show_files:
            showing += app_module._files_limits()[1]
        if APP.show_transcript:
            showing += app_module._transcript_limits()[1]
        assert showing <= width, f"panes do not fit at {width}px"


def test_a_narrow_window_keeps_the_workflow_pane_and_run():
    """Whatever gives way, it is never the middle pane or the action on it."""
    from portia.ui.state import APP

    APP.resize(700)

    assert (APP.show_files, APP.show_transcript) == (False, False)


def test_a_window_with_room_to_spare_gets_the_designed_pane_sizes():
    """The ceilings only tighten where the window cannot honour them."""
    from portia.ui import app as app_module
    from portia.ui.state import APP

    APP.width, APP.show_files, APP.show_transcript = 1920, True, True

    assert app_module._files_limits() == app_module.FILES_LIMITS
    assert app_module._transcript_limits() == app_module.TRANSCRIPT_LIMITS


def test_focusing_a_card_is_a_request_with_a_token_not_a_flag():
    """The workflow pane renders more than once per click. Clearing a flag as a
    render consumed it meant the first render ate the request and the render that
    reached the screen had nothing to mark — so the client dedupes on a token and
    a repeated render is simply harmless."""
    app = state.App()

    app.focus("stg_orders")
    first = app.focus_token
    assert app.focus_model == "stg_orders"

    # rendering does not consume it
    assert app.focus_model == "stg_orders"
    assert app.focus_token == first

    app.focus("stg_orders")
    assert app.focus_token > first, "asking again is a new request, even for the same card"


# --- importing outside data (docs/PIPELINE.md §2.7) -------------------------


@pytest.fixture
def project(tmp_path):
    from portia.ui.state import App

    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "orders.csv").write_text("a,b\n1,2\n")
    return App(root=root), outside


def test_a_planned_import_says_exactly_what_lands_where(project):
    """The confirmation shows the real thing rather than a description of one."""
    from portia.ui import engine

    app, outside = project

    pairs = engine.plan_import(str(outside / "orders.csv"), "data", app)

    assert pairs == [(outside / "orders.csv", app.root / "data" / "orders.csv")]
    assert (outside / "orders.csv").exists(), "planning copies nothing"
    assert not (app.root / "data" / "orders.csv").exists()


def test_the_destination_is_the_one_the_operator_chose(project):
    from portia.ui import engine

    app, outside = project

    pairs = engine.plan_import(str(outside / "orders.csv"), "data/raw", app)

    assert pairs[0][1] == app.root / "data" / "raw" / "orders.csv"


def test_an_empty_destination_falls_back_to_the_data_directory(project):
    from portia.ui import engine

    app, _ = project

    assert engine.destination_in("", app) == app.root / engine.DATA_DIR
    assert engine.destination_in("   ", app) == app.root / engine.DATA_DIR


def test_a_destination_outside_the_project_is_refused(project):
    """Data lives in the repo (`PIPELINE.md` §2.7) — the destination field is not
    the place to make an exception to that."""
    from portia.ui import engine

    app, outside = project

    with pytest.raises(ValueError, match="must be inside the project"):
        engine.plan_import(str(outside / "orders.csv"), str(outside), app)


def test_a_name_already_taken_is_refused_before_anything_moves(project):
    from portia.ui import engine

    app, outside = project
    (app.root / "data" / "orders.csv").write_text("already here")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        engine.plan_import(str(outside / "orders.csv"), "data", app)

    assert (app.root / "data" / "orders.csv").read_text() == "already here"


def test_importing_copies_and_never_moves(project):
    """A tool that relocates someone's data is not a data-harmonization concern."""
    import asyncio

    from portia.ui import engine

    app, outside = project
    pairs = engine.plan_import(str(outside / "orders.csv"), "data/raw", app)

    copied = asyncio.run(engine.import_files(pairs, app))

    assert copied == [app.root / "data" / "raw" / "orders.csv"]
    assert copied[0].read_text() == "a,b\n1,2\n"
    assert (outside / "orders.csv").exists(), "the original is left where it was"


def test_the_window_and_the_terminal_plan_the_same_import(project):
    """Not two surfaces written to agree — one function, called by both."""
    from portia.cli.import_data import plan
    from portia.ui import engine

    app, outside = project
    sources = [outside / "orders.csv"]

    assert engine.plan_import(str(outside / "orders.csv"), "data", app) == plan(
        sources, app.root / "data", app.root
    )
