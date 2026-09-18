"""`plot_data` — the same SELECT as `query_data`, with the answer sent elsewhere.

`docs/VISUALIZATION.md`. Two things here are load-bearing and neither is visible
in a screenshot: the rows do **not** come back to the model (§2.3), and the
encoding cannot compute (§2.4). Everything else is refusals, which are the part
that decides whether the agent can recover.
"""

import asyncio
import json

import pytest

from portia.agent import chartspec, drawn, handlers
from portia.catalog import index_source, init_project
from portia.fixtures import sales_customers, sales_orders
from portia.ops.sql import SqlNotAllowed


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """Two indexed sources, in a project rooted at ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


@pytest.fixture(autouse=True)
def no_subscribers():
    """No surface is listening unless a test says so."""
    drawn.reset()
    yield
    drawn.reset()


def bars(mark="bar", **channels):
    """The smallest spec that draws: one mark, the columns the query returned."""
    encoding = {"x": {"field": "customer_id"}, "y": {"field": "n"}, **channels}
    return {"mark": mark, "encoding": encoding}


def by_customer(sales, **kw):
    return handlers.plot_data(
        "SELECT customer_id, count(*) AS n FROM orders GROUP BY 1 ORDER BY 1",
        ["orders"],
        "how many orders per customer?",
        kw.pop("tab", "Orders per customer"),
        kw.pop("vega", bars()),
        portia_dir=sales,
        **kw,
    )


# --- it draws ----------------------------------------------------------------


def test_it_returns_the_rows_and_the_spec(sales):
    got = by_customer(sales)
    assert got["tab"] == "Orders per customer"
    assert got["columns"] == ["customer_id", "n"]
    assert got["n_rows"] == len(got["rows"])
    assert got["vega"] == bars()


def test_the_whole_answer_is_jsonable(sales):
    """It goes over a websocket to a browser, so numpy types would break it."""
    json.dumps(by_customer(sales))


def test_the_rows_are_not_capped(sales):
    """§2.7 — the model's context is not the constraint, so there is no number
    for code to pick.

    `query_data` applies `QUERY_ROWS` when the caller does not say; this applies
    nothing at all, so every row of the relation arrives. The fixture is small,
    so what this pins is that ``n_rows`` and the rows delivered are the same
    number — a cap would make them differ.
    """
    got = handlers.plot_data(
        "SELECT * FROM orders",
        ["orders"],
        "what is in here?",
        "Orders",
        {"mark": "point", "encoding": {"x": {"field": "order_id"}, "y": {"field": "amount"}}},
        portia_dir=sales,
    )
    assert len(got["rows"]) == got["n_rows"] == len(sales_orders())
    # Nothing in the module names a row limit for a chart, which is the decision.
    assert not hasattr(handlers, "PLOT_ROWS")


def test_it_writes_nothing(sales, tmp_path):
    by_customer(sales)
    assert not (tmp_path / "specs").exists()
    assert not (tmp_path / "findings").exists()


def test_a_zero_is_a_chart_with_no_rows(sales):
    """Not a failure. An empty result means nothing matched, which is an answer."""
    got = handlers.plot_data(
        "SELECT customer_id, count(*) AS n FROM orders WHERE amount < -1 GROUP BY 1",
        ["orders"],
        "any negative orders?",
        "Negative orders",
        bars(),
        portia_dir=sales,
    )
    assert got["n_rows"] == 0
    assert got["rows"] == []


# --- the sandbox is `ops.sql`'s, unchanged -----------------------------------


def test_it_refuses_anything_that_is_not_a_select(sales):
    with pytest.raises(SqlNotAllowed):
        handlers.plot_data("DROP TABLE orders", ["orders"], "q", "T", bars(), portia_dir=sales)


def test_it_cannot_reach_a_table_it_did_not_declare(sales):
    """The sandbox is the guarantee, not `check_sql` — `ops/sql.py` says so itself."""
    with pytest.raises(Exception, match="customers"):
        handlers.plot_data(
            "SELECT count(*) AS n FROM customers",
            ["orders"],
            "how many customers?",
            "Customers",
            {"mark": "bar", "encoding": {"x": {"field": "n"}}},
            portia_dir=sales,
        )


# --- the encoding cannot compute (§2.4) --------------------------------------


def test_a_channel_naming_a_column_the_query_did_not_return_is_refused(sales):
    """The aggregate-in-the-encoding mistake, caught where the agent can read it.

    Asking for `y: count` off a query that never counted is exactly the move
    every chart grammar would let you make, and the number it produced would be
    in no log at all.
    """
    with pytest.raises(ValueError) as exc:
        handlers.plot_data(
            "SELECT customer_id FROM orders",
            ["orders"],
            "how many orders per customer?",
            "Orders per customer",
            bars(y={"field": "count"}),
            portia_dir=sales,
        )
    assert "count" in str(exc.value)
    # The refusal has to say what *is* available or it is a dead end.
    assert "customer_id" in str(exc.value)


def test_a_field_differing_from_the_column_only_by_case_is_respelled(sales):
    """`count(*) AS count` comes back from a warehouse as COUNT and the spec says
    `count`: six of twelve charts in one session were refused for it. The spec is
    respelled to the rows' own column names, so the browser finds the field."""
    out = handlers.plot_data(
        'SELECT customer_id, count(*) AS "N" FROM orders GROUP BY 1 ORDER BY 1',
        ["orders"],
        "how many orders per customer?",
        "Orders per customer",
        bars(y={"field": "n"}),
        portia_dir=sales,
    )
    assert out["columns"] == ["customer_id", "N"]
    assert out["vega"]["encoding"]["y"]["field"] == "N"
    assert chartspec.fields(out["vega"]) == ["customer_id", "N"]


def test_an_unknown_mark_is_refused_and_names_the_known_ones(sales):
    with pytest.raises(ValueError) as exc:
        by_customer(sales, vega=bars(mark="pie"))
    for mark in chartspec.MARKS:
        assert mark in str(exc.value)


def test_a_tab_is_required(sales):
    with pytest.raises(ValueError):
        by_customer(sales, tab="  ")


def test_a_question_is_required(sales):
    with pytest.raises(ValueError):
        handlers.plot_data(
            "SELECT customer_id FROM orders",
            ["orders"],
            "",
            "T",
            bars(),
            portia_dir=sales,
        )


def test_the_spec_is_carried_through_untouched(sales):
    """Portia adds nothing to it. The rows, the types and the theme are
    `assets/chart.js`'s, at draw time, where the palette and the box size are
    known — a spec half-completed on the server is a spec that disagrees with the
    one in the log.
    """
    written = {
        "layer": [
            {"mark": {"type": "bar", "color": "#FF9900"}},
            {"mark": {"type": "line", "color": "#146EB4"}, "encoding": {"y": {"field": "n"}}},
        ],
        "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}},
    }
    got = by_customer(sales, vega=written)
    assert got["vega"] == written


# --- the spec is the agent's, minus the compute surface (§2.9) ---------------


def test_a_layered_spec_with_a_palette_is_drawn(sales):
    """The chart the 2026-09-03 session could not have: bars, a line over them,
    and colours the user asked for by name."""
    got = by_customer(
        sales,
        vega={
            "layer": [
                {"mark": {"type": "bar", "color": "#FF9900"}},
                {
                    "mark": {"type": "line", "color": "#146EB4", "strokeWidth": 2},
                    "encoding": {"y": {"field": "n"}},
                },
            ],
            "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}},
            "config": {"axis": {"labelFontSize": 13}},
        },
    )
    assert got["vega"]["layer"][0]["mark"]["color"] == "#FF9900"


@pytest.mark.parametrize(
    "vega",
    [
        {"mark": "bar", "encoding": {"y": {"aggregate": "count"}}},
        {"mark": "bar", "encoding": {"x": {"field": "customer_id", "bin": True}}},
        {"mark": "bar", "encoding": {"x": {"field": "customer_id", "timeUnit": "month"}}},
        {"mark": "bar", "transform": [{"calculate": "datum.n * 2", "as": "double"}]},
        {"mark": "line", "transform": [{"regression": "n", "on": "customer_id"}]},
        {"mark": "bar", "encoding": {"y": {"field": "n"}, "opacity": {"value": {"expr": "0.5"}}}},
        {"layer": [{"mark": "bar"}, {"mark": "line", "encoding": {"y": {"aggregate": "mean"}}}]},
    ],
)
def test_a_spec_that_computes_is_refused_wherever_it_computes(sales, vega):
    """§2.4, and the whole reason a free spec is safe. A number the browser
    worked out is in no log, so `review_queries` cannot show it and a finding
    resting on it is prose with nothing underneath."""
    with pytest.raises(ValueError) as exc:
        by_customer(sales, vega=vega)
    assert "SQL" in str(exc.value) or "sql" in str(exc.value)


def test_a_spec_may_not_bring_its_own_data(sales):
    """Two sources for the rows is a chart that can disagree with its query."""
    with pytest.raises(ValueError) as exc:
        by_customer(sales, vega={"mark": "bar", "data": {"values": [{"x": 1}]}})
    assert "data" in str(exc.value)


def test_a_spec_with_nothing_that_draws_is_refused(sales):
    with pytest.raises(ValueError) as exc:
        by_customer(sales, vega={"encoding": {"x": {"field": "customer_id"}}})
    assert "mark" in str(exc.value)


def test_an_empty_spec_is_refused(sales):
    with pytest.raises(ValueError):
        by_customer(sales, vega={})


def test_a_field_in_a_layer_is_checked_against_the_query(sales):
    """Vega-Lite draws an empty axis for a field it cannot find, so a typo two
    layers down is a chart that looks finished and answers nothing."""
    with pytest.raises(ValueError) as exc:
        by_customer(
            sales,
            vega={
                "layer": [{"mark": "bar"}, {"mark": "line", "encoding": {"y": {"field": "trend"}}}],
                "encoding": {"x": {"field": "customer_id"}, "y": {"field": "n"}},
            },
        )
    assert "trend" in str(exc.value)


def test_a_boxplot_and_a_stack_are_allowed(sales):
    """Both work something out and neither asserts a number nobody selected: the
    rows are all measured and all in the log, and Vega-Lite stacks a bar chart by
    default anyway — refusing the explicit `stack` would only catch the person
    who wrote it down."""
    by_customer(sales, vega={"mark": "boxplot", "encoding": {"y": {"field": "n"}}})
    by_customer(sales, vega=bars(y={"field": "n", "stack": "zero"}))


def test_sorting_by_a_measured_value_is_allowed(sales):
    """`DESIGN.md`'s never-rank rule is about portia asserting a verdict nobody
    asked for. An analyst ordering their own bars is judgment, which is the
    agent's job."""
    by_customer(sales, vega=bars(x={"field": "customer_id", "sort": "-y"}))


# --- the split: rows to the browser, a receipt to the model (§2.3) -----------


def _call(**kw):
    from portia.agent import tools

    return asyncio.run(tools.plot_data.handler(kw))


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_the_model_gets_a_receipt_and_never_the_rows(sales):
    result = _call(
        sql="SELECT customer_id, count(*) AS n FROM orders GROUP BY 1 ORDER BY 1",
        inputs=["orders"],
        question="how many orders per customer?",
        tab="Orders per customer",
        vega=bars(),
        portia_dir=sales,
    )
    receipt = json.loads(_text(result))
    assert receipt["drawn"] == "Orders per customer"
    assert receipt["n_rows"] > 0
    assert "rows" not in receipt


def test_the_rows_reach_a_subscriber(sales):
    got: list[dict] = []
    drawn.subscribe(got.append)
    _call(
        sql="SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
        inputs=["orders"],
        question="q",
        tab="Orders per customer",
        vega=bars(),
        portia_dir=sales,
    )
    assert len(got) == 1
    assert got[0]["rows"] and got[0]["tab"] == "Orders per customer"


def test_a_failed_query_publishes_nothing(sales):
    got: list[dict] = []
    drawn.subscribe(got.append)
    result = _call(
        sql="SELECT no_such_column FROM orders",
        inputs=["orders"],
        question="q",
        tab="T",
        vega={"mark": "bar", "encoding": {"x": {"field": "no_such_column"}}},
        portia_dir=sales,
    )
    assert result.get("is_error") or "Error" in _text(result) or "error" in _text(result).lower()
    assert got == []


def test_a_broken_surface_does_not_break_the_query(sales):
    """A renderer that raises would otherwise tell the model its query failed."""
    quiet: list[dict] = []

    def explode(chart: dict) -> None:
        raise RuntimeError("no browser")

    drawn.subscribe(explode)
    drawn.subscribe(quiet.append)
    result = _call(
        sql="SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
        inputs=["orders"],
        question="q",
        tab="T",
        vega=bars(),
        portia_dir=sales,
    )
    assert json.loads(_text(result))["drawn"] == "T"
    assert len(quiet) == 1


def test_unsubscribing_stops_delivery(sales):
    got: list[dict] = []
    stop = drawn.subscribe(got.append)
    stop()
    drawn.publish({"tab": "x"})
    assert got == []


# --- the receipt says enough to talk about the chart -------------------------
#
# `VISUALIZATION.md` §2.5. The first shape summarised each encoded column on its
# own and paired nothing, which is what let the copilot map a range's ends onto a
# category list and invent the value in between (§2.5.1). What a receipt owes is
# not truth per column — it had that — but being reasonable-from.


def test_a_small_chart_comes_back_with_its_rows_paired(sales):
    """Under `RECEIPT_ROWS`, the receipt is the plotted rows themselves.

    Not §2.3 reversed: that argument is about a 5,000-point scatter costing
    30,000 characters, and six bars is not that.
    """
    from portia.agent import tools

    chart = by_customer(sales)
    receipt = tools._receipt(chart)
    assert tools.UNPAIRED not in receipt
    assert receipt["plotted"] == [
        {"customer_id": row["customer_id"], "n": row["n"]} for row in chart["rows"]
    ]


def test_the_paired_rows_hold_only_what_the_spec_encodes(sales):
    """A free spec has no fixed channels to read back, and the useful answer was
    never *the x channel* — it is what is on the axis the user is looking at."""
    from portia.agent import tools

    chart = handlers.plot_data(
        "SELECT customer_id, count(*) AS n, sum(amount) AS total FROM orders GROUP BY 1",
        ["orders"],
        "how many orders per customer?",
        "Orders per customer",
        bars(),
        portia_dir=sales,
    )
    receipt = tools._receipt(chart)
    plotted = receipt["plotted"][0]
    assert set(plotted) == {"customer_id", "n"}
    # Returned by the query, drawn by nothing: it is in `columns` and not plotted.
    assert "total" in receipt["columns"]


def test_a_chart_too_big_to_pair_says_so_as_a_field(sales):
    """The absence of a pairing is the one thing that may not be left implied.

    An omission reads as *nothing to report*; §2.5.1 is what happens when the
    model completes it by inference instead. So `unpaired` is in the payload.
    """
    from portia.agent import tools

    chart = {
        "tab": "T",
        "question": "q",
        "n_rows": 40,
        "columns": ["city", "n"],
        "rows": [{"city": f"city-{i}", "n": i} for i in range(40)],
        "vega": {"mark": "bar", "encoding": {"x": {"field": "city"}, "y": {"field": "n"}}},
    }
    receipt = tools._receipt(chart)
    assert receipt[tools.UNPAIRED] is True
    assert "plotted" not in receipt
    assert set(receipt["n"]) == {"min", "max"}
    assert receipt["city"]["n_distinct"] == 40


def test_the_receipt_can_no_longer_produce_the_inversion_it_produced(sales):
    """The 2026-09-04 regression, as its own case.

    Three risk categories and a failure rate each. The old receipt said
    `Risk: [three names]` and `failure_rate_pct: {min: 25.2, max: 43.9}` — from
    which the copilot wrote 43.9 / 25.2 / 38.9 against a measured 25.2 / 29.5 /
    43.9, inverting the finding and inventing the third number. The receipt has
    to carry which rate belongs to which category, or nothing here holds.
    """
    from portia.agent import tools

    rows = [
        {"Risk": "Risk 1 (High)", "failure_rate_pct": 25.2},
        {"Risk": "Risk 2 (Medium)", "failure_rate_pct": 29.5},
        {"Risk": "Risk 3 (Low)", "failure_rate_pct": 43.9},
    ]
    chart = {
        "tab": "3. Chicago Failure Rate by Risk",
        "question": "failure rate by risk category",
        "n_rows": 3,
        "columns": ["Risk", "total_inspections", "failure_rate_pct"],
        "rows": rows,
        "vega": {
            "mark": "bar",
            "encoding": {
                "x": {"field": "Risk"},
                "y": {"field": "failure_rate_pct"},
            },
        },
    }
    assert tools._receipt(chart)["plotted"] == rows


def test_a_long_category_list_is_summarised_rather_than_listed():
    from portia.agent import tools

    rows = [{"city": f"city-{i}"} for i in range(80)]
    span = tools._span(rows, "city")
    assert span["n_distinct"] == 80
    assert len(span["showing"]) == tools.RECEIPT_VALUES


# --- keeping a chart writes a figure file (§6) -------------------------------


def test_a_review_numbers_both_doors(sales):
    """`plot_data` and `query_data` are one list, so the agent can keep a chart
    as a finding through `record_finding` — which is still the path for a claim."""
    from portia import findings

    assert findings.QUERY_TOOL in findings.QUERY_TOOLS
    assert findings.PLOT_TOOL in findings.QUERY_TOOLS


def a_saved_chart(**kw):
    from portia.ui.state import Chart

    return Chart(
        name=kw.pop("name", "Orders per customer"),
        question="how many orders per customer?",
        inputs=["orders"],
        sql="SELECT customer_id, count(*) AS n FROM orders GROUP BY 1",
        rows=[{"customer_id": 1000, "n": 1}],
        columns=["customer_id", "n"],
        vega=bars(),
        **kw,
    )


def test_keeping_a_chart_writes_a_figure_with_its_rows_in_it(sales, tmp_path):
    """§6, reversed 2026-09-03 — the file has to open with nothing running.

    A finding keeps the sentence and the SQL, which re-runs. A figure keeps the
    rows, so looking at it again costs a read.
    """
    from portia import figures
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=sales)
    path = engine.save_figure(app, a_saved_chart(), notes="a few place most of them")
    assert path.parent.name == figures.FIGURES_DIR

    saved = figures.load(path)
    assert saved["rows"] == [{"customer_id": 1000, "n": 1}]
    assert saved["vega"]["mark"] == "bar"
    assert saved["notes"] == "a few place most of them"
    assert saved["sql"].startswith("SELECT")


def test_a_note_is_optional(sales, tmp_path):
    """A figure is a picture you wanted back, not a claim. `findings/` is where
    `so` is required, and it is required there because a finding is a claim."""
    from portia import figures
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=sales)
    engine.save_figure(app, a_saved_chart())
    assert figures.load_all(tmp_path)[0]["notes"] == ""


def test_two_figures_of_one_name_are_two_files(sales, tmp_path):
    """Silently replacing the first is the one outcome nobody wants from Save."""
    from portia import figures
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=sales)
    first = engine.save_figure(app, a_saved_chart())
    second = engine.save_figure(app, a_saved_chart())
    assert first != second
    assert len(figures.load_all(tmp_path)) == 2


def test_a_figure_saves_into_a_folder_that_did_not_exist(sales, tmp_path):
    from portia import figures
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=sales)
    engine.save_figure(app, a_saved_chart(), folder="volumes/monthly")
    saved = figures.load_all(tmp_path)[0]
    assert saved["path"] == "figures/volumes/monthly/orders-per-customer.json"


def test_a_folder_name_cannot_climb_out_of_the_gallery(sales, tmp_path):
    """A typo is the interesting failure, and no typo may write outside it."""
    from portia import figures
    from portia.ui import engine
    from portia.ui.state import App

    app = App(root=tmp_path, portia_dir=sales)
    path = engine.save_figure(app, a_saved_chart(), folder="../../etc")
    assert figures.FIGURES_DIR in str(path.relative_to(tmp_path))


# --- the gallery is arranged by the user -------------------------------------


def test_a_figure_moves_between_folders(tmp_path):
    from portia import figures

    figures.save({"tab": "Cities", "rows": [{"n": 1}]}, root=tmp_path)
    moved = figures.move("figures/cities.json", "regions", root=tmp_path)
    assert moved.parent.name == "regions"
    assert figures.load_all(tmp_path)[0]["path"] == "figures/regions/cities.json"


def test_a_figure_moves_back_to_the_top_level(tmp_path):
    """The gallery root is in `folders()`, so a move into a folder is not one-way."""
    from portia import figures

    figures.save({"tab": "Cities", "rows": [{"n": 1}]}, folder="regions", root=tmp_path)
    figures.move("figures/regions/cities.json", "", root=tmp_path)
    assert figures.load_all(tmp_path)[0]["path"] == "figures/cities.json"
    assert "" in figures.folders(tmp_path)


def test_an_empty_folder_is_removed_and_a_full_one_is_refused(tmp_path):
    """`cli/remove_spec`'s rule: a delete bigger than the thing you clicked is
    how work disappears."""
    from portia import figures

    figures.make_folder("keep", root=tmp_path)
    figures.save({"tab": "Cities", "rows": [{"n": 1}]}, folder="keep", root=tmp_path)
    figures.make_folder("empty", root=tmp_path)

    figures.remove("figures/empty", root=tmp_path)
    assert "empty" not in figures.folders(tmp_path)
    with pytest.raises(ValueError, match="not empty"):
        figures.remove("figures/keep", root=tmp_path)


def test_a_figure_is_deleted(tmp_path):
    from portia import figures

    figures.save({"tab": "Cities", "rows": [{"n": 1}]}, root=tmp_path)
    figures.remove("figures/cities.json", root=tmp_path)
    assert figures.load_all(tmp_path) == []


def test_an_empty_gallery_is_a_gallery(tmp_path):
    """It is drawn either way, so the read has to work on nothing."""
    from portia import figures

    assert figures.load_all(tmp_path) == []
    assert figures.folders(tmp_path) == [""]


def test_a_saved_figure_needs_nothing_running_to_be_read(tmp_path):
    """The whole argument for the file: no database, no chat, no project even."""
    from portia import figures

    figures.save(
        {
            "tab": "Cities",
            "question": "how do they split?",
            "sql": "SELECT city, count(*) AS n FROM t GROUP BY 1",
            "vega": {"mark": "bar", "encoding": {"x": {"field": "city"}}},
            "columns": ["city", "n"],
            "rows": [{"city": "Paris", "n": 3}],
        },
        notes="Paris dominates",
        root=tmp_path,
    )
    saved = figures.load(tmp_path / "figures" / "cities.json")
    assert saved["rows"] and saved["vega"] and saved["notes"] == "Paris dominates"
