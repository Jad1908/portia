"""The app's non-visual rules — the ones a screenshot would never catch.

Rendering is checked by looking at it; these are the invariants underneath. Two
of them are the product's own rules applied to pixels (docs/DESIGN.md): colour
communicates *kind* and never *rank*, and the UI never computes.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

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
from portia.ui import graph  # noqa: E402

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


# --- prose: the copilot's markdown, as it writes it ---------------------------


def _prose(text: str) -> str:
    """What `c.markdown` puts on the page, through the function `ui.markdown` calls."""
    from nicegui.elements.markdown import prepare_content

    return prepare_content(text, extras=" ".join(c.MARKDOWN_EXTRAS))


def test_a_list_right_under_a_sentence_is_a_list():
    html = _prose("**Pipeline, this layer:**\n- `stg_a` (staging)\n- `stg_b` (staging)\n\nAfter.")
    assert "<p><strong>Pipeline, this layer:</strong></p>" in html
    assert "<li><code>stg_a</code> (staging)</li>" in html
    assert "<p>After.</p>" in html


def test_a_numbered_list_right_under_a_sentence_is_a_numbered_list():
    html = _prose("Two options:\n1. keep it\n2. drop it")
    assert "<p>Two options:</p>" in html
    assert "<ol>\n<li>keep it</li>\n<li>drop it</li>\n</ol>" in html


def test_a_cuddled_list_leaves_code_tables_and_identifiers_alone():
    html = _prose(
        "Joined on customer_id and name_x:\n"
        "- first_name\n\n"
        "```\n- not a list\n1. nor this\n```\n\n"
        "Counts:\n| a | b |\n|---|---|\n| 1 | - |"
    )
    assert "<li>first_name</li>" in html
    assert "customer_id and name_x" in html  # code-friendly: no emphasis from `_`
    assert "- not a list\n1. nor this" in html  # a fence is still verbatim
    assert "<td>-</td>" in html


# --- app state ---------------------------------------------------------------


def test_a_fresh_app_has_no_project_open():
    """`root` always has a value, so it can't be what says a project is open."""
    assert App().opened is False


def test_the_accent_asks_the_project_what_there_is_to_run():
    """Go carries it until the project has a spec; then Run does. Never both.

    The question is about the **project**, not the open spec: Run executes what
    the canvas is showing, so it is live even when the pane happens to be on a
    table nobody has recorded a step on yet. `App.spec_has_steps` asked the older,
    narrower question and went with the Build button (2026-08-15).
    """
    import inspect

    from portia.ui import app as app_module

    body = inspect.getsource(app_module.run_controls.func).split('"""')[-1]
    assert "engine.specs_in(APP)" in body
    assert "APP.spec_has_steps" not in body
    assert not hasattr(App(), "spec_has_steps"), "the narrower question is gone, not shadowed"


def test_a_turn_in_flight_makes_the_app_busy():
    app = App()
    assert app.busy is False
    stream = app.start_exchange("merge these", model="claude-haiku-4-5", effort="low")
    assert app.busy is True
    stream.exchange.running = False
    assert app.busy is False and stream.exchange.ended is True


def test_a_goal_with_nothing_open_starts_a_chat_and_opens_it():
    """Sent from the list's composer, the reply belongs in front of you
    (`docs/CHAT_SESSIONS.md` §3.2)."""
    app = App()
    assert app.open is None
    chat = app.start_exchange("again", model="m", effort=None)
    assert app.open is chat
    assert chat.rows == [] and chat.started
    assert app.chats == [chat]


def test_a_job_gets_its_own_chat_and_never_takes_the_screen():
    """§3.5: nothing switches you. A job is a row with the running dot, and
    the chat you were in stays in front of you."""
    app = App()
    mine = app.start_exchange("a goal I typed", model="m", effort=None)
    mine.exchange.running = False
    job = app.start_exchange("index these", model="m", effort=None, kind=state.INDEXING)
    assert job is not mine and job.is_job
    assert app.open is mine, "the job did not take the screen"
    assert app.live is job and app.live_job is job
    assert mine.rows == [] and job.rows == []


def test_a_job_started_from_the_sources_view_opens_in_it():
    """The sources view draws a running job's banner and none of its rows, so a
    read started there looked like a thread with no tool calls."""
    import inspect

    from portia.ui import exchange, transcript

    assert "show=True" in inspect.getsource(transcript._interpret_ticked)
    start = inspect.getsource(exchange.start)
    assert start.index("if show:") < start.index("transcript.pane.refresh()")
    assert "show" not in inspect.getsource(
        __import__("portia.ui.screens").ui.screens._interpret_pending
    )


def test_the_allow_caret_has_no_tooltip_and_keeps_its_words():
    import inspect

    from portia.ui import transcript

    source = inspect.getsource(transcript._allow_controls)
    assert "more.tooltip(" not in source and "aria-label" in source


def test_a_turn_anywhere_makes_the_app_busy():
    """The engine is single-turn: a chat turn must block an indexing one."""
    app = App()
    app.start_exchange("index these", model="m", effort=None, kind=state.INDEXING)
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
    stream = app.new_chat()
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
    ran = asyncio.run(
        engine.index(sorted(tmp_path.glob("*.csv")), app, on_progress=lambda *a: seen.append(a))
    )

    assert ran.names == ["s0", "s1", "s2"] and ran.failed == []
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


def test_an_opening_screen_taller_than_the_window_scrolls_to_its_top():
    """`justify-content: center` over content taller than the window puts half
    the overflow above a scroll range that starts at zero. Reported on a 13"
    screen: no logo, and no way to scroll up to it."""
    import re
    from pathlib import Path

    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.p-centered \{(.*?)\}", css, re.S).group(1)
    assert "justify-content" not in rule
    assert "overflow-y: auto" in rule
    child = re.search(r"\n\.p-centered > \* \{(.*?)\}", css, re.S).group(1)
    assert "margin-block: auto" in child


def test_the_way_out_of_add_data_says_a_read_is_running(loop):
    """The read starts when profiling ends, so the screen you are still on has
    to name the turn that is running rather than promise one that is coming."""
    from portia.ui import screens
    from portia.ui.state import App

    app = App(catalog={"sources": {"orders": {}}})
    with _as_app(screens, app):
        app.indexed = 1
        assert screens._action_note(0) == screens.PROFILED.format(n="1 source")

        stream = app.start_exchange("read them", model="m", effort=None, kind=state.INDEXING)
        assert screens._action_note(0) == screens.READING_NOW

        # Stopped for a human who may have dismissed the popup: the screen must
        # not go on saying it is working.
        stream.rows.append(Decision("question", {}, loop.create_future()))
        assert screens._action_note(0) == screens.WAITING_ON_YOU

        stream.exchange.running = False
        assert screens._action_note(0) not in (screens.READING_NOW, screens.WAITING_ON_YOU)


def _reading(app, monkeypatch, on_start=None):
    """Run the add-data screen's read with the model turn stubbed out.

    Returns the list of labels each turn was started with — one entry per turn,
    so the drain loop is visible as two entries rather than inferred. ``on_start``
    runs inside the fake turn, which is where "something happened while it was
    reading" has to happen to mean anything.
    """
    import asyncio

    from nicegui import core

    from portia.ui import exchange, screens

    started: list[str] = []

    async def fake_start(prompt, *, model, effort, kind, label, chat=None):
        started.append(label)
        if on_start is not None:
            on_start(len(started))

    async def read() -> None:
        # Redrawing a refreshable is fire-and-forget through NiceGUI's own task
        # helper, which wants the app's loop. There is no server here, so the
        # test's loop is it — with nothing rendered, the redraws are no-ops.
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        await screens._interpret_pending()
        await asyncio.sleep(0)  # let those no-op redraws run before the loop shuts

    monkeypatch.setattr(exchange, "start", fake_start)
    with _as_app(screens, app):
        asyncio.run(read())
    return started


def test_the_read_starts_without_waiting_for_the_way_out(monkeypatch):
    """Profiling ends, the read begins — with the add-data screen still up.

    It used to wait for `Open the workspace`, which spent the wait twice: the
    screen went quiet and the minute of model time only started when you noticed.
    """
    from portia.ui.state import App

    app = App(catalog={"sources": {"orders": {}}}, pending_interpret=["orders"])

    assert _reading(app, monkeypatch) == ["orders"]
    assert app.pending_interpret == [], "a source read is a source no longer queued"


def test_indexing_hands_straight_over_to_the_read(tmp_path, monkeypatch):
    """The whole change, at the level of the one button that does it: profiling
    ends and the read begins, with nothing clicked in between and the add-data
    screen still up."""
    import asyncio

    from nicegui import core, ui

    from portia.ui import exchange, screens
    from portia.ui.state import App

    pd.DataFrame({"a": [1, 2]}).to_csv(tmp_path / "orders.csv", index=False)
    monkeypatch.chdir(tmp_path)  # the catalog resolves source paths from the cwd
    app = App(root=tmp_path)
    catalog.init_project("test", portia_dir=app.portia_dir)

    started: list[str] = []

    async def fake_start(prompt, *, model, effort, kind, label, chat=None):
        started.append(label)
        assert not app.left_add_data, "the read runs with the add-data screen still showing"

    monkeypatch.setattr(exchange, "start", fake_start)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)  # no client to notify

    async def index() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        await screens._index_and_interpret([tmp_path / "orders.csv"])
        await asyncio.sleep(0)

    with _as_app(screens, app):
        asyncio.run(index())

    assert list(app.sources) == ["orders"], "profiled, deterministically, as always"
    assert started == ["orders"], "and read, without waiting to be asked twice"


def test_a_pressed_index_is_busy_until_profiling_ends_and_takes_one_press(tmp_path, monkeypatch):
    """The button kept its live form through the whole run, so a second press
    started a second run. Busy from the press to the end of the free half, and
    a button again before the read, which is when a second batch is indexed."""
    import asyncio

    from nicegui import core, ui

    from portia.ui import engine, exchange, screens
    from portia.ui.state import App

    pd.DataFrame({"a": [1, 2]}).to_csv(tmp_path / "orders.csv", index=False)
    monkeypatch.chdir(tmp_path)
    app = App(root=tmp_path)
    catalog.init_project("test", portia_dir=app.portia_dir)

    seen: dict[str, str] = {}
    real_index = engine.index

    async def watched_index(paths, app_, **kwargs):
        seen["while profiling"] = app.indexing_pressed
        await screens._index_now()  # the second press, mid-run
        return await real_index(paths, app_, **kwargs)

    async def fake_start(prompt, *, model, effort, kind, label, chat=None):
        seen["at the read"] = app.indexing_pressed

    monkeypatch.setattr(engine, "index", watched_index)
    monkeypatch.setattr(exchange, "start", fake_start)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(screens, "_ticked", lambda: [tmp_path / "orders.csv"])
    monkeypatch.setattr(screens._actions, "refresh", lambda *a, **k: None)

    # A press arrives inside the pressed button's slot. `asyncio.run` starts a
    # task with an empty slot stack, so the test's own client is entered by hand.
    from nicegui import context

    client = context.client

    async def press() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        with client:
            await screens._index_now()

    with _as_app(screens, app):
        asyncio.run(press())

    assert seen == {"while profiling": "Index 1 file", "at the read": ""}
    assert list(app.sources) == ["orders"], "one run, not two"
    assert app.indexing_pressed == ""


def test_a_busy_button_keeps_its_fill_and_its_words():
    """Quasar's `loading` hid the label and muddied the accent. Busy is the
    same button washed out, and the CSS is what stops the second press."""
    import re
    from pathlib import Path

    with ui.element("div"):
        busy = c.button("Index 3 files", kind="primary", busy=True)
    assert "btn-busy" in busy.classes and "btn-primary" in busy.classes
    assert busy.text == "Index 3 files"
    assert "loading" not in busy.props
    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.btn\.btn-busy \{(.*?)\}", css, re.S).group(1)
    assert "pointer-events: none" in rule and "background" not in rule


def test_the_index_press_holds_its_client_before_it_redraws_its_own_button():
    """The first build refreshed `_actions` from inside the button's handler
    and then notified through the deleted slot. The run died at its first
    toast with the button busy for good (2026-09-18, found by the user)."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._index_now)
    held, redraw, entered = (
        source.index("client = context.client"),
        source.index("_actions.refresh()"),
        source.index("with client:"),
    )
    assert held < redraw < entered
    assert "_actions.refresh()" in inspect.getsource(screens._pressed_done)
    assert source.index("finally:") < source.rindex("_pressed_done()")


def test_a_running_index_always_offers_the_way_out():
    """It was offered only once the project had a source, so a first index that
    hung held the screen with nothing but Back."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._actions.func)
    branch = source[source.index("if outstanding or busy:") : source.index("elif APP.sources")]
    assert "_leave_label(in_dialog)" in branch
    assert "if APP.sources or in_dialog:" not in branch


def test_opening_the_workspace_lands_in_the_job_that_is_running(monkeypatch):
    """The caption under the button says the copilot is reading. The workspace
    opened on the chat list, with that job one more click away."""
    from portia.ui import app as app_module
    from portia.ui import screens
    from portia.ui.state import App

    monkeypatch.setattr(app_module.shell, "refresh", lambda *a, **k: None)
    app = App(catalog={"sources": {"orders": {}}})
    with _as_app(screens, app):
        screens._leave(in_dialog=False)
        assert app.open is None, "nothing running, so the list"

        app.left_add_data = False
        job = app.start_exchange("read them", model="m", effort=None, kind=state.INDEXING)
        assert app.open is None, "a job still never takes the screen by itself"
        screens._leave(in_dialog=False)
        assert app.open is job and app.left_add_data


def test_a_batch_indexed_while_the_first_is_being_read_is_not_dropped(monkeypatch):
    """Indexing again mid-read is ordinary on this screen, and `exchange.start`
    refuses a second live turn *silently* — so the queue is drained in a loop
    rather than read once."""
    from portia.ui.state import App

    app = App(catalog={"sources": {"orders": {}}}, pending_interpret=["orders"])

    def index_again(nth: int) -> None:
        if nth == 1:  # a second batch lands while the first is being read
            app.pending_interpret = ["invoices"]

    assert _reading(app, monkeypatch, index_again) == ["orders", "invoices"]
    assert app.pending_interpret == []


def test_the_switch_being_off_queues_the_sources_rather_than_discarding_them(monkeypatch):
    """One rule, at both moments it can fire: whatever is profiled and unread
    gets read while the switch is on. Consuming the names with it off would make
    turning it on afterwards a control that does nothing."""
    from portia.ui.state import App

    app = App(catalog={"sources": {"orders": {}}}, pending_interpret=["orders"], interpret=False)

    assert _reading(app, monkeypatch) == []
    assert app.pending_interpret == ["orders"]

    app.interpret = True
    assert _reading(app, monkeypatch) == ["orders"]


def test_a_read_already_running_is_not_started_a_second_time(monkeypatch):
    """The engine runs one turn at a time. A second start is a no-op that would
    take the queue with it."""
    from portia.ui.state import App

    app = App(catalog={"sources": {"orders": {}}})
    app.start_exchange("read them", model="m", effort=None, kind=state.INDEXING)
    app.pending_interpret = ["invoices"]

    assert _reading(app, monkeypatch) == []
    assert app.pending_interpret == ["invoices"], "the running read picks them up when it ends"


def test_the_invitation_names_which_kind_of_stop_it_is():
    """A question and a pending write are two different stops, and the popup is
    the only thing on screen saying which one is waiting."""
    from portia.agent import events
    from portia.ui import screens
    from portia.ui.state import App

    app = App()
    with _as_app(screens, app):
        app.decision_waiting = events.QUESTION
        assert screens._decision_line() == screens.DECISION_QUESTION
        app.decision_waiting = events.APPROVAL
        assert screens._decision_line() == screens.DECISION_APPROVAL


def test_the_breakdown_partitions_the_button_and_does_not_double_count(tmp_path):
    """An imported file is profiled like every other one. Splitting the line into
    "copies 1" and "profiles 22" made two numbers that looked like they should
    sum to the button's 23 when 23 is what gets profiled — the copy is something
    one of them additionally needed, not a separate job. The parts say where each
    file came *from*, which is a real partition."""
    from portia.ui import screens
    from portia.ui.state import App

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    app = App(root=tmp_path, catalog={"data_dir": "data"})
    app.import_plan = [(tmp_path / "x.csv", tmp_path / "data" / "x.csv")]

    with _as_app(screens, app):
        note = screens._action_note(2)

    assert "Profiles 2 files" in note, note
    assert "1 copied in to data/" in note and "1 already in the repo" in note


def test_a_metadata_press_says_so_in_one_sentence_with_the_word_in_the_accent():
    """*Free; nothing is scanned* and *Each is scanned once on the warehouse*
    are gone (the user, 2026-09-18). What tells the two presses apart is the
    word *metadata*, in the accent, and only on the press that scans nothing."""
    from portia.ui import screens
    from portia.ui.state import App

    app = App()
    app.scope_ticks = frozenset({"DB.STG.ORDERS", "DB.STG.lines"})
    with _as_app(screens, app):
        assert screens._action_note(2) == "Adds 2 tables as metadata."
        with ui.element("div") as slot:
            screens._note_line(screens._action_note(2), accent=screens.METADATA_WORD)
        app.profile_on_add = True
        assert screens._action_note(2) == "Adds and profiles 2 tables."
    runs = [
        (str(e.text), "c-accent" in e.classes) for e in slot.descendants() if hasattr(e, "text")
    ]
    assert ("metadata", True) in runs
    assert [text for text, accent in runs if accent] == ["metadata"]


def test_the_breakdown_is_only_the_repo_when_nothing_is_being_imported(tmp_path):
    from portia.ui import screens
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={"data_dir": "data"})

    with _as_app(screens, app):
        note = screens._action_note(4)

    assert "Profiles 4 files" in note and "4 already in the repo" in note
    assert "copied in" not in note


def _as_app(module, app):
    """Point a UI module's module-level ``APP`` at a throwaway one.

    The app is a process singleton on purpose (`state.APP`), which is right for
    the product and awkward for a test that wants two projects. Swapping the
    module's reference is the smallest honest way in.
    """
    import contextlib

    @contextlib.contextmanager
    def swapped():
        original = module.APP
        module.APP = app
        try:
            yield app
        finally:
            module.APP = original

    return swapped()


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


def test_runs_chats_and_indexing_are_three_different_lists(tmp_path):
    """Three artifacts, three lists (`docs/CONVERSATION.md` §3): a *run* executed
    a spec, a *chat* was a conversation about what it should say, an *indexing*
    was a job the app ran. The pane must not merge any of them."""
    from portia import runlog
    from portia.ui import engine

    app = App(root=tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "2026-07-29T09-00-00.md").write_text("# a spec run", encoding="utf-8")
    chat = runlog.start(app.catalog_dir, kind=runlog.CHAT)
    job = runlog.start(app.catalog_dir, kind=runlog.INDEXING)

    assert [p.suffix for p in engine.runs_in(app)] == [".md"]
    assert set(engine.logs_in(app)) == {chat.path, job.path}, "one list, both kinds"
    assert engine.log_listing(app, chat.path)["kind"] == runlog.CHAT
    assert engine.log_listing(app, job.path)["kind"] == runlog.INDEXING


def test_a_logs_counts_come_from_the_engine_not_the_panel(tmp_path):
    """`DESIGN.md`: nothing in `ui/` computes. The window and `cli.history` have to
    quote the same number for how often the copilot asked."""
    from portia import runlog
    from portia.agent import events
    from portia.ui import engine

    app = App(root=tmp_path)
    log = runlog.start(app.catalog_dir)
    log.event(events.prompt_event("a goal", model="m", effort="low"))
    log.event(events.question_event([{"question": "which grain?"}]))

    path = engine.log_path(app, log.path.name)
    assert path is not None
    logged = runlog.read(path)
    assert engine.log_summary(logged) == runlog.summary(logged)
    assert engine.log_summary(logged)["questions"] == 1


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
    for name in ("read_text", "read_log", "read_table"):
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


def test_an_unread_source_says_so_instead_of_showing_the_auto_draft():
    """`catalog._auto_summary` restates the profile so the YAML is never empty.
    In the prose slot it reads as a *read* of the data — prose, saying true
    things — when what it actually means is that nobody has looked yet."""
    from portia.ui import workflow
    from portia.ui.state import App

    entry = {"summary": f"3 rows, 2 columns. {catalog.AUTO_DRAFT_MARKER} — edit freely.)"}

    with _as_app(workflow, App()), ui.element("div") as slot:
        workflow._summary(entry)

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert catalog.AUTO_DRAFT_MARKER not in drawn
    assert workflow._NOT_READ in drawn


def test_a_wide_source_folds_its_columns_until_asked():
    """Thirty columns is the normal case, and the rows, the actions and the
    preview all sit below them. Nothing is hidden without the count saying so."""
    from portia.ui import workflow
    from portia.ui.state import App

    columns = [{"name": f"c{i}"} for i in range(30)]
    app = App()

    with _as_app(workflow, app):
        assert len(workflow._shown_columns("orders", columns)) == workflow.COLUMNS_FOLDED
        app.columns_open = "orders"
        assert workflow._shown_columns("orders", columns) == columns
        assert len(workflow._shown_columns("customers", columns)) == workflow.COLUMNS_FOLDED


def test_a_narrow_source_is_never_folded():
    from portia.ui import workflow
    from portia.ui.state import App

    columns = [{"name": f"c{i}"} for i in range(workflow.COLUMNS_FOLDED)]

    with _as_app(workflow, App()):
        assert workflow._shown_columns("orders", columns) == columns


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
    (outside / "orders.csv").write_text("a,b\n1,2\n", encoding="utf-8")
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


def test_an_empty_destination_means_the_project_root(project):
    """Reversed 2026-08-06. It used to fall through to `data/`, on the reading
    that files loose at the top of a project are nobody's deliberate choice.

    That is wrong for the project whose data *is* its root — a folder of CSVs
    opened directly — where inventing `data/` is the surprise. The checkbox
    above the field is still the default, so an empty box is now something you
    cleared on purpose.
    """
    from portia.ui import engine

    app, _ = project

    assert engine.destination_in("", app) == app.root
    assert engine.destination_in("   ", app) == app.root
    assert engine.destination_in("raw", app) == app.root / "raw"


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
    (app.root / "data" / "orders.csv").write_text("already here", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        engine.plan_import(str(outside / "orders.csv"), "data", app)

    assert (app.root / "data" / "orders.csv").read_text(encoding="utf-8") == "already here"


def test_importing_copies_and_never_moves(project):
    """A tool that relocates someone's data is not a data-harmonization concern."""
    import asyncio

    from portia.ui import engine

    app, outside = project
    pairs = engine.plan_import(str(outside / "orders.csv"), "data/raw", app)

    copied = asyncio.run(engine.import_files(pairs, app))

    assert copied == [app.root / "data" / "raw" / "orders.csv"]
    assert copied[0].read_text(encoding="utf-8") == "a,b\n1,2\n"
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
        assert "c.model_effort(" in source, f"{module.__name__} rolls its own"
        assert "on_provider=" in source, f"{module.__name__} offers no provider"
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


def test_opening_a_project_leaves_the_last_ones_charts_behind(tmp_path, monkeypatch):
    """Unsaved charts used to follow you from project to project."""
    from portia.ui import engine
    from portia.ui.state import App, Chart

    monkeypatch.chdir(tmp_path)
    app = App()
    app.portia_dir = ".portia"
    app.show_chart(Chart(name="Events", rows=[{"x": 1}]))
    assert app.tabs

    engine.open_project(tmp_path / "next", app)

    assert app.charts == []


def test_switching_projects_refuses_while_a_message_is_in_flight(monkeypatch):
    """A switch mid-exchange leaves the copilot writing into a directory the
    window has stopped looking at."""
    import asyncio

    from portia.ui import settings
    from portia.ui.state import APP

    APP.chats = [state.Chat(exchange=state.Exchange(prompt="g", model="m", effort="low"))]
    said = []
    monkeypatch.setattr(settings.ui, "notify", said.append)
    monkeypatch.setattr(settings, "_close", lambda: None)
    APP.opened = True

    asyncio.run(settings._switch_project())

    assert APP.opened is True, "still in the project"
    assert said == [settings.SWITCH_BUSY]
    APP.chats = []


def test_switching_projects_closes_the_chat_it_leaves(monkeypatch):
    """A chat holds a live SDK subprocess (`CONVERSATION.md` §4). Leaving a
    project without closing it leaks one for as long as the window lives."""
    import asyncio

    from portia.ui import settings
    from portia.ui.state import APP

    closed = []

    class FakeChat:
        async def close(self):
            closed.append(True)

    from portia.ui import app as app_module

    APP.chats = [state.Chat(conversation=FakeChat()), state.Chat(conversation=FakeChat())]
    monkeypatch.setattr(settings.ui, "notify", lambda *a: None)
    monkeypatch.setattr(settings, "_close", lambda: None)
    monkeypatch.setattr(app_module.shell, "refresh", lambda *a, **k: None)
    APP.opened = True

    asyncio.run(settings._switch_project())

    assert closed == [True, True], "every parked client, not one"
    assert all(chat.conversation is None for chat in APP.chats)
    assert APP.opened is False
    APP.chats = []


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

    assert app_module.ACTION_TIPS == ("Run every table", "Save tables and reports")
    for tip in (*app_module.ACTION_TIPS, app_module._SETTINGS_TIP):
        assert "\n" not in tip and len(tip) <= 26, f"{tip!r} is explaining, not naming"


def test_the_run_actions_are_drawn_on_the_pane_they_act_on():
    """From the far corner of the toolbar they floated above the transcript — the
    one pane they have nothing to do with. Chrome above the panes also cannot
    align to the middle pane's edge: a dragged pane's width never reaches the
    server (`_room_beside_files`), so the actions have to be drawn inside it.

    Inside the *canvas*, since 2026-08-15 — see
    `test_the_run_actions_are_drawn_by_the_canvas_not_beside_it`.
    """
    import inspect

    from portia.ui import app as app_module
    from portia.ui import workflow

    assert "run_controls()" in inspect.getsource(workflow._workflow)
    assert "run_controls" not in inspect.getsource(app_module.toolbar.func)


# --- closing a pane is a drag, and the rail is how it comes back -------------


def test_dragging_a_pane_past_its_floor_closes_it():
    """`DESIGN.md`: below its minimum a pane stops being worth having, and the
    honest move is to close it rather than to squeeze it. The splitter used to
    simply refuse to go further, which left the toolbar toggle as the only way."""
    from portia.ui import app as app_module

    closed: list[str] = []
    kept: list[int] = []
    floor = app_module.FILES_LIMITS[0]

    app_module._dragged(floor, floor, lambda: closed.append("files"), kept.append)
    assert closed == [], "at the floor it is still readable"
    assert kept == [floor], "and it is where the pane opens next time"

    app_module._dragged(floor - 1, floor, lambda: closed.append("files"), kept.append)
    assert closed == ["files"]
    assert kept == [floor], "a width the pane closed at is not one to open at"


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

    css = theme.CSS.read_text(encoding="utf-8")
    block = re.search(r"\.p-pane-row \{([^}]*)\}", css)
    assert block, ".p-pane-row is not styled"
    assert "width: 100%" in block.group(1) and "height: 100%" in block.group(1)


def test_every_settings_tab_has_something_to_draw():
    """A tab with no body renders an empty panel, and the failure is silent."""
    from portia.ui import settings

    assert tuple(settings._BODY) == settings.TABS


def test_picking_a_setting_does_not_throw_you_back_to_the_first_tab(monkeypatch):
    """Picking a theme or an effort redraws the section. If the showing tab
    were rebuilt with it, every pick would bounce you back to Project."""
    from portia.ui import settings, transcript

    monkeypatch.setattr(settings._section, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "refresh_spend", lambda: None)
    monkeypatch.setattr(settings.theme, "set_mode", lambda *a, **k: None)
    monkeypatch.setattr(settings, "_TAB", "Appearance")

    settings._set_theme("light")
    assert settings._TAB == "Appearance"

    settings._set_effort("high")
    assert settings._TAB == "Appearance"


def test_a_click_in_settings_redraws_what_it_changed_and_never_the_card(monkeypatch):
    """Switching section used to rebuild the whole panel, title and list and
    Close included (2026-09-23, the user: *the whole card rerenders instead of
    just switching*); then a theme or an effort rebuilt the whole section (the
    user again, the same day: *every floating card gets a full refresh when it's
    clicked*). Only a section switch redraws the section. A theme and an effort
    redraw nothing, and a provider redraws `_spend` alone."""
    from portia.ui import settings, transcript

    def redrawn(what):
        def fail(*a, **k):
            raise AssertionError(f"{what} was redrawn")

        return fail

    sections, spends, behind = [], [], []
    monkeypatch.setattr(settings._panel, "refresh", redrawn("the whole settings card"))
    monkeypatch.setattr(settings._section, "refresh", lambda *a, **k: sections.append(1))
    monkeypatch.setattr(settings._spend, "refresh", lambda *a, **k: spends.append(1))
    monkeypatch.setattr(transcript, "refresh_spend", lambda: behind.append(1))
    monkeypatch.setattr(settings.theme, "set_mode", lambda *a, **k: None)
    from nicegui import background_tasks

    monkeypatch.setattr(background_tasks, "create", lambda coroutine: coroutine.close())
    monkeypatch.setattr(settings, "_TAB", "Project")

    settings._show_tab("Copilot")
    settings._show_tab("Copilot")  # already showing: nothing to draw
    assert sections == [1]

    monkeypatch.setattr(settings._section, "refresh", redrawn("the settings section"))
    settings._set_theme("light")
    settings._set_effort("high")
    assert spends == [] and behind == [1], "the effort moves itself; the composer follows"
    settings._set_provider("anthropic")
    assert spends == [1], "a provider changes the spend setting, and only that"
    assert settings._TAB == "Copilot"


def test_a_setting_that_only_moves_a_highlight_moves_it_in_place():
    """The segments, the theme cards and the settings rail all move a class on
    the elements already drawn (`c.mark_selected`); none of them is rebuilt."""
    import inspect

    from portia.ui import settings

    with ui.element("div"):
        rows = {k: ui.element("div").classes("row") for k in ("a", "b", "c")}
    rows["a"].classes(add="picked")
    c.mark_selected(rows, "c", "picked")
    assert ["picked" in r.classes for r in rows.values()] == [False, False, True]

    assert "mark_selected(" in inspect.getsource(c.segmented)
    assert "c.mark_selected(" in inspect.getsource(settings._set_theme)
    assert "c.mark_selected(" in inspect.getsource(settings._show_tab)
    for fn in (settings._set_theme, settings._set_effort):
        assert "_section.refresh()" not in inspect.getsource(fn), fn.__name__
    # *Customize* shows and hides what it folds; the section redraw left in it
    # is for a panel whose elements are already gone.
    assert "folded.set_visibility(_CUSTOMIZING)" in inspect.getsource(settings._toggle_customize)


def test_a_segment_pressed_moves_its_wash_before_the_caller_hears():
    from nicegui.events import ClickEventArguments, handle_event

    picked = []
    with ui.element("div") as slot:
        c.segmented(["low", "high"], "low", picked.append)
    buttons = [e for e in slot.descendants() if isinstance(e, ui.button)]
    click = next(x for x in buttons[1]._event_listeners.values() if x.type == "click")
    handle_event(click.handler, ClickEventArguments(sender=buttons[1], client=buttons[1].client))
    assert picked == ["high"]
    assert ["seg-active" in b.classes for b in buttons] == [False, True]


def test_a_field_turns_required_in_place():
    """The connect dialog's sign-in method decides which fields are required,
    and picking one turns the word beside each rather than redrawing the boxes
    somebody is typing into."""
    with ui.element("div") as slot:
        box = c.field("Key file")
    word = next(e for e in slot.descendants() if isinstance(e, ui.label) and e.text == "optional")
    c.set_required(box, True)
    assert word.text == "required" and "field-required" in word.classes
    assert "field-optional" not in word.classes
    c.set_required(box, False)
    assert word.text == "optional" and "field-optional" in word.classes


def test_picking_a_provider_redraws_its_detail_and_nothing_else(monkeypatch):
    """Settings → Providers: a click on a row switches the detail on the right.
    It used to redraw the whole settings panel, rows and header included."""
    from portia.ui import providers as providers_ui
    from portia.ui.state import APP

    def redrawn(what):
        def fail(*a, **k):
            raise AssertionError(f"{what} was redrawn")

        return fail

    import inspect

    details = []
    monkeypatch.setattr(providers_ui._head, "refresh", redrawn("the header"))
    monkeypatch.setattr(providers_ui._detail_view, "refresh", lambda *a, **k: details.append(1))
    monkeypatch.setattr(APP, "provider_pick", "anthropic")

    providers_ui._pick("codex")
    providers_ui._pick("codex")

    assert APP.provider_pick == "codex"
    assert details == [1]
    # **The list is never redrawn after the section is** (2026-09-23): the
    # dashboard is not a refreshable, a check sets each row's light and line in
    # place, and a variable added or removed redraws the variables alone.
    assert not hasattr(providers_ui._dashboard, "refresh")
    assert "c.set_light(" in inspect.getsource(providers_ui._checked)
    for fn in (providers_ui._add_var, providers_ui._drop_var):
        source = inspect.getsource(fn)
        assert "_variables.refresh()" in source and "_detail_view" not in source, fn.__name__


def test_the_settings_body_lays_its_sections_out_as_the_composer_does_not():
    """Three rules measured in a browser on 2026-09-23. The effort segments'
    `spend-detail` is a full line in the composer's wrapping row, and inside a
    `setting` (a column) the same 100% became a height, so the segments sat on
    the next setting's title. A section is never squeezed to fit the box. And
    the providers dashboard is stretched to the body, or its rows' unwrapped
    status lines set its width and push the detail off the right edge."""
    import re

    from portia.ui import theme

    css = theme.CSS.read_text(encoding="utf-8")

    def block(selector):
        found = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
        assert found, f"{selector} is not styled"
        return found.group(1)

    assert "flex: 0 0 auto" in block(".setting > .spend-detail")
    assert "flex-shrink: 0" in block(".settings-body > *")
    assert "align-self: stretch" in block(".providers-layout")
    assert "flex-wrap: nowrap" in block(".provider-row .provider-row-state")
    # The section list is a rail of glyphs, its names in tooltips (the user's
    # call, 2026-09-23), qualified so `.artifact-row .artifact-body` below it
    # does not draw them back.
    assert "display: none" in block(".settings-nav .artifact-row .artifact-body")
    # The variables are one grid, so a name sits level with its box.
    assert "display: grid" in block(".provider-vars")


def test_the_providers_detail_says_what_a_field_is_for_on_hover():
    """A caption under every field was most of what the detail showed (the
    user, 2026-09-23): the sentences are `help_tip`s beside the names now."""
    import inspect

    from portia.ui import providers as providers_ui

    source = inspect.getsource(providers_ui._detail) + inspect.getsource(
        providers_ui._variables.func
    )
    assert "hint=" not in source
    assert "help=why" in source
    assert "c.help_tip(notes[key])" in source
    assert "c.caption(VARIABLES_WHY)" not in source


def test_the_settings_sections_reuse_the_left_panes_row_vocabulary():
    """A list beside the settings, drawn with `artifact_row` — one row shape in
    the app rather than a second one for a dialog *(2026-09-04)*. Every section
    gets a glyph, and the row is the one a fifth section would not overflow."""
    import inspect

    from portia.ui import settings

    source = inspect.getsource(settings._nav)
    assert "c.artifact_row(" in source
    assert set(settings._ICONS) == set(settings.TABS)


def test_every_setting_is_one_row_shape():
    """A title, what it does, and the control: `c.setting`, opened by each
    section for each preference. Captions stacked in whatever order a tab
    happened to draw them read three ways for one kind of thing."""
    import inspect

    from portia.ui import settings

    for body in settings._BODY.values():
        assert "c.setting(" in inspect.getsource(body), body.__name__


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

    css = theme.CSS.read_text(encoding="utf-8")

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


def test_run_carries_its_word_and_save_does_not():
    """The action that *executes* something is the one worth naming on screen.

    Two buttons, not four (2026-08-15): Run is scoped by the canvas rather than
    by which button you press, and Save writes the tables with their reports.
    """
    import inspect
    import re

    from portia.ui import app as app_module

    # Collapsed, because the formatter wraps a long call across lines and this is
    # a statement about the arguments, not about where they sit.
    source = re.sub(r"\s+", " ", inspect.getsource(app_module.run_controls.func))

    def calls(label: str, handler: str) -> bool:
        return re.search(rf'c\.button\( ?"{label}", {handler}', source) is not None

    assert calls("Run", "_run")
    assert source.count("split=True") == 1, "only the one that executes something"
    assert calls("", "_write")
    assert "_build" not in source and "_save_report" not in source


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


#: The only things in the left pane allowed a tooltip: bare icon controls, which
#: have no label and cannot say what they do any other way (`components.button`
#: makes the same call — "a caller that passes only an icon owes it a tooltip").
_MAY_TOOLTIP = ("add", "move", "drop", "save")


def test_the_tree_never_pops_a_box_repeating_the_row_you_are_pointing_at():
    """Instant tooltips fired all the way down the pane as you scanned it, each
    saying what the row already said.

    **The rule is about rows, and this test used to ban the word outright**
    *(narrowed 2026-09-03)*. That was right while every tooltip in the module was
    on a row; the gallery added bare icon controls — new folder, move, delete —
    which have no label at all, so a tooltip is the only thing that can say what
    they do. Banning those would trade a box repeating a row for a glyph nobody
    can read.

    So what is checked is the receiver: a tooltip may hang off an icon control
    and never off an `artifact_row`.
    """
    import inspect

    from portia.ui import artifacts

    source = inspect.getsource(artifacts)
    for line in source.splitlines():
        if ".tooltip(" not in line:
            continue
        receiver = line.strip().split(".tooltip(")[0].strip()
        unlabelled = receiver in _MAY_TOOLTIP or receiver.startswith('c.button("",')
        assert unlabelled, f"something with a label is being tooltipped: {line.strip()}"
    assert "c.hint(row, APP.project_context)" in source, "the brief still says what a row cannot"


def test_a_gallery_name_with_a_space_survives_the_prop_parser():
    """`data-folder=palette test` parsed as a folder called `palette` and a boolean
    prop called `test`, so drops into that folder landed in one of its first word.
    Three comments said a quoted value did not survive either; this is the
    measurement that says it does (`components.prop_value`)."""
    from nicegui.props import Props

    from portia.ui import components as c

    assert Props.parse(f"data-folder={c.prop_value('palette test')}") == {
        "data-folder": "palette test"
    }
    assert Props.parse(f"data-figure={c.prop_value('figures/a b/c.json')} draggable=true") == {
        "data-figure": "figures/a b/c.json",
        "draggable": "true",
    }
    quoted = 'figure:figures/say "hi"/x.json'
    assert Props.parse(f"data-opens={c.prop_value(quoted)}") == {"data-opens": quoted}


def test_a_failed_refresh_never_stops_the_settings_panel_opening(monkeypatch):
    """Redrawing first is a nicety; opening is the point.

    `refresh()` walks targets a page reload or a second tab may have
    invalidated, and a raise there used to mean the gear silently did nothing —
    showing a stale project path is a far smaller failure than a settings panel
    that will not come up.
    """
    from portia.ui import settings

    opened, said = [], []

    class Dialog:
        is_deleted = False

        def open(self):
            opened.append(True)

    def boom():
        raise RuntimeError("slot is gone")

    monkeypatch.setattr(settings, "_DIALOG", Dialog())
    monkeypatch.setattr(settings._panel, "refresh", boom)
    monkeypatch.setattr(settings.ui, "notify", said.append)

    settings.open_dialog()

    assert opened == [True], "the panel opened anyway"
    assert said and said[0] == settings.STALE_PANEL.format(why="RuntimeError")


def test_a_missing_settings_panel_says_so_rather_than_doing_nothing():
    """The one case where not opening is correct — and it has to be audible."""
    from portia.ui import settings

    said = []
    original_dialog, original_notify = settings._DIALOG, settings.ui.notify
    settings._DIALOG = None
    settings.ui.notify = said.append
    try:
        settings.open_dialog()
    finally:
        settings._DIALOG, settings.ui.notify = original_dialog, original_notify

    assert said == [settings.NO_PANEL]


def test_a_folder_looks_the_same_open_or_shut_and_only_the_caret_moves():
    """Two marks for one piece of state, and the second one lied: a filled glyph
    going hollow is how this app says *different kind of thing*, not *same thing,
    expanded*. The caret is the disclosure control; the icon says `folder`."""
    import inspect

    from portia.ui import artifacts

    source = inspect.getsource(artifacts._folder)
    assert "icon=ICON[tree.FOLDER]" in source
    assert "CARET_OPEN if is_open else CARET_SHUT" in source
    # The glyph name as a string, not `APP.folder_open` or the note explaining why.
    assert '"folder_open"' not in inspect.getsource(artifacts)


# --- add data: two routes, one index -----------------------------------------


def test_an_import_lands_with_the_rest_of_the_data_by_default(tmp_path):
    """An import that lands beside the data is one folder layout; one that lands
    in a second place is two, and nobody chose the second."""
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={"data_dir": "warehouse/raw"})

    assert app.import_dir(engine.DATA_DIR) == "warehouse/raw"


def test_with_no_data_folder_chosen_an_import_creates_the_data_directory(tmp_path):
    """A file arriving at the project root is not a decision anyone made."""
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={})

    assert app.import_dir(engine.DATA_DIR) == "data"


def test_the_project_root_as_a_data_folder_does_not_become_an_import_destination(tmp_path):
    """ "The whole repo" is a legitimate *scope* and a nonsensical *destination* —
    it would drop imported files loose at the top of the project."""
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={"data_dir": "."})

    assert app.import_dir(engine.DATA_DIR) == "data"


def test_a_typed_destination_wins_when_the_default_is_turned_off(tmp_path):
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={"data_dir": "data"})
    app.import_to_data_dir = False
    app.import_destination = "vendor/acme"

    assert app.import_dir(engine.DATA_DIR) == "vendor/acme"


def test_everything_under_the_chosen_folder_is_ticked_by_default(tmp_path):
    """ "This folder is my data" is a statement about the folder. Un-ticking is
    for the exception, and a list that arrived empty would make the ordinary
    case thirty clicks."""
    from portia.ui import screens
    from portia.ui.state import App

    (tmp_path / "data" / "2024").mkdir(parents=True)
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "data" / "2024" / "events.csv").write_text("a\n1\n", encoding="utf-8")
    app = App(root=tmp_path, catalog={"data_dir": "data"})

    with _as_app(screens, app):
        screens._seed_ticks()
        ticked = sorted(str(p.relative_to(tmp_path)) for p in screens._ticked())

    assert ticked == ["data/2024/events.csv", "data/orders.csv"]


def test_a_file_already_profiled_arrives_unticked(tmp_path):
    """Re-profiling is idempotent, so it is not wrong — it is a minute of work on
    real extracts that nobody asked for."""
    from portia.ui import screens
    from portia.ui.state import App

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "data" / "new.csv").write_text("a\n1\n", encoding="utf-8")
    app = App(
        root=tmp_path,
        catalog={"data_dir": "data", "sources": {"orders": {"source": "data/orders.csv"}}},
    )

    with _as_app(screens, app):
        screens._seed_ticks()
        ticked = [p.name for p in screens._ticked()]

    assert ticked == ["new.csv"]


def test_an_already_indexed_file_cannot_be_ticked_back_on(tmp_path):
    """The screen does not offer re-indexing at all, so nothing it offers — not
    "All", not a stale exclusion set — can put a catalogued file back in the
    button's count. Re-indexing one source stays available on that source."""
    from portia.ui import screens
    from portia.ui.state import App

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "data" / "new.csv").write_text("a\n1\n", encoding="utf-8")
    app = App(
        root=tmp_path,
        catalog={"data_dir": "data", "sources": {"orders": {"source": "data/orders.csv"}}},
    )

    with _as_app(screens, app):
        app.tick_all({"data/orders.csv", "data/new.csv"}, True)  # what "All" does
        ticked = [p.name for p in screens._ticked()]

    assert ticked == ["new.csv"]


def test_unticking_survives_the_list_being_rebuilt(tmp_path):
    """The state is a set of *exclusions* precisely so that it does: a file
    imported into the middle of the folder must not re-tick the one you cleared."""
    from portia.ui import screens
    from portia.ui.state import App

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    app = App(root=tmp_path, catalog={"data_dir": "data"})

    with _as_app(screens, app):
        screens._seed_ticks()
        app.tick("data/orders.csv", False)
        (tmp_path / "data" / "arrived.csv").write_text("a\n1\n", encoding="utf-8")
        ticked = [p.name for p in screens._ticked()]

    assert ticked == ["arrived.csv"]


def test_the_screen_offers_exactly_one_accented_action(tmp_path):
    """`DESIGN.md` allows one solid accent fill per view, and here it does real
    work: while there are files to profile the accent is on profiling them, and
    the moment there are none it moves to the way out. Both at once is a screen
    asking you to guess which one it meant."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._actions.func)

    assert source.count('kind="primary"') == 2, "one per branch of the same if/elif"
    assert "if outstanding or busy:" in source
    assert "elif APP.sources and not in_dialog:" in source, "and the dialog's Close is not one"


def test_no_route_into_the_project_streams_data_through_the_browser():
    """The drop zone was a third route doing the picker's and the importer's job,
    and the only one that could refuse a file for reasons portia could not
    explain. Every path it served is served by one of the other two."""
    import inspect

    from portia.ui import engine, screens

    for module in (screens, engine):
        source = inspect.getsource(module)
        assert "ui.upload" not in source, f"{module.__name__} still uploads"
        assert "store_upload" not in source, f"{module.__name__} still stores an upload"


def test_choosing_the_project_root_is_recorded_as_a_choice_not_as_silence(tmp_path):
    """`""` and `"."` are the same *scope* and different *answers*: one is "the
    data is the whole repo" and the other is "nobody has said". Storing the empty
    string made the button at the top level appear to do nothing — the screen read
    it back as unset and drew the picker again."""
    from portia import catalog
    from portia.ui import engine
    from portia.ui.state import App

    catalog.init_project("a project", portia_dir=tmp_path / ".portia")
    app = App(root=tmp_path, portia_dir=str(tmp_path / ".portia"))

    engine.set_data_dir("", app)

    assert app.data_dir == "."
    assert catalog.load_catalog(tmp_path / ".portia")["data_dir"] == "."


def test_the_whole_repo_as_a_scope_still_draws_every_readable_file(tmp_path):
    """The scope it means is the one it always meant — `tree` collapses `"."` back
    to "anywhere", so the setting is legible without changing what is drawn."""
    from portia.ui import engine
    from portia.ui.state import App

    (tmp_path / "notebooks").mkdir()
    (tmp_path / "notebooks" / "scratch.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("a\n1\n", encoding="utf-8")

    app = App(root=tmp_path, catalog={"data_dir": "."})
    names = [n.name for n in engine.project_tree(app)]

    assert names == ["data", "notebooks"]


# --- the brief: one card, and one line of guidance ---------------------------


def test_the_brief_and_add_data_are_the_same_card():
    """Two first-run surfaces, one panel — `p-panel` and its three regions.

    They were separately-assembled layouts of the same idea, and the brief's was
    a bare centered column: a heading, a box, and loose text with a raw path
    under it. Neither screen owns the card, so neither may grow its own.
    """
    import inspect

    from portia.ui import screens

    gate = inspect.getsource(screens.project_context)
    add_data = inspect.getsource(screens.panel.func)  # it is a refreshable
    for region in ("p-panel", "p-panel-head", "p-panel-body", "p-panel-actions"):
        assert region in gate and region in add_data, region
    assert "add-data-card" not in inspect.getsource(screens), "the card is not add-data's any more"


def test_the_brief_teaches_its_shape_in_one_line_and_shows_no_example():
    """The guidance had become the notes that described this screen — four shape
    lines, a rule about what not to write, and a worked brief from another
    industry, together longer than the answer they were introducing."""
    from portia.ui import screens

    assert isinstance(screens.CONTEXT_SHAPE, str), "the shape is one line, not a list of them"
    assert not hasattr(screens, "CONTEXT_EXAMPLE"), "no example — people edit one instead of it"
    assert not hasattr(screens, "CONTEXT_WHY"), "the sentence over the box went on 2026-09-18"


def test_back_on_a_first_run_screen_goes_to_the_brief_and_keeps_the_project_open(monkeypatch):
    """Both screens' Back closed the project, and nothing led back to the brief."""
    import inspect

    from portia.ui import app as app_module
    from portia.ui import screens
    from portia.ui.state import App

    monkeypatch.setattr(app_module.shell, "refresh", lambda *a, **k: None)
    app = App(catalog={"project": "Harmonise three booking feeds."}, opened=True)
    with _as_app(screens, app):
        screens._back_to_brief()
        assert app.opened and app.editing_brief
        assert app.goal == "Harmonise three booking feeds.", "the box opens on the saved brief"
    assert "or APP.editing_brief" in inspect.getsource(app_module.shell.func)
    assert "APP.editing_brief = False" in inspect.getsource(screens._save_context)
    for screen in (screens.choose_data, screens._actions.func):
        source = inspect.getsource(screen)
        assert "_back_to_brief" in source and "_back_to_picker" not in source
    # One step back from add data is the question before it, while it is open.
    assert "_reopen_choice if engine.can_change_data(APP)" in inspect.getsource(
        screens._actions.func
    )


def test_where_the_data_is_can_be_answered_again_until_something_is_indexed(tmp_path, monkeypatch):
    """The answer was final from the press of a card (the user, 2026-09-18):
    Back skipped the question, and Settings' *Connect a warehouse* opened the
    file panel. It is open until a source pins the project, because a project
    reads from one place, and choosing files un-names a connection nothing
    has scoped a table through."""
    from portia.ui import app as app_module
    from portia.ui import engine, screens
    from portia.ui.state import App

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module.shell, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(engine, "install_backend", lambda _app: None)
    catalog.init_project("Three feeds.", portia_dir=".portia")
    catalog.set_connection("work", portia_dir=".portia")
    app = App(root=tmp_path, portia_dir=".portia", catalog=catalog.load_catalog(".portia"))
    app.data_mode, app.left_add_data = state.WAREHOUSE_DATA, False
    assert app.connection == "work" and engine.can_change_data(app)

    with _as_app(screens, app):
        screens._reopen_choice()
        assert app.data_mode == "" and app.needs_data_choice
        screens._choose(state.LOCAL_DATA)
    assert app.data_mode == state.LOCAL_DATA
    assert not app.connection, "or the next open would put the project back on the warehouse"

    app.catalog = {**app.catalog, "sources": {"ORDERS": {"source": "data/ORDERS.csv"}}}
    assert not engine.can_change_data(app)
    with pytest.raises(ValueError, match="one place"):
        engine.reopen_data_choice(app)
    with _as_app(screens, app), ui.element("div") as slot:
        screens._data_kind(remote=False)
    assert not [e for e in slot.descendants() if e.tag == "q-btn"], "no Change once pinned"


def test_every_connection_button_says_what_it_opens():
    """*Connect* and *Use another connection* were drawn with no connection
    named: nothing to connect to, and nothing to be other than."""
    import inspect

    from portia.ui import screens

    assert screens._initial_view(["work"], None) == (False, "")
    source = inspect.getsource(screens._connection_state)
    assert source.index("if APP.connection:") < source.index("CONNECT_TO.format")
    assert "open_connect_dialog(new=True)" in source and "open_connect_dialog(new=False)" in source
    assert (screens.ADD_CONNECTION, screens.USE_EXISTING) == (
        "Add connection",
        "Use existing connection",
    )


def test_the_sources_row_is_a_card_with_a_chevron_and_no_bigger_than_a_chat_row():
    """It read as the oldest chat in the list. The outline says *a different
    kind of thing*, and nothing about it is larger or bolder than a chat row."""
    import inspect
    import re
    from pathlib import Path

    from portia.ui import transcript

    assert "chat-row-open" in inspect.getsource(transcript._pinned_sources_row)
    assert len(transcript._SOURCES_META) <= 40, "one line, and it is cut with an ellipsis"
    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.chat-row--pinned \{(.*?)\n\}", css, re.S).group(1)
    assert "border: 1px solid var(--hairline-strong)" in rule
    assert not re.search(r"font-size|font-weight|padding|--accent", rule), "kind, never rank"


def test_a_written_path_shows_its_name_apart_from_its_folders():
    """`path-row`: the name identifies the file, the folders say where it sits,
    and `$HOME` is a third of the string that tells the reader nothing."""
    with ui.element("div"):
        row = c.path_row(Path.home() / "projects" / "demo" / ".portia" / "project.yaml")

    labels = [el for el in row.descendants() if isinstance(el, ui.label)]
    assert [el.text for el in labels] == ["~/projects/demo/.portia/", "project.yaml"]
    assert "path-row-name" in labels[-1].classes


def test_a_pending_decision_invites_you_in_rather_than_moving_you(project):
    """The opening read runs with the add-data screen still up, so the copilot
    can stop for a human whose transcript is nowhere on screen. It asks: the
    popup says what is waiting, and *going* there stays the human's move —
    swapping the window under someone mid-tick is what this replaced.
    """
    from portia.agent import events

    app, _ = project

    app.left_add_data = False
    assert app.prompt_for_decision(events.QUESTION) is True
    assert app.decision_waiting == events.QUESTION
    assert not app.left_add_data, "the popup offers the way through; it is not the way through"

    app.enter_workspace()
    assert app.left_add_data
    assert app.decision_waiting == ""

    # Already there: the decision is on screen, and there is nothing to invite.
    assert app.prompt_for_decision(events.APPROVAL) is False
    assert app.decision_waiting == ""

    # Skipped past adding data is the other way to be in the workspace, and it
    # is `app.shell`'s rule that decides — one rule, or the popup floats over a
    # workspace saying the transcript is somewhere else.
    app.left_add_data, app.skipped_sources = False, True
    assert app.prompt_for_decision(events.QUESTION) is False


# --- the chat (docs/CONVERSATION.md §7, §9) ----------------------------------


def test_a_follow_up_appends_rather_than_wiping_the_transcript():
    from portia.agent import events

    """The whole point of a chat: message two is a follow-up *to* message one,
    and a transcript that cleared between them would say otherwise."""
    from portia.ui.state import App

    app = App()
    stream = app.start_exchange("merge them", model="m", effort="low")
    stream.rows.append(events.Event(events.TEXT, {"text": "done"}))
    stream.exchange.running = False

    stream.conversation = object()  # a client is parked
    app.start_exchange("actually inner join", model="m", effort="low")

    assert len(stream.rows) == 1, "the first exchange is still there"
    assert len(stream.exchanges) == 2
    assert stream.exchange.prompt == "actually inner join"


def test_each_indexing_job_is_its_own_row():
    """A job is one-shot (§6). Two jobs are two rows in the list, not one
    transcript replaced by the other (`CHAT_SESSIONS.md` §3.2)."""
    from portia.agent import events
    from portia.ui.state import App

    app = App()
    first = app.start_exchange("read these", model="m", effort=None, kind=state.INDEXING)
    first.rows.append(events.Event(events.TEXT, {"text": "done"}))
    first.exchange.running = False

    second = app.start_exchange("read those", model="m", effort=None, kind=state.INDEXING)

    assert second is not first
    assert first.rows != [] and second.rows == []
    assert len(first.exchanges) == 1 and len(second.exchanges) == 1


def test_a_new_chat_can_open_while_another_runs():
    """§3.1 — one in flight, many open. "+" always works; what you cannot do
    in the new one is send, and the composer says which chat is running."""
    from portia.ui.state import App

    app = App()
    running = app.start_exchange("a long build", model="m", effort=None)
    assert app.busy and app.open is running

    fresh = app.new_chat()
    app.show_chat(fresh)
    assert app.open is fresh and running in app.chats
    assert app.live is running, "the running chat is still the live one"
    assert not fresh.started, "nothing sent, nothing to list"

    app.show_chat(running)
    assert fresh not in app.chats, "left empty, it is dropped"
    assert app.open is running


def test_a_question_in_another_chat_badges_and_never_switches(loop):
    """§3.5 — the row and the header carry a dot; nobody is moved."""
    from portia.ui.state import App

    app = App()
    asking = app.start_exchange("first", model="m", effort=None)
    asking.exchange.running = False
    mine = app.new_chat()
    app.show_chat(mine)

    asking.rows.append(Decision("question", {}, loop.create_future()))
    assert app.waiting == [asking]
    assert app.open is mine, "not switched"


def test_an_open_chat_sitting_idle_is_not_busy():
    """§9 — if `busy` meant "a client exists", an open chat would block indexing
    for as long as it stayed open."""
    from portia.ui.state import App

    app = App()
    stream = app.start_exchange("merge them", model="m", effort="low")
    stream.conversation = object()
    stream.exchange.running = False

    assert stream.open is True
    assert stream.busy is False
    assert app.busy is False


def test_a_chats_spend_is_summed_over_its_exchanges():
    """A chat that spent four cents over three messages spent four cents."""
    from portia.ui.state import App

    app = App()
    stream = app.start_exchange("one", model="m", effort="low")
    stream.exchange.cost_usd = 0.02
    stream.exchange.running = False
    app.start_exchange("two", model="m", effort="low")
    stream.exchange.cost_usd = 0.03

    assert stream.spent == pytest.approx(0.05)
    assert stream.messages == 2

    stream.prior = {"exchanges": 3, "cost_usd": 0.10}
    assert stream.spent == pytest.approx(0.15), "the logged half counts (§3.3)"
    assert stream.messages == 5


def test_a_chat_with_nothing_reported_has_no_spend_rather_than_zero():
    """Nobody reported is not the same claim as it cost nothing."""
    from portia.ui.state import App

    app = App()
    chat = app.start_exchange("one", model="m", effort="low")
    assert chat.spent is None


def test_a_cancelled_decision_reads_as_interrupted_not_as_a_live_form():
    """§8 — an interrupt cancels the parked callback, so the future behind that
    form is one nobody will ever read. Drawing it as answerable is the silent
    wrong thing this project forbids, where the human is being asked to act."""
    import asyncio

    from portia.agent import events

    async def go():
        decision = Decision(
            events.QUESTION,
            {"questions": [{"question": "which key?"}]},
            asyncio.get_running_loop().create_future(),
        )
        assert decision.interrupted is False
        decision.future.cancel()
        return decision

    decision = asyncio.run(go())
    assert decision.interrupted is True
    assert decision.resolved is False


def test_an_answered_decision_is_never_interrupted():
    import asyncio

    from portia.agent import events

    async def go():
        decision = Decision(
            events.QUESTION, {"questions": []}, asyncio.get_running_loop().create_future()
        )
        decision.resolve({"which key?": "hotel_id"})
        return decision

    decision = asyncio.run(go())
    assert decision.interrupted is False
    assert decision.resolved is True


# --- the transcript's tool cards --------------------------------------------


def _call(name: str, call_id: str, **arguments):
    from portia.agent import events

    return events.Event(events.TOOL_CALL, {"name": name, "id": call_id, "input": arguments})


def _result(call_id: str, text: str = "{}", is_error: bool = False):
    from portia.agent import events

    return events.Event(events.TOOL_RESULT, {"id": call_id, "text": text, "is_error": is_error})


def test_one_call_runs_and_the_rest_of_the_batch_is_queued():
    """The model asks for several tools at once; the results come back one at a time.

    Without the distinction every open call drew the same marker, so a call that
    had not started looked exactly like the one taking the time.
    """
    from portia.ui import transcript

    rows = [
        _call("mcp__portia__describe_source", "a", source="orders"),
        _result("a"),
        _call("mcp__portia__profile_source", "b", source="orders"),
        _call("mcp__portia__graph_lookup", "c", table="orders"),
        _call("mcp__portia__describe_source", "d", source="regions"),
    ]
    states = transcript._call_states(rows, transcript._results_by_call(rows), busy=True)
    assert states == {
        "a": transcript.DONE,
        "b": transcript.RUNNING,
        "c": transcript.QUEUED,
        "d": transcript.QUEUED,
    }
    assert transcript._pending_calls(rows, busy=True) == [
        ("b", "profile_source", transcript.RUNNING),
        ("c", "graph_lookup", transcript.QUEUED),
        ("d", "describe_source", transcript.QUEUED),
    ]


def test_nothing_is_running_once_the_exchange_has_ended():
    """A spinner on a call nobody is waiting for is a spinner that spins forever."""
    from portia.ui import transcript

    rows = [_call("mcp__portia__profile_source", "a", source="orders")]
    states = transcript._call_states(rows, {}, busy=False)
    assert states == {"a": transcript.DROPPED}
    assert transcript._pending_calls(rows, busy=False) == []


def test_a_failed_call_is_its_own_state_and_not_a_running_one():
    from portia.ui import transcript

    rows = [
        _call("mcp__portia__run_spec", "a", spec_path="specs/x.yaml"),
        _result("a", "boom", True),
    ]
    states = transcript._call_states(rows, transcript._results_by_call(rows), busy=True)
    assert states == {"a": transcript.ERRORED}


def test_an_open_disclosure_survives_the_pane_being_rebuilt():
    """Every streamed event rebuilds the transcript.

    An expansion holding its own value shuts itself the moment the next tool
    call arrives — which is exactly when someone is reading the result they just
    opened, so the answer lives on the app instead.
    """
    app = App()
    app.toggle_row("toolu_01", True)
    assert "toolu_01" in app.open_rows
    app.toggle_row("toolu_01", False)
    assert app.open_rows == frozenset()


def test_the_volume_of_a_result_is_a_count_and_not_a_verdict():
    """A finished card says how much came back. Nothing ranks or sorts by it."""
    from portia.ui import transcript

    assert transcript._volume("x" * 42) == "42 chars"
    assert transcript._volume("x" * 4200) == "4.2k chars"


def test_a_call_head_names_its_subject_before_its_other_arguments():
    """`profile_source` is *about* a source; the rest is what the line gives up."""
    from portia.ui import transcript

    assert transcript.SUBJECT_FIELDS[0] == "source"
    summary = transcript._summarize({"right": "orders", "on": ["order_id"]})
    assert summary.startswith("right='orders'") or summary.startswith("right=orders")
    assert "on=" in summary


# --- the accent, per pane ----------------------------------------------------


def test_send_carries_the_accent_whatever_the_workflow_pane_is_doing():
    """The scarcity rule is **per pane** (2026-08-13, `DESIGN.md`).

    Send used to hand the fill to Run the moment a spec had steps, which is a
    rule about a page applied to three panes you work in at once: a chat whose
    send button greys out because a *different* pane has something to run reads
    as a chat that is closed.
    """
    import inspect
    import re

    from portia.ui import transcript

    source = re.sub(r"\s+", " ", inspect.getsource(transcript._composer))
    assert re.search(r'c\.button\( ?"Send", _go, kind="primary"', source)
    assert "spec_has_steps" not in source, "Send no longer asks what the middle pane holds"


def test_inside_the_transcript_only_one_accent_fill_is_ever_drawn():
    """What keeps the rule true within the pane: Send becomes Stop while a
    message is in flight, so it can never sit beside the Answer or Allow the
    loop is stopped on."""
    import inspect

    from portia.ui import transcript

    source = inspect.getsource(transcript._composer)
    send = source.index('"Send"')
    stop = source.index('"Stop"')
    assert stop < send, "Stop is the in-flight branch; Send is the other one"


# --- what the window is doing ------------------------------------------------


def test_a_run_in_flight_is_its_own_state_and_not_the_copilots():
    """`busy` is about the copilot's streams; `running` is about this pane.

    They block different things — a chat in flight does not stop you running a
    spec, and a run does not stop you typing the next message — so folding them
    into one field would make each say something false about the other.
    """
    app = App()
    assert app.running is None and app.busy is False

    app.running = state.RUNNING
    assert app.busy is False, "a run is not a turn"

    app.start_exchange("merge these", model="claude-haiku-4-5", effort="low")
    assert app.busy is True and app.running == state.RUNNING


def test_every_run_action_says_what_it_is_doing_while_it_does_it():
    """All four go to a thread, so the window stays live — which is what made
    the silence a problem: a minute of work behind a button that looks exactly
    as it did before it was pressed.

    The one that executes a pipeline says it through `_stoppable`, which is
    `_in_flight` plus the handle that ends the run; the one that writes files uses
    `_in_flight` directly. Either way the word is announced.
    """
    import inspect

    from portia.ui import app as app_module

    for action, word, opener in (
        (app_module._run, "RUNNING", "_stoppable"),
        (app_module._write, "WRITING", "_in_flight"),
    ):
        source = inspect.getsource(action)
        assert f"{opener}(state.{word})" in source, f"{action.__name__} is silent"

    # `_stoppable` is a wrapper around `_in_flight`, not a second way of saying it.
    assert "_in_flight(word)" in inspect.getsource(app_module._stoppable)


def test_the_in_flight_word_is_given_back_even_when_the_action_raises(monkeypatch):
    """`engine.execute` catches into `run_error`; the two write actions do not,
    and a run that raised would otherwise leave the pane claiming to be running
    for the rest of the session."""
    import asyncio
    import inspect
    from types import SimpleNamespace

    from portia.ui import app as app_module

    assert "finally:" in inspect.getsource(app_module._in_flight)

    # A refreshable needs a live client to redraw into; what is under test is
    # the state either side of it.
    quiet = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(app_module, "run_controls", quiet)
    monkeypatch.setattr(app_module.workflow, "pane", quiet)

    app = App()
    with _as_app(app_module, app):

        async def blow_up():
            async with app_module._in_flight(state.RUNNING):
                raise RuntimeError("the join failed")

        with pytest.raises(RuntimeError):
            asyncio.run(blow_up())
    assert app.running is None


# --- the middle pane: one list, not two panels -------------------------------


def test_a_step_is_one_block_and_there_is_no_second_detail_panel():
    """Selecting a step used to open a second panel under the canvas, so the
    lower middle pane became two stacked panels about the same step with nothing
    saying which was which. They are two halves of one step, so they are one
    block, and the selected one opens in place."""
    from portia.ui import theme, workflow

    assert not hasattr(workflow, "_step_detail")
    assert ".graph-detail" not in theme.CSS.read_text(encoding="utf-8")


def test_the_report_lists_the_spec_even_before_anything_has_run():
    """A recorded step is a decision on the record whether or not it has been
    executed. "No run yet" put the whole decision record behind one sentence."""
    from portia.ui import workflow

    app = App()
    app.spec = {"steps": [{"id": "joined", "op": "join"}, {"id": "clean", "op": "normalize"}]}
    with _as_app(workflow, app):
        assert [step["id"] for step, _ in workflow._blocks()] == ["joined", "clean"]
        assert all(result is None for _, result in workflow._blocks())


def test_a_step_the_window_has_not_built_is_not_called_unrun():
    """`record_step` measures a step before it enters the spec, and the copilot
    quotes those numbers. A pane saying "not run" beside that reply was wrong:
    what has not happened is Run in this window, which builds the table."""
    from portia.ui import workflow

    said = (workflow._NOT_BUILT + " " + workflow._STEP_NOT_BUILT).lower()
    assert "not run" not in said
    assert "measured when recorded" in said
    assert "not built" in workflow._STEP_NOT_BUILT


def test_a_measurement_whose_step_left_the_spec_is_still_drawn():
    """Dropping it would be the pane deciding a measurement did not happen."""
    from types import SimpleNamespace

    from portia.ui import workflow

    app = App()
    app.spec = {"steps": [{"id": "joined", "op": "join"}]}
    app.results = [SimpleNamespace(id="gone", op="sql")]
    with _as_app(workflow, app):
        assert [step["id"] for step, _ in workflow._blocks()] == ["joined", "gone"]


def test_the_report_is_in_spec_order_and_not_in_the_order_the_run_got_to():
    """The order is the recorded sequence of decisions (`DESIGN.md` → the
    graph). Deriving it from the run would change the list's shape with how far
    the run got, which is the ranking this system forbids arriving sideways."""
    from types import SimpleNamespace

    from portia.ui import workflow

    app = App()
    app.spec = {"steps": [{"id": "a", "op": "join"}, {"id": "b", "op": "sql"}]}
    app.results = [SimpleNamespace(id="b", op="sql"), SimpleNamespace(id="a", op="join")]
    with _as_app(workflow, app):
        assert [step["id"] for step, _ in workflow._blocks()] == ["a", "b"]


def test_opening_a_block_is_a_request_with_a_token_like_the_canvas_focus():
    """The workflow pane renders more than once per click, so bringing the
    opened block into view is a token the client acts on once — not a flag a
    render clears."""
    app = App()

    app.pick_step("joined")
    first = app.step_token
    assert app.selected_step == "joined"

    app.pick_step("joined")
    assert app.selected_step is None, "the same step again shuts it"
    assert app.step_token > first

    app.pick_step("joined")
    assert app.selected_step == "joined"
    assert app.step_token > first + 1, "reopening it is a new request"


def test_an_acknowledgement_is_drawn_once_and_never_inside_the_payload():
    """The banner names the flags and the rationale; repeating them in the
    payload below is the one place a second, quieter copy reads as a second,
    lesser thing (`DESIGN.md` → `write-confirm`)."""
    from portia.ui import workflow

    step = {
        "id": "joined",
        "op": "join",
        "on": ["order_id"],
        "acknowledge": ["join_dropped_all_rows"],
        "rationale": "the legacy ids are known to be unmatched",
    }
    with ui.element("div") as slot:
        workflow._decided(step, ["join_dropped_all_rows"])

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert "order_id" in drawn
    assert "join_dropped_all_rows" not in drawn
    assert "legacy ids" not in drawn


# --- following a live chat without fighting the reader -----------------------


def test_the_transcript_keeps_its_place_declaratively_rather_than_by_script():
    """It pinned itself with a `run_javascript` on every event, which is the
    race `canvas.js` documents: fired mid-render it reached the client before
    the rows existed, clamped to 0, and threw the chat to the top — most
    visibly when a question arrived and when it was answered, since both rebuild
    the pane."""
    import inspect

    from portia.ui import transcript

    assert not hasattr(transcript, "_stay_at_the_bottom")
    source = inspect.getsource(transcript.pane.func)
    assert "ui.run_javascript(" not in source
    assert "stick=True" in source


def test_a_sticking_region_states_both_its_key_and_that_it_follows():
    """Two tabs are two places to keep, so the key still names *what* is being
    scrolled; sticking is a second, separate statement about that region."""
    with ui.element("div"):
        area = c.scroll_area("transcript:chat", classes="p-pad", stick=True)
        plain = c.scroll_area("artifacts")

    assert area._props["data-scroll-key"] == "transcript:chat"
    assert area._props["data-scroll-stick"] == "bottom"
    assert "data-scroll-stick" not in plain._props


def test_following_the_newest_row_stops_the_moment_you_scroll_up():
    """The point of the rule: a question is answered by reading the evidence
    above it, and a panel that yanks you back to the foot while you do that is
    worse than one that never followed at all."""
    script = (Path(__file__).resolve().parents[1] / "portia/ui/assets/scroll.js").read_text(
        encoding="utf-8"
    )

    assert "was && !was.foot" in script, "scrolled up means keep the place, not follow"
    assert "keep(el)" in script


# --- progress: where the run is, said on the loop that can draw it -----------


def test_build_progress_arrives_on_the_event_loop_and_not_on_the_builder():
    """The build is on a worker; NiceGUI's element tree belongs to the loop.

    A callback that refreshed a pane from the builder's thread would be mutating
    that tree from outside the loop that owns it — the class of bug that shows up
    as a pane that is intermittently half-drawn. `engine.build` hops every
    callback across with `call_soon_threadsafe`, and this is that hop.
    """
    import asyncio
    import threading

    from portia import pipeline as pipeline_module

    app = App()
    app.root = Path("/nowhere")
    seen: list[tuple[int, str]] = []

    def fake_build_project(root, *, only=None, on_progress=None, stop=None):
        # Exactly what the real one does: call back from this thread.
        on_progress(pipeline_module.BuildProgress("m", 0, 1, "s", 0, 2))
        return []

    async def go():
        loop_thread = threading.get_ident()

        def note(progress):
            seen.append((threading.get_ident(), progress.step))

        original = engine_module.pipeline.build_project
        engine_module.pipeline.build_project = fake_build_project
        try:
            await engine_module.build(app, on_progress=note)
        finally:
            engine_module.pipeline.build_project = original
        # `call_soon_threadsafe` schedules; give the loop a turn to run it.
        await asyncio.sleep(0)
        return loop_thread

    loop_thread = asyncio.run(go())

    assert seen, "the progress callback never arrived"
    assert seen[0][0] == loop_thread, "progress was delivered on the builder's thread"
    assert seen[0][1] == "s"


def test_a_finished_run_stops_claiming_to_be_on_a_step(monkeypatch):
    """`progress` is a statement about a run in flight. Left set, the window
    would go on naming step four of a build that ended."""
    import asyncio
    import inspect
    from types import SimpleNamespace

    from portia.ui import app as app_module

    assert "APP.progress = None" in inspect.getsource(app_module._in_flight)

    quiet = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(app_module, "run_controls", quiet)
    monkeypatch.setattr(app_module.workflow, "pane", quiet)

    app = App()
    with _as_app(app_module, app):

        async def blow_up():
            async with app_module._in_flight(state.RUNNING):
                app.progress = state.Progress(model="m", step="s", steps_total=3)
                raise RuntimeError("the join failed")

        with pytest.raises(RuntimeError):
            asyncio.run(blow_up())

    assert app.progress is None


def test_the_model_count_is_drawn_only_when_it_can_move():
    """Run scopes to one spec *plus what it reads*, so a single-spec project
    would otherwise carry a permanent `model 1 of 1` beside the count that is
    actually changing."""
    from portia.ui import app as app_module

    alone = state.Progress(model="orders", models_done=0, models_total=1, steps_total=4)
    assert "model" not in app_module._where(alone)
    assert "step 1 of 4" in app_module._where(alone)

    several = state.Progress(model="orders", models_done=1, models_total=3, steps_total=4)
    assert "model 2 of 3" in app_module._where(several)


def test_progress_counts_read_as_positions_not_as_offsets():
    """`steps_done` is how many are finished; a human reads `step 1 of 4` for the
    first one, never `step 0 of 4`."""
    from portia.ui import app as app_module

    first = state.Progress(steps_done=0, steps_total=4)
    last = state.Progress(steps_done=3, steps_total=4)

    assert "step 1 of 4" in app_module._where(first)
    assert "step 4 of 4" in app_module._where(last)


def test_the_progress_line_offers_no_percentage_and_no_estimate():
    """Step costs differ by two orders of magnitude inside one spec (0.6s and
    47.6s sit next to each other in the demo project), so a bar or an ETA would
    be a measurement portia did not make. `DESIGN.md` -> `in-flight` reaches the
    same rule from the other side: nothing about the mark grows."""
    from portia.ui import app as app_module

    line = app_module._where(state.Progress(models_total=3, models_done=1, steps_total=10))

    assert "%" not in line
    assert not any(word in line.lower() for word in ("eta", "remaining", "left"))


def test_the_elapsed_clock_is_monotonic_and_survives_a_step_boundary():
    """It times the press, not the step: a per-step clock restarting at every
    boundary hides the one case worth seeing, which is a single step holding for
    most of the build."""
    import inspect

    from portia.ui import app as app_module

    assert "monotonic" in inspect.getsource(state.Progress)
    # `_advance` carries the start across rather than taking a fresh one.
    assert "APP.progress.started if APP.progress" in inspect.getsource(app_module._advance)


def test_the_elapsed_tick_does_nothing_while_the_window_is_idle(monkeypatch):
    """One predicate a second when nothing is running — the timer is registered
    for the page's life, so its resting cost is the thing to keep near zero."""
    from types import SimpleNamespace

    from portia.ui import app as app_module

    refreshed = []
    monkeypatch.setattr(
        app_module, "progress_note", SimpleNamespace(refresh=lambda: refreshed.append(1))
    )

    app = App()
    with _as_app(app_module, app):
        app_module.tick_progress()
        assert refreshed == []

        app.running = state.RUNNING
        app.progress = state.Progress(model="m", steps_total=3)
        app_module.tick_progress()
        assert refreshed == [1]


def test_the_mark_draws_the_word_the_step_and_where_it_is():
    """What the operator actually sees, drawn rather than computed: the word, the
    step id in mono because it is an identifier, and the counts beside it."""
    from portia.ui import app as app_module

    app = App()
    app.running = state.RUNNING
    app.progress = state.Progress(
        model="hotel_month",
        models_done=0,
        models_total=1,
        step="events_nearby_hotels",
        steps_done=5,
        steps_total=10,
    )

    with _as_app(app_module, app), ui.element("div") as slot:
        app_module.progress_note()

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert state.RUNNING in drawn
    assert "events_nearby_hotels" in drawn
    assert "step 6 of 10" in drawn
    assert "model" not in drawn  # one model in this run, so the count cannot move
    # The step id is an identifier, so it is mono and the prose around it is not.
    mono = [e for e in slot.descendants() if "t-mono" in " ".join(e.classes)]
    assert [str(e.text) for e in mono] == ["events_nearby_hotels"]


def test_the_mark_still_draws_before_the_first_callback_arrives():
    """The press sets `running` and the builder has not called back yet. The word
    alone is the pre-existing behaviour and has to survive."""
    from portia.ui import app as app_module

    app = App()
    app.running = state.RUNNING

    with _as_app(app_module, app), ui.element("div") as slot:
        app_module.progress_note()

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert state.RUNNING in drawn
    assert "step" not in drawn


def test_nothing_is_drawn_when_nothing_is_running():
    """No resting state to design: the mark exists exactly as long as the work."""
    from portia.ui import app as app_module

    with _as_app(app_module, App()), ui.element("div") as slot:
        app_module.progress_note()

    assert not [str(getattr(e, "text", "")) for e in slot.descendants() if getattr(e, "text", "")]


# --- stopping: the button, and what a stop leaves behind ----------------------


def test_stop_takes_runs_place_while_work_is_in_flight():
    """Same word and same rule as the composer: while work is in flight the one
    thing to do about it is stop it. Added *beside* the disabled Run it would
    widen the bar under the pointer that just pressed it."""
    from portia.core import cancel
    from portia.ui import app as app_module

    app = App()
    app.running = state.RUNNING
    app.stop = cancel.Scope()
    try:
        with _as_app(app_module, app), ui.element("div") as slot:
            app_module.run_controls()
        drawn = [str(getattr(e, "text", "")) for e in slot.descendants()]
    finally:
        app.stop.close()

    assert "Stop" in drawn
    assert "Run" not in drawn


def test_run_comes_back_once_there_is_nothing_to_stop():
    from portia.ui import app as app_module

    with _as_app(app_module, App()), ui.element("div") as slot:
        app_module.run_controls()

    drawn = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert "Run" in drawn and "Stop" not in drawn


def test_pressing_stop_with_nothing_running_does_nothing():
    """The button is drawn from state one refresh old, so the press that lands
    just after a run ends is an ordinary race, not an error."""
    from portia.ui import app as app_module

    with _as_app(app_module, App()):
        app_module._stop_run()  # does not raise


def test_a_stopped_run_leaves_no_error_behind(monkeypatch):
    """`Cancelled` is the answer to a button the human pressed. Reported in the
    run pane it would leave the app apologising, in red, for doing as it was
    told — and it would sit there until the next run."""
    import asyncio

    from portia.core import cancel
    from portia.ui import engine as engine_module

    app = App()
    app.root = Path("/nowhere")

    def build_that_was_stopped(root, *, only=None, on_progress=None, stop=None):
        raise cancel.Cancelled("stopped")

    monkeypatch.setattr(engine_module.pipeline, "build_project", build_that_was_stopped)

    built = asyncio.run(engine_module.execute(app))

    assert built == []
    assert app.run_error is None, "a stop is not a failure"
    assert app.results is None


def test_a_real_build_failure_still_lands_in_the_pane(monkeypatch):
    """The other half of the rule above — errors must not be swallowed with it."""
    import asyncio

    from portia.ui import engine as engine_module

    app = App()
    app.root = Path("/nowhere")

    def build_that_broke(root, *, only=None, on_progress=None, stop=None):
        raise ValueError("the join failed")

    monkeypatch.setattr(engine_module.pipeline, "build_project", build_that_broke)

    asyncio.run(engine_module.execute(app))

    assert app.run_error == "ValueError: the join failed"


def test_the_run_scope_is_closed_when_the_action_ends(monkeypatch):
    """A cancelled scope holds a thread that goes on interrupting connections the
    next run may reuse."""
    import asyncio
    from types import SimpleNamespace

    from portia.ui import app as app_module

    quiet = SimpleNamespace(refresh=lambda: None)
    monkeypatch.setattr(app_module, "run_controls", quiet)
    monkeypatch.setattr(app_module.workflow, "pane", quiet)

    app = App()
    held = []
    with _as_app(app_module, app):

        async def go():
            async with app_module._stoppable(state.RUNNING) as stop:
                held.append(stop)

        asyncio.run(go())

    assert held[0]._closed.is_set(), "the scope outlived its run"
    assert app.stop is None


# --- stopping: indexing ------------------------------------------------------


def test_indexing_stops_between_files_and_reports_what_it_profiled(monkeypatch):
    """Per-file hops are what make this cheap: a press always stops the *next*
    file, and what was already profiled is in the catalog and stays there."""
    import asyncio

    from portia.core import cancel
    from portia.ui import engine as engine_module

    app = App()
    app.portia_dir = "/nowhere/.portia"
    stop = cancel.Scope()
    paths = [Path(f"/data/f{i}.csv") for i in range(5)]
    profiled = []

    def fake_index_one(path, portia_dir, stop=None):
        # What the real one does once its profile is interrupted: `cancel.scope`
        # turns DuckDB's `InterruptException` into `Cancelled` on the way out.
        if stop is not None and stop.cancelled:
            raise cancel.Cancelled("stopped")
        profiled.append(path.stem)
        return path.stem

    monkeypatch.setattr(engine_module, "_index_one", fake_index_one)
    monkeypatch.setattr(engine_module, "refresh_catalog", lambda app: None)
    monkeypatch.setattr(engine_module, "sync_knowledge", lambda app: None)

    def say(done, total, name):
        if done == 2:
            stop.cancel()

    try:
        names = asyncio.run(engine_module.index(paths, app, on_progress=say, stop=stop)).names
    finally:
        stop.close()

    assert names == ["f0", "f1"], "it kept going past the press"
    assert profiled == ["f0", "f1"]


def test_only_the_files_that_were_profiled_stop_being_outstanding():
    """Retiring the rest because they were *selected* would leave the screen
    claiming a file had been read when nothing had read it."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._index_and_interpret)
    # What the engine says finished, by name. It was `paths[: len(names)]` until
    # a file could fail without ending the run, and a prefix stopped being true.
    assert "for p in done_paths" in source and "paths[: len(names)]" not in source.split('"""')[2]


def test_stopping_indexing_does_not_spend_money_on_interpretation():
    """Profiling is free and deterministic; interpretation is a model turn that
    costs money. Running one on whatever finished, immediately after the human
    asked the app to stop, is the app spending their money to disagree."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._index_and_interpret)
    assert "if stop.cancelled" in source
    assert source.index("if stop.cancelled") < source.index("_interpret_pending()")
    # The stop ends the waiting job rather than reading into it (2026-09-23).
    assert source.index("_abandon(job, STOPPED_NO_READ") < source.index("_interpret_pending()")


def test_indexing_offers_a_stop_while_it_is_indexing():
    """Twenty real extracts is a minute of profiling, and the point of a
    no-terminal app is that ^C is not the way out of it."""
    from portia.core import cancel
    from portia.ui import screens

    app = App()
    app.indexing_status = "Profiling bookings — 1 of 5"
    app.indexing_stop = cancel.Scope()
    try:
        with _as_app(screens, app), ui.element("div") as slot:
            screens._progress()
        drawn = [str(getattr(e, "text", "")) for e in slot.descendants()]
    finally:
        app.indexing_stop.close()

    assert "Stop" in drawn
    assert app.indexing_status in drawn


def test_indexing_shows_no_stop_when_nothing_is_indexing():
    from portia.ui import screens

    app = App()
    app.indexing_status = "Profiling bookings — 1 of 5"  # status without a scope

    with _as_app(screens, app), ui.element("div") as slot:
        screens._progress()

    assert "Stop" not in [str(getattr(e, "text", "")) for e in slot.descendants()]


def test_pressing_stop_on_indexing_with_nothing_running_does_nothing():
    from portia.ui import screens

    with _as_app(screens, App()):
        screens._stop_indexing()  # does not raise


def test_the_run_actions_report_through_the_client_not_the_ambient_slot():
    """**A pre-existing bug, fixed here because Stop depends on it.** Each action
    ends by refreshing `run_controls`, which deletes the button the handler is
    still standing in, so a plain `ui.notify` afterwards raises *The parent
    element this slot belongs to has been deleted* — swallowed into the log.
    Verified against the app: no "built 1 model(s)" ever reached the window.
    """
    import inspect

    from portia.ui import app as app_module

    for action in (app_module._run, app_module._write):
        source = inspect.getsource(action)
        assert "context.client" in source, f"{action.__name__} never captures its client"
        assert "ui.notify(" not in source, f"{action.__name__} notifies through the dead slot"

    assert "with client:" in inspect.getsource(app_module._say)


# --- a card is a table: the 2026-08-15 canvas --------------------------------


def _quiet_refreshes(app: App):
    """Silence the three refreshables a spec pick touches.

    They need a running NiceGUI loop, and what these tests are about is the state
    the handler leaves behind — the drawing is asserted separately by `_drawn`.
    """
    import contextlib

    from portia.ui import app as app_module
    from portia.ui import artifacts, workflow

    @contextlib.contextmanager
    def quiet():
        targets = [artifacts.pane, workflow.pane, app_module.run_controls]
        originals = [t.refresh for t in targets]
        for t in targets:
            t.refresh = lambda *a, **k: None
        try:
            yield
        finally:
            for t, original in zip(targets, originals, strict=True):
                t.refresh = original

    return quiet()


def _canvas_project(tmp_path: Path, *, index: bool = True) -> App:
    """Two specs, one reading the other, with a file each."""
    import yaml

    (tmp_path / "data").mkdir()
    (tmp_path / "specs").mkdir()
    pd.DataFrame({"id": [1, 2], "amount": [10, 20]}).to_csv(
        tmp_path / "data" / "orders.csv", index=False
    )
    pd.DataFrame({"id": [1, 2], "region": ["eu", "us"]}).to_csv(
        tmp_path / "data" / "regions.csv", index=False
    )
    (tmp_path / "specs" / "stg.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {"orders": "data/orders.csv"},
                "steps": [
                    {
                        "id": "clean",
                        "op": "sql",
                        "inputs": ["orders"],
                        "sql": "select id, amount from orders",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "specs" / "mart.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {"regions": "data/regions.csv"},
                "steps": [
                    {
                        "id": "joined",
                        "op": "sql",
                        "inputs": ["stg", "regions"],
                        "sql": (
                            "select stg.id, amount, region from stg "
                            "join regions on stg.id = regions.id"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = App(root=tmp_path)
    if index:
        for rel in ("data/orders.csv", "data/regions.csv"):
            catalog.index_source(tmp_path / rel, portia_dir=tmp_path / ".portia")
    engine_module.refresh_catalog(app)
    engine_module._LINEAGE = ((), {})
    engine_module.select_spec(tmp_path / "specs" / "mart.yaml", app)
    app.select(state.SPEC, "mart.yaml")
    app.expanded = frozenset({"mart"})
    return app


def _quiet_canvas(monkeypatch) -> None:
    """Stub the redraws a canvas press makes, which need a running page to reach.

    The pane's own is already stubbed by each test; these are the parts a press
    redraws in its place (`workflow._canvas_changed`, `_spec_changed`).
    """
    from portia.ui import app as app_module
    from portia.ui import workflow

    for part in (
        workflow._canvas,
        workflow._report,
        workflow._report_rail,
        app_module.run_controls,
    ):
        monkeypatch.setattr(part, "refresh", lambda *a, **k: None)


def _drawn(app: App):
    """Render the canvas half and hand back its elements, with a class helper."""
    from portia.ui import workflow

    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._graph_half()

    def klass(element) -> str:
        return " ".join(getattr(element, "classes", []) or [])

    return list(slot.descendants()), klass


def test_a_step_is_never_drawn_as_a_card(tmp_path, monkeypatch):
    """The whole of the 2026-08-15 change, at the surface: a spec's steps are rows
    inside its card, and nothing on the canvas is a step."""
    monkeypatch.chdir(tmp_path)
    els, klass = _drawn(_canvas_project(tmp_path))
    assert not [e for e in els if "step-card" in klass(e)]
    cards = [e for e in els if "model-card" in klass(e)]
    assert len(cards) == 2, "two specs, two cards — and nothing else is a card"


def test_an_open_card_lists_what_it_reads_and_how_it_is_built(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    els, klass = _drawn(_canvas_project(tmp_path))
    assert [e.text for e in els if "model-section" in klass(e)] == ["Reads", "Steps"]
    rows = [e.text for e in els if "model-row-name" in klass(e)]
    assert rows == ["stg", "regions", "joined"], "its two inputs, then its one step"


def test_the_reads_list_is_the_arrows_even_when_nothing_is_indexed(tmp_path, monkeypatch):
    """Column lineage needs the catalog; the arrows do not. Driving the list off
    the column walk made a table with two arrows into it draw an empty section
    that read as *this reads nothing*."""
    monkeypatch.chdir(tmp_path)
    els, klass = _drawn(_canvas_project(tmp_path, index=False))
    rows = [e.text for e in els if "model-row-name" in klass(e)]
    assert "stg" in rows and "regions" in rows


def test_unfolding_an_input_shows_which_columns_came_along_it(tmp_path, monkeypatch):
    """What an arrow *means*, and the answer comes off `knowledge.build`'s walk —
    the same one the knowledge graph is built from, with no database running."""
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.open_input = ("mart", "stg")
    els, klass = _drawn(app)
    pairs = [e for e in els if "model-column" in klass(e)]
    assert len(pairs) == 2, "id and amount arrived from stg"


def test_an_unindexed_input_says_so_rather_than_drawing_nothing(tmp_path, monkeypatch):
    """*Nobody profiled this* and *this contributed nothing* are different facts."""
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path, index=False)
    app.open_input = ("mart", "regions")
    els, klass = _drawn(app)
    assert not [e for e in els if "model-column" in klass(e)]
    assert [e.text for e in els if "model-note" in klass(e)]


def test_selecting_a_table_lights_its_ancestry_and_hides_nothing(tmp_path, monkeypatch):
    """Selecting highlights; narrowing is a separate, explicit control."""
    monkeypatch.chdir(tmp_path)
    els, klass = _drawn(_canvas_project(tmp_path))
    lit = {e for e in els if "graph-node--lit" in klass(e)}
    nodes = {e for e in els if "graph-node" in klass(e)}
    assert len(nodes) == 4, "two models and two files, all still drawn"
    assert len(lit) == 4, "mart, stg and both files feed the selected table"


def test_every_arrow_gets_a_hit_area_and_the_line_stays_a_hairline(tmp_path, monkeypatch):
    """A 1px bezier is not clickable, and widening it would make arrows shout."""
    monkeypatch.chdir(tmp_path)
    els, _ = _drawn(_canvas_project(tmp_path))
    svg = next(
        str(getattr(e, "content", "")) for e in els if "graph-hit" in str(getattr(e, "content", ""))
    )
    assert svg.count("graph-hit") == 3, "orders→stg, stg→mart, regions→mart"
    assert "stroke-width" not in svg, "widths belong in the stylesheet, not the markup"


def test_clicking_an_arrow_opens_its_target_at_that_input(tmp_path, monkeypatch):
    """The card click and the arrow click end on the same row of the same card."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.expanded = frozenset()
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow.open_edge("mart|stg")
    assert "mart" in app.expanded
    assert app.open_input == ("mart", "stg")


def test_hiding_a_table_takes_everything_built_from_it(tmp_path, monkeypatch):
    """**Descendants follow it out** (2026-08-16). `mart` is built from `stg`, so
    a canvas keeping `mart` after `stg` went would draw a card whose incoming
    arrow points at nothing — a wrong picture, not a shorter one."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow._toggle_visible("stg", False)
    assert app.visible == frozenset()
    assert graph.project_layout(engine_module.project_docs(app), visible=app.visible).empty


def test_hiding_a_leaf_leaves_what_it_was_built_from(tmp_path, monkeypatch):
    """The rule only runs downstream: nothing is built from `mart`, so hiding it
    takes nothing else, and `stg` stays because it was chosen too."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow._toggle_visible("mart", False)
    assert app.visible == frozenset({"stg"})
    els, klass = _drawn(app)
    assert {e.text for e in els if "model-name" in klass(e)} == {"stg"}


def test_select_all_is_a_toggle_between_everything_and_nothing(tmp_path, monkeypatch):
    """With every table already on there is nothing an "all on" press can do, so
    the useful press at that moment is the opposite one."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow._select_all(False)
        assert app.visible == frozenset(), "off is a set, and it is empty"
        workflow._select_all(True)
    assert app.visible is None, "on is *nothing chosen*, so a new table is drawn too"


def test_a_tick_in_the_table_filter_keeps_the_menu_and_redraws_the_canvas(tmp_path, monkeypatch):
    """Every tick redrew the whole middle pane, which rebuilt the button the
    menu hangs from, so the menu shut after each one (2026-09-23). The canvas
    redraws, and the menu's rows and the button's count are set in place."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    els, klass = _drawn(app)
    rows = {
        e.text: e
        for e in els
        if "menu-row-name" in klass(e) and "menu-row--head" not in klass(e.parent_slot.parent)
    }
    assert set(rows) >= {"stg", "mart"}

    def pane_redrawn(*a, **k):
        raise AssertionError("the whole middle pane was redrawn")

    canvases = []
    monkeypatch.setattr(workflow.pane, "refresh", pane_redrawn)
    monkeypatch.setattr(workflow._canvas, "refresh", lambda *a, **k: canvases.append(a))
    with _as_app(workflow, app):
        workflow._toggle_visible("mart", False)
    assert canvases, "the canvas redraws"
    mart = rows["mart"].parent_slot.parent
    assert "menu-row--on" not in mart.classes, "the row says off, in place"
    button = workflow._VIEW_MENU["button"]
    assert button.text == workflow._SHOWING.format(n=1, of=2)


def test_a_press_on_a_canvas_card_redraws_the_canvas_and_not_the_pane(tmp_path, monkeypatch):
    """A card press redrew the whole middle pane and the whole left pane
    (2026-09-23: 463 of 701 elements for one card opened, measured). The
    canvas's contents redraw, because an open card moves the grid; the report
    redraws when the press picked another spec; the tree only moves its wash."""
    import inspect

    from portia.ui import artifacts, workflow

    def body(fn) -> str:
        source = inspect.getsource(fn)
        return source.split('"""')[-1] if source.count('"""') >= 2 else source

    for fn in (
        workflow._open_model,
        workflow._select_step,
        workflow._open_input,
        workflow.open_edge,
        workflow.move_card,
        workflow.reset_layout,
        workflow._reveal_preview,
    ):
        code = body(fn)
        assert "artifacts.pane.refresh()" not in code, fn.__name__
        # `_select_step`'s one pane redraw is for a report whose slots are gone.
        if fn is not workflow._select_step:
            assert "pane.refresh()" not in code, fn.__name__
    assert "artifacts.show_selection()" in body(workflow._spec_changed)

    # Opening a card on the spec already open: the canvas, and nothing else.
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    _drawn(app)

    def redrawn(what):
        def fail(*a, **k):
            raise AssertionError(f"{what} was redrawn")

        return fail

    canvases = []
    monkeypatch.setattr(workflow.pane, "refresh", redrawn("the middle pane"))
    monkeypatch.setattr(artifacts.pane, "refresh", redrawn("the left pane"))
    monkeypatch.setattr(workflow._report, "refresh", redrawn("the report"))
    monkeypatch.setattr(workflow._canvas, "refresh", lambda *a, **k: canvases.append(1))
    app.spec_path = tmp_path / "specs" / "mart.yaml"
    with _as_app(workflow, app):
        workflow._open_model("mart")
    assert canvases == [1]


def test_the_left_pane_moves_its_wash_without_being_redrawn(tmp_path, monkeypatch):
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.select(state.SOURCE, "orders")
    with _as_app(artifacts, app), ui.element("div") as slot:
        artifacts.pane.func()
    rows = {
        e.text: e.parent_slot.parent.parent_slot.parent
        for e in slot.descendants()
        if isinstance(e, ui.label) and "artifact-name" in e.classes
    }
    assert "artifact-row--selected" in rows["orders.csv"].classes
    app.select(state.SOURCE, "regions")
    with _as_app(artifacts, app):
        artifacts.show_selection()
    assert "artifact-row--selected" not in rows["orders.csv"].classes
    assert "artifact-row--selected" in rows["regions.csv"].classes


def test_where_you_were_looking_is_remembered_per_project(tmp_path, monkeypatch):
    """In `prefs`, beside recents: where you last looked is about you, and is
    not a fact anyone else on the project should inherit through a commit."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow._toggle_visible("mart", False)
    assert engine_module.remembered_view(tmp_path) == frozenset({"stg"})
    assert engine_module.remembered_view(tmp_path / "elsewhere") is None, "never chosen"


def test_the_run_actions_are_drawn_by_the_canvas_not_beside_it():
    """Over a file preview or a replayed chat they are verbs with no object.

    Structural rather than a flag: a guard would live in one place and the nine
    handlers that change the selection would each have to remember to refresh a
    bar they do not otherwise touch — and one of them already didn't, which is how
    Run and Save ended up hanging over a source inspector.
    """
    import inspect

    from portia.ui import app as app_module
    from portia.ui import workflow

    assert "run_controls()" in inspect.getsource(workflow._workflow)
    assert "run_controls()" not in inspect.getsource(app_module._middle)
    assert "run_controls" not in inspect.getsource(app_module.toolbar.func)


def test_an_unread_step_is_no_longer_drawn_as_a_fact_on_the_row(tmp_path, monkeypatch):
    """**The `unused` chip went 2026-09-02** (`docs/FINDINGS.md` §7).

    Drawing it was right while an unread step arrived honestly: `record_step` was
    append-only and was the only way to ask the data a question, so a spec
    collected the questions somebody asked on the way to it. Both causes are gone
    — questions go to `query_data` and answers to `findings/`, and a superseded
    attempt is replaced rather than left behind — so an unread step is a bug, and
    `pipeline.unread_steps` reports it from `cli/build --check`.

    Every step is still listed. Nothing was hidden; a verdict was removed from a
    screen that has no goal to judge against."""
    import yaml

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    (tmp_path / "specs" / "mart.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {"regions": "data/regions.csv"},
                "steps": [
                    {
                        "id": "abandoned",
                        "op": "sql",
                        "inputs": ["regions"],
                        "sql": "select id from regions",
                    },
                    {
                        "id": "joined",
                        "op": "sql",
                        "inputs": ["stg", "regions"],
                        "sql": (
                            "select stg.id, amount, region from stg "
                            "join regions on stg.id = regions.id"
                        ),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    engine_module.select_spec(tmp_path / "specs" / "mart.yaml", app)
    els, klass = _drawn(app)

    rows = {
        e.text: e for e in els if "model-row-name" in klass(e) and e.text in ("abandoned", "joined")
    }
    assert set(rows) == {"abandoned", "joined"}, "both steps are still listed"
    chips = [str(e.text) for e in els if "type-chip" in klass(e)]
    assert "unused" not in chips
    assert chips.count("sql") == 2, "each step still says what op it is"

    # The fact did not disappear; it moved to where a bug of that shape belongs.
    from portia import pipeline

    assert pipeline.unread_steps(tmp_path) == {"mart": ["abandoned"]}


def test_the_report_can_be_shut_and_the_rail_names_what_it_would_show(tmp_path, monkeypatch):
    """A button, not only a drag: the report is the half you shut often, because
    opening a card is how you read the canvas and the canvas is what you want the
    room for."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    assert app.report_open, "the evidence is showing by default"

    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        workflow._close_report()
    assert app.report_open is False

    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._workflow()
    els = list(slot.descendants())
    rail = [e for e in els if "p-rail-bottom" in " ".join(getattr(e, "classes", []) or [])]
    assert rail, "a closed report leaves a rail, as a closed side pane does"
    assert any("mart.yaml" in str(getattr(e, "text", "")) for e in els), "the rail names the spec"

    with _as_app(workflow, app):
        workflow._open_report()
    assert app.report_open is True


# --- picking a spec: the light click and the heavy one ------------------------


def test_clicking_a_spec_the_canvas_already_draws_only_highlights(tmp_path, monkeypatch):
    """One gesture was doing three things: it opened the card, panned to it and
    selected it, whether or not you wanted any of that — so clicking down a list
    of specs to find one reshuffled the view on every row."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.expanded = frozenset()
    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts._open_spec(tmp_path / "specs" / "mart.yaml")

    assert app.spec_path.name == "mart.yaml", "it is selected, which is the highlight"
    assert app.expanded == frozenset(), "and nothing was unfolded"
    assert app.previewing is None, "nothing is missing, so there is nothing to ghost"


def test_clicking_a_spec_the_canvas_is_missing_ghosts_what_it_would_need(tmp_path, monkeypatch):
    """*What am I not looking at*, answered without changing what is drawn."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()  # everything off
    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts._open_spec(tmp_path / "specs" / "mart.yaml")

    assert app.previewing == "mart"
    assert app.visible == frozenset(), "a preview changes nothing about what is drawn"

    els, klass = _drawn(app)
    ghosts = {e.text for e in els if "model-name" in klass(e)}
    assert ghosts == {"mart", "stg"}, "the whole ancestry, as ghosts"
    assert all("model-card--ghost" in klass(e) for e in els if "model-card" in klass(e))


def test_a_ghost_is_never_drawn_open(tmp_path, monkeypatch):
    """It is not on the canvas, so there is nothing to read inside it."""
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()
    app.previewing = "mart"
    app.expanded = frozenset({"mart"})
    els, klass = _drawn(app)
    assert not [e for e in els if "model-card--open" in klass(e)]


def test_clicking_one_ghost_puts_all_of_them_on_the_canvas(tmp_path, monkeypatch):
    """The ghosts are one answer to one question, and adding them one at a time
    would mean four presses to see a picture only correct once all four are up."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()
    app.previewing = "mart"
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow._reveal_preview()

    assert app.previewing is None
    assert app.visible == frozenset({"mart", "stg"})


def test_double_clicking_a_spec_draws_it_opens_it_and_moves_to_it(tmp_path, monkeypatch):
    """Everything the single click deliberately withholds."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()
    app.expanded = frozenset()
    before = app.focus_token
    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts._reveal_spec(tmp_path / "specs" / "mart.yaml")

    assert app.visible == frozenset({"mart", "stg"}), "drawn"
    assert "mart" in app.expanded, "opened"
    assert app.focus_model == "mart" and app.focus_token > before, "moved to"
    assert app.previewing is None, "and the preview is spent"


def test_looking_at_something_else_clears_the_ghosts(tmp_path, monkeypatch):
    """The preview belongs to "I am looking at this spec" and dies with it."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.previewing = "mart"
    with _as_app(artifacts, app):
        artifacts.pane.refresh = lambda *a, **k: None
        artifacts._select(state.SOURCE, "orders.csv")
    assert app.previewing is None


def test_a_resolved_double_click_reveals_and_a_single_one_does_not(tmp_path, monkeypatch):
    """The gesture arrives already told apart (`assets/pick.js`), so Python only
    has to honour it. **Neither end could work it out.** The browser cannot: the
    first press refreshes the left pane, which replaces the row, so the presses
    land on two DOM nodes and no `dblclick` is dispatched. Nor can the server:
    acting on the first press moves the rows under a stationary cursor, and two
    presses 140ms apart at one position were measured hitting `stg_orders.yaml`
    and then `staging` — by the time it reaches Python it is about the wrong row.
    """
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()
    app.expanded = frozenset()

    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts.pick_spec("mart", reveal=False)
        assert app.previewing == "mart", "a single click is the light action"
        assert app.visible == frozenset(), "which changes nothing about what is drawn"

        artifacts.pick_spec("mart", reveal=True)

    assert app.visible == frozenset({"mart", "stg"}), "a double click reveals"
    assert "mart" in app.expanded and app.focus_model == "mart"


def test_a_double_click_works_without_selecting_the_spec_first(tmp_path, monkeypatch):
    """It used to need a single click first, because the second press of a real
    double click never arrived as one. A resolved double click stands alone."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()
    app.expanded = frozenset()
    assert app.spec_path is not None and app.spec_path.stem != "stg"

    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts.pick_spec("stg", reveal=True)

    assert app.visible == frozenset({"stg"})
    assert "stg" in app.expanded and app.focus_model == "stg"


def test_a_pick_of_a_spec_that_is_gone_does_nothing(tmp_path, monkeypatch):
    """The row was rendered from a tree that may be a moment behind the disk."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    before = (app.spec_path, app.visible, app.expanded)

    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts.pick_spec("deleted", reveal=True)

    assert (app.spec_path, app.visible, app.expanded) == before


def test_picking_two_different_specs_quickly_is_never_a_double_click(tmp_path, monkeypatch):
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.visible = frozenset()

    with _as_app(artifacts, app), _quiet_refreshes(app):
        artifacts._open_spec(tmp_path / "specs" / "stg.yaml")
        artifacts._open_spec(tmp_path / "specs" / "mart.yaml")

    assert app.visible == frozenset(), "two light actions, nothing revealed"
    assert app.previewing == "mart"


def test_a_row_never_registers_a_dblclick_handler(tmp_path):
    """Guarding the reason: a handler here would silently never fire."""
    import inspect

    body = inspect.getsource(c.artifact_row).split('"""')[2]
    assert 'row.on("dblclick"' not in body
    assert "on_dblclick" not in body


def test_the_lit_path_carries_its_hop_so_the_flow_has_a_direction(tmp_path, monkeypatch):
    """A static outline says *these are involved*; running it in hop order also
    says *the data travels this way*. Every lit element gets the same animation
    and only its phase differs, so this is direction and never rank."""
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    els, klass = _drawn(app)

    lit = [e for e in els if "graph-node--lit" in klass(e)]
    assert lit, "the selected table's path is lit"
    hops = [(getattr(e, "_style", None) or {}).get("--hop") for e in lit]
    assert all(h is not None for h in hops), f"each carries its distance, got {hops}"
    assert "0" in hops, "the table you picked is where the flow arrives"
    assert len(set(hops)) > 1, "and the path into it runs further away"


def test_the_destination_of_the_flow_does_not_pulse():
    """It is where the flow arrives, and a destination blinking along with the
    path reads as one more stop on it."""
    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    block = css[css.index(".graph-node--lit .model-card--selected") :][:200]
    assert "animation: none" in block


def test_the_flow_stops_for_anyone_who_asked_it_to():
    """The path still lights — the statement survives; only the motion goes."""
    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    # Every reduced-motion block, not the first: the theme previews in Settings
    # carry one of their own (2026-09-23) and sit earlier in the file.
    blocks = [chunk[:600] for chunk in css.split("prefers-reduced-motion")[1:]]
    for selector in (".graph-node--lit .model-card", ".graph-edges path.is-lit"):
        assert any(selector in block for block in blocks)


def test_a_spec_row_carries_its_name_for_the_client_to_read(tmp_path, monkeypatch):
    """`assets/pick.js` matches the two presses of a double click by **spec name**,
    not by element — the element is exactly what does not survive the refresh
    between them. Names are unique across a project by construction."""
    from portia.ui import artifacts

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.open_folders = frozenset({"specs"})

    with _as_app(artifacts, app), ui.element("div") as slot:
        artifacts.pane()

    picks = {e.props["data-spec"] for e in slot.descendants() if "data-spec" in (e.props or {})}
    assert picks == {"mart", "stg"}


def test_a_spec_row_has_no_server_click_handler(tmp_path, monkeypatch):
    """One path only. Wiring `on_click` as well would send the light action a
    second time on the second press of a double click."""
    import inspect

    from portia.ui import artifacts

    source = inspect.getsource(artifacts._file)
    assert "on_click=None if node.kind == SPEC" in source
    assert "pick=" in source


# --- folding a run of tool calls ---------------------------------------------
#
# Asked for after the 2026-08-31 runs, which drew 98 cards in the indexing pass
# and 35 in the chat, each in full. The three tests after the first are the
# rules that decide whether the fold is safe: it must never hide what you are
# waiting on, it must not shut itself under you as a run grows, and a write
# confirmation must stay out of it.


def _calls(*specs):
    """``(id, name)`` pairs as tool-call events, each with its result."""
    from portia.agent import events

    rows = []
    for call_id, name in specs:
        rows.append(events.Event(events.TOOL_CALL, {"id": call_id, "name": name, "input": {}}))
        rows.append(events.Event(events.TOOL_RESULT, {"id": call_id, "text": "{}"}))
    return rows


def _folded(rows, *, busy=False):
    from portia.ui import transcript
    from portia.ui.state import App

    with _as_app(transcript, App()), ui.element("div") as slot:
        transcript._rows(rows, busy=busy)
    return slot


def test_a_run_of_finished_calls_becomes_one_row():
    """The indexing pass's 46-call run, in miniature."""
    from portia.ui import transcript

    slot = _folded(_calls(*((str(i), "describe_source") for i in range(4))))

    heads = [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]
    assert len(heads) == 1
    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._N_CALLS.format(n=4) in drawn
    # Which tools, not just how many: a run of four `describe_source` calls is
    # the app reading a folder, and the count alone cannot say that.
    assert "describe_source ×4" in drawn


def test_two_is_a_run_and_one_is_not():
    """`GROUP_MIN`. "1 tool call" is a longer way of saying what the card says."""
    from portia.ui import transcript

    assert transcript.GROUP_MIN == 2
    lone = _folded(_calls(("1", "profile_source")))
    assert not [e for e in lone.descendants() if "tool-group" in getattr(e, "classes", [])]


def test_a_run_holding_a_live_call_stays_open():
    """Folding it would put `_tool_queue`'s whole job behind a click: a call
    still running or queued is where the loop is *right now*, and the pane must
    not hide the thing the human is waiting on."""
    rows = _calls(("1", "describe_source"), ("2", "describe_source"))
    rows += _calls(("3", "profile_source"))[:1]  # call, no result

    slot = _folded(rows, busy=True)

    assert not [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]


def test_a_run_holding_an_errored_call_stays_open():
    """A tool that failed is a fact the copilot is about to reason from."""
    from portia.agent import events

    rows = _calls(("1", "describe_source"), ("2", "describe_source"))
    rows.append(events.Event(events.TOOL_CALL, {"id": "3", "name": "profile_source", "input": {}}))
    rows.append(events.Event(events.TOOL_RESULT, {"id": "3", "text": "boom", "is_error": True}))

    slot = _folded(rows)

    assert not [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]


def test_a_write_waiting_on_the_human_breaks_the_run():
    """A `record_step` parked on a confirmation is the loop stopping for you.
    It arrives as a `Decision` in the stream and must never land inside a fold."""
    import asyncio

    from portia.agent import events

    rows = _calls(("1", "describe_source"), ("2", "describe_source"))
    loop = asyncio.new_event_loop()
    try:
        rows.append(
            Decision(events.APPROVAL, {"name": "record_step", "input": {}}, loop.create_future())
        )
        rows += _calls(("3", "profile_source"), ("4", "profile_source"))
        slot = _folded(rows)
    finally:
        loop.close()

    # Two runs of two, one either side of the decision — never one run of four.
    groups = [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]
    assert len(groups) == 2


def test_a_group_is_keyed_on_the_first_call_so_it_survives_the_run_growing():
    """Keyed on the count or the position, a group you opened at twenty-three
    cards becomes a different group when the twenty-fourth arrives — and shuts
    itself while you are reading it. That is the bug `c.disclosure` exists to
    prevent, arriving by a new route."""
    from portia.ui import transcript
    from portia.ui.state import App

    app = App()
    app.toggle_row("calls:1", True)

    def group_of(n):
        with _as_app(transcript, app), ui.element("div") as slot:
            transcript._rows(_calls(*((str(i), "describe_source") for i in range(1, n + 1))))
        return next(e for e in slot.descendants() if "tool-group" in getattr(e, "classes", []))

    assert group_of(3).value is True
    assert group_of(4).value is True  # still open, one card later


def test_a_replayed_log_folds_the_same_way_the_live_pane_does():
    """One set of renderers, live and replayed — or the window ends up with a
    second opinion about something already written down (`CLAUDE.md` → ui/)."""
    from portia import runlog
    from portia.ui import transcript
    from portia.ui.state import App

    run = runlog.Transcript(
        path=Path("replayed.jsonl"),
        header={"kind": runlog.CHAT},
        events=_calls(*((str(i), "describe_source") for i in range(4))),
    )
    with _as_app(transcript, App()), ui.element("div") as slot:
        transcript.replay(run)

    assert len([e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]) == 1


# --- folding the write confirmations too --------------------------------------
#
# Found by driving the real app rather than by a test: the calls folded and the
# confirmations did not, so a replayed indexing log showed one "22 tool calls"
# line followed by twenty-two `set_interpretation / allowed / payload` blocks.


def _writes(*specs):
    """``(name, allowed)`` pairs as the two events a logged write is made of."""
    from portia.agent import events

    rows = []
    for name, allowed in specs:
        rows.append(events.Event(events.APPROVAL, {"name": name, "input": {"x": 1}}))
        if allowed is not None:
            rows.append(events.Event(events.APPROVAL_RESULT, {"name": name, "allowed": allowed}))
    return rows


def _replayed(rows):
    from portia import runlog
    from portia.ui import transcript
    from portia.ui.state import App

    run = runlog.Transcript(
        path=Path("replayed.jsonl"), header={"kind": runlog.INDEXING}, events=rows
    )
    with _as_app(transcript, App()), ui.element("div") as slot:
        transcript.replay(run)
    return slot


def _groups(slot):
    return [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]


def test_a_run_of_allowed_writes_becomes_one_row():
    from portia.ui import transcript

    slot = _replayed(_writes(*(("set_interpretation", True) for _ in range(22))))

    assert len(_groups(slot)) == 1
    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._N_WRITES.format(n=22) in drawn
    assert "set_interpretation ×22" in drawn


def test_a_refused_write_keeps_its_own_row():
    """A refusal is one of the two outcomes worth reading, so it never folds."""
    slot = _replayed(_writes(("set_interpretation", True), ("record_step", False)))
    assert not _groups(slot)


def test_a_write_the_turn_died_on_keeps_its_own_row():
    """No result under it: the shape of an interrupted run, which
    `EVALUATION.md` cares about. Not the same fact as a refusal, and not one to
    put behind a click."""
    slot = _replayed(_writes(("set_interpretation", True), ("record_step", None)))
    assert not _groups(slot)


def test_a_pending_write_is_never_folded_in_a_live_stream():
    """The loop is stopped on a human. `DESIGN.md`'s write-confirm rule is about
    exactly this moment, and a decision behind a fold is a loop that looks hung."""
    import asyncio

    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App

    loop = asyncio.new_event_loop()
    try:
        done = [
            Decision(
                events.APPROVAL, {"name": "set_interpretation", "input": {}}, loop.create_future()
            )
            for _ in range(3)
        ]
        for d in done:
            d.resolved, d.outcome = True, True
        pending = Decision(
            events.APPROVAL, {"name": "record_step", "input": {}}, loop.create_future()
        )
        with _as_app(transcript, App()), ui.element("div") as slot:
            transcript._rows([*done, pending])
    finally:
        loop.close()

    assert len(_groups(slot)) == 1  # the three resolved ones, and not the pending one
    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._WRITE_TITLE in drawn  # the pending decision is still drawn in full


def test_the_live_stream_and_its_replay_fold_writes_the_same_way():
    """One set of renderers, or the window has a second opinion about a turn it
    already wrote down."""
    import asyncio

    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App

    loop = asyncio.new_event_loop()
    try:
        live = []
        for _ in range(4):
            d = Decision(
                events.APPROVAL,
                {"name": "set_interpretation", "input": {"x": 1}},
                loop.create_future(),
            )
            d.resolved, d.outcome = True, True
            live.append(d)
        with _as_app(transcript, App()), ui.element("div") as slot:
            transcript._rows(live)
    finally:
        loop.close()

    replayed = _replayed(_writes(*(("set_interpretation", True) for _ in range(4))))

    def head(rendered):
        named = (e for e in rendered.descendants() if "tool-card-name" in getattr(e, "classes", []))
        return str(next(named).text)

    assert head(slot) == head(replayed) == transcript._N_WRITES.format(n=4)


# --- writes that never stopped ------------------------------------------------
#
# J2. The read/write line is drawn already; this is the line inside the writes.


def test_the_switchable_writes_are_the_catalog_ones_and_only_those():
    """`record_step` runs the op and *then* writes the step, so switching its
    confirmation off is switching off the only look anyone gets at a step before
    it exists. That is a fact about the engine, not a preference."""
    assert state.AUTO_ALLOWABLE == ("set_interpretation", "set_group")
    assert "record_step" not in state.AUTO_ALLOWABLE


def test_every_write_stops_until_someone_says_otherwise():
    assert App().auto_allow == frozenset()


def test_the_window_decides_per_tool_at_the_moment_of_the_call():
    """Read live rather than captured at connect: `Conversation` holds one client
    across exchanges, so a preference frozen when the chat opened would be one
    you have to end the chat to change."""
    from portia.ui import exchange

    app = App()
    with _as_app(exchange, app):
        assert exchange.auto_allow("mcp__portia__set_interpretation") is False
        app.auto_allow = frozenset({"set_interpretation"})
        assert exchange.auto_allow("mcp__portia__set_interpretation") is True
        assert exchange.auto_allow("mcp__portia__record_step") is False


# --- autopilot (`docs/CONVERSATION.md` §14) ----------------------------------


def test_autopilot_is_off_until_somebody_turns_it_on():
    """A mode you can be in without having chosen to be is the one thing this
    design cannot afford, so it starts off and is not persisted."""
    assert App().autopilot is False


def test_autopilot_lets_every_write_through_including_recording_a_step():
    from portia.ui import exchange

    app = App()
    with _as_app(exchange, app):
        assert exchange.auto_allow("mcp__portia__record_step") is False
        app.autopilot = True
        assert exchange.auto_allow("mcp__portia__record_step") is True
        assert exchange.auto_allow("mcp__portia__set_interpretation") is True
        # Anything at all, including a tool portia did not put on the list.
        assert exchange.auto_allow("mcp__whatever__write") is True


def test_autopilot_is_read_live_so_it_can_be_switched_off_mid_run():
    """The direction that matters. `Conversation` holds one client across
    exchanges, so a mode captured at connect time would be one you have to end
    the chat to escape."""
    from portia.ui import exchange

    app = App()
    with _as_app(exchange, app):
        app.autopilot = True
        assert exchange.auto_allow("mcp__portia__record_step") is True
        app.autopilot = False
        assert exchange.auto_allow("mcp__portia__record_step") is False


def test_autopilot_does_not_put_record_step_in_the_switchable_set():
    """It is a mode, not a third entry. The set stays two names — those are
    preferences you set once and forget, and this is one you turn on for a task
    you are watching."""
    assert state.AUTO_ALLOWABLE == ("set_interpretation", "set_group")


def test_an_automatic_approval_stays_in_the_live_stream():
    """No `Decision` is ever made for one, because the loop did not stop — so
    the event is the panel's only record that a durable write happened.
    Dropping it as `_OWNED` lost the row entirely."""
    from portia.agent import events
    from portia.ui import exchange

    asked = events.approval_event("mcp__portia__set_interpretation", {"source": "g"}, auto=True)
    stopped = events.approval_event("mcp__portia__record_step", {"spec_path": "x"})
    assert exchange._owned(asked) is False
    assert exchange._owned(stopped) is True


def test_an_automatic_write_says_so_rather_than_saying_allowed():
    """Nobody allowed it. `allowed` would report a decision that was not made."""
    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    rows = [
        events.approval_event("mcp__portia__set_interpretation", {"source": "g"}, auto=True),
        events.approval_result_event("mcp__portia__set_interpretation", True, auto=True),
    ]
    with _as_app(transcript, _App()), ui.element("div") as slot:
        transcript._rows(rows)

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._ALLOWED_AUTO in drawn


def test_a_run_of_automatic_writes_folds_and_says_which_kind():
    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    rows = []
    for _ in range(5):
        rows.append(events.approval_event("mcp__portia__set_interpretation", {"s": 1}, auto=True))
        rows.append(
            events.approval_result_event("mcp__portia__set_interpretation", True, auto=True)
        )
    with _as_app(transcript, _App()), ui.element("div") as slot:
        transcript._rows(rows)

    groups = [e for e in slot.descendants() if "tool-group" in getattr(e, "classes", [])]
    assert len(groups) == 1
    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._N_WRITES.format(n=5) in drawn
    assert transcript._ALLOWED_AUTO in drawn
    assert "allowed" != drawn.strip()


def test_a_mixed_run_makes_the_weaker_claim():
    """One decided and four automatic is not five allowed."""
    import asyncio

    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    loop = asyncio.new_event_loop()
    try:
        decided = Decision(
            events.APPROVAL, {"name": "mcp__portia__set_group", "input": {}}, loop.create_future()
        )
        decided.resolved, decided.outcome = True, True
        rows = [decided]
        for _ in range(4):
            rows.append(events.approval_event("mcp__portia__set_interpretation", {}, auto=True))
            rows.append(
                events.approval_result_event("mcp__portia__set_interpretation", True, auto=True)
            )
        with _as_app(transcript, _App()), ui.element("div") as slot:
            transcript._rows(rows)
    finally:
        loop.close()

    heads = [str(e.text) for e in slot.descendants() if "tool-state" in getattr(e, "classes", [])]
    assert transcript._ALLOWED in heads
    assert transcript._ALLOWED_AUTO not in heads


def test_allow_and_stop_asking_is_offered_only_where_it_can_be_honoured():
    """A disabled control reads as something you have not earned yet rather than
    as something the engine cannot do, so `record_step` simply does not offer it."""
    import asyncio

    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    loop = asyncio.new_event_loop()
    try:

        def drawn_for(tool):
            d = Decision(
                events.APPROVAL, {"name": f"mcp__portia__{tool}", "input": {}}, loop.create_future()
            )
            with _as_app(transcript, _App()), ui.element("div") as slot:
                transcript._rows([d])
            return " ".join(str(getattr(e, "text", "")) for e in slot.descendants())

        catalog_write = drawn_for("set_interpretation")
        step_write = drawn_for("record_step")
    finally:
        loop.close()

    what = state.WRITE_LABELS["set_interpretation"]
    assert transcript._ALLOW_ALWAYS.format(what=what) in catalog_write
    for label in state.WRITE_LABELS.values():
        assert transcript._ALLOW_ALWAYS.format(what=label) not in step_write
    # Autopilot is offered on every write: it is the mode you turn on from the
    # card where you found out you wanted it.
    assert transcript._ALLOW_ALL in catalog_write
    assert transcript._ALLOW_ALL in step_write


def test_stopping_asking_records_the_tool_and_allows_this_one(monkeypatch):
    """Two places to change one setting, and never two settings: the panel holds
    it, and this changes it from where the question is being asked."""
    import asyncio

    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    resolved: list[tuple] = []
    monkeypatch.setattr(transcript, "_resolve_write", lambda d, ok: resolved.append((d, ok)))

    app = _App()
    loop = asyncio.new_event_loop()
    try:
        pending = Decision(
            events.APPROVAL,
            {"name": "mcp__portia__set_interpretation", "input": {}},
            loop.create_future(),
        )
        with _as_app(transcript, app):
            transcript._allow_always(pending)
    finally:
        loop.close()

    assert app.auto_allow == frozenset({"set_interpretation"})
    assert resolved == [(pending, True)]


def test_stopping_asking_does_not_reach_back_over_what_was_already_decided():
    """The writes above it were decisions somebody made. Re-labelling them
    automatic would put a claim in the transcript that the log does not make, so
    `auto` is read off each event and never off the setting as it stands now."""
    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App as _App

    app = _App()
    app.auto_allow = frozenset({"set_interpretation"})  # switched on *after* the fact
    decided = [
        events.approval_event("mcp__portia__set_interpretation", {"source": "g"}),
        events.approval_result_event("mcp__portia__set_interpretation", True, 44.0),
    ]
    with _as_app(transcript, app):
        slot = _replayed(decided)

    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert "allowed" in drawn
    assert transcript._ALLOWED_AUTO not in drawn


def test_a_replayed_automatic_write_says_so():
    from portia.agent import events
    from portia.ui import transcript

    slot = _replayed(
        [
            events.approval_event("mcp__portia__set_interpretation", {"source": "g"}, auto=True),
            events.approval_result_event("mcp__portia__set_interpretation", True, auto=True),
        ]
    )
    drawn = " ".join(str(getattr(e, "text", "")) for e in slot.descendants())
    assert transcript._ALLOWED_AUTO in drawn


# --- the pulse keeps its place across a rebuild -------------------------------
#
# J3. NiceGUI replaces a refreshable's elements, so the app's one piece of
# motion was restarting at full opacity several times a second — not a slow
# animation, but no animation, restarted.


def test_a_pulse_starts_partway_through_its_beat():
    """Always negative, so it begins already underway rather than waiting."""
    from portia.ui import components

    phase = components.pulse_phase()
    assert phase.startswith("--pulse-phase: -")
    seconds = float(phase.split(": ")[1].rstrip("s"))
    assert -components.PULSE_PERIOD <= seconds <= 0


def test_two_pulses_built_a_moment_apart_are_a_moment_apart():
    """The whole mechanism: a dot built later resumes where its predecessor was,
    rather than both starting at 0%."""
    import time

    from portia.ui import components

    first = components.pulse_phase()
    time.sleep(0.05)
    second = components.pulse_phase()
    assert first != second


def test_every_pulse_in_the_app_is_given_a_phase():
    """A bare `classes("tool-pulse")` is the bug coming back, so nothing outside
    `components.pulse` may create one. The same shape of guard as
    `tests/test_agent_prompts.py` uses for inline prompt text."""
    import portia.ui

    ui_dir = Path(portia.ui.__file__).parent
    offenders = [
        f"{path.name}:{n}"
        for path in ui_dir.glob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if 'classes("tool-pulse")' in line and path.name != "components.py"
    ]
    assert offenders == []


def test_the_pulse_period_matches_the_stylesheet():
    """The phase is computed in Python and the animation runs in CSS. A period
    that only matches by memory is one that drifts the first time either moves."""
    import portia.ui
    from portia.ui import components

    css = (Path(portia.ui.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    assert f"animation: p-pulse {components.PULSE_PERIOD}s" in css


def test_a_live_thinking_row_carries_the_phase_and_a_finished_one_does_not_need_to():
    """This row rebuilds on every event of a live exchange, which is what made
    its ellipsis the twitch that got reported first."""
    from portia.ui import transcript
    from portia.ui.state import App as _App

    with _as_app(transcript, _App()), ui.element("div") as slot:
        transcript._thinking_row("thinking about it", key="k", live=True)

    dots = next(e for e in slot.descendants() if "thinking-dots" in getattr(e, "classes", []))
    # Stated once on the container; custom properties inherit, so each of the
    # three dots subtracts its own stagger from it in CSS.
    assert dots._style["--pulse-phase"].startswith("-")
    assert dots._style["--pulse-phase"].endswith("s")


def test_the_report_carries_this_models_journal(tmp_path, monkeypatch):
    """*Why does this pipeline look like this* — the human half of `findings.py`.

    In the report region rather than on the canvas card, and that is a layout
    fact worth pinning: an open card's height is *counted* by `_body_rows` rather
    than measured, so a section of unpredictable length would clip its own last
    rows. The report region already scrolls.
    """
    from portia import findings

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)

    findings.record(
        question="does binning hotels to a 15km grid work?",
        answer="no — 574 in one Paris cell against 52 localities",
        so="matched on division centroids instead",
        about=["orders"],
        queries=[],
        spec_name="stg",
        root=tmp_path,
        portia_dir=tmp_path / ".portia",
    )

    engine_module.select_spec(tmp_path / "specs" / "stg.yaml", app)
    # Folded twice: the journal open shows the question, the question open
    # shows the record (the 2026-09-07 tests further down cover each fold).
    from portia import findings as findings_module
    from portia.ui import workflow

    path = findings_module.load_all(tmp_path)[0]["path"]
    app.open_rows = frozenset({"journal:stg", f"finding:{path}"})
    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._report_half()
    text = " ".join(str(e.text) for e in slot.descendants() if getattr(e, "text", None))

    assert "does binning hotels to a 15km grid work?" in text
    assert "matched on division centroids instead" in text


# --- the approval controls, revised (`docs/CONVERSATION.md` §14.5) -----------


def test_the_composer_states_the_mode_whichever_it_is():
    """A chip drawn only while autopilot was on said nothing about the normal
    case. A picker names the mode you are in, on or off."""
    from portia.ui import components as c
    from portia.ui.state import App as _App

    for on in (False, True):
        app = _App()
        app.autopilot = on
        with ui.element("div"):
            select = c.approval_mode(app)
        assert select.value == (state.AUTOPILOT if on else state.ASK)
        assert set(select.options) == set(state.MODES)


def test_the_picker_and_the_flag_are_one_setting():
    app = App()
    app.set_mode(state.AUTOPILOT)
    assert app.autopilot is True and app.mode == state.AUTOPILOT
    app.set_mode(state.ASK)
    assert app.autopilot is False and app.mode == state.ASK
    app.set_mode("something else")
    assert app.autopilot is False, "an unknown word changes nothing"


def test_allowing_every_write_from_the_card_turns_autopilot_on(monkeypatch):
    """The card is where somebody finds out they want it."""
    import asyncio

    from portia.agent import events
    from portia.ui import settings, transcript
    from portia.ui.state import App as _App

    resolved: list[tuple] = []
    monkeypatch.setattr(transcript, "_resolve_write", lambda d, ok: resolved.append((d, ok)))
    monkeypatch.setattr(settings, "refresh_if_open", lambda: None)

    app = _App()
    loop = asyncio.new_event_loop()
    try:
        pending = Decision(
            events.APPROVAL, {"name": "mcp__portia__record_step", "input": {}}, loop.create_future()
        )
        with _as_app(transcript, app):
            transcript._allow_all(pending)
    finally:
        loop.close()

    assert app.autopilot is True
    assert resolved == [(pending, True)]


def test_every_switchable_write_is_labelled_by_what_it_writes():
    """A switch reading `set_interpretation` is a tool name where a person
    expected a sentence."""
    assert set(state.WRITE_LABELS) == set(state.AUTO_ALLOWABLE)
    for label in state.WRITE_LABELS.values():
        assert "_" not in label


def test_the_settings_panel_carries_no_sentence_under_a_setting():
    """The user went through the panel line by line (2026-09-23) and deleted
    each description or moved it into a `help_tip`. A `_WHY` constant is the
    shape one took; a new one belongs in ``help=`` or nowhere."""
    import inspect

    from portia.ui import feedback, settings

    assert not [name for name in vars(settings) if name.endswith("_WHY")]
    assert "NOTHING_SENT" not in vars(feedback)
    source = inspect.getsource(settings)
    assert "c.setting(BRIEF_WHAT, help=BRIEF_HELP)" in source


def test_the_catalog_switches_wait_behind_customize(monkeypatch):
    """Folded by default, and a redraw of the section does not fold them back."""
    from portia.ui import settings

    monkeypatch.setattr(settings._section, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(settings, "_CUSTOMIZING", False)
    settings._toggle_customize()
    assert settings._CUSTOMIZING is True
    settings._toggle_customize()
    assert settings._CUSTOMIZING is False


def test_where_the_data_lives_cannot_be_changed_from_settings_once_pinned(monkeypatch):
    """A project reads from one place (`engine.can_change_data`): the other side
    of the Data section's choice is disabled once a source is in, and a press
    that reaches `_choose_data` anyway changes nothing."""
    from portia.ui import settings
    from portia.ui.state import APP

    chosen = []
    monkeypatch.setattr(settings.engine, "can_change_data", lambda app: False)
    monkeypatch.setattr(settings.engine, "choose_data", lambda mode, app: chosen.append(mode))
    monkeypatch.setattr(APP, "data_mode", state.LOCAL_DATA)

    settings._choose_data(state.WAREHOUSE_DATA)

    assert chosen == []
    assert APP.data_mode == state.LOCAL_DATA


def test_the_theme_previews_restate_the_tokens_they_draw():
    """A light card stays light in dark mode, so its palette cannot read the
    tokens and restates them. Each restated value is held to its token here."""
    import re

    from portia.ui import theme

    css = theme.CSS.read_text(encoding="utf-8")

    def block(selector):
        found = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
        assert found, f"{selector} is not styled"
        return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", found.group(1)))

    pairs = {
        "--tp-canvas": "--canvas",
        "--tp-surface": "--surface",
        "--tp-hairline": "--hairline",
        "--tp-ink": "--ink",
        "--tp-stone": "--stone",
        "--tp-accent": "--accent-text",
    }
    for preview, tokens in ((".tp-light", ":root"), (".tp-dark", "body.body--dark")):
        restated, real = block(preview), block(tokens)
        for mine, theirs in pairs.items():
            assert restated[mine] == real[theirs], (preview, mine)
    assert settings_spider_exists()


def settings_spider_exists():
    from portia.ui import settings

    return settings.SPIDER.is_file() and "currentColor" in settings.SPIDER.read_text(
        encoding="utf-8"
    )


def test_notes_are_drawn_dated_in_the_order_they_were_learned():
    """`docs/COPILOT.md` §2: a note is a sentence from one chat that the next has to see."""
    from portia.ui import workflow
    from portia.ui.state import App

    entry = {
        "summary": "A read.",
        "notes": [
            {"text": "outcome is a letter grade, not pass/fail", "at": "2026-09-04T09:10:00+02:00"},
            {"text": "GRADE is null on 51% of rows", "at": "2026-09-05T08:00:00+02:00"},
        ],
    }

    with _as_app(workflow, App()), ui.element("div") as slot:
        workflow._notes(entry)

    drawn = [str(getattr(e, "text", "")) for e in slot.descendants() if getattr(e, "text", "")]
    assert drawn == [
        "2026-09-04",
        "outcome is a letter grade, not pass/fail",
        "2026-09-05",
        "GRADE is null on 51% of rows",
    ]


# --- motion: arrival is keyed, and a refresh needs a reason (2026-09-05) ------


def test_the_enter_duration_matches_motion_js():
    """The animation runs in CSS and the class is removed by a timeout in
    `motion.js`. A pair that only matches by memory drifts the first time
    either moves — the pulse-period test's argument, one file over."""
    import portia.ui

    assets = Path(portia.ui.__file__).parent / "assets"
    assert "animation: p-enter 220ms" in (assets / "portia.css").read_text(encoding="utf-8")
    assert "DURATION_MS = 220" in (assets / "motion.js").read_text(encoding="utf-8")


def test_an_enter_key_survives_any_name():
    """A chart is named by a sentence and a folder by a person, so a key can
    hold both quote kinds at once — the one case `prop_value` refuses to
    serve. Folding beats quoting here because a key is never read back."""
    both = c._enter_key('figure:the "big" one\'s chart')
    assert '"' not in both and "'" not in both and " " not in both
    # A machine id passes through readable, which is what makes keys debuggable.
    assert c._enter_key("call:toolu_abc-1.2/x") == "call:toolu_abc-1.2/x"


def test_a_marked_element_states_its_key_in_the_dom():
    """`motion.js` reads `data-enter` off the element; nothing round-trips."""
    with ui.element("div"):
        el = c.enters(ui.element("div"), "tree:data/orders.csv")
        slot = c.enter_slot("chat:1:queue")
    assert el._props["data-enter"] == "tree:data/orders.csv"
    assert slot._props["data-enter"] == "chat:1:queue"
    assert "p-enter" in slot.classes


def test_a_tool_card_enters_under_its_call_id():
    """The one key that survives everything a run does to a card — the state
    moving, the position sliding as earlier rows fold, the pane rebuilding."""
    from portia.ui import transcript

    call = {"id": "toolu_1", "name": "profile_source", "input": {}}
    with _as_app(transcript, App()), ui.element("div") as slot:
        transcript._tool_cards([(call, None, transcript.DONE, None)])
    keys = [
        e._props["data-enter"]
        for e in slot.descendants()
        if getattr(e, "_props", {}).get("data-enter")
    ]
    assert keys == ["call:toolu_1"]


def test_every_chat_keys_its_rows_by_its_own_identity():
    """`chat:generation` is a row's entrance key, so one chat's row zero must
    not inherit another's — it would arrive as *seen* and never animate. A
    follow-up appends and keeps the generation."""
    from portia.ui import transcript

    app = App()
    first = app.start_exchange("one", model="m", effort=None)
    first.exchange.running = False
    second = app.new_chat()
    assert first.key != second.key
    assert transcript._key_prefix(first) != transcript._key_prefix(second)
    before = transcript._key_prefix(first)
    app.start_exchange("two", model="m", effort=None, chat=first)
    assert transcript._key_prefix(first) == before


def test_the_artifact_stamp_holds_still_unless_something_was_written(tmp_path):
    """`exchange._sync_artifacts` redraws three panes only when this moves.
    A stamp that stirred on a growing chat log would move on every event of
    the exchange doing the asking — exactly the refreshes it exists to
    prevent — while a *new* log is a new row in the left pane and must move it."""
    import os

    app = App()
    app.root = tmp_path
    figure = tmp_path / "figures" / "split.json"
    figure.parent.mkdir()
    figure.write_text("{}", encoding="utf-8")

    first = engine_module.artifact_stamp(app)
    assert first == engine_module.artifact_stamp(app)

    os.utime(figure, (1, 1))
    touched = engine_module.artifact_stamp(app)
    assert touched != first

    log = tmp_path / ".portia" / "chats" / "chat.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("one\n", encoding="utf-8")
    with_log = engine_module.artifact_stamp(app)
    assert with_log != touched
    log.write_text("one\ntwo\n", encoding="utf-8")
    assert engine_module.artifact_stamp(app) == with_log


# --- the composer's send shortcut --------------------------------------------


def _listener_parts(combination: str) -> dict:
    """What NiceGUI's client will make of one of `transcript.SEND_KEYS`.

    Parsed by NiceGUI's own `EventListener`, never by a copy of its rules here:
    the whole point of the assertions below is that the browser reads these
    strings the way the composer means them.
    """
    from nicegui.event_listener import EventListener

    return EventListener(
        element_id=0,
        type=combination,
        args=[],
        handler=lambda: None,
        js_handler=None,
        throttle=0.0,
        leading_events=True,
        trailing_events=True,
        request=None,
    ).to_dict()


def test_the_composer_sends_on_a_modifier_and_never_on_a_bare_enter():
    """Enter writes a newline — a goal is a paragraph. Both modifiers are bound
    because the composer is the same box on every platform."""
    from portia.ui import transcript

    modifiers = {m for k in transcript.SEND_KEYS for m in _listener_parts(k)["modifiers"]}
    assert {"meta", "ctrl"} <= modifiers
    for combination in transcript.SEND_KEYS:
        assert _listener_parts(combination)["keys"] == ["enter"]


def test_the_send_shortcut_never_swallows_a_newline():
    """Vue reads modifiers left to right and stops at the first that does not
    match, so the modifier has to be named before `prevent`. The other order
    calls `preventDefault` on every Enter in the box, and Enter is how a
    paragraph gets written."""
    from portia.ui import transcript

    for combination in transcript.SEND_KEYS:
        modifiers = _listener_parts(combination)["modifiers"]
        assert "prevent" in modifiers
        assert modifiers.index("prevent") == len(modifiers) - 1


def test_the_shortcut_sends_the_text_the_keystroke_carried(monkeypatch):
    """Measured in a browser, 2026-09-06: Quasar defers `update:model-value`
    through a `setTimeout`, so a keydown handled in the same tick as the input
    before it read an empty `APP.goal` and sent nothing. The keystroke brings
    its own text, so the two are no longer racing."""
    import asyncio

    from portia.ui import transcript

    sent = []

    async def _start(goal, **kwargs):
        sent.append(goal)

    app = App()
    monkeypatch.setattr(transcript, "APP", app)
    monkeypatch.setattr(transcript.exchange_driver, "start", _start)

    event = SimpleNamespace(args="two lines\nand the last word")
    asyncio.run(transcript._go_from_key(event))

    assert sent == ["two lines\nand the last word"]
    assert app.goal == ""


def test_the_shortcut_obeys_the_same_send_rule_as_the_button(monkeypatch):
    """`docs/CONVERSATION.md` §7: there is no queue, and §7.1 is not an
    exception to it. On a busy composer the keystroke does what the dark button
    does, which is nothing — the guard is `_go`'s, shared, not a second copy."""
    import asyncio

    from portia.ui import transcript

    sent = []

    async def _start(goal, **kwargs):
        sent.append(goal)

    app = App()
    app.start_exchange("something the copilot is still working on", model="m", effort=None)
    assert app.busy
    monkeypatch.setattr(transcript, "APP", app)
    monkeypatch.setattr(transcript.exchange_driver, "start", _start)

    asyncio.run(transcript._go_from_key(SimpleNamespace(args="while it works")))

    assert sent == []
    # The text stays in the box: a message that will not go must not vanish.
    assert app.goal == "while it works"


# --- the clock on a tool call (2026-09-06) ------------------------------------
#
# A three-minute BigQuery profile with nothing moving on screen read as a hang.
# The clock is the window's own measurement, small, in the state slot, on every
# tool call — chat and indexing alike.


def _clocks(slot):
    return [str(e.text) for e in slot.descendants() if "tool-clock" in getattr(e, "classes", [])]


def test_a_running_call_carries_a_ticking_clock_and_a_call_without_a_timing_does_not():
    from portia.ui import transcript

    with ui.element("div") as slot:
        transcript._tool_state(
            transcript.RUNNING, None, state.Timing(started=time.monotonic() - 12)
        )
    assert _clocks(slot) == ["12s"]

    with ui.element("div") as bare:
        transcript._tool_state(transcript.RUNNING, None, None)
    assert _clocks(bare) == []


def test_a_finished_call_says_what_it_took_beside_how_much_came_back():
    from portia.ui import transcript

    with ui.element("div") as slot:
        transcript._tool_state(transcript.DONE, {"text": "x" * 40}, state.Timing.of(91.2))
    texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert "40 chars" in texts and _clocks(slot) == ["1m 31s"]

    # An errored call took time too, and says so in the same slot.
    with ui.element("div") as errored:
        transcript._tool_state(transcript.ERRORED, {"is_error": True}, state.Timing.of(3.4))
    assert _clocks(errored) == ["3.4s"]


def test_the_clock_is_a_count_and_the_tick_only_runs_while_something_is_busy(monkeypatch):
    from portia.ui import transcript

    refreshed = []
    monkeypatch.setattr(transcript.tool_clock, "refresh", lambda: refreshed.append(1))
    with _as_app(transcript, App()):
        transcript.tick_clocks()
        assert refreshed == []


def test_the_window_clocks_a_call_as_it_goes_out_and_stops_it_at_the_result():
    from portia.agent import events
    from portia.ui import exchange

    stream = state.Chat()
    exchange._clock(events.Event(events.TOOL_CALL, {"id": "a", "name": "x", "input": {}}), stream)
    assert stream.timings["a"].ended is None and stream.timings["a"].elapsed >= 0
    exchange._clock(events.Event(events.TOOL_RESULT, {"id": "a", "text": "{}"}), stream)
    assert stream.timings["a"].ended is not None
    # A result for a call the window never saw go out is not a clock.
    exchange._clock(events.Event(events.TOOL_RESULT, {"id": "zz", "text": "{}"}), stream)
    assert "zz" not in stream.timings


def test_a_new_job_starts_with_no_clocks_from_the_last_one():
    app = App()
    stream = app.start_exchange("x", model="m", effort=None, kind=state.INDEXING)
    stream.timings["old"] = state.Timing.of(5)
    stream.exchange.running = False
    second = app.start_exchange("y", model="m", effort=None, kind=state.INDEXING)
    assert second is not stream and second.timings == {}, "a job is its own chat"


def test_a_replayed_call_shows_the_time_the_log_stamped():
    from portia import runlog
    from portia.agent import events

    run = runlog.Transcript(
        path=Path("replayed.jsonl"),
        header={"kind": runlog.CHAT},
        events=[
            events.Event(events.TOOL_CALL, {"id": "a", "name": "profile_source", "input": {}}),
            events.Event(events.TOOL_RESULT, {"id": "a", "text": "{}"}),
        ],
        times=["2026-09-06T19:10:10.940", "2026-09-06T19:16:22.000"],
    )
    from portia.ui import transcript

    with _as_app(transcript, App()), ui.element("div") as slot:
        transcript.replay(run)
    assert _clocks(slot) == ["6m 11s"]


# --- Stop reaches an indexing job (2026-09-06) --------------------------------


def test_stop_reaches_a_job_that_has_no_chat_behind_it():
    """A job runs on a one-shot conversation `session.run` used to hide; the
    Stop button interrupted nothing, and the copilot's tool threads ran on."""
    import asyncio

    from portia.ui import exchange

    class Job:
        interrupted = 0

        async def interrupt(self):
            self.interrupted += 1

    app = App()
    stream = app.start_exchange("read these", model="m", effort=None, kind=state.INDEXING)
    stream.job = Job()
    with _as_app(exchange, app):
        asyncio.run(exchange.interrupt())
    assert stream.job.interrupted == 1


# --- the job exists from the first hop of profiling (2026-09-23) ---------------


def test_profiling_makes_the_job_it_is_ahead_of_and_the_read_lands_in_it(tmp_path, monkeypatch):
    """Ten minutes of profiling on a warehouse had nowhere to stand: the job
    began with its model turn. It begins with profiling now, waits with the
    lines profiling writes, and the exchange lands in the same chat."""
    import asyncio

    from nicegui import core, ui

    from portia.ui import engine, exchange, screens
    from portia.ui.state import App

    pd.DataFrame({"a": [1, 2]}).to_csv(tmp_path / "orders.csv", index=False)
    monkeypatch.chdir(tmp_path)
    app = App(root=tmp_path)
    catalog.init_project("test", portia_dir=app.portia_dir)

    seen: dict = {}
    real_index = engine.index

    async def watched_index(paths, app_, **kwargs):
        job = app.profiling
        seen["during"] = job
        seen["title"] = job.title
        seen["listed"] = job.started
        return await real_index(paths, app_, **kwargs)

    async def fake_start(prompt, *, model, effort, kind, label, chat=None):
        seen["read in"] = chat
        seen["waiting at read"] = chat.waiting
        seen["lines"] = list(chat.prelude)

    monkeypatch.setattr(engine, "index", watched_index)
    monkeypatch.setattr(exchange, "start", fake_start)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)

    async def index() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        await screens._index_and_interpret([tmp_path / "orders.csv"])
        await asyncio.sleep(0)

    with _as_app(screens, app):
        asyncio.run(index())

    job = seen["during"]
    assert job is not None and job.kind == state.INDEXING
    assert seen["title"] == "Indexing 1 file" and seen["listed"]
    assert seen["read in"] is job and seen["waiting at read"] is False
    assert seen["lines"] == ["Reading 1 file…", "Profiling orders, 1 of 1", "Profiled 1 source."]
    assert app.profiling is None, "nothing waits once the read has started"


def test_no_job_waits_when_the_read_switch_is_off_and_one_job_takes_a_second_batch():
    from portia.ui import screens
    from portia.ui.state import App

    app = App(interpret=False)
    with _as_app(screens, app):
        assert screens._waiting_job(3, "file") is None
        app.interpret = True
        first = screens._waiting_job(3, "file")
        assert first is app.profiling and first.waiting and first.title == "Indexing 3 files"
        assert screens._waiting_job(2, "table") is first, "a second batch joins the waiting job"


def test_a_stop_ends_the_waiting_job_and_drops_it_unless_it_is_on_screen(monkeypatch):
    from portia.ui import screens
    from portia.ui.state import App

    app = App()
    monkeypatch.setattr(screens, "_redraw_indexing", lambda: None)  # tested on its own
    with _as_app(screens, app):
        job = screens._waiting_job(2, "file")
        screens._abandon(job, screens.STOPPED_NO_READ)
        assert job not in app.chats, "nobody was looking, nothing happened in it"

        job = screens._waiting_job(2, "file")
        app.show_chat(job)
        screens._abandon(job, screens.STOPPED_NO_READ)
        assert job in app.chats and not job.waiting and job.prelude[-1] == screens.STOPPED_NO_READ
        app.show_chat(None)
        assert job not in app.chats, "and it goes when they leave it"


def test_opening_the_workspace_lands_in_the_job_waiting_on_profiling(monkeypatch):
    """The way out during profiling threw you into a workspace with nothing on
    it that said anything was happening (the user's report)."""
    from portia.ui import app as app_module
    from portia.ui import screens
    from portia.ui.state import App

    monkeypatch.setattr(app_module.shell, "refresh", lambda *a, **k: None)
    app = App(catalog={"sources": {"orders": {}}})
    with _as_app(screens, app):
        job = screens._waiting_job(2, "file")
        screens._leave(in_dialog=False)
        assert app.open is job

        app.chats.clear()
        app.open = None
        app.left_add_data = False
        app.indexing_status = "Profiling orders, 1 of 2"  # the switch off: no job
        screens._leave(in_dialog=False)
        assert app.open is None and app.right == state.SOURCES


def test_a_hop_redraws_every_line_that_draws_profiling(monkeypatch):
    """The add-data screen redrew its own line only, so the sources view stuck
    on the count it was drawn with until a page reload."""
    from portia.ui import screens, transcript
    from portia.ui.state import App

    drawn: list[str] = []
    monkeypatch.setattr(screens._progress, "refresh", lambda: drawn.append("add-data"))
    monkeypatch.setattr(transcript._index_progress, "refresh", lambda: drawn.append("sources"))
    monkeypatch.setattr(transcript._prelude_view, "refresh", lambda: drawn.append("job"))
    app = App()
    with _as_app(screens, app):
        job = screens._waiting_job(1, "file")
        screens._profiling_moved(job, "Profiling orders, 1 of 1")
    assert drawn == ["add-data", "sources", "job"]
    assert app.indexing_status == "Profiling orders, 1 of 1" and job.prelude == []
    with _as_app(screens, app):
        screens._profiling_moved(job, "")
    assert job.prelude == ["Profiling orders, 1 of 1"] and app.indexing_status == ""


def test_a_waiting_job_is_the_first_row_of_the_list_and_carries_the_light(tmp_path, monkeypatch):
    from portia.ui import screens, transcript
    from portia.ui.state import App

    app = App(root=tmp_path)
    monkeypatch.setattr(transcript.engine, "logs_in", lambda app_: [])
    with _as_app(screens, app), _as_app(transcript, app):
        job = screens._waiting_job(2, "file")
        with ui.element("div") as slot:
            transcript._chat_list()
    texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert job.title in texts and transcript._PROFILING in texts
    lights = [e for e in slot.descendants() if "status-light--live" in e.classes]
    assert lights, "running, the way a chat's row says a chat is"
    assert transcript._NO_CHATS not in texts


def test_the_pinned_sources_row_lights_up_while_profiling_with_no_job():
    from portia.ui import transcript
    from portia.ui.state import App

    app = App(indexing_status="Profiling orders, 1 of 2")
    with _as_app(transcript, app), ui.element("div") as slot:
        transcript._pinned_sources_row()
    assert [e for e in slot.descendants() if "status-light--live" in e.classes]


def test_the_back_control_in_another_chat_knows_profiling_is_running():
    from portia.ui import screens, transcript
    from portia.ui.state import App

    app = App()
    with _as_app(screens, app), _as_app(transcript, app):
        job = screens._waiting_job(2, "file")
        other = app.new_chat()
        assert transcript._elsewhere(other) is job
        assert transcript._elsewhere(job) is None


def test_the_waiting_jobs_header_draws_the_lines_the_status_and_stop():
    from portia.core import cancel
    from portia.ui import screens, transcript
    from portia.ui.state import App

    app = App(indexing_status="Profiling invoices, 2 of 2")
    app.indexing_stop = cancel.Scope()
    try:
        with _as_app(screens, app), _as_app(transcript, app):
            job = screens._waiting_job(2, "file")
            job.prelude.append("Profiling orders, 1 of 2")
            with ui.element("div") as slot:
                transcript._job_header(job)
        texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    finally:
        app.indexing_stop.close()
    assert transcript._WAITING_WHY in texts
    assert "Profiling orders, 1 of 2" in texts and "Profiling invoices, 2 of 2" in texts
    assert "Stop" in texts

    # The read has started: the lines fold shut under the exchange's banner.
    app.indexing_status = ""
    job.waiting = False
    app.start_exchange("read them", model="m", effort=None, kind=state.INDEXING, chat=job)
    with _as_app(transcript, app), ui.element("div") as slot:
        transcript._job_header(job)
    texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    labels = [str(e._props.get("label", "")) for e in slot.descendants()]
    assert transcript._PRELUDE_DONE.format(n="1 line") in labels
    assert transcript._WAITING_WHY not in texts


def test_a_waiting_job_draws_no_empty_note_under_its_header():
    from portia.ui import screens, transcript
    from portia.ui.state import App

    app = App()
    with _as_app(screens, app), _as_app(transcript, app):
        job = screens._waiting_job(2, "file")
        app.show_chat(job)
        with ui.element("div") as slot:
            transcript.stream_view()
    texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert transcript._IDLE_JOB not in texts


def test_a_jobs_instruction_is_not_drawn_in_its_transcript():
    """The app's own template stood where a human's message stands, shut, and
    the user called it useless. The log keeps it; the pane does not draw it."""
    from portia.agent import events
    from portia.ui import transcript

    with ui.element("div") as slot:
        transcript._event(
            events.prompt_event("These sources were just indexed: 'a'.", model="m", effort=None),
            job=True,
        )
    assert not list(slot.descendants())


def test_a_job_is_titled_by_what_it_indexes(tmp_path, monkeypatch):
    """Its title was the first line of the template it was sent, so every job
    in the list and the header of every one opened read *These sources were
    just indexed: 'A', 'B', …*."""
    import asyncio

    from portia import runlog
    from portia.agent import events
    from portia.ui import exchange, transcript

    app = App(root=tmp_path)
    made: dict = {}

    class FakeJob:
        def __init__(self, **kw):
            made.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, prompt):
            yield events.prompt_event(prompt, model="m", effort=None)
            yield events.Event(events.RESULT, {"subtype": "success"})

    class FakeSession:
        conversation = staticmethod(lambda **kw: FakeJob(**kw))

    import portia.agent

    monkeypatch.setattr(portia.agent, "session", FakeSession, raising=False)
    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: False)

    async def passes(provider, model):
        return True

    monkeypatch.setattr(exchange, "_preflight", passes)
    with _as_app(exchange, app):
        asyncio.run(
            exchange.start(
                "These sources were just indexed: 'orders', 'invoices'.",
                model="m",
                effort=None,
                kind=state.INDEXING,
                label="orders, invoices",
            )
        )
    job = app.chats[-1]
    assert job.title == "Indexing orders, invoices"
    listing = runlog.read_listing(job.path, app.portia_dir)
    assert listing["title"] == "Indexing orders, invoices", (
        "the list reads the same name off the file"
    )


def test_the_status_line_keeps_one_size_and_cuts_the_name():
    """The sentence names the table, so the row grew and shrank with every hop
    (the user's call): the sentence gives way with an ellipsis, never the row."""
    import re
    from pathlib import Path

    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    rule = re.search(r"\n\.indexing-status > \.pre-wrap \{(.*?)\}", css, re.S).group(1)
    assert "white-space: nowrap" in rule and "text-overflow: ellipsis" in rule
    assert "min-width: 0" in rule
    row = re.search(r"\n\.indexing-status \{(.*?)\}", css, re.S).group(1)
    assert "width: 100%" in row and "flex-wrap: nowrap" in row


def test_a_batch_queued_behind_a_chat_is_read_when_the_chat_ends(monkeypatch):
    from portia.ui import exchange
    from portia.ui.state import App

    kicked: list = []
    from nicegui import background_tasks

    monkeypatch.setattr(
        background_tasks, "create", lambda coro, **k: (kicked.append(coro), coro.close())
    )
    app = App(pending_interpret=["orders"])
    with _as_app(exchange, app):
        exchange._resume_reads()
    assert len(kicked) == 1
    app.pending_interpret = []
    with _as_app(exchange, app):
        exchange._resume_reads()
    assert len(kicked) == 1, "nothing queued, nothing kicked"


def test_the_sources_view_draws_a_running_job_as_a_row_and_keeps_its_list(tmp_path, monkeypatch):
    """It drew the job's banner and none of its rows, and nothing refreshed it:
    whoever followed profiling from here saw the read start and then nothing
    move (the user's report, 2026-09-23). A row that opens the job now."""
    from portia.ui import screens, transcript
    from portia.ui.state import App

    app = App(root=tmp_path, catalog={"sources": {"orders": {"path": "data/orders.csv"}}})
    monkeypatch.setattr(transcript.engine, "source_states", lambda app_: [])
    app.indexing_status = screens.INTERPRETING.format(n="1 source")
    job = app.start_exchange("read them", model="m", effort=None, kind=state.INDEXING)
    with _as_app(transcript, app), ui.element("div") as slot:
        transcript._sources_view()
    texts = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert transcript._RUNNING in texts and transcript._INDEX_WHAT in texts
    assert "working" not in texts, "the job's own pane says that; this row opens it"
    assert [e for e in slot.descendants() if "status-light--live" in e.classes]
    assert app.indexing_status not in texts, "a sentence written for the add-data screen"

    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    with _as_app(transcript, app):
        transcript._open_held(job)
    assert app.open is job


def test_a_job_is_opened_on_a_conversation_that_cannot_build(monkeypatch):
    """Indexing reads: the window opens its job with `builds=False`, so the
    copilot is offered no `record_step` and no `run_spec` (2026-09-23)."""
    import asyncio

    from portia.agent import events
    from portia.ui import exchange, transcript

    app = App()
    made: dict = {}

    class FakeJob:
        def __init__(self, **kw):
            made.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, prompt):
            yield events.prompt_event(prompt, model="m", effort=None)
            yield events.Event(events.RESULT, {"subtype": "success"})

    class FakeSession:
        conversation = staticmethod(lambda **kw: FakeJob(**kw))

    import portia.agent

    monkeypatch.setattr(portia.agent, "session", FakeSession, raising=False)
    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: False)
    monkeypatch.setattr(exchange, "_preflight", _passes)
    with _as_app(exchange, app):
        asyncio.run(exchange.start("read these", model="m", effort=None, kind=state.INDEXING))

    assert made["builds"] is False


async def _passes(provider, model):
    return True


def test_the_indexing_banner_offers_stop_while_a_job_runs():
    from portia.ui import transcript

    app = App()
    stream = app.start_exchange("read these", model="m", effort=None, kind=state.INDEXING)
    with _as_app(transcript, app), ui.element("div") as slot:
        transcript._job_header(stream)
    labels = [str(getattr(e, "text", "")) for e in slot.descendants()]
    assert "Stop" in labels


# --- built tables are listed with the sources (2026-09-07) -----------------------


def test_the_indexing_list_carries_the_tables_the_project_built(tmp_path, monkeypatch):
    """A built table has an entry from the moment a step is recorded, and what it
    is carries as much context as a source does — so it is a row here, read or
    not read, never unindexed, and Interpret takes it by name."""
    from portia.core.io import connect
    from portia.core.table import Table
    from portia.ui import engine
    from portia.ui.state import App

    monkeypatch.chdir(tmp_path)
    app = App()
    app.root = tmp_path
    app.portia_dir = ".portia"
    catalog.init_project("test", portia_dir=app.portia_dir)
    con = connect()
    catalog.index_model(
        "stg_orders", Table.from_frame(pd.DataFrame({"a": [1, 2]}), "t", con), portia_dir=".portia"
    )
    (rows,) = [s for s in engine.source_states(app) if s.name == "stg_orders"]
    assert rows.kind == state.MODEL and rows.state == engine.UNREAD and rows.indexed
    catalog.set_interpretation("stg_orders", summary="Orders, staged.", portia_dir=".portia")
    (rows,) = [s for s in engine.source_states(app) if s.name == "stg_orders"]
    assert rows.state == engine.INTERPRETED


def test_the_picker_will_not_scope_a_table_this_project_built(monkeypatch):
    from portia.ui import engine, screens

    app = App(catalog={"scope": ["p.raw.CUSTOMERS"]})
    app.scope_listing = {
        "": ["p"],
        "p": ["raw", "staging"],
        "p.raw": [("CUSTOMERS", "table"), ("orders", "table")],
        "p.staging": [("stg_x", "table")],
    }
    app.scope_open = frozenset({"p", "p.raw", "p.staging"})
    monkeypatch.setattr(engine, "written_tables", lambda _app: {"p.staging.stg_x": "stg_x"})

    with _as_app(screens, app), ui.element("div") as slot:
        screens._scope_tree()
    done = [row for row in slot.descendants() if "tree-row--done" in getattr(row, "classes", [])]
    notes = [
        str(e.text)
        for row in done
        for e in row.descendants()
        if "tree-row-meta" in getattr(e, "classes", [])
    ]
    assert notes == [screens.IN_SCOPE_NOTE, screens.BUILT_NOTE.format(model="stg_x")]
    leaves = [
        box
        for row in slot.descendants()
        if "tree-row--leaf" in getattr(row, "classes", [])
        for box in row.descendants()
        if box.tag == "q-checkbox"
    ]
    assert [bool(b._props.get("disable", False)) for b in leaves] == [True, False, True]


def _node_boxes(slot) -> dict[str, object]:
    """Each container's box value, by the name drawn beside it."""
    boxes = {}
    for row in slot.descendants():
        if "tree-row--node" not in getattr(row, "classes", []):
            continue
        inside = list(row.descendants())
        box = [e for e in inside if e.tag == "q-checkbox"]
        name = [e for e in inside if "tree-row-name" in getattr(e, "classes", [])]
        if box and name:
            boxes[str(name[0].text)] = box[0].value
    return boxes


def _node_counts(slot) -> list[str]:
    return [
        str(e.text)
        for row in slot.descendants()
        if "tree-row--node" in getattr(row, "classes", [])
        for e in row.descendants()
        if "tree-row-meta" in getattr(e, "classes", [])
    ]


def test_a_schema_row_says_what_is_ticked_under_it_without_being_opened(monkeypatch):
    """The complaint (2026-09-18): tick tables in one schema, go to another,
    and nothing on screen says the first ticks exist. A shut schema now carries
    a box and a count, and so does the database above it."""
    from portia.ui import engine, screens

    app = App()
    app.scope_listing = {
        "": ["DB"],
        "DB": ["STG_A", "STG_B"],
        "DB.STG_A": [("ORDERS", "table"), ("lines", "table")],
        "DB.STG_B": [("SHIFTS", "table")],
    }
    app.scope_open = frozenset({"DB"})
    app.scope_ticks = frozenset({"DB.STG_A.ORDERS", "DB.STG_B.SHIFTS"})
    monkeypatch.setattr(engine, "written_tables", lambda _app: {})

    with _as_app(screens, app), ui.element("div") as slot:
        screens._scope_tree()

    assert _node_boxes(slot) == {"DB": None, "STG_A": None, "STG_B": True}
    assert _node_counts(slot) == ["2 of 3", "1 of 2", "1 of 1"]


def test_ticking_a_schema_lists_it_and_ticks_the_tables_there_now(monkeypatch):
    """A snapshot, never a rule: the press takes the names the listing returned,
    leaves out what is already in scope, and writes table names, not a pattern."""
    import asyncio

    from portia.ui import engine, screens

    app = App(catalog={"scope": ["DB.STG_A.ORDERS"]})
    app.scope_listing = {"": ["DB"], "DB": ["STG_A"]}
    asked = []

    async def browse(app_, at, on_start=None):
        asked.append(at)
        app_.scope_listing[at] = [("ORDERS", "table"), ("lines", "table"), ("V_X", "view")]
        return app_.scope_listing[at]

    monkeypatch.setattr(engine, "browse_remote", browse)
    monkeypatch.setattr(engine, "written_tables", lambda _app: {})
    monkeypatch.setattr(screens, "_redraw_scope", lambda: None)

    with _as_app(screens, app), ui.element("div"):
        asyncio.run(screens._tick_schema("DB.STG_A", True))
        assert asked == ["DB.STG_A"]
        assert app.scope_ticks == {"DB.STG_A.lines", "DB.STG_A.V_X"}

        app.scope_filter = "v_"
        asyncio.run(screens._tick_schema("DB.STG_A", False))
    assert app.scope_ticks == {"DB.STG_A.lines"}, "a filtered press takes the rows on screen"


def test_a_folder_box_unticks_every_file_under_it(tmp_path, monkeypatch):
    from portia.ui import screens

    (tmp_path / "data" / "2023").mkdir(parents=True)
    (tmp_path / "data" / "2024").mkdir()
    for rel in ("2023/a.csv", "2023/B.csv", "2024/c.csv", "orders.csv"):
        (tmp_path / "data" / rel).write_text("a\n1\n", encoding="utf-8")
    app = App(root=tmp_path, catalog={"data_dir": "data"})
    monkeypatch.setattr(screens._actions, "refresh", lambda: None)

    with _as_app(screens, app):
        screens._seed_ticks()
        with ui.element("div") as slot:
            screens._file_tree()
        assert _node_boxes(slot) == {"2023": True, "2024": True}

        screens._tick_folder("data/2023", False)
        assert sorted(p.name for p in screens._ticked()) == ["c.csv", "orders.csv"]
        # Told in place: the same elements, no redraw, now saying what is ticked.
        assert _node_boxes(slot) == {"2023": False, "2024": True}

        screens._tick("data/2023/B.csv", True)
        assert _node_boxes(slot) == {"2023": None, "2024": True}
        assert _node_counts(slot) == ["1 of 2", "1 of 1"]


# --- a scoped table has two axes: read, and profiled (2026-09-07) ----------------


class _MetadataOnlyWarehouse:
    """The least a connection has to answer for `catalog.scope_table`: no scan."""

    def columns(self, qualified):
        return [("ID", "NUMBER"), ("country", "VARCHAR")]

    def table_facts(self, qualified):
        return {"size": 30, "mtime": "2026-09-07T09:00:00", "rows": 3, "kind": "table"}


def _warehouse_project(tmp_path, monkeypatch, *names):
    """A project on a connection with ``names`` scoped as metadata, and an app on it."""
    monkeypatch.chdir(tmp_path)
    app = App(root=tmp_path, portia_dir=".portia")
    catalog.init_project("test", portia_dir=app.portia_dir)
    catalog.set_connection("prod", portia_dir=app.portia_dir)
    for qualified in names:
        catalog.scope_table(qualified, _MetadataOnlyWarehouse(), portia_dir=app.portia_dir)
    engine_module.refresh_catalog(app)
    return app


def test_a_scoped_table_is_indexed_and_not_profiled_and_the_tab_can_tell(tmp_path, monkeypatch):
    """The user's report: six tables added from the picker sat at *not read* on
    the Indexing tab with Index greyed out, because the tab knew one axis. A
    scoped table is in the catalog (indexed) with nothing measured (not
    profiled), and Index is what profiles it."""
    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.orders")

    (row,) = engine_module.source_states(app)
    assert row.indexed and row.state == engine_module.UNREAD
    assert not row.profiled and row.needs_profile

    files, remote = engine_module.to_index([row])
    assert files == [] and [r.name for r in remote] == ["orders"]

    # Once measured, Index has nothing left to do with it.
    entry = dict(app.sources["orders"], profiled={"at": "2026-09-07T10:00:00"})
    catalog._write(tmp_path / ".portia" / "sources" / "orders.yaml", entry)
    engine_module.refresh_catalog(app)
    (row,) = engine_module.source_states(app)
    assert row.profiled and engine_module.to_index([row]) == ([], [])


def test_a_local_file_and_a_built_table_never_ask_for_a_scan(tmp_path, monkeypatch):
    """`profiled` defaults to yes: a file is profiled by being indexed, and a
    model built locally is profiled by being built. The scan is a warehouse thing."""
    from portia.core.io import connect
    from portia.core.table import Table

    monkeypatch.chdir(tmp_path)
    app = App(root=tmp_path, portia_dir=".portia")
    catalog.init_project("test", portia_dir=app.portia_dir)
    pd.DataFrame({"a": [1]}).to_csv(tmp_path / "orders.csv", index=False)
    catalog.index_source(tmp_path / "orders.csv", portia_dir=app.portia_dir)
    catalog.index_model(
        "stg_orders",
        Table.from_frame(pd.DataFrame({"a": [1]}), "t", connect()),
        portia_dir=".portia",
    )
    engine_module.refresh_catalog(app)

    rows = engine_module.source_states(app)
    assert {r.name: r.profiled for r in rows} == {"orders": True, "stg_orders": True}
    assert engine_module.to_index(rows) == ([], [])


def test_the_indexing_tab_row_says_metadata_only(tmp_path, monkeypatch):
    """One phrase for the state on every surface. The left pane said *not
    profiled*, the tab said *not read*, the inspector said *indexed as
    metadata*, and the user read three states where there was one."""
    from portia.ui import artifacts, transcript

    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.orders")
    (row,) = engine_module.source_states(app)
    with _as_app(transcript, app), ui.element("div") as slot:
        transcript._source_state_row(row)
    labels = [str(e.text) for e in slot.descendants() if e.tag == "q-label" or hasattr(e, "text")]
    assert transcript._METADATA_ONLY in labels
    assert artifacts.NOT_PROFILED_NOTE == transcript._METADATA_ONLY


def test_index_on_the_tab_scans_a_ticked_metadata_only_table(tmp_path, monkeypatch):
    """The fix the user asked for by name: a table added as metadata can be
    profiled from the Indexing tab, with the same button that profiles a file."""
    import asyncio

    from nicegui import core

    from portia.ui import transcript

    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.orders", "memory.sales.customers")
    app.index_ticks = frozenset({"orders"})
    scanned: list[str] = []
    statuses: list[str] = []

    async def fake_profile_tables(app_, names, *, on_progress=None, stop=None):
        for i, name in enumerate(names):
            on_progress(i, len(names), name)
            statuses.append(app_.indexing_status)
            scanned.append(name)
        return engine_module.Indexed(names=list(names), items=list(names), failed=[])

    async def fake_index(paths, app_, **kw):
        raise AssertionError("no file was ticked, so nothing local should be profiled")

    monkeypatch.setattr(engine_module, "profile_tables", fake_profile_tables)
    monkeypatch.setattr(engine_module, "index", fake_index)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)

    async def press() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        await transcript._index_ticked()
        await asyncio.sleep(0)

    with _as_app(transcript, app):
        asyncio.run(press())

    assert scanned == ["orders"], "the ticked one, and only that one"
    assert statuses == ["Profiling orders, 1 of 1"], "the tab says which table is on the meter"
    assert app.indexing_status == "" and app.indexing_stop is None, "and clears it after"


def test_adding_warehouse_tables_scans_them_only_when_the_switch_says_so(tmp_path, monkeypatch):
    """`CONNECTOR.md` §2.6, revised: metadata is still the default, and the
    profile is asked for on the add-data screen before the press rather than
    from the inspector after it. Off, no scan; on, every scoped table."""
    import asyncio

    from nicegui import core

    from portia.ui import exchange, screens

    app = _warehouse_project(tmp_path, monkeypatch)
    app.interpret = False
    scanned: list[list[str]] = []

    async def fake_scope(app_, names, *, on_progress=None, stop=None):
        return engine_module.Indexed(
            names=[n.split(".")[-1] for n in names], items=list(names), failed=[]
        )

    async def fake_profile_tables(app_, names, *, on_progress=None, stop=None):
        scanned.append(list(names))
        return engine_module.Indexed(names=list(names), items=list(names), failed=[])

    async def fake_start(*a, **k):
        raise AssertionError("the read is off")

    monkeypatch.setattr(engine_module, "scope", fake_scope)
    monkeypatch.setattr(engine_module, "profile_tables", fake_profile_tables)
    monkeypatch.setattr(exchange, "start", fake_start)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)

    async def add() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        await screens._scope_and_interpret(["memory.sales.orders", "memory.sales.customers"])
        await asyncio.sleep(0)

    with _as_app(screens, app):
        app.profile_on_add = False
        asyncio.run(add())
        assert scanned == [], "off: a table arrives as metadata and nothing is scanned"
        app.profile_on_add = True
        asyncio.run(add())
        assert scanned == [["orders", "customers"]], "on: every scoped table, in order"


def test_the_toast_after_adding_says_what_happened_and_where_to_look():
    """The dialog is gone by the time the read starts, so the one line left on
    screen has to say where the work went. Nothing told the user (2026-09-07)."""
    from portia.core import cancel
    from portia.ui import screens

    app = App(interpret=True)
    with _as_app(screens, app):
        stop = cancel.Scope()
        assert screens._will_read(stop)
        stop.cancel()
        assert not screens._will_read(stop), "a stop stops the paid half too"
        stop.close()

        note = screens._scoped_note(3, 0, reading=True)
        assert note.startswith("Added 3 tables as metadata.") and "chat list" in note
        note = screens._scoped_note(3, 2, reading=False)
        assert note.startswith("Added 3 tables, 2 profiled.") and "Indexing tab" in note


# --- picking a chat up (`docs/CHAT_SESSIONS.md` §3.3) --------------------------


def _logged_chat(app, *, session_id="sess-1", kind=None):
    """A chat another process wrote: two exchanges, a result carrying the id."""
    from portia import runlog
    from portia.agent import events

    log = runlog.start(app.catalog_dir, kind=kind or runlog.CHAT)
    log.event(events.prompt_event("merge the customers", model="claude-haiku-4-5", effort="low"))
    result = {"subtype": "success", "cost_usd": 0.01, "usage": {}}
    if session_id:
        result["session_id"] = session_id
    log.event(events.Event(events.RESULT, result))
    return log.path


def test_opening_a_logged_chat_reads_it_and_connects_nothing(tmp_path, monkeypatch):
    """Opening to read must cost nothing: the log is drawn through `replay`
    and the resume waits for the first send."""
    from portia.ui import exchange

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    path = _logged_chat(app)

    chat = exchange.open_from_disk(path)

    assert app.open is chat and chat in app.chats
    assert chat.path == path and chat.logged is not None
    assert chat.session_id == "sess-1" and not chat.legacy
    assert chat.conversation is None, "nothing connected"
    assert chat.title == "merge the customers"
    assert chat.model == "claude-haiku-4-5" and chat.effort == "low"
    assert chat.messages == 1 and chat.spent == pytest.approx(0.01)
    assert exchange.open_from_disk(path) is chat, "held once, shown again"


def test_a_chat_with_no_session_id_is_legacy_and_says_why(tmp_path, monkeypatch):
    from portia.ui import exchange

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    chat = exchange.open_from_disk(_logged_chat(app, session_id=None))
    assert chat.legacy == exchange.LEGACY_NO_SESSION
    assert not chat.continuable


def test_a_pre_rename_log_is_legacy(tmp_path, monkeypatch):
    from portia import runlog
    from portia.ui import exchange

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    legacy = app.catalog_dir / runlog.LEGACY_DIR
    legacy.mkdir(parents=True)
    path = legacy / "2026-08-01T10-00-00.jsonl"
    path.write_text(
        '{"kind": "header", "data": {"started": "2026-08-01T10:00:00", "prompt": "old"}}\n',
        encoding="utf-8",
    )

    chat = exchange.open_from_disk(path)
    assert chat.legacy == exchange.LEGACY_PRE_RENAME
    assert chat.title == "old"


def test_a_logged_job_opens_as_a_job(tmp_path, monkeypatch):
    from portia import runlog
    from portia.ui import exchange

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    chat = exchange.open_from_disk(_logged_chat(app, kind=runlog.INDEXING))
    assert chat.is_job and not chat.continuable and not chat.legacy


def test_the_first_send_after_opening_resumes_and_appends(tmp_path, monkeypatch):
    """The whole point: the client gets the recorded session id, and the log
    is reopened rather than started (§3.9)."""
    import asyncio

    from portia import runlog
    from portia.agent import events
    from portia.ui import exchange, transcript

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    monkeypatch.setattr(transcript, "APP", app)
    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    monkeypatch.setattr(transcript.stream_view, "refresh", lambda: None)
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: None)
    path = _logged_chat(app)
    chat = exchange.open_from_disk(path)

    made = {}

    class FakeConversation:
        def __init__(self, **kw):
            made.update(kw)
            self.model = kw["model"]

        async def connect(self):
            made["connected"] = True

        async def send(self, prompt):
            yield events.prompt_event(prompt, model=self.model, effort=None)
            yield events.Event(events.RESULT, {"subtype": "success", "session_id": "sess-1"})

        async def context_usage(self):
            return None

    class FakeSession:
        Conversation = FakeConversation
        # The window opens a chat through the harness factory (`PROVIDERS.md` §9).
        conversation = staticmethod(lambda **kw: FakeConversation(**kw))

    import portia.agent

    monkeypatch.setattr(portia.agent, "session", FakeSession)

    asyncio.run(exchange.start("and dedupe them", model="m", effort="high", chat=chat))

    assert made["resume"] == "sess-1" and made["connected"]
    assert made["effort"] == "high", "a new client, so effort is choosable again"
    assert chat.resumed is True
    assert chat.log is not None and chat.log.path == path, "appended, no second file"
    run = runlog.read(path)
    assert run.resumes == [2]
    assert runlog.summary(run)["exchanges"] == 2
    assert chat.messages == 2


def test_a_resume_the_sdk_refuses_makes_the_chat_legacy(tmp_path, monkeypatch):
    import asyncio

    from portia.ui import exchange, transcript

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    monkeypatch.setattr(transcript, "APP", app)
    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: None)
    chat = exchange.open_from_disk(_logged_chat(app))

    class Refusing:
        def __init__(self, **kw):
            self.model = kw["model"]

        async def connect(self):
            raise RuntimeError("No conversation found with session ID")

        async def close(self):
            pass

    class FakeSession:
        Conversation = Refusing

    import portia.agent

    monkeypatch.setattr(portia.agent, "session", FakeSession)
    asyncio.run(exchange.start("go on", model="m", effort=None, chat=chat))

    assert chat.legacy.startswith("the SDK could not resume it")
    assert chat.conversation is None and not chat.continuable
    assert chat.exchange.error


def test_deleting_a_chat_is_refused_while_it_runs_and_takes_the_log_otherwise(
    tmp_path, monkeypatch
):
    import asyncio

    from portia.ui import exchange

    app = App(root=tmp_path)
    monkeypatch.setattr(exchange, "APP", app)
    path = _logged_chat(app)
    chat = exchange.open_from_disk(path)
    chat.exchange = state.Exchange(prompt="x", model="m", effort=None)
    assert asyncio.run(exchange.delete_chat(chat)) is False
    assert path.exists()

    chat.exchange.running = False
    assert asyncio.run(exchange.delete_chat(chat)) is True
    assert not path.exists()
    assert chat not in app.chats and app.open is None


def test_a_days_label_is_today_yesterday_or_the_date():
    from datetime import date

    from portia.ui import transcript

    today = date(2026, 9, 7)
    assert transcript._day_label("2026-09-07T10:00:00", today) == transcript._TODAY
    assert transcript._day_label("2026-09-06T23:59:00", today) == transcript._YESTERDAY
    assert transcript._day_label("2026-09-01T08:00:00", today) == "2026-09-01"
    assert transcript._day_label(None, today) == transcript._UNDATED


# --- responsiveness: what a streamed event may redraw (2026-09-07) ------------
#
# Measured before any of this: a 239-event chat rebuilt 2,635 elements and sent
# 567 KB per streamed event, a shut tool card shipped its whole result, and the
# middle pane ran one `count(*)` per step per click. These pin the three
# mechanisms that replaced that: the transcript's settled prefix, the lazy
# disclosure body, and the shape cache measured off the loop.


def _stream(*rows):
    from portia.ui import transcript

    return transcript.settled_before(list(rows), busy=True)


def test_the_last_unit_is_never_settled_because_it_can_still_grow():
    from portia.agent import events
    from portia.ui import transcript

    text = events.Event(events.TEXT, {"text": "so"})
    think = events.Event(events.THINKING, {"text": "hm"})
    assert _stream() == 0
    assert _stream(text) == 0
    assert _stream(text, think, think) == 1  # the thinking run is one unit, and the last
    rows = [text, think, think, *_calls(("1", "describe_source")), text]
    assert transcript.settled_before(rows, busy=True) == len(rows) - 1


def test_a_call_still_running_holds_the_boundary_at_its_unit():
    """A result landing there changes the state of every call after it."""
    from portia.agent import events
    from portia.ui import transcript

    text = events.Event(events.TEXT, {"text": "so"})
    pending = events.Event(events.TOOL_CALL, {"id": "p", "name": "profile_source", "input": {}})
    rows = [text, pending, text, text]
    assert transcript.settled_before(rows, busy=True) == 1
    # Not busy, the call is *dropped* — a fact, and one that will not change.
    assert transcript.settled_before(rows, busy=False) == 3


def test_a_decision_nobody_has_made_holds_the_boundary():
    import asyncio

    from portia.agent import events
    from portia.ui import transcript

    loop = asyncio.new_event_loop()
    try:
        text = events.Event(events.TEXT, {"text": "so"})
        asked = Decision(events.QUESTION, {"questions": []}, loop.create_future())
        assert transcript.settled_before([text, asked, text, text], busy=True) == 1
        asked.resolve({"q": "a"})
        assert transcript.settled_before([text, asked, text, text], busy=True) == 3
        cut = Decision(events.QUESTION, {"questions": []}, loop.create_future())
        cut.future.cancel()
        assert transcript.settled_before([text, cut, text, text], busy=True) == 3
    finally:
        loop.close()


def test_a_slice_of_the_stream_folds_and_keys_exactly_as_the_whole_does():
    """`_segments` is the one cut; drawing from a `start` must land on it."""
    from portia.agent import events
    from portia.ui import transcript

    text = events.Event(events.TEXT, {"text": "so"})
    rows = [text, *_calls(*((str(i), "describe_source") for i in range(4))), text]
    whole = list(transcript._segments(rows, transcript._results_by_call(rows)))
    tail = list(transcript._segments(rows, transcript._results_by_call(rows), start=1))
    assert whole == [(0, 1), (1, 9), (9, 10)]
    assert tail == whole[1:]


def test_a_streamed_event_appends_to_the_settled_slot_and_never_rebuilds_it():
    from portia.agent import events
    from portia.ui import transcript
    from portia.ui.state import App

    app = App()
    chat = app.new_chat()
    app.show_chat(chat)
    chat.exchange = state.Exchange(prompt="x", model="m", effort=None)
    text = events.Event(events.TEXT, {"text": "so"})
    chat.rows.extend([text, *_calls(("1", "describe_source"))])
    with _as_app(transcript, app), ui.element("div"):
        transcript.stream_view()
    slot = chat.settled_slot
    assert slot is not None and chat.settled == 1
    drawn_first = [e.id for e in slot.descendants()]

    # A message arrives: the run of calls settles, the message is the tail.
    chat.rows.append(text)
    with _as_app(transcript, app):
        assert transcript.settle(chat) is True
    assert chat.settled == 3
    drawn_after = [e.id for e in slot.descendants()]
    assert drawn_after[: len(drawn_first)] == drawn_first  # appended, not rebuilt
    assert len(drawn_after) > len(drawn_first)

    # Nothing new settled: nothing drawn.
    with _as_app(transcript, app):
        assert transcript.settle(chat) is True
    assert [e.id for e in slot.descendants()] == drawn_after


def test_settling_with_no_slot_asks_for_the_whole_redraw():
    from portia.ui import transcript
    from portia.ui.state import App

    app = App()
    chat = app.new_chat()
    assert transcript.settle(chat) is False
    with ui.element("div") as slot:
        pass
    chat.settled_slot = slot
    slot.delete()
    assert transcript.settle(chat) is False


def test_an_event_for_a_chat_not_on_screen_redraws_nothing(monkeypatch):
    from portia.agent import events
    from portia.ui import exchange, transcript
    from portia.ui.state import App

    app = App()
    shown, background = app.new_chat(), app.new_chat()
    app.show_chat(shown)
    refreshed = []
    monkeypatch.setattr(exchange, "APP", app)
    monkeypatch.setattr(transcript.tail_view, "refresh", lambda: refreshed.append("tail"))
    monkeypatch.setattr(transcript.stream_view, "refresh", lambda: refreshed.append("whole"))
    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: None)
    exchange._record(events.Event(events.TEXT, {"text": "so"}), background)
    assert refreshed == [] and len(background.rows) == 1
    exchange._record(events.Event(events.TEXT, {"text": "so"}), shown)
    assert refreshed == ["whole"]  # no slot yet: the whole stream, once


def test_a_shut_disclosure_carries_no_body_until_it_is_opened():
    toggled = []
    with ui.element("div") as slot:
        exp = c.disclosure(
            lambda: ui.label("head"),
            lambda: ui.label("BODY"),
            open=False,
            on_toggle=toggled.append,
        )
    texts = lambda: [str(e.text) for e in slot.descendants() if getattr(e, "text", None)]  # noqa: E731
    assert "head" in texts() and "BODY" not in texts()
    exp.value = True  # what the browser's click does
    assert "BODY" in texts() and toggled == [True]
    exp.value = False
    exp.value = True
    assert texts().count("BODY") == 1  # drawn once, however often it is toggled
    assert toggled == [True, False, True]


def test_an_open_disclosure_draws_its_body_at_once():
    with ui.element("div") as slot:
        c.disclosure(lambda: ui.label("head"), lambda: ui.label("BODY"), open=True)
    assert "BODY" in [str(e.text) for e in slot.descendants() if getattr(e, "text", None)]


def test_a_shape_is_measured_once_per_key_and_read_back_free():
    class Counted:
        asked = 0

        def __len__(self):
            Counted.asked += 1
            return 3

        def head(self, n):
            return pd.DataFrame({"a": [1, 2, 3]}).head(n)

    key = ("test", "counted")
    engine_module._SHAPES.pop(key, None)
    assert engine_module.shape_of(key) is None
    total, head = engine_module.measure_shape(key, Counted())
    assert (total, len(head)) == (3, 3) and Counted.asked == 1
    engine_module.measure_shape(key, Counted())
    assert Counted.asked == 1
    assert engine_module.shape_of(key)[0] == 3


def test_warming_a_run_measures_every_step_that_produced_a_table():
    results = [
        SimpleNamespace(id="a", table=pd.DataFrame({"x": [1, 2]})),
        SimpleNamespace(id="b", table=None),
    ]
    engine_module.warm_shapes(results)
    assert engine_module.shape_of(engine_module.result_shape_key(results[0]))[0] == 2
    assert engine_module.shape_of(engine_module.result_shape_key(results[1])) is None


def test_the_report_draws_the_count_from_the_cache_and_never_asks_the_relation():
    from portia.ui import workflow

    class Untouchable:
        def __len__(self):
            raise AssertionError("the render pass asked the relation")

        head = __len__

    result = SimpleNamespace(id="s", table=Untouchable())
    key = engine_module.result_shape_key(result)
    engine_module._SHAPES[key] = (7, pd.DataFrame({"x": [1]}))
    with ui.element("div") as slot:
        workflow._table(result)
    texts = [str(e.text) for e in slot.descendants() if getattr(e, "text", None)]
    assert any("7 rows" in t for t in texts)


def test_a_missing_shape_is_measured_inline_only_when_there_is_no_loop():
    """Under the window there is a loop and the count lands later; a bare
    render is the CLI-less case and measures as the pane always did."""
    from portia.ui import workflow

    result = SimpleNamespace(id="t", table=pd.DataFrame({"x": [1, 2, 3, 4]}))
    engine_module._SHAPES.pop(engine_module.result_shape_key(result), None)
    with ui.element("div") as slot:
        workflow._table(result)
    texts = [str(e.text) for e in slot.descendants() if getattr(e, "text", None)]
    assert any("4 rows" in t for t in texts)


def test_the_sync_reloads_nothing_while_the_stamp_holds_still(tmp_path, monkeypatch):
    from portia.ui import app as app_module
    from portia.ui import artifacts, exchange, workflow
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=str(tmp_path / ".portia"))
    (tmp_path / ".portia").mkdir()
    reloaded = []
    monkeypatch.setattr(exchange, "APP", app)
    monkeypatch.setattr(engine_module, "refresh_catalog", lambda a: reloaded.append("catalog"))
    monkeypatch.setattr(engine_module, "reload_spec", lambda a: reloaded.append("spec"))
    for pane in (artifacts.pane, workflow.pane, app_module.run_controls):
        monkeypatch.setattr(pane, "refresh", lambda: None)
    exchange._sync_artifacts()
    assert reloaded == ["catalog"]  # the first look; nothing to compare against
    exchange._sync_artifacts()
    assert reloaded == ["catalog"]  # unchanged: not even the catalog is re-read
    (tmp_path / ".portia" / "project.yaml").write_text("context: hi\n", encoding="utf-8")
    exchange._sync_artifacts()
    assert reloaded == ["catalog", "catalog"]


# --- dragging the cards (2026-09-07) -----------------------------------------


def test_dropping_a_card_records_where_it_landed_per_project(tmp_path, monkeypatch):
    """A drop arrives once, as a delta; it accumulates, it is written beside
    the canvas filter, and Reset puts everything back and removes the entry."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        workflow.move_card("mart", 40, -10)
        workflow.move_card("mart", 40, -10)
        assert app.card_offsets == {"mart": (80, -20)}
        assert engine_module.remembered_layout(tmp_path) == {"mart": (80, -20)}
        assert engine_module.remembered_layout(tmp_path / "elsewhere") == {}

        workflow.move_card("mart", -80, 20)
        assert app.card_offsets == {}, "back on the grid is not an offset of zero"

        workflow.move_card("stg", 5, 5)
        workflow.reset_layout()
        assert app.card_offsets == {}
        assert engine_module.remembered_layout(tmp_path) == {}


def test_the_canvas_names_every_card_and_every_arrow_for_the_drag(tmp_path, monkeypatch):
    """`canvas.js` moves a card by its ``data-node`` and redraws the arrows whose
    ``<g>`` names it at either end; a ghost is marked so it cannot be dragged."""
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.card_offsets = {"mart": (30, 0)}
    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._graph_half()
    named = {e._props.get("data-node") for e in slot.descendants() if "data-node" in e._props}
    assert {"stg", "mart", "orders", "regions"} <= named
    svg = next(e for e in slot.descendants() if isinstance(e, ui.html))
    assert "<g data-src='stg' data-dst='mart'>" in svg.content
    assert "<g data-src='regions' data-dst='mart'>" in svg.content
    node = next(e for e in slot.descendants() if e._props.get("data-node") == "mart")
    assert "data-ghost" not in node._props

    button = next(
        e for e in slot.descendants() if isinstance(e, ui.button) and e.text == "Reset layout"
    )
    assert button.enabled, "something is moved, so there is something to reset"


def test_reset_layout_is_disabled_while_nothing_is_moved(tmp_path, monkeypatch):
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._graph_half()
    button = next(
        e for e in slot.descendants() if isinstance(e, ui.button) and e.text == "Reset layout"
    )
    assert not button.enabled


def test_opening_a_project_restores_the_canvas_as_you_left_it(tmp_path, monkeypatch):
    """Both, beside recents in `prefs`: the filter (written since 2026-08-16 and
    read back by nothing until 2026-09-07) and the arrangement."""
    project = tmp_path / "proj"
    project.mkdir()
    engine_module.remember_view(project.resolve(), frozenset({"stg"}))
    engine_module.remember_layout(project.resolve(), {"stg": (12, -8)})

    app = App()
    engine_module.open_project(project, app)
    assert app.visible == frozenset({"stg"})
    assert app.card_offsets == {"stg": (12, -8)}


# --- the journal, folded twice (2026-09-07) ----------------------------------


def _journal_project(tmp_path):
    """The canvas project with two findings: one for `mart`, one about its input."""
    from portia import findings

    app = _canvas_project(tmp_path)
    findings.record(
        question="does binning hotels to a 15km grid work?",
        answer="no. 574 in one Paris cell against 52 localities",
        so="matched on division centroids instead",
        about=["orders.id", "regions"],
        queries=[
            {"question": "how many per cell", "sql": "select 1", "result": '{"rows": [[574]]}'}
        ],
        spec_name="mart",
        root=tmp_path,
        portia_dir=tmp_path / ".portia",
    )
    findings.record(
        question="are ids unique in orders?",
        answer="yes, 2 of 2",
        so="no dedupe step",
        about=["orders"],
        queries=[],
        spec_name="stg",
        root=tmp_path,
        portia_dir=tmp_path / ".portia",
    )
    # Both were recorded in the same second; the input's finding is the older
    # one, and the stamp is what the journal orders by.
    import yaml

    for path in (tmp_path / "findings").glob("*.yaml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if doc["spec"] == "stg":
            doc["at"] = "2026-09-01T09:00:00+02:00"
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )
    return app


def _report_text(app) -> tuple[str, list]:
    from portia.ui import workflow

    engine_module.select_spec(app.root / "specs" / "mart.yaml", app)
    with _as_app(workflow, app), ui.element("div") as slot:
        workflow._report_half()
    elements = list(slot.descendants())
    # Labels carry `text`; a `code_block` is `ui.html` and carries `content`.
    said = [str(e.text) for e in elements if getattr(e, "text", None)]
    said += [str(e.content) for e in elements if isinstance(e, ui.html)]
    return " ".join(said), elements


def test_the_journal_is_one_list_and_shut_it_is_a_word_and_a_count(tmp_path, monkeypatch):
    """No *findings about its inputs*: a finding about an input is part of why
    this table looks like it does, so both halves are one chronological list.
    Shut, the journal ships nothing but its head."""
    monkeypatch.chdir(tmp_path)
    app = _journal_project(tmp_path)
    text, _ = _report_text(app)
    assert "Journal" in text and " 2" in text
    assert "Findings about its inputs" not in text
    assert "does binning hotels" not in text, "shut: the questions are not drawn yet"


def test_open_the_journal_and_you_see_the_questions_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _journal_project(tmp_path)
    app.open_rows = frozenset({"journal:mart"})
    text, _ = _report_text(app)
    assert "are ids unique in orders?" in text, "the input's finding, in the one list"
    assert "does binning hotels to a 15km grid work?" in text
    assert text.index("are ids unique") < text.index("does binning"), "oldest first"
    assert "574 in one Paris cell" not in text, "a shut question is its question"


def test_open_a_question_and_you_get_the_whole_record(tmp_path, monkeypatch):
    """Every field the file holds, the tables as chips, the spec when it is
    another one, the queries verbatim, and a moved table as a fact."""
    monkeypatch.chdir(tmp_path)
    app = _journal_project(tmp_path)
    # The file moves after the measurement.
    pd.DataFrame({"id": [1, 2, 3], "amount": [10, 20, 30]}).to_csv(
        tmp_path / "data" / "orders.csv", index=False
    )
    catalog.index_source(tmp_path / "data" / "orders.csv", portia_dir=tmp_path / ".portia")
    engine_module.refresh_catalog(app)
    from portia import findings

    paths = {f["question"]: f["path"] for f in findings.load_all(tmp_path)}
    app.open_rows = frozenset({"journal:mart", *(f"finding:{p}" for p in paths.values())})
    text, elements = _report_text(app)

    assert "no. 574 in one Paris cell against 52 localities" in text
    assert "matched on division centroids instead" in text
    assert "select 1" in text and "574" in text, "the query and its result, verbatim"
    chips = {e.text for e in elements if "type-chip" in e._classes}
    assert {"orders.id", "regions", "orders"} <= chips
    assert "measured before orders changed" in text
    assert "recorded for stg" in text, "the input's finding names the spec it was for"
    assert "recorded for mart" not in text, "this model's own finding does not"


def test_the_journal_states_when_it_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    text, _ = _report_text(app)
    assert "Journal" in text and "nothing recorded for this table yet" in text


def test_a_drop_onto_another_card_is_refused(tmp_path, monkeypatch):
    """Two cards never stack: the offset is not recorded and nothing is written."""
    from portia.ui import graph as graph_module
    from portia.ui import workflow

    monkeypatch.chdir(tmp_path)
    app = _canvas_project(tmp_path)
    app.expanded = frozenset()
    with _as_app(workflow, app):
        workflow.pane.refresh = lambda *a, **k: None
        _quiet_canvas(monkeypatch)
        _, placed, _ = workflow._layout()
        stg = next(n for n in placed.nodes if n.id == "stg")
        mart = next(n for n in placed.nodes if n.id == "mart")
        workflow.move_card("mart", stg.x - mart.x, stg.y - mart.y)
        assert app.card_offsets == {}
        assert engine_module.remembered_layout(tmp_path) == {}
        # Beside it is fine.
        workflow.move_card("mart", 0, stg.h + graph_module.ROW_GAP)
        assert app.card_offsets == {"mart": (0, stg.h + graph_module.ROW_GAP)}


# --- where the model comes from (`docs/PROVIDERS.md` §5) --------------------


def test_the_provider_travels_with_the_chat_and_every_exchange_in_it():
    """A parked client cannot change server, so the chat's provider wins over
    the window's default once it has one (§4.2)."""
    app = state.App()
    app.provider = "ollama"
    chat = app.start_exchange("ask", model="qwen3:8b", effort=None)
    assert chat.provider == "ollama" and chat.exchange.provider == "ollama"
    chat.exchange.running = False
    app.provider = "anthropic"
    again = app.start_exchange("again", model="qwen3:8b", effort=None, chat=chat)
    assert again is chat and chat.exchange.provider == "ollama"


def test_the_default_model_follows_the_provider():
    """A Claude name sent to Ollama is a guaranteed refusal (§4.3)."""
    from portia.agent import providers

    app = state.App()
    assert app.default_model == providers.get("anthropic").default_model
    app.provider = "ollama"
    assert app.default_model == providers.get("ollama").default_model


def test_a_refused_preflight_opens_no_exchange_and_says_why_in_the_composer(monkeypatch):
    """A refusal is not an exchange: nothing was sent, so nothing is logged and
    no prompt row is drawn. The reason and the remedy land where the message
    was typed (§4.5, §5)."""
    import asyncio

    from portia.agent import providers
    from portia.ui import exchange, transcript
    from portia.ui.state import APP

    refused = providers.Preflight(
        False, reason="qwen3:8b is not installed.", remedy="ollama pull qwen3:8b"
    )

    async def fake_preflight(app, kind, model):
        assert (kind, model) == ("ollama", "qwen3:8b")
        return refused

    monkeypatch.setattr(engine_module, "preflight", fake_preflight)
    monkeypatch.setattr(transcript.pane, "refresh", lambda: None)
    APP.chats = []
    APP.spend_alert = None
    try:
        asyncio.run(exchange.start("hello", model="qwen3:8b", effort=None, provider="ollama"))
        assert APP.chats == []
        assert APP.spend_alert == ("qwen3:8b is not installed.", "ollama pull qwen3:8b")
        assert APP.preflight_status == ""
    finally:
        APP.chats = []
        APP.spend_alert = None


def test_the_picker_draws_the_providers_list_and_never_lists_on_the_loop():
    """`Provider.models` is a network call; the component reads what
    `engine.list_models` stored and nothing else (§4.3)."""
    import inspect

    source = inspect.getsource(c.model_effort) + inspect.getsource(c._drawn_list)
    assert "_drawn_list(kind)" in inspect.getsource(c.model_effort)
    assert "provider_models" in source
    assert ".models()" not in source + inspect.getsource(c.model_menu)


def test_a_provider_with_a_written_list_offers_it_before_anything_is_listed():
    """Anthropic has no server to ask, so nothing listed it when the page
    opened and the picker held one model until a provider switch asked. Codex
    on an account names its catalog the same way; on a local server its list
    is the server's, like every local provider's."""
    import inspect

    from portia.agent import providers

    anthropic = providers.get("anthropic")
    assert [m.name for m in anthropic.static_models] == list(providers.anthropic.MODELS)
    assert anthropic.models() == list(anthropic.static_models)
    assert providers.get("codex").static_models == providers.codex.CATALOG
    assert all(not providers.get(kind).static_models for kind in ("ollama", "llamacpp"))
    assert "static_models" in inspect.getsource(c._drawn_list)


def _picker_rows(slot) -> list[str]:
    """The model names a drawn picker offers, in the order it draws them."""
    return [
        str(e.props["data-name"]).strip("\"'")
        for e in slot.descendants()
        if "modelpick-row" in e.classes and "data-name" in e.props
    ]


def test_a_window_that_never_switched_provider_offers_every_anthropic_model():
    """The picker as drawn on a fresh app, nothing listed, read off its rows:
    every model Claude Code offers, current and legacy, in its order."""
    from portia.agent import providers
    from portia.ui import state

    fresh = state.App()
    assert fresh.provider_models == {}
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None)
    finally:
        state.APP = original
    assert _picker_rows(slot) == list(providers.anthropic.MODELS)
    legacy = [e for e in slot.descendants() if "modelpick-legacy" in e.classes]
    assert len(legacy) == sum(m.legacy for m in providers.anthropic.CATALOG) == 7


def test_the_picker_is_one_control_for_the_provider_and_the_model():
    """Closed, the mark and the model's label; open, a rail over every offered
    provider with each one's list drawn beside it (the user's call,
    2026-09-23, after T3 Code's picker)."""
    from portia.agent import providers
    from portia.ui import state

    fresh = state.App(provider="anthropic", model="claude-opus-5-5")
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None, on_provider=lambda *a: None)
    finally:
        state.APP = original
    drawn = list(slot.descendants())
    trigger = next(e for e in drawn if "modelpick-trigger" in e.classes)
    names = [e.text for e in trigger.descendants() if "modelpick-trigger-name" in e.classes]
    assert names == ["Claude Opus 5.5"]
    rails = [e.props["data-modelpick-rail"] for e in drawn if "modelpick-rail-item" in e.classes]
    assert rails == list(providers.offered_kinds())
    panels = [e for e in drawn if "modelpick-panel" in e.classes]
    assert [("data-active" in p.props) for p in panels] == [k == "anthropic" for k in rails]
    selected = [e for e in drawn if "modelpick-row" in e.classes and "data-selected" in e.props]
    assert len(selected) == 1


def test_a_parked_chat_changes_model_and_never_provider():
    """The rail holds the one mark a parked client runs on (§4.2)."""
    from portia.ui import state

    fresh = state.App(provider="anthropic", model="claude-sonnet-5")
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(
                fresh, lambda effort: None, on_provider=lambda *a: None, provider_fixed=True
            )
    finally:
        state.APP = original
    rails = [e for e in slot.descendants() if "modelpick-rail-item" in e.classes]
    assert [r.props["data-modelpick-rail"] for r in rails] == ["anthropic"]
    assert "claude-opus-5-5" in _picker_rows(slot)


def test_a_fresh_window_opens_the_picker_on_current_models_with_legacy_folded():
    """The user, 2026-09-23: the default was Haiku 4.5, a legacy model, so
    every fresh picker opened on its fold."""
    from portia.agent import providers
    from portia.ui import state

    default = providers.anthropic.DEFAULT_MODEL
    assert not next(m for m in providers.anthropic.CATALOG if m.name == default).legacy
    fresh = state.App()
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None)
    finally:
        state.APP = original
    assert fresh.model == default
    panel = next(e for e in slot.descendants() if "modelpick-panel" in e.classes)
    assert "data-legacy-open" not in panel.props


def test_a_legacy_pick_opens_its_fold():
    from portia.ui import state

    fresh = state.App(provider="anthropic", model="claude-haiku-4-5")
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None)
    finally:
        state.APP = original
    panel = next(e for e in slot.descendants() if "modelpick-panel" in e.classes)
    assert "data-legacy-open" in panel.props


def test_a_server_with_no_models_offers_none_and_says_nothing_about_effort():
    """The picker showed the provider's default name over a line saying nothing
    is installed, and under it a sentence about a control that is not there."""
    from portia.ui import state

    fresh = state.App(provider="ollama")
    fresh.provider_models["ollama"] = []
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None)
    finally:
        state.APP = original
    drawn = list(slot.descendants())
    trigger = next(e for e in drawn if "modelpick-trigger" in e.classes)
    assert [e.text for e in trigger.descendants() if "modelpick-trigger-name" in e.classes] == [
        "no models"
    ]
    assert not trigger.enabled
    assert not any("modelpick-menu" in e.classes for e in drawn)
    detail = next(e for e in drawn if "spend-detail" in e.classes)
    assert not list(detail.descendants()), "the row is reserved, and empty"


def test_the_picker_splits_current_from_legacy_and_keeps_a_name_nobody_listed():
    from portia.agent import providers

    listed = [
        providers.Model("gpt-6-astra", label="GPT-6 Astra"),
        providers.Model("gpt-5.5", label="GPT-5.5", legacy=True),
    ]
    current, legacy = c.model_rows("gpt-6-astra", listed)
    assert [m.name for m in current] == ["gpt-6-astra"]
    assert [m.name for m in legacy] == ["gpt-5.5"]
    typed, _ = c.model_rows("gpt-oss:20b", [providers.Model("qwen3:8b", 1, "")])
    assert [m.name for m in typed] == ["gpt-oss:20b", "qwen3:8b"]
    assert c.shown_name("gpt-5.5", listed) == "GPT-5.5"
    assert c.shown_name("qwen3:8b", None) == "qwen3:8b"


def test_a_local_model_is_listed_with_the_vendors_own_size():
    from portia.agent import providers
    from portia.ui import state

    fresh = state.App(provider="ollama", model="qwen3:8b")
    fresh.provider_models["ollama"] = [providers.Model("qwen3:8b", 5_225_388_164, "8.2B")]
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None)
    finally:
        state.APP = original
    sizes = [e.text for e in slot.descendants() if "modelpick-size" in e.classes]
    assert sizes == ["5.2 GB"]


def test_a_provider_that_is_down_shows_how_to_start_it_and_an_empty_one_how_to_add():
    from portia.agent import providers

    ollama = providers.get("ollama")
    down = providers.Status(reachable=False, detail="not answering")
    assert c._provider_note(ollama, None, down) == ollama.start_remedy
    assert "ollama pull <name>" in c._provider_note(ollama, [], providers.Status(True))
    assert c._provider_note(ollama, [providers.Model("x")], providers.Status(True)) == ""
    assert c._provider_note(providers.get("anthropic"), None, None) == ""


def test_every_provider_has_a_mark_the_stylesheet_knows():
    """One SVG per kind under `assets/providers/`, and a mask rule for each."""
    from pathlib import Path

    from portia.agent import providers
    from portia.ui import theme

    css = (theme.ASSETS / "portia.css").read_text(encoding="utf-8")
    for kind in providers.KINDS:
        assert (Path(theme.ASSETS) / "providers" / f"{kind}.svg").exists(), kind
        assert f".provider-glyph-{kind}" in css, kind


def test_an_asset_url_changes_when_the_asset_does():
    """The stylesheet names `/portia-assets/…`; the page gets a route stamped
    with the files' contents, so a changed mark is never the browser's cached
    one (2026-09-08, the provider marks)."""
    from portia.ui import theme

    assert theme.ASSET_ROUTE.startswith(theme.ASSET_TOKEN + "/")
    stamp = theme.ASSET_ROUTE.rsplit("/", 1)[1]
    assert len(stamp) == 8 and stamp == theme._asset_stamp()
    assert theme.LOGO.startswith(theme.ASSET_ROUTE)
    css = theme.CSS.read_text(encoding="utf-8")
    assert theme.ASSET_TOKEN + "/providers/" in css
    assert theme.ASSET_ROUTE not in css, "the stylesheet must stay stable; the page rewrites it"


def test_a_sent_message_is_drawn_before_its_own_event_arrives():
    """From Send until the binary has started, the exchange is the only place
    the message exists; the pane draws it from there and never says *no
    messages yet* over it (2026-09-08, a minute of nothing on Ollama)."""
    from portia.agent import events
    from portia.ui import exchange, transcript

    app = state.App()
    chat = app.start_exchange("what is in orders?", model="mistral:latest", effort=None)
    assert transcript._starting(chat)
    assert not chat.exchange.opened

    exchange._record(events.prompt_event("what is in orders?", model="mistral:latest"), chat)
    assert chat.exchange.opened
    assert not transcript._starting(chat)

    chat.exchange.running = False
    assert not transcript._starting(chat)


def test_a_send_carries_no_effort_to_a_provider_that_ignores_it():
    """`App.effort` keeps the last value picked for Claude; a send on Ollama must
    not hand it to `build_options`, which refuses it (2026-09-08: every send on
    Ollama in the window died on that refusal before the client started)."""
    from portia.ui import exchange

    assert exchange.spend_effort("anthropic", "low") == "low"
    assert exchange.spend_effort("ollama", "low") is None
    assert exchange.spend_effort("ollama", None) is None


def test_a_chat_exchange_that_died_before_a_reply_is_drawn_not_hidden():
    from portia.ui import transcript

    app = state.App()
    chat = app.start_exchange("hello", model="qwen2.5:3b", effort=None, provider="ollama")
    assert not transcript._failed(chat)
    chat.exchange.error = "ValueError: Ollama does not honour effort; leave it unset"
    chat.exchange.running = False
    assert transcript._failed(chat)
    assert not transcript._starting(chat)


def test_a_local_models_cost_is_not_drawn_because_it_is_about_nothing(monkeypatch):
    """The SDK prices a result as if the model were Claude's. On Ollama the
    pane said *~$0.0076* for qwen2.5:3b on the user's own machine."""
    from portia.agent import events
    from portia.ui import exchange

    monkeypatch.setattr(exchange, "_sync_artifacts", lambda: None)
    app = state.App()
    result = events.Event(events.RESULT, {"subtype": "success", "cost_usd": 0.0076, "usage": {}})
    local = app.start_exchange("hi", model="qwen2.5:3b", effort=None, provider="ollama")
    exchange._record(result, local)
    assert local.exchange.cost_usd is None
    local.exchange.running = False
    # A new chat, because the open one keeps its provider (§4.2).
    remote = state.App().start_exchange(
        "hi", model="claude-haiku-4-5", effort=None, provider="anthropic"
    )
    exchange._record(result, remote)
    assert remote.exchange.cost_usd == 0.0076


# --- the provider is a select, and a server portia can start has a panel (§4.9) ---


def test_the_provider_is_picked_on_the_pickers_rail_and_the_glyph_stays():
    """The user's call, 2026-09-14, kept through the 2026-09-23 overhaul: one
    control to read before the model, not one segment per provider, and the
    mark still says what a chat runs on."""
    import inspect

    from portia.ui import components

    source = inspect.getsource(components.model_menu)
    # The kinds are the machine's enabled ones (`providers.offered_kinds`,
    # Settings → Providers), never the whole list by name (2026-09-22).
    assert "providers.offered_kinds()" in source
    assert "provider_glyph(kind, tip=False)" in source
    assert not hasattr(components, "_provider_pick")
    # A pick on another provider carries the model picked with it.
    assert "on_provider(picked_kind, name)" in source


def test_where_the_open_picker_is_looking_never_reaches_the_server():
    """The rail, the search and the fold are the client's (`modelpick.js`);
    only a pick is an event."""
    from portia.ui import theme

    assert theme.MODELPICK_JS in theme.BEHAVIOUR
    script = theme.MODELPICK_JS.read_text(encoding="utf-8")
    assert "emitEvent" not in script
    for hook in ("data-modelpick-rail", "data-modelpick-search", "data-modelpick-fold"):
        assert hook in script


def test_every_picker_offers_the_start_panel_and_the_page_builds_it():
    """A provider portia can start gets the panel wherever the picker is drawn,
    and the dialog is built once at page level like the connect dialog."""
    import inspect

    from portia.ui import app, screens, settings, transcript

    for module in (settings, transcript, screens):
        assert "on_start=" in inspect.getsource(module), f"{module.__name__} offers no start"
    assert "screens.build_server_dialog()" in inspect.getsource(app.page)
    assert (
        "on_start"
        in inspect.signature(
            __import__("portia.ui.components", fromlist=["x"]).model_effort
        ).parameters
    )


def test_the_start_panel_is_a_form_over_the_machines_one_configuration():
    from portia.agent.providers import llamacpp

    app = state.App()
    assert app.server_form == {} and app.server_status == "" and app.server_error == ""
    assert state.STARTING == "starting"
    assert llamacpp.ServerConfig("/m.gguf").as_form().keys() == {"model", "context", "port"}


def test_the_start_panel_never_redraws_the_box_being_typed_in():
    """A refresh per keystroke rebuilds the input and takes the caret with it;
    the command line under the fields is updated in place instead."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._server_field)
    assert "refresh()" not in source and "_COMMAND_LINE" in source


def test_a_listing_that_raises_never_leaves_the_picker_spinning():
    """`engine.list_models` keeps whatever went wrong as the status; the
    background task used to die with it and nothing redrew the pane."""
    import asyncio

    from portia.agent import providers
    from portia.ui import engine

    class Broken:
        def status(self):
            return providers.Status(reachable=True, detail="up")

        def models(self):
            raise RuntimeError("HTTP 404")

    status, models = engine._ask_provider(Broken())
    assert status.reachable is False and "HTTP 404" in status.detail and models == []

    app = state.App()
    real = engine._ask_provider
    try:
        engine._ask_provider = lambda provider: (_ for _ in ()).throw(ValueError("boom"))
        asyncio.run(engine.list_models(app, "llamacpp"))
    finally:
        engine._ask_provider = real
    assert "llamacpp" not in app.models_listing
    assert app.provider_status["llamacpp"].reachable is False
    assert "boom" in app.provider_status["llamacpp"].detail


def _draw_server_panel(monkeypatch, registry, form, elsewhere=False):
    from pathlib import Path

    from portia.agent.providers import llamacpp
    from portia.ui import screens

    monkeypatch.setattr(llamacpp, "registry_models", lambda: [Path(p) for p in registry])
    monkeypatch.setattr(llamacpp, "running", lambda: None)
    monkeypatch.setattr(screens, "_SERVER_ELSEWHERE", elsewhere)
    monkeypatch.setattr(state.APP, "server_form", dict(form))
    monkeypatch.setattr(state.APP, "server_status", "")
    monkeypatch.setattr(state.APP, "server_error", "")
    with ui.element("div") as slot:
        screens._server_panel()
    return slot


def test_the_start_panel_picks_off_the_registry_and_keeps_a_path_behind_a_toggle(monkeypatch):
    """The user, 2026-09-23: no *Elsewhere…* option in the list; a path or a
    repository is behind a toggle, the way Settings keeps its switches behind
    *Customize*, and every sentence the panel had is behind a *?*."""
    from portia.ui import screens

    slot = _draw_server_panel(
        monkeypatch, ["/m/Qwen3-8B.gguf"], {"model": "/m/Qwen3-8B.gguf", "context": "32768"}
    )
    drawn = list(slot.descendants())
    select = next(e for e in drawn if isinstance(e, ui.select))
    assert list(select.options) == ["/m/Qwen3-8B.gguf"] and select.value == "/m/Qwen3-8B.gguf"
    texts = [e.text for e in drawn if isinstance(e, ui.label)]
    for sentence in (screens.SERVER_CONTEXT_HINT, screens.SERVER_MODEL_HINT):
        assert sentence not in texts, "a sentence under a field is behind its ?"
    assert not [e for e in drawn if "field-hint" in e.classes]
    tips = [e for e in drawn if "help-tip" in e.classes]
    assert len(tips) == 4, "model, path or repository, context, port"
    assert not [e for e in drawn if "server-elsewhere" in e.classes], "shut until asked"
    labels = [e.text for e in drawn if isinstance(e, ui.button)]
    assert screens.SERVER_ELSEWHERE in labels


def test_a_model_kept_elsewhere_opens_the_path_field_and_an_empty_registry_is_dark(monkeypatch):
    slot = _draw_server_panel(monkeypatch, [], {"model": "Qwen/Qwen3-8B-GGUF:Q4_K_M"}, True)
    drawn = list(slot.descendants())
    select = next(e for e in drawn if isinstance(e, ui.select))
    assert not select.enabled and select.value is None
    boxes = [e for e in drawn if isinstance(e, ui.input) and e.value == "Qwen/Qwen3-8B-GGUF:Q4_K_M"]
    assert boxes, "the repository is in the path field"


def test_the_start_panel_redraws_the_part_that_changed_and_never_the_card():
    """The Settings pass (2026-09-23): a pick, the toggle, Start and Stop each
    redraw their own part."""
    import inspect

    from portia.ui import screens

    for fn in (
        screens._server_model_picked,
        screens._server_path_typed,
        screens._toggle_elsewhere,
        screens._server_changed,
        screens._start_server,
        screens._stop_server,
        screens._server_field,
    ):
        assert "_server_panel.refresh()" not in inspect.getsource(fn), fn.__name__
    assert "_server_path.refresh()" in inspect.getsource(screens._toggle_elsewhere)
    assert "_server_model.refresh()" not in inspect.getsource(screens._toggle_elsewhere)
    assert "_server_state.refresh()" in inspect.getsource(screens._server_changed)


def test_no_press_inside_a_floating_card_redraws_the_card():
    """The user, 2026-09-23: *every floating card gets a full refresh when it's
    clicked instead of just updating what is happening*. Each handler below is a
    press inside a dialog, a menu or the composer, and each used to redraw the
    card it sat in (or the pane behind it). None of them may now; what each may
    redraw instead is the part it changed."""
    import inspect

    from portia.ui import feedback, screens, settings, transcript, workflow

    def body(fn) -> str:
        fn = getattr(fn, "func", fn)
        source = inspect.getsource(fn)
        # The code, not the docstring that says what it used to do.
        return source.split('"""')[-1] if source.count('"""') >= 2 else source

    never = {
        "_connect_panel.refresh()": (
            screens._pick,
            screens._start_new,
            screens._back_to_pick,
            screens._pick_provider,
            screens._set_auth,
            screens._connect_now,
        ),
        "_panel.refresh()": (feedback._set_include, feedback._post),
        # `_refresh` is the whole add-data card.
        "_refresh()": (
            screens._browse_to,
            screens._choose_folder,
            screens._repick,
            screens._keep_folder,
            screens._interpret_switched,
            screens._set_indexing_effort,
            screens._set_indexing_provider,
            screens._list_databases,
            screens._redraw_pickers,
        ),
        "pane.refresh()": (
            transcript._set_effort,
            transcript._set_provider,
            transcript._mode_changed,
            transcript._resolve_write,
            transcript._allow_all,
            transcript._set_indexing_effort,
            screens._redraw_pickers,
            workflow._toggle_visible,
            workflow._select_all,
        ),
    }
    for call, handlers in never.items():
        for fn in handlers:
            assert call not in body(fn).replace(f"_{call}", ""), f"{fn.__name__}: {call}"
    assert "_auth_secret.refresh()" in body(screens._set_auth)
    assert "_pick_secret.refresh()" in body(screens._pick)
    assert "_repo_body.refresh()" in body(screens._browse_to)
    assert "_canvas.refresh(" in body(workflow._set_visible_in_place)
    # The profile switch's line is set in place; the card is never redrawn.
    assert ".on_value_change(_refresh)" not in inspect.getsource(screens._profile_toggle)
    # Settings' copy of the composer's controls follows without a redraw of either.
    assert "transcript.show_mode()" in body(settings._mode_changed)
    assert "settings.show_mode()" in body(transcript._mode_changed)


def test_starting_is_on_screen_before_the_load_and_the_panel_stays_open_after():
    """The user pressed Start and saw nothing for the length of a model load
    (2026-09-14): the panel had been redrawn before the state was set, and it
    closed itself on success."""
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._start_server)
    assert source.index("APP.server_status = state.STARTING") < source.index(
        "await engine.start_server"
    )
    assert "_close_server_dialog()" not in source
    assert "SERVER_SUB" not in inspect.getsource(screens), (
        "the subtitle was removed on the user's call"
    )


def test_the_pickers_one_control_reads_start_or_stop_by_state_and_always_opens_the_panel():
    import inspect

    from portia.agent import providers
    from portia.agent.providers import llamacpp
    from portia.ui import components

    source = inspect.getsource(components.server_controls)
    for name in ("START_SERVER", "STOP_SERVER", "STARTING_SERVER", "provider.started()"):
        assert name in source, name
    assert source.count("on_start(kind)") == 2, "both labels open the same panel"
    # Under the picker for the picked provider, and inside the open picker on
    # the provider's own panel, which is the only way to it while another
    # provider is picked (2026-09-23).
    assert "server_controls(kind, on_start)" in inspect.getsource(components.model_effort)
    assert "server_controls(kind, on_start)" in inspect.getsource(components._model_panel)
    assert not providers.get("anthropic").started() and not providers.get("ollama").started()
    assert not llamacpp.PROVIDER.started()


def test_a_status_light_is_a_closed_list_of_kinds_and_only_live_moves():
    """Four kinds, none of them a rank. The fade's period is written twice, in
    the CSS that runs it and in the Python that phases it, so they are pinned."""
    import inspect
    from pathlib import Path

    from portia.ui import artifacts, settings, transcript

    assert c.LIGHTS == ("live", "waiting", "on", "off")
    with pytest.raises(ValueError, match="unknown status light"):
        c.status_light("urgent")

    css = (Path(c.__file__).parent / "assets" / "portia.css").read_text(encoding="utf-8")
    for kind in c.LIGHTS:
        assert f".status-light--{kind}" in css
    assert f"animation: p-live {c.LIVE_PERIOD}s" in css
    assert css.count("animation: p-live") == 1, "one light moves"
    assert ".chat-dot" not in css, "the grey dot it replaced is gone, not kept beside it"

    assert "c.LIVE" in inspect.getsource(transcript._dot)
    for module in (artifacts, settings):
        assert "c.ON if APP.connected else c.OFF" in inspect.getsource(module)


# --- one table failing is not the run failing (2026-09-21) -------------------------


class _OneTableOverflows(_MetadataOnlyWarehouse):
    """A warehouse whose ``BROKEN`` table cannot be scanned, as Snowflake's could not."""

    remote = True

    def table_facts(self, qualified):
        return {**super().table_facts(qualified), "at": "2026-09-21T10:00:00"}


def test_one_table_failing_its_profile_does_not_end_the_run(tmp_path, monkeypatch):
    """The first real Snowflake account: thirty tables, the eleventh overflowed
    in the warehouse, and the exception left the loop. Nineteen tables were
    never tried, the catalog was never reloaded so the ten that *had* finished
    drew as metadata only with their profiles on disk, and nothing said why.
    In this fixture the table that fails is in upper case, as the real one was."""
    import asyncio

    app = _warehouse_project(
        tmp_path, monkeypatch, "memory.sales.first", "memory.sales.BROKEN", "memory.sales.third"
    )

    seen_mid_run: list[bool] = []

    def profile_remote(name, con, *, portia_dir):
        if name == "third":
            # A left pane opened now draws the first table as profiled: most of
            # an hour of scans is too long to wait for the run's end to say so.
            seen_mid_run.append(catalog.is_profiled(app.sources["first"]))
        if name == "BROKEN":
            raise RuntimeError(
                "Number out of representable range: type FIXED[SB16](38,0)\nLINE 1: SELECT"
            )
        path = tmp_path / ".portia" / "sources" / f"{name}.yaml"
        catalog._write(path, dict(catalog._read(path), profiled={"at": "2026-09-21T10:00:00"}))
        return {}

    monkeypatch.setattr(engine_module.catalog, "profile_remote", profile_remote)
    monkeypatch.setattr(engine_module, "connect", lambda: object())
    monkeypatch.setattr(engine_module, "sync_knowledge", lambda app_: "")
    seen: list[str] = []

    ran = asyncio.run(
        engine_module.profile_tables(
            app, ["first", "BROKEN", "third"], on_progress=lambda d, t, n: seen.append(n)
        )
    )

    assert seen == ["first", "BROKEN", "third"], "the table after the failure is still tried"
    assert seen_mid_run == [True]
    assert ran.names == ["first", "third"] and ran.failed == ["BROKEN"]
    assert ran.items == ["first", "third"], "what finished, by name: not a prefix of what was asked"
    # One line, and not the statement DuckDB or a driver quotes back under it.
    assert app.indexing_failed == {
        "BROKEN": "RuntimeError: Number out of representable range: type FIXED[SB16](38,0)"
    }
    # The window's catalog is what is on disk: the two that finished are profiled.
    profiled = {r.name: r.profiled for r in engine_module.source_states(app)}
    assert profiled == {"first": True, "BROKEN": False, "third": True}
    assert catalog.is_profiled(app.sources["first"]), "reloaded, not only written"

    # A later success clears the sentence; nothing else does.
    monkeypatch.setattr(engine_module.catalog, "profile_remote", lambda *a, **k: {})
    asyncio.run(engine_module.profile_tables(app, ["BROKEN"]))
    assert app.indexing_failed == {}


def test_a_stop_is_still_not_a_failure(tmp_path, monkeypatch):
    import asyncio

    from portia.core import cancel

    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.first", "memory.sales.second")

    def profile_remote(name, con, *, portia_dir):
        raise cancel.Cancelled()

    monkeypatch.setattr(engine_module.catalog, "profile_remote", profile_remote)
    monkeypatch.setattr(engine_module, "connect", lambda: object())
    monkeypatch.setattr(engine_module, "sync_knowledge", lambda app_: "")
    seen: list[str] = []

    ran = asyncio.run(
        engine_module.profile_tables(
            app, ["first", "second"], on_progress=lambda d, t, n: seen.append(n)
        )
    )
    assert seen == ["first"], "a stop ends the run where a failure does not"
    assert ran.names == [] and ran.failed == [] and app.indexing_failed == {}


def test_the_indexing_tab_takes_its_spinner_down_when_the_run_raises(tmp_path, monkeypatch):
    """The spinner the user found still turning over a job that had died: the
    status was cleared in a ``finally`` and the panes were refreshed after it."""
    import asyncio

    from nicegui import core

    from portia.ui import artifacts, transcript

    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.orders")
    app.index_ticks = frozenset({"orders"})
    refreshed: list[str] = []

    async def fake_profile_tables(app_, names, *, on_progress=None, stop=None):
        raise RuntimeError("the engine itself broke")

    monkeypatch.setattr(engine_module, "profile_tables", fake_profile_tables)
    monkeypatch.setattr(
        transcript.pane, "refresh", lambda *a, **k: refreshed.append(app.indexing_status)
    )
    monkeypatch.setattr(artifacts.pane, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(transcript._index_actions, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(ui, "notify", lambda *a, **k: None)

    async def press() -> None:
        monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
        with pytest.raises(RuntimeError):
            await transcript._index_ticked()

    with _as_app(transcript, app):
        asyncio.run(press())
    assert refreshed == [""], "the pane is redrawn once the status is clear, error or not"


def test_a_warehouse_project_lists_no_local_file_as_a_source(tmp_path, monkeypatch):
    """A project folder on a work machine is a repository. With no `data_dir`
    the walk is the whole root, so thirty scoped tables were listed among
    seventy stray files by stem: *forecast_runs* ten times, each
    tickable for an Index that would have profiled it into a project that
    cannot join it to anything. The left tree already drew none of them."""
    app = _warehouse_project(tmp_path, monkeypatch, "memory.sales.ORDERS")
    for run in ("run1", "run2"):
        (tmp_path / "experiments" / run).mkdir(parents=True)
        pd.DataFrame({"x": [1]}).to_csv(
            tmp_path / "experiments" / run / "forecast_runs.csv", index=False
        )

    assert [s.name for s in engine_module.source_states(app)] == ["ORDERS"]
    in_the_tree = [
        n.ident
        for db in engine_module.warehouse_tree(app)
        for sch in db.children
        for n in sch.children
    ]
    assert in_the_tree == ["ORDERS"], "the tab and the left pane list the same tables"
    assert engine_module.project_tree(app) == ()

    # A local project still lists what it has not indexed.
    catalog.set_connection(None, portia_dir=app.portia_dir)
    engine_module.refresh_catalog(app)
    names = [s.name for s in engine_module.source_states(app)]
    assert names.count("forecast_runs") == 2


# --- charts a host drew, arriving through `.portia/drawn/` --------------------


def _stash(root, tab="rates", rate=1):
    from portia import figures

    return figures.stash(
        {
            "tab": tab,
            "question": "how do rates differ?",
            "sql": "SELECT 1",
            "inputs": ["t"],
            "vega": {"mark": "bar"},
            "columns": ["RISK", "rate"],
            "rows": [{"RISK": "a", "rate": rate}],
        },
        root / ".portia",
    )


def test_opening_a_project_lists_what_a_host_drew_and_opens_none_of_it(tmp_path, monkeypatch):
    """Drawn while no window was open. Unsaved charts in the gallery, not forty tabs."""
    monkeypatch.chdir(tmp_path)
    _stash(tmp_path)
    app = App()
    engine_module.open_project(tmp_path, app)
    (chart,) = app.charts
    assert chart.name == "rates" and chart.stashed and chart.closed
    assert app.tabs == []


def test_a_chart_is_taken_once_and_again_only_when_it_is_drawn_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = App()
    engine_module.open_project(tmp_path, app)
    _stash(tmp_path)
    assert [c.name for c in engine_module.take_drawn(app)] == ["rates"]
    assert engine_module.take_drawn(app) == []
    time.sleep(0.01)
    _stash(tmp_path, rate=2)
    (again,) = engine_module.take_drawn(app)
    assert again.rows == [{"RISK": "a", "rate": 2}]


def test_keeping_or_discarding_a_hosts_chart_takes_its_stash(tmp_path, monkeypatch):
    """One picture is never two artifacts, and a discarded one must not come back."""
    from portia import figures

    monkeypatch.chdir(tmp_path)
    app = App()
    engine_module.open_project(tmp_path, app)
    _stash(tmp_path, "kept")
    _stash(tmp_path, "dropped")
    kept, dropped = sorted(engine_module.take_drawn(app), key=lambda c: c.name == "dropped")
    engine_module.save_figure(app, kept)
    engine_module.unstash(app, kept)
    engine_module.unstash(app, dropped)
    assert figures.stashed(app.catalog_dir) == []
    assert [f["name"] for f in figures.load_all(tmp_path)] == ["kept"]
    assert engine_module.take_drawn(app) == []


def test_the_window_says_it_is_open_and_stops_saying_so_when_it_leaves(tmp_path, monkeypatch):
    from portia.agent import drawn

    monkeypatch.chdir(tmp_path)
    app = App()
    app.url = "http://127.0.0.1:8080"
    engine_module.open_project(tmp_path / "one", app)
    assert drawn.watching(tmp_path / "one" / ".portia") == {
        "window": "open",
        "url": "http://127.0.0.1:8080",
    }
    engine_module.open_project(tmp_path / "two", app)
    assert drawn.watching(tmp_path / "one" / ".portia")["window"] == "closed"
    assert drawn.watching(tmp_path / "two" / ".portia")["window"] == "open"


def test_the_windows_own_announcement_does_not_make_it_redraw(tmp_path, monkeypatch):
    from portia.agent import drawn

    monkeypatch.chdir(tmp_path)
    app = App()
    engine_module.open_project(tmp_path, app)
    before = engine_module.artifact_stamp(app)
    drawn.announce(app.catalog_dir, "http://somewhere-else")
    assert engine_module.artifact_stamp(app) == before
    _stash(tmp_path)
    assert engine_module.artifact_stamp(app) != before, "a drawn chart is a change"


def test_a_provider_with_nothing_to_offer_still_opens_so_the_rail_can_leave_it():
    """2026-09-23, in the browser: a pick on Codex with nobody signed in listed
    nothing, and a disabled button left the composer with no way back."""
    from portia.ui import state

    fresh = state.App(provider="codex", model="gpt-6-sol")
    fresh.provider_models["codex"] = []
    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None, on_provider=lambda *a: None)
    finally:
        state.APP = original
    drawn = list(slot.descendants())
    trigger = next(e for e in drawn if "modelpick-trigger" in e.classes)
    assert trigger.enabled
    assert any("modelpick-menu" in e.classes for e in drawn)
    codex = next(
        e
        for e in drawn
        if "modelpick-panel" in e.classes and e.props["data-modelpick-panel"] == "codex"
    )
    assert not [e for e in codex.descendants() if "modelpick-row" in e.classes]


def test_a_name_picked_before_its_list_arrived_reads_as_its_label():
    assert c.shown_name("gpt-6-sol", [], "codex") == "GPT-6 Sol"
    assert c.shown_name("claude-opus-5-5", None, "anthropic") == "Claude Opus 5.5"
    assert c.shown_name("qwen3:8b", None, "ollama") == "qwen3:8b"


# --- the open picker survives a listing (2026-09-23) ------------------------------


def _draw_picker(fresh, **kw):
    from portia.ui import state

    original, state.APP = state.APP, fresh
    try:
        with ui.element("div") as slot:
            c.model_effort(fresh, lambda effort: None, on_provider=lambda *a: None, **kw)
    finally:
        state.APP = original
    return slot


def test_a_listing_redraws_an_open_picker_in_place_and_the_pane_once_it_shuts(monkeypatch):
    """Pressing *List models* shut the menu it was pressed in, because the
    listing redrew the pane and the pane is what the menu hangs from."""
    from portia.ui import state

    slot = _draw_picker(state.App(provider="anthropic", model="claude-sonnet-5"))
    menu = next(e for e in slot.descendants() if "modelpick-menu" in e.classes)
    bodies: list[str] = []
    panes: list[str] = []
    monkeypatch.setattr(c, "_PICKERS", [(menu, lambda: bodies.append("body"))])
    monkeypatch.setattr(c, "_HELD", [])

    def pane() -> None:
        panes.append("pane")

    c.redraw_behind_picker(pane)
    assert panes == ["pane"], "shut, a redraw runs at once"

    menu.value = True
    c.redraw_behind_picker(pane)
    c.redraw_behind_picker(pane)
    assert bodies == ["body", "body"] and panes == ["pane"], "open, the body redraws alone"
    menu.value = False
    assert panes == ["pane", "pane"], "shut again, the pane redraws once"


def test_a_menu_deleted_with_its_pane_does_not_hold_redraws_forever(monkeypatch):
    from portia.ui import state

    slot = _draw_picker(state.App(provider="anthropic", model="claude-sonnet-5"))
    menu = next(e for e in slot.descendants() if "modelpick-menu" in e.classes)
    menu.value = True
    monkeypatch.setattr(c, "_PICKERS", [(menu, lambda: None)])
    monkeypatch.setattr(c, "_HELD", [])
    panes: list[str] = []
    c.redraw_behind_picker(lambda: panes.append("pane"))
    assert panes == []
    slot.delete()
    c._flush_behind_picker()
    assert panes == ["pane"]


def test_every_listing_host_goes_through_the_picker_helper():
    import inspect

    from portia.ui import screens, settings, transcript

    for module in (settings, transcript, screens):
        source = inspect.getsource(module._list_models)
        assert "c.list_models_behind_picker(" in source, module.__name__
        assert "engine.list_models" not in source, module.__name__


def test_llamacpp_can_be_started_from_inside_the_picker_while_another_is_picked():
    """You cannot pick a model off a server that is not running, so the start
    panel has to be reachable from llama.cpp's own panel (2026-09-23)."""
    from portia.agent import providers
    from portia.ui import state

    fresh = state.App(provider="anthropic", model="claude-sonnet-5")
    fresh.provider_status["llamacpp"] = providers.Status(reachable=False, detail="down")
    fresh.provider_models["llamacpp"] = []
    started: list[str] = []
    slot = _draw_picker(
        fresh, on_refresh=lambda kind: None, on_start=lambda kind: started.append(kind)
    )
    panel = next(
        e
        for e in slot.descendants()
        if "modelpick-panel" in e.classes and e.props["data-modelpick-panel"] == "llamacpp"
    )
    labels = [e.text for e in panel.descendants() if isinstance(e, ui.button)]
    assert c.START_SERVER in labels and "List models" in labels
    ollama = next(
        e
        for e in slot.descendants()
        if "modelpick-panel" in e.classes and e.props["data-modelpick-panel"] == "ollama"
    )
    assert c.START_SERVER not in [e.text for e in ollama.descendants() if isinstance(e, ui.button)]


def test_a_started_server_moves_the_model_with_the_provider():
    import inspect

    from portia.ui import screens

    source = inspect.getsource(screens._start_server)
    assert "APP.model = llamacpp.PROVIDER.default_model" in source
