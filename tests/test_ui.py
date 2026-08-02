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

from nicegui import ui  # noqa: E402  (after the extra is confirmed)

from portia.ui import components as c  # noqa: E402
from portia.ui import engine as engine_module  # noqa: E402

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


# --- keeping your place across a rebuild -------------------------------------


def test_the_middle_pane_draws_in_one_pass():
    """An `await` mid-render costs a painted frame.

    A refresh deletes the pane's elements and only then runs the function. With
    an await in between, the delete and the rebuild leave in two batches and the
    browser paints the gap — a blank middle pane, intermittently, on every click.
    Reads for this pane are therefore synchronous (`ui.engine.read_text`).
    """
    import inspect

    from portia.ui import workflow

    assert not inspect.iscoroutinefunction(workflow.pane.func), "see the docstring on pane()"
    for name in ("read_text", "read_turn", "read_table"):
        reader = getattr(engine_module, name)
        assert not inspect.iscoroutinefunction(reader), f"{name} is drawn, not awaited"


def test_a_scroll_region_states_its_key_in_the_dom():
    """Where a pane is scrolled to is client state, like the canvas's pan and zoom.

    The server never learns it; it states a key and `assets/scroll.js` puts the
    position back on whatever element carries that key after a rebuild. Driving
    it from a render instead would race the DOM patch, which is the same mistake
    the focus mark was fixed for.
    """
    with ui.element("div"):
        area = c.scroll_area("artifacts", classes="p-pad")

    assert area._props["data-scroll-key"] == "artifacts"
    assert "p-scroll" in area.classes and "p-pad" in area.classes


def test_two_artifacts_in_one_pane_do_not_share_a_scroll_key():
    """Otherwise opening the second saved run drops you at the first one's offset."""
    from portia.ui import workflow

    state.APP.select(state.RUN, "a.md")
    with ui.element("div"):
        first = workflow._inspector_scroll()
    state.APP.select(state.RUN, "b.md")
    with ui.element("div"):
        second = workflow._inspector_scroll()
    state.APP.select(None)

    assert first._props["data-scroll-key"] != second._props["data-scroll-key"]


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


# --- settings: one place, and no second setting ------------------------------


def test_the_theme_offers_all_three_modes_by_name():
    """The cycling toolbar button showed the mode it was *in*, which cannot
    distinguish "dark" from "auto, and it is night". Settings names all three."""
    from portia.ui import theme

    assert set(theme.MODES) == set(theme.MODE_LABEL)
    assert [theme.MODE_LABEL[m] for m in theme.MODES] == ["auto", "light", "dark"]
    for mode in theme.MODES:
        assert theme.MODE_VALUE[theme.MODE_LABEL[mode]] is mode


def test_the_model_and_effort_are_one_setting_in_every_place_they_are_picked():
    """Three hand-rolled copies of the pair is how they stop agreeing — an option
    added to one list and not the others, or a select writing a field the turn
    never reads. One component, bound to the two fields a turn is started with."""
    import inspect

    from portia.ui import screens, settings, transcript

    for module in (settings, transcript, screens):
        source = inspect.getsource(module)
        assert "c.model_effort(APP" in source, f"{module.__name__} rolls its own"
        assert "MODELS" not in source, f"{module.__name__} still builds a model list"


def test_every_setting_binds_a_field_the_rest_of_the_app_actually_reads():
    """A second place to change a setting, never a second setting.

    A typo'd binding is a control that looks live, writes an attribute nothing
    reads, and silently does nothing — which is the whole failure mode a
    settings panel invites.
    """
    import dataclasses
    import inspect
    import re

    from portia.ui import settings

    bound = re.findall(r'\.bind_value\(\s*APP,\s*"([a-z_]+)"', inspect.getsource(settings))
    fields = {f.name for f in dataclasses.fields(state.App)}

    assert bound, "the panel binds nothing at all"
    assert set(bound) <= fields, f"settings writes what nothing reads: {set(bound) - fields}"


def test_switching_projects_refuses_while_a_turn_is_running(monkeypatch):
    """A switch mid-turn leaves the copilot writing into a directory the window
    has stopped looking at."""
    from portia.ui import settings
    from portia.ui.state import APP

    APP.streams[state.CHAT].turn = state.Turn(prompt="g", model="m", effort="low")
    said = []
    monkeypatch.setattr(settings.ui, "notify", said.append)
    monkeypatch.setattr(settings, "_close", lambda: None)
    APP.opened = True

    settings._switch_project()

    assert APP.opened is True, "still in the project"
    assert said == [settings.SWITCH_BUSY]
    APP.streams[state.CHAT].turn = None


def test_the_toolbar_no_longer_carries_a_preference():
    """Theme, the brief and the project switch were three buttons across the top
    of every screen. A toolbar says where you are and acts on what is in front of
    you; none of those three is either."""
    import inspect

    from portia.ui import app as app_module

    source = inspect.getsource(app_module)
    for gone in ('c.button("Brief"', "_cycle_theme", "MODE_LABEL", "_switch_project"):
        assert gone not in source, f"{gone} is back in the toolbar"


# --- icon buttons owe you a sentence -----------------------------------------


def test_an_icon_with_no_label_is_styled_as_an_icon_button():
    """A text button's 6px/14px padding is shaped around a word; around a 16px
    glyph it reads as a button that lost something."""
    with ui.element("div"):
        icon_only = c.button("", icon="play_arrow")
        labelled = c.button("Run", icon="play_arrow")
        no_icon = c.button("Run")

    assert "btn-icon" in icon_only.classes
    assert "btn-icon" not in labelled.classes
    assert "btn-icon" not in no_icon.classes


def test_a_run_actions_hover_is_the_name_of_the_action_and_nothing_else():
    """An icon has to name its verb; it does not have to explain it. A hover is
    read in the moment before a click, and the sentence that used to be here —
    what the action does, plus the path it writes to — was three lines of prose
    in a floating box. The sentences live in the docstring and in `DESIGN.md`."""
    from portia.ui import app as app_module

    assert app_module.ACTION_TIPS == (
        "Run spec",
        "Build full pipeline",
        "Write outputs",
        "Save report",
    )
    for tip in (*app_module.ACTION_TIPS, app_module._SETTINGS_TIP):
        assert "\n" not in tip and len(tip) <= 24, f"{tip!r} is explaining, not naming"


def test_the_run_actions_are_drawn_on_the_pane_they_act_on():
    """From the far corner of the toolbar they floated above the transcript — the
    one pane they have nothing to do with. Chrome above the panes also cannot
    align to the middle pane's edge: a dragged pane's width never reaches the
    server (`_room_beside_files`), so the actions have to be drawn inside it."""
    import inspect

    from portia.ui import app as app_module

    assert "run_controls()" in inspect.getsource(app_module._middle)
    assert "run_controls" not in inspect.getsource(app_module.toolbar.func)


# --- closing a pane is a drag, and the rail is how it comes back -------------


def test_dragging_a_pane_past_its_floor_closes_it():
    """`DESIGN.md`: below its minimum a pane stops being worth having, and the
    honest move is to close it rather than to squeeze it. The splitter used to
    simply refuse to go further, which left the toolbar toggle as the only way."""
    from portia.ui import app as app_module

    closed: list[str] = []
    floor = app_module.FILES_LIMITS[0]

    app_module._past_the_floor(floor, floor, lambda: closed.append("files"))
    assert closed == [], "at the floor it is still readable"

    app_module._past_the_floor(floor - 1, floor, lambda: closed.append("files"))
    assert closed == ["files"]


def test_a_splitter_can_be_dragged_below_its_floor_at_all():
    """The floor is a threshold now, not a wall — Quasar's own limit has to let
    the drag reach it or the close can never fire."""
    from portia.ui import app as app_module
    from portia.ui.state import APP

    APP.width, APP.show_files, APP.show_transcript = 1920, True, True
    with ui.element("div"):
        split = app_module._splitter(260, app_module.FILES_LIMITS, on_collapse=lambda: None)

    assert split._props["limits"][0] == 0


def test_a_drag_that_keeps_reporting_below_the_floor_redraws_once():
    """A splitter reports its width continuously while it is held. Refreshing the
    shell per frame rebuilds all three panes under a mouse that is still down."""
    from portia.ui import app as app_module
    from portia.ui.state import APP

    APP.show_files = True
    redrawn: list[int] = []
    original = app_module.shell.refresh
    app_module.shell.refresh = lambda *a, **k: redrawn.append(1)  # type: ignore[method-assign]
    try:
        for _ in range(5):
            app_module._close_files()
    finally:
        app_module.shell.refresh = original  # type: ignore[method-assign]

    assert APP.show_files is False
    assert redrawn == [1], "one redraw for one state change"
    APP.show_files = True


def test_the_toolbar_no_longer_toggles_a_pane():
    """Two controls at the top of the window for something you do at the side."""
    import inspect

    from portia.ui import app as app_module

    source = inspect.getsource(app_module.toolbar.func)
    assert 'c.button("Files"' not in source
    assert 'c.button("Transcript"' not in source


def test_the_pane_beside_a_rail_states_both_its_dimensions():
    """A splitter panel does not stretch its children, so a flex row inside one
    has to say its own width *and* height. Measured at 1280px before this: the
    workflow pane came out 404px wide inside a 1019px panel, with the transcript
    rail floating in the middle of the window.

    `.p-body` cannot be reused for it — that one is the window's own row and
    takes its height from `.p-window`'s flex column, where an explicit
    `height: 100%` resolves against the viewport and swallows the toolbar.
    """
    import inspect
    import re

    from portia.ui import app as app_module
    from portia.ui import theme

    assert 'classes("p-pane-row")' in inspect.getsource(app_module._workflow_and_transcript)

    css = theme.CSS.read_text()
    block = re.search(r"\.p-pane-row \{([^}]*)\}", css)
    assert block, ".p-pane-row is not styled"
    assert "width: 100%" in block.group(1) and "height: 100%" in block.group(1)


def test_every_settings_tab_has_something_to_draw():
    """A tab with no body renders an empty panel, and the failure is silent."""
    from portia.ui import settings

    assert tuple(settings._BODY) == settings.TABS


def test_picking_a_setting_does_not_throw_you_back_to_the_first_tab(monkeypatch):
    """Picking a theme or an effort refreshes the whole panel. If the showing tab
    were rebuilt with it, every pick would bounce you back to Project."""
    from portia.ui import settings

    monkeypatch.setattr(settings._panel, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(settings.theme, "set_mode", lambda *a, **k: None)
    monkeypatch.setattr(settings, "_TAB", "Appearance")

    settings._set_theme("light")
    assert settings._TAB == "Appearance"

    settings._set_effort("high")
    assert settings._TAB == "Appearance"


def test_the_settings_tabs_reuse_the_transcripts_tab_vocabulary():
    """One tab style in the app, not two that have to be kept looking alike."""
    import inspect

    from portia.ui import settings, transcript

    for module in (settings, transcript):
        source = inspect.getsource(module)
        assert 'classes("pane-tabs")' in source
        assert "pane-tab--active" in source


# --- chrome that got out of the way ------------------------------------------


def test_a_pane_holds_on_below_the_width_it_used_to_close_at():
    """The floor doubles as the close threshold, so a generous floor reads as a
    pane that gives up under a drag that meant "make this narrower". Both are
    still real floors — measured in a browser at 180px (left) and 290px (right),
    where the old 200/330 would have closed them."""
    from portia.ui import app as app_module

    assert app_module.FILES_LIMITS[0] < 200
    assert app_module.TRANSCRIPT_LIMITS[0] < 330
    # ...but a floor of nothing is not a floor: below these the pane cannot show
    # a file name at the tree's indent, or the question form's option rows.
    assert app_module.FILES_LIMITS[0] >= 120
    assert app_module.TRANSCRIPT_LIMITS[0] >= 240


def test_the_css_backstop_agrees_with_the_floor_the_splitter_enforces():
    """Two numbers for one rule: the pane's `min-width` holds it up if a drag
    ever gets past the splitter, so a mismatch renders a pane wider than the
    panel reserved for it — which is how the left pane once ended up drawn
    underneath the transcript."""
    import re

    from portia.ui import app as app_module
    from portia.ui import theme

    css = theme.CSS.read_text()

    def floor(selector: str) -> int:
        # Every block naming this selector, not the first — the three panes share
        # a block that sets no width, and it comes first in the file.
        for block in re.findall(rf"^{re.escape(selector)} \{{([^}}]*)\}}", css, re.MULTILINE):
            found = re.search(r"min-width: (\d+)px", block)
            if found:
                return int(found.group(1))
        raise AssertionError(f"{selector} declares no min-width")

    assert floor(".p-pane-left") == app_module.FILES_LIMITS[0]
    assert floor(".p-pane-right") == app_module.TRANSCRIPT_LIMITS[0]
    assert floor(".p-pane-mid") == app_module.WORKFLOW_MIN


def test_run_and_build_carry_their_word_and_the_two_saves_do_not():
    """The pair that executes something is the pair worth naming on screen. Four
    labelled buttons is the row that made this a toolbar problem in the first
    place."""
    import inspect
    import re

    from portia.ui import app as app_module

    # Collapsed, because the formatter wraps a long call across lines and this is
    # a statement about the arguments, not about where they sit.
    source = re.sub(r"\s+", " ", inspect.getsource(app_module.run_controls.func))

    assert 'c.button( "Run", _run' in source or 'c.button("Run", _run' in source
    assert 'c.button("Build", _build' in source
    assert source.count("split=True") == 2, "exactly the two that execute something"
    assert 'c.button("", _write' in source and 'c.button("", _save_report' in source


def test_a_split_button_is_the_only_one_that_gets_a_rule_through_it():
    """Most icon-plus-label buttons are ordinary buttons that happen to have an
    icon; a rule through all of them would be decoration."""
    with ui.element("div"):
        split = c.button("Run", icon="play_arrow", split=True)
        plain = c.button("Add data", icon="add")
        icon_only = c.button("", icon="add", split=True)

    assert "btn-split" in split.classes
    assert "btn-split" not in plain.classes
    assert "btn-split" not in icon_only.classes, "an icon with no label has nothing to rule off"


def test_the_tree_never_pops_a_box_repeating_the_row_you_are_pointing_at():
    """Instant tooltips fired all the way down the pane as you scanned it, each
    saying what the row already said."""
    import inspect

    from portia.ui import artifacts

    source = inspect.getsource(artifacts)
    assert ".tooltip(" not in source, "the tree is back to tooltipping its own rows"
    assert "c.hint(row, APP.project_context)" in source, "the brief still says what a row cannot"
