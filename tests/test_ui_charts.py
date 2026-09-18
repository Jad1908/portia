"""The middle pane's tab strip — the rules underneath, without a browser.

`docs/VISUALIZATION.md` §3. These are the ones a screenshot would not catch:
what reusing a tab name does, what closing one leaves behind, and where focus
lands afterwards. They run against `state.App` alone, which imports no NiceGUI —
the same reason `state.py`, `graph.py` and `tree.py` do not.
"""

from __future__ import annotations

from portia.ui.state import CANVAS, LEFT, RIGHT, App, Chart


def a_chart(name: str, **kw) -> Chart:
    return Chart(name=name, question=f"why {name}?", rows=[{"x": 1}], **kw)


# --- tab zero ----------------------------------------------------------------


def test_a_fresh_app_is_on_the_canvas_with_no_tabs():
    app = App()
    assert app.charts == []
    assert app.active[LEFT] == CANVAS
    assert app.showing(LEFT) is None


def test_the_canvas_is_not_a_member_of_the_chart_list():
    """§3.2 — it cannot be closed, so it is not in the list closing works on.

    Putting it in there would make the empty middle pane representable, which is
    a state with nothing to say and no way out of.
    """
    app = App()
    app.show_chart(a_chart("Events by month"))
    assert [c.name for c in app.charts] == ["Events by month"]


# --- a chart stays in its project ---------------------------------------------


def test_leaving_a_project_takes_its_charts_and_the_strips_arrangement():
    """An unsaved chart carried into the next project is a picture of data that
    project does not have, listed as unsaved beside figures it has nothing to do
    with (2026-09-04, reported from a session)."""
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.split("Cities")
    app.figures_closed = frozenset({"old"})
    app.figures_adding = "old"

    app.leave_project()

    assert app.charts == [] and app.tabs == []
    assert app.active == {LEFT: CANVAS, RIGHT: ""}
    assert app.focus_group == LEFT and app.canvas_group == LEFT
    assert app.figures_closed == frozenset() and app.figures_adding is None


# --- reusing a name replaces (§3.3) ------------------------------------------


def test_reusing_a_tab_name_replaces_the_chart():
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(Chart(name="Events by month", rows=[{"x": 99}]))
    assert len(app.charts) == 1
    assert app.showing(LEFT) is not None and app.showing(LEFT).rows == [{"x": 99}]


def test_a_replaced_chart_keeps_its_place_on_the_strip():
    """A corrected chart appearing at the far right is one you have to go find."""
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.show_chart(Chart(name="Events by month", rows=[{"x": 2}]))
    assert [c.name for c in app.tabs] == ["Events by month", "Cities"]


def test_a_new_name_opens_a_second_tab():
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    assert [c.name for c in app.tabs] == ["Events by month", "Cities"]


# --- focus (§3.4) ------------------------------------------------------------


def test_a_new_chart_takes_focus():
    """No rule distinguishes 'you asked for this' from 'I drew it on the way'.

    That rule would be `KNOWLEDGE_GRAPH.md` §4.4's hazard in a new place: three
    situations nobody can tell apart from the outside.
    """
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    assert app.active[LEFT] == "Cities"


def test_replacing_the_chart_you_are_not_looking_at_still_takes_focus():
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.show_chart(Chart(name="Events by month", rows=[{"x": 3}]))
    assert app.active[LEFT] == "Events by month"


# --- closing keeps the chart (§3.5) ------------------------------------------


def test_closing_a_tab_keeps_the_chart_in_the_list():
    """The left pane lists it, so closing is reversible and therefore cheap."""
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.close_chart("Events by month")
    assert [c.name for c in app.tabs] == []
    assert [c.name for c in app.charts] == ["Events by month"]


def test_closing_the_tab_you_are_in_lands_on_the_canvas():
    """Not on the neighbouring tab, which is a place nobody chose."""
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.close_chart("Cities")
    assert app.active[LEFT] == CANVAS


def test_closing_a_tab_you_are_not_in_leaves_focus_alone():
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.close_chart("Events by month")
    assert app.active[LEFT] == "Cities"


def test_reopening_restores_the_tab_in_its_original_place():
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.show_chart(a_chart("Cities"))
    app.close_chart("Events by month")
    app.reopen_chart("Events by month")
    assert [c.name for c in app.tabs] == ["Events by month", "Cities"]
    assert app.active[LEFT] == "Events by month"


def test_closing_and_reopening_an_unknown_name_does_nothing():
    app = App()
    app.close_chart("nope")
    app.reopen_chart("nope")
    assert app.charts == []


# --- the strip and the left pane are two ways of saying one thing (§3.2) -----


def test_selecting_on_the_left_returns_to_tab_zero():
    """Otherwise a click on a source changes a tab you cannot see."""
    app = App()
    app.show_chart(a_chart("Events by month"))
    app.select("source", "golden")
    assert app.active[LEFT] == CANVAS
    assert app.selection == ("source", "golden")


def test_picking_a_chart_tab_leaves_the_selection_alone():
    """So closing the tab drops you back on what you were reading."""
    app = App()
    app.select("source", "golden")
    app.show_chart(a_chart("Events by month"))
    assert app.selection == ("source", "golden")
    app.close_chart("Events by month")
    assert app.selection == ("source", "golden")


def test_show_canvas_returns_to_tab_zero_without_touching_the_selection():
    app = App()
    app.select("source", "golden")
    app.show_chart(a_chart("Events by month"))
    app.show_canvas()
    assert app.active[LEFT] == CANVAS
    assert app.selection == ("source", "golden")


# --- the three states a tab can be in ----------------------------------------


def test_a_pending_chart_is_not_drawable():
    assert not Chart(name="x", pending=True).drawable


def test_a_failed_chart_is_not_drawable_and_keeps_its_query():
    chart = Chart(name="x", error="no such column: CITY", sql="select CITY from t")
    assert not chart.drawable
    assert chart.sql == "select CITY from t"


def test_a_chart_with_no_rows_is_not_drawable():
    """Zero rows is an answer, and it is not a picture."""
    assert not Chart(name="x", rows=[]).drawable


def test_a_chart_with_rows_is_drawable():
    assert a_chart("x").drawable


# --- nothing here computes ---------------------------------------------------


def test_the_rows_are_kept_verbatim():
    """`ui/` never computes. The rows came out of `ops/sql.py` and are untouched.

    A chart that re-aggregated its own rows would be `docs/VISUALIZATION.md`
    §2.4's ban arriving through the back door: a number no ``SELECT`` produced.
    """
    rows = [{"city": "Paris", "n": 3}, {"city": "Lyon", "n": 1}]
    app = App()
    app.show_chart(Chart(name="Cities", rows=list(rows)))
    assert app.showing(LEFT) is not None and app.showing(LEFT).rows == rows


# --- the payload the browser actually parses ---------------------------------


def test_the_payload_is_one_json_document_the_client_can_parse():
    """The bug this pins: it reached the browser encoded **twice**.

    `json.dumps(to_json(...))` produces a JSON *string* holding an indented JSON
    document, and `JSON.parse` refuses it at the first newline — so the figure
    element was in the DOM, the rows were in it, and nothing drew. Nothing in
    Python raised, which is why only a browser found it.
    """
    import json

    from portia.ui import charts

    chart = Chart(
        name="Cities",
        vega={"mark": "bar", "encoding": {"x": {"field": "city"}, "y": {"field": "n"}}},
        columns=["city", "n"],
        rows=[{"city": "Paris", "n": 3}, {"city": "Lyon", "n": 1}],
    )
    got = json.loads(charts.payload(chart))
    assert got["rows"] == chart.rows
    assert got["columns"] == chart.columns
    assert got["vega"]["mark"] == "bar"


def test_the_payload_has_no_newlines():
    """Compact, not indented. Half of what made the first version unparseable."""
    from portia.ui import charts

    assert "\n" not in charts.payload(a_chart("x"))


def test_the_payload_survives_a_value_that_looks_like_markup():
    """A category called ``</script>`` is a value, not a way out of the document."""
    import json

    from portia.ui import charts

    chart = Chart(name="x", columns=["c"], rows=[{"c": "</script><b>hi</b>"}])
    assert json.loads(charts.payload(chart))["rows"][0]["c"] == "</script><b>hi</b>"


# --- saving leaves one artifact, not two (§6.4) ------------------------------


def test_retiring_a_chart_takes_its_tab_and_its_row():
    """Discarding an unsaved chart, and what a deleted figure does to its tab."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.retire_chart("Cities")
    assert [c.name for c in app.charts] == ["Regions"]


def test_retiring_the_chart_you_are_looking_at_lands_on_the_canvas():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.retire_chart("Cities")
    assert app.active[LEFT] == CANVAS


def test_retiring_is_not_closing():
    """Closing a tab keeps the chart — that is what makes closing cheap (§3.5).
    Retiring is the other verb, and the gallery needs both."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.close_chart("Cities")
    assert [c.name for c in app.charts] == ["Cities"]
    app.retire_chart("Cities")
    assert app.charts == []


def test_retiring_something_that_is_not_there_does_nothing():
    app = App()
    app.retire_chart("nope")
    assert app.charts == []


# --- a saved figure keeps its tab (§6.4) -------------------------------------


def test_saving_keeps_the_tab_and_moves_it_to_the_file():
    """**The fix.** Saving used to retire the chart, so pressing *keep* on a
    picture took you away from it. One artifact — which is what retiring was
    after — but the tab is the file from then on rather than gone."""
    app = App()
    chart = app.show_chart(a_chart("Cities"))
    app.figure_saved(chart, "figures/cities.json")
    assert [c.name for c in app.tabs] == ["Cities"]
    assert app.active[LEFT] == "figures/cities.json"
    assert app.showing(LEFT) is chart
    assert chart.saved


def test_a_saved_chart_is_keyed_by_its_path():
    """Two figures may be named the same in two folders; two paths may not."""
    app = App()
    first = app.show_chart(a_chart("Cities"))
    app.figure_saved(first, "figures/one/cities.json")
    second = app.show_chart(a_chart("Cities"))
    app.figure_saved(second, "figures/two/cities.json")
    assert [c.key for c in app.tabs] == ["figures/one/cities.json", "figures/two/cities.json"]


def test_drawing_again_under_a_kept_name_does_not_replace_the_file():
    """§3.3 replaces a *chart* in place. A figure is a file the user kept, and
    new work under the same name is new work beside it, not a correction of it."""
    app = App()
    kept = app.show_chart(a_chart("Cities"))
    app.figure_saved(kept, "figures/cities.json")
    app.show_chart(a_chart("Cities"))
    assert [c.key for c in app.tabs] == ["figures/cities.json", "Cities"]
    assert app.active[LEFT] == "Cities"


def test_moving_a_figure_takes_its_tab_with_it():
    app = App()
    chart = app.show_chart(a_chart("Cities"))
    app.figure_saved(chart, "figures/cities.json")
    app.figure_moved("figures/cities.json", "figures/volumes/cities.json")
    assert app.active[LEFT] == "figures/volumes/cities.json"
    assert app.chart("figures/volumes/cities.json") is chart


def test_deleting_a_figure_takes_its_tab():
    """A tab drawing a picture the gallery says is deleted is one artifact in two
    places, the other way round."""
    app = App()
    chart = app.show_chart(a_chart("Cities"))
    app.figure_saved(chart, "figures/cities.json")
    app.retire_chart("figures/cities.json")
    assert app.charts == []
    assert app.active[LEFT] == CANVAS


# --- two groups, not a pinned tab (§3.7) -------------------------------------


def test_a_fresh_app_has_one_group():
    app = App()
    assert app.is_split is False
    assert app.keys(LEFT) == [CANVAS]
    assert app.keys(RIGHT) == []


def test_moving_a_tab_right_splits_the_pane():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    assert app.is_split
    assert app.keys(LEFT) == [CANVAS, "Regions"]
    assert app.keys(RIGHT) == ["Cities"]


def test_each_group_shows_its_own_tab():
    """The whole of what a group buys: switching what is on the right does not
    disturb what is on the left."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    assert app.active[RIGHT] == "Cities"
    app.focus_tab(CANVAS)
    assert app.active[LEFT] == CANVAS
    assert app.active[RIGHT] == "Cities"


def test_tab_zero_can_move_across():
    """Reading the canvas beside the chart that made you ask about it is the case
    the whole gesture is for."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.move_tab(CANVAS, RIGHT)
    assert app.canvas_group == RIGHT
    assert app.keys(LEFT) == ["Cities"]
    assert app.keys(RIGHT) == [CANVAS]


def test_the_canvas_beside_one_chart_is_a_legitimate_split():
    """The case the gesture exists for: the pipeline on one side, the picture
    that made you ask about it on the other."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.move_tab("Cities", RIGHT)
    assert app.is_split
    assert app.keys(LEFT) == [CANVAS]
    assert app.keys(RIGHT) == ["Cities"]


def test_a_tab_that_would_empty_its_half_takes_the_half_with_it():
    """Moving the last thing out of a group is not a way to get an empty one."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.move_tab(CANVAS, RIGHT)
    app.move_tab("Cities", RIGHT)
    assert app.is_split is False
    assert app.keys(LEFT) == [CANVAS, "Cities"]


def test_closing_the_last_tab_in_a_half_closes_the_half():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    app.close_chart("Cities")
    assert app.is_split is False
    assert app.active[RIGHT] == ""


def test_emptying_the_left_half_slides_the_right_one_over():
    """Two halves with a gap on one side is not a layout."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab(CANVAS, RIGHT)
    app.move_tab("Regions", RIGHT)
    app.close_chart("Cities")
    assert app.is_split is False
    assert app.canvas_group == LEFT
    assert app.keys(LEFT) == [CANVAS, "Regions"]


def test_unsplitting_moves_the_tabs_back_and_closes_nothing():
    """The ✕ shuts a *half*, and a half is a layout — so it puts the tabs back."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    app.unsplit()
    assert app.is_split is False
    assert set(app.keys(LEFT)) == {CANVAS, "Cities", "Regions"}
    assert [c.name for c in app.tabs] == ["Regions", "Cities"]


def test_a_new_chart_opens_in_the_group_you_are_working_in():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    assert app.focus_group == RIGHT
    app.show_chart(a_chart("Prices"))
    assert app.chart("Prices").group == RIGHT


def test_a_replaced_chart_stays_in_its_own_group():
    """A correction that jumped to the other half is one you have to go find."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    app.focus_tab("Regions")
    app.show_chart(Chart(name="Cities", rows=[{"x": 9}]))
    assert app.chart("Cities").group == RIGHT


def test_focusing_a_tab_shows_it_where_it_is():
    """**The bug** *(2026-09-03, reported from a session)*: clicking a figure on
    the left while it was showing in the split half moved it to the main one, so
    the arrangement you had made went away. Opening a thing that is on screen is
    not a request to rearrange the screen."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    app.focus_tab("Regions")
    app.focus_tab("Cities")
    assert app.is_split
    assert app.active[RIGHT] == "Cities"
    assert app.active[LEFT] == "Regions"
    assert app.focus_group == RIGHT


# --- reordering, which is the same gesture (§3.7) ----------------------------


def test_a_tab_drops_before_the_one_it_landed_on():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.show_chart(a_chart("Prices"))
    app.move_tab("Prices", LEFT, 1)
    assert app.keys(LEFT) == [CANVAS, "Prices", "Cities", "Regions"]


def test_a_tab_drops_at_the_end_when_it_landed_past_the_last_one():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", LEFT, None)
    assert app.keys(LEFT) == [CANVAS, "Regions", "Cities"]


def test_the_canvas_keeps_its_place_at_the_head_of_its_strip():
    """§3.2 — always there, always first. It is the one tab you cannot lose, so
    its position is worth more as a constant than as a preference."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Regions", LEFT, 0)
    assert app.keys(LEFT)[0] == CANVAS


# --- preview tabs (§3.8) -----------------------------------------------------


def test_one_click_opens_a_preview_and_the_next_replaces_it():
    """Reading down a list of twenty figures costs one tab, not twenty."""
    app = App()
    app.show_chart(Chart(name="A", path="figures/a.json", rows=[{"x": 1}], preview=True))
    app.show_chart(Chart(name="B", path="figures/b.json", rows=[{"x": 1}], preview=True))
    assert [c.name for c in app.tabs] == ["B"]


def test_a_double_click_keeps_it():
    app = App()
    app.show_chart(Chart(name="A", path="figures/a.json", rows=[{"x": 1}], preview=True))
    app.pin_tab("figures/a.json")
    app.show_chart(Chart(name="B", path="figures/b.json", rows=[{"x": 1}], preview=True))
    assert [c.name for c in app.tabs] == ["A", "B"]


def test_a_chart_the_agent_drew_is_never_a_preview():
    """A preview is what browsing opens. Work the copilot did on purpose is not
    browsing, and the next click must not take it away."""
    app = App()
    app.show_chart(a_chart("Cities"))
    assert app.chart("Cities").preview is False


def test_an_unsaved_preview_is_closed_and_not_destroyed():
    """A figure is a file, so letting go of it costs a read. A session chart is
    the only copy there is."""
    app = App()
    app.show_chart(a_chart("Cities"))
    app.chart("Cities").preview = True
    app.show_chart(Chart(name="B", path="figures/b.json", rows=[{"x": 1}], preview=True))
    assert [c.name for c in app.charts] == ["Cities", "B"]
    assert app.chart("Cities").closed is True


def test_saving_a_preview_keeps_it():
    """You have said this one is worth keeping; a tab that vanished on the next
    click would be arguing."""
    app = App()
    chart = app.show_chart(a_chart("Cities"))
    chart.preview = True
    app.figure_saved(chart, "figures/cities.json")
    assert chart.preview is False


# --- a drag says which tab by position (§3.7) --------------------------------


def test_position_zero_is_the_canvas():
    app = App()
    app.show_chart(a_chart("Cities"))
    assert app.tab_at(LEFT, 0) == CANVAS


def test_positions_are_the_strip_in_order_after_the_canvas():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    assert app.tab_at(LEFT, 1) == "Cities"
    assert app.tab_at(LEFT, 2) == "Regions"


def test_positions_are_per_group():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.move_tab("Cities", RIGHT)
    assert app.tab_at(RIGHT, 0) == "Cities"
    assert app.tab_at(LEFT, 1) == "Regions"


def test_a_position_past_the_end_is_nothing_rather_than_a_guess():
    """A drag that landed on a tab that has since gone is refused, not applied
    to whatever is now in that slot."""
    app = App()
    app.show_chart(a_chart("Cities"))
    assert app.tab_at(LEFT, 4) is None
    assert app.tab_at(LEFT, -1) is None


def test_a_closed_tab_is_not_in_the_positions():
    app = App()
    app.show_chart(a_chart("Cities"))
    app.show_chart(a_chart("Regions"))
    app.close_chart("Cities")
    assert app.tab_at(LEFT, 1) == "Regions"


def test_the_gallery_starts_open():
    """Its caret is what says the section is empty, so it has to start showing."""
    assert App().figures_open is True


def test_the_folder_field_is_shut_on_a_fresh_chart():
    """The top level is the right answer nearly every time (§6.3)."""
    assert Chart(name="x").keep_where is False


# --- a failed render travels (§11) ------------------------------------------
#
# Two silences, stacked, each individually well-argued: `drawn.publish` swallows
# a sink's exception and `assets/chart.js` writes Vega-Lite's refusal into the
# figure and stops. Together they meant a chart could fail in the browser and no
# part of portia outside that one `<div>` ever knew — while the agent held a
# receipt saying `drawn`.


def test_the_payload_carries_the_key_the_client_reports_back_with():
    """`chart.js` has no other handle on which chart it is drawing."""
    import json

    from portia.ui import charts

    chart = a_chart("Orders")
    assert json.loads(charts.payload(chart))["key"] == chart.key


def test_a_failed_render_marks_the_chart_and_reaches_the_agent(monkeypatch):
    from portia.agent import drawn
    from portia.ui import charts

    drawn.reset()
    app = App()
    monkeypatch.setattr(charts, "APP", app)
    monkeypatch.setattr(charts, "_refresh", lambda: None)
    app.show_chart(a_chart("Orders"))

    charts.render_failed("Orders", "Invalid specification: unknown mark")

    chart = app.chart("Orders")
    assert chart.error == "Invalid specification: unknown mark"
    assert chart.pending is False
    # Held for the next receipt: the tool call it belongs to has already returned.
    assert drawn.take_failures() == {"Orders": "Invalid specification: unknown mark"}


def test_a_failure_is_reported_once():
    """Taken rather than read.

    A receipt restating a chart the agent has already fixed would be a second
    wrong thing to reason from, and the tab is on screen saying so regardless.
    """
    from portia.agent import drawn

    drawn.reset()
    drawn.report_failure("Orders", "boom")
    assert drawn.take_failures() == {"Orders": "boom"}
    assert drawn.take_failures() == {}


def test_a_tab_redrawn_under_one_name_is_one_fact():
    """A tab is a chart (§3.3), so two failures for it are not two charts."""
    from portia.agent import drawn

    drawn.reset()
    drawn.report_failure("Orders", "first")
    drawn.report_failure("Orders", "second")
    assert drawn.take_failures() == {"Orders": "second"}


def test_a_failure_for_an_unknown_tab_is_ignored(monkeypatch):
    """A stale client can report a chart the strip has already forgotten."""
    from portia.agent import drawn
    from portia.ui import charts

    drawn.reset()
    monkeypatch.setattr(charts, "APP", App())
    monkeypatch.setattr(charts, "_refresh", lambda: None)
    charts.render_failed("gone", "boom")
    assert drawn.take_failures() == {}


def test_the_next_receipt_carries_the_failure():
    """§11.2 — late, because there is no way to amend a result already sent.

    Worth having late all the same: a reply that draws nine charts can still fix
    the last six once the third has said it broke.
    """
    from portia.agent import drawn, tools

    drawn.reset()
    drawn.report_failure("Orders", "Invalid specification")
    receipt = tools._receipt(
        {
            "tab": "Something else",
            "question": "q",
            "n_rows": 1,
            "columns": ["a"],
            "rows": [{"a": 1}],
            "vega": {"mark": "bar", "encoding": {"x": {"field": "a"}}},
        }
    )
    assert receipt["render_failures"] == {"Orders": "Invalid specification"}


# --- the fourth tab (§3.10) --------------------------------------------------
#
# Driven in a browser: the drag logic held, and what changed at the fourth tab
# was geometry — the strip overflowed and the drop zone covered it. These pin
# the two client-side rules, the way `test_ui` pins `scroll.js`'s.


def _asset(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "portia/ui/assets" / name).read_text()


def test_the_drop_zone_starts_under_the_strip_not_over_it():
    """From `top: 0` it covered the strip, so a tab in the strip's right half went
    under the overlay the moment it was picked up. The height is measured at the
    press, off the strip, rather than copied out of the stylesheet."""
    script = _asset("tabs.js")
    assert "function place(row)" in script
    assert "zone.style.top" in script
    assert "bar.getBoundingClientRect().bottom" in script
    css = _asset("portia.css")
    assert "`top` is set by `tabs.js`" in css


def test_the_active_tab_is_brought_into_view_once_per_element():
    """A new chart takes focus, and with five tabs its tab was drawn past the
    strip's edge — active and invisible. Declarative on the class the server
    sets, acted on once per element so a rebuilt strip is a fresh request."""
    script = _asset("tabs.js")
    assert "pane-tab--active" in script
    assert 'scrollIntoView({ inline: "nearest", block: "nearest" })' in script
    assert "portiaShown" in script


def test_a_chart_tab_shrinks_before_the_strip_scrolls():
    """Five tabs at the transcript's padding were 1037px in a 740px strip."""
    css = _asset("portia.css")
    block = css.split(".chart-strip .pane-tab {", 1)[1].split("}", 1)[0]
    assert "flex: 0 1 auto" in block
    assert "min-width" in block


def test_the_gesture_rides_the_data_transfer_not_a_variable():
    """A module variable set at the press is state on portia's side of a drag
    the browser owns. NiceGUI replaces the strip on any middle-pane refresh, and
    Chrome fires no `dragend` for a source it cannot find — so the drop reads the
    origin back off the DataTransfer, under a type of its own, and `dragover`
    recognises a tab by that type rather than by whether the variable survived."""
    script = _asset("tabs.js")
    assert 'const TYPE = "application/x-portia-tab"' in script
    assert "event.dataTransfer.setData(TYPE, payload)" in script
    assert "function origin(event)" in script
    assert "event.dataTransfer.getData(TYPE)" in script
    assert "types.includes(TYPE)" in script


def test_dragenter_is_cancelled_as_well_as_dragover():
    """The drag-and-drop model picks the drop target on entry and only then
    consults `dragover`; an entry nothing cancelled falls back to the body.
    Chrome forgives it, and the window does not choose its browser."""
    script = _asset("tabs.js")
    assert 'document.addEventListener("dragenter", accept)' in script
    assert 'document.addEventListener("dragover"' in script


def test_an_unrelated_drag_clears_a_tab_gesture_that_never_ended():
    """A figure dragged out of the gallery must not be read as a tab because the
    last tab drag lost its `dragend`."""
    script = _asset("tabs.js")
    start = script.split('document.addEventListener("dragstart"', 1)[1].split("});", 1)[0]
    assert "dragging = null;" in start
    assert "if (!row) return;" in start


# --- looking around a chart (§3.9.1) ----------------------------------------
#
# Zoom, pan and the filled view are the client's state, so there is no Python
# behaviour to test. What can be pinned from here is the two lines a later edit
# is most likely to undo without noticing, because neither fails loudly: both
# were found in a browser, and both pass every other test when broken.


def _asset(name: str) -> str:
    from pathlib import Path

    from portia.ui import charts

    return (Path(charts.__file__).parent / "assets" / name).read_text()


def test_where_you_are_looking_in_a_chart_never_reaches_the_server():
    """`canvas.js`'s rule. The height grip and a failed render are the only two
    things a chart reports, and zooming adds no third."""
    import re

    js = _asset("chart.js")
    emitted = set(re.findall(r'emitEvent\("(portia:[a-z-]+)"', js))
    assert emitted == {"portia:chart-failed", "portia:chart-height"}
    assert "const LOOKING = new Map()" in js, "kept per chart key, never per element"


def test_a_zoomed_mount_cannot_make_its_own_figure_taller():
    """The figure's height is a flex basis of its content and `chart.js` sizes
    the mount from the view, so a mount in normal flow is a feedback loop. It
    also has to out-specify the `.vega-embed` rule `vegaEmbed` injects later."""
    import re

    css = _asset("portia.css")
    rule = re.search(r"\n\.chart-view > \.chart-mount \{(.*?)\}", css, re.S)
    assert rule, "a bare `.chart-mount` ties with vega-embed's own rule and loses on order"
    assert "position: absolute" in rule.group(1)
    assert re.search(r"\n\.chart-mount canvas \{\s*display: block;", css)
