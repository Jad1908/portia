"""The edge where a handler's dict becomes the text the model reads.

`handlers.py` returns plain jsonable dicts, which is what makes it testable
without the SDK; the encoding lives here in the wrappers. These pin that seam,
because the temptation when a payload is too large is to shrink it one layer
down, in the check or the handler, where it stops being an encoding and starts
being a decision about which evidence the copilot gets.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from portia.agent import tools
from portia.core import listing
from portia.core.serialize import to_json_compact


def _call(name: str, args: dict) -> dict:
    tool = next(t for t in tools.ALL_TOOLS if t.name == name)
    return asyncio.run(tool.handler(args))


def _text(result: dict) -> str:
    return result["content"][0]["text"]


@pytest.mark.parametrize("name", ["describe_source", "profile_source"])
def test_the_column_rungs_answer_in_compact_json_with_their_fields_once(monkeypatch, name):
    """The tab-separated table these two sent (`core/columnar.render`) went with
    the cards (2026-10-13): a list is ``fields`` once and a row of values each
    (`core/listing.py`), which is JSON and as lean."""
    payload = {
        "source": "golden",
        "n_rows": 2,
        "columns": listing.listing(("name", "top"), [["a", "x"], ["b", None]], n=2),
    }
    monkeypatch.setattr(tools.handlers, name, lambda *a, **k: payload)

    text = _text(_call(name, {"source": "golden"}))

    assert json.loads(text) == payload
    assert text.count('"name"') == 1


def test_a_tool_that_returns_no_column_list_still_hands_back_json(monkeypatch):
    payload = {"project": "aqn", "groups": ["events"]}
    monkeypatch.setattr(tools.handlers, "get_context", lambda *a, **k: payload)

    assert json.loads(_text(_call("get_context", {}))) == payload


def test_the_json_the_model_reads_is_not_padded(monkeypatch):
    """Compact, unlike `to_json`, which stays indented for the terminal surfaces."""
    monkeypatch.setattr(tools.handlers, "get_context", lambda *a, **k: {"a": 1, "b": 2})

    assert _text(_call("get_context", {})) == '{"a":1,"b":2}'


def test_a_handler_that_raises_is_still_an_error_the_agent_can_read(monkeypatch):
    def boom(*a, **k):
        raise ValueError("no such source")

    monkeypatch.setattr(tools.handlers, "profile_source", boom)
    result = _call("profile_source", {"source": "nope"})

    assert result["is_error"] is True
    assert "no such source" in _text(result)


def test_a_long_error_is_shortened_by_portia_and_says_so(monkeypatch):
    """Claude Code cuts the middle out of an error past 11,024 characters; portia cuts first."""
    start, end = "ValueError: the step broke because", "Fix the keys and record it again."
    message = f"the step broke because{' x' * 30_000}. {end}"

    def boom(*a, **k):
        raise ValueError(message)

    monkeypatch.setattr(tools.handlers, "profile_source", boom)
    text = _text(_call("profile_source", {"source": "nope"}))

    whole = f"ValueError: {message}"
    assert len(text) <= tools.ERROR_BUDGET
    notice, kept = text.split("\n\n", 1)
    assert notice.startswith(f"This error was {len(whole):,} characters")
    assert kept.startswith(start) and kept.endswith(end)
    head, marker, tail = kept.split("\n", 2)
    cut = len(whole) - len(head) - len(tail)
    assert f"{cut:,} characters" in notice and f"{cut:,} characters" in marker
    assert whole.startswith(head) and whole.endswith(tail)


def test_an_error_inside_the_budget_is_untouched(monkeypatch):
    message = "y" * (tools.ERROR_BUDGET - len("ValueError: "))

    def boom(*a, **k):
        raise ValueError(message)

    monkeypatch.setattr(tools.handlers, "profile_source", boom)
    assert _text(_call("profile_source", {"source": "nope"})) == f"ValueError: {message}"


def test_a_missing_required_argument_still_raises_where_the_agent_sees_it(monkeypatch):
    """`_evidence` takes a thunk so the KeyError happens inside the try."""
    monkeypatch.setattr(tools.handlers, "profile_source", lambda *a, **k: {})
    result = _call("profile_source", {})

    assert result["is_error"] is True
    assert "source" in _text(result)


@pytest.mark.parametrize("name", [t.name for t in tools.ALL_TOOLS])
def test_no_tool_encodes_its_own_payload(name):
    """One encoder per shape, chosen in `_evidence` — not a `json.dumps` per tool."""
    import inspect

    body = inspect.getsource(next(t for t in tools.ALL_TOOLS if t.name == name).handler)
    assert "json" not in body and "to_json" not in body


# --- the size guard ----------------------------------------------------------


def _evidence(thunk, **kwargs) -> dict:
    return asyncio.run(tools._evidence(thunk, **kwargs))


def test_an_oversized_result_is_refused_with_something_the_agent_can_do():
    """The SDK's own refusal sends the model after tools it does not have.

    Three times in the 2026-08-17 AQN runs a result was refused with "use offset
    and limit ... and jq to make structured queries" against a saved file path,
    to an agent configured with no filesystem and no shell. We cannot intercept
    that — it happens after the tool returns — so the guard is not returning an
    oversized payload in the first place.
    """
    oversized = {"columns": [{"name": f"c{i}", "blob": "x" * 200} for i in range(400)]}
    result = _evidence(lambda: oversized)

    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "NOT sent" in text
    for absent in ("jq", "offset", "limit", ".txt"):
        assert absent not in text, f"{absent!r} points at a tool the agent does not have"


def test_a_result_inside_the_budget_is_untouched():
    result = _evidence(lambda: {"n_rows": 3})

    assert "is_error" not in result
    assert result["content"][0]["text"] == '{"n_rows":3}'


def test_the_guard_measures_the_encoded_text_not_the_dict():
    """Two encodings of one dict do not cost the same, so the payload has to be
    encoded before it can be judged."""
    records = {"columns": [{"name": f"col_{i:04d}", "blob": "x" * 20} for i in range(800)]}

    def leaner(payload: dict) -> str:
        return "\n".join(c["name"] for c in payload["columns"])

    as_json = _evidence(lambda: records)  # 40,013 characters
    as_names = _evidence(lambda: records, encode=leaner)  # 8,799

    assert as_json["is_error"] is True
    assert "is_error" not in as_names, "the same dict fits through the cheaper encoder"


# --- Stop reaches a running tool -----------------------------------------------


def test_a_tool_stopped_mid_call_comes_back_as_stopped_not_a_traceback():
    """`tools.stopping` installs the exchange's scope; a call that ends because
    of the press reads as *stopped* to the model, whatever the driver raised."""
    from portia.core import cancel

    scope = cancel.Scope()

    def slow():
        scope.cancel()
        raise RuntimeError("InterruptException: query interrupted")

    with tools.stopping(scope):
        result = _evidence(slow)

    assert result["is_error"] is True
    assert "Stop" in _text(result)
    assert "InterruptException" not in _text(result)


def test_a_connection_a_tool_opens_is_watched_by_the_exchanges_scope(monkeypatch):
    from portia.core import cancel
    from portia.core.io import connect

    scope = cancel.Scope()
    watched: list = []
    monkeypatch.setattr(scope, "watch", watched.append)

    with tools.stopping(scope):
        _evidence(lambda: connect().execute("SELECT 1").fetchone())

    assert len(watched) == 1


def test_the_slot_is_restored_after_the_exchange():
    from portia.core import cancel

    assert tools._stop is None
    with tools.stopping(cancel.Scope()):
        assert tools._stop is not None
    assert tools._stop is None


# --- the data is read by one call at a time (2026-10-08) ------------------------


def _handler(name: str):
    return next(t for t in tools.ALL_TOOLS if t.name == name).handler


_QUERY = {"sql": "SELECT 1", "inputs": ["a"], "question": "q"}


def test_calls_that_read_data_take_turns_and_the_others_do_not(monkeypatch):
    """A harness runs a message's read-only calls together, and each opens its own
    database sized to most of the machine: three wide `query_data` calls at once
    swapped a 16 GB laptop. A catalog read is never held behind them."""
    import threading
    import time

    lock = threading.Lock()
    running = {"now": 0, "most": 0}

    def busy(*args, **kwargs):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.2)
        with lock:
            running["now"] -= 1
        return {"ok": True}

    for name in ("profile_source", "query_data", "describe_source"):
        monkeypatch.setattr(tools.handlers, name, busy)

    async def burst(calls):
        return await asyncio.gather(*(_handler(name)(args) for name, args in calls))

    asyncio.run(
        burst([("profile_source", {"source": "a"}), ("query_data", _QUERY), ("profile_source", {})])
    )
    assert running["most"] == tools.DATA_READERS == 1

    running["most"] = 0
    asyncio.run(burst([("describe_source", {"source": s}) for s in "abc"]))
    assert running["most"] == 3


def test_a_call_waiting_its_turn_is_ended_by_a_press(monkeypatch):
    """Nothing runs while a call waits, so there is no query for Stop to
    interrupt; the wait itself has to notice the press."""
    import threading

    from portia.core import cancel

    started, release = threading.Event(), threading.Event()

    def holds_the_data(*args, **kwargs):
        started.set()
        release.wait(5)
        return {"ok": True}

    def must_not_run(*args, **kwargs):
        raise AssertionError("ran after the press")

    monkeypatch.setattr(tools.handlers, "profile_source", holds_the_data)
    monkeypatch.setattr(tools.handlers, "query_data", must_not_run)
    scope = cancel.Scope()

    async def press_while_waiting():
        first = asyncio.ensure_future(_handler("profile_source")({"source": "a"}))
        await asyncio.to_thread(started.wait, 5)
        waiting = asyncio.ensure_future(_handler("query_data")(_QUERY))
        await asyncio.sleep(0.2)
        scope.cancel()
        stopped = await asyncio.wait_for(waiting, 5)
        release.set()
        await first
        return stopped

    with tools.stopping(scope):
        stopped = asyncio.run(press_while_waiting())
    scope.close()

    assert stopped["is_error"] is True and "Stop" in _text(stopped)


# --- a job that reads is offered no way to build (2026-09-23) -------------------


def test_an_indexing_job_is_offered_every_tool_but_the_build_half():
    """The prompt said *build nothing* once, at its end; the tool list said
    otherwise on every request. Read-only by construction now, the way the
    agent has no filesystem by construction."""
    reads = {t.name for t in tools.offered(builds=False)}
    assert reads == {t.name for t in tools.offered()} - {"record_step", "run_spec"}
    assert {t.name for t in tools.BUILD_TOOLS} == {"record_step", "run_spec"}
    # The job's own output, its questions and its pictures all stay.
    assert {
        "set_interpretation",
        "set_group",
        "measure_overlaps",
        "query_data",
        "plot_data",
    } <= reads
    assert "record_step" not in tools.descriptions(builds=False)


# --- a table too wide to list comes back as its card (2026-10-13) -----------------------
#
# It came back grouped by type from 2026-10-08 (`_by_type`), every column counted
# and no flagged column named. The card names them (`core/listing.flagged`) and
# a filter lists any of them.


@pytest.fixture
def meters(tmp_path):
    """A real indexed table: a timestamp, twelve readings, a site and an empty column."""
    import pandas as pd

    from portia.catalog import index_source, init_project

    hours = 24
    frame = pd.DataFrame({"read_at": pd.date_range("2026-01-01", periods=hours, freq="h")})
    for i in range(12):
        frame[f"meter_{i:02d}"] = [float(i * h) if h % (i + 2) else None for h in range(hours)]
    frame["site"] = ["north", "south", "east"] * (hours // 3)
    frame["spare"] = None
    frame.to_csv(tmp_path / "meters.csv", index=False)
    portia_dir = tmp_path / ".portia"
    init_project("hourly meter readings", portia_dir=portia_dir)
    index_source(tmp_path / "meters.csv", portia_dir=portia_dir)
    return str(portia_dir)


@pytest.fixture
def wide_meters(tmp_path):
    """A real indexed table wider than a card lists and a profile gives: 260 readings."""
    import duckdb

    from portia.catalog import index_source, init_project

    readings = [
        f"CASE WHEN i % 4 = 0 THEN i * {k} END AS meter_{k:03d}"
        if k % 20 == 0
        else f"i * {k} AS meter_{k:03d}"
        for k in range(260)
    ]
    path = tmp_path / "wide.csv"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT i AS reading, {', '.join(readings)} FROM range(12) t(i)) TO '{path}' (HEADER)"
    )
    con.close()
    portia_dir = tmp_path / ".portia"
    init_project("one column per meter", portia_dir=portia_dir)
    index_source(path, portia_dir=portia_dir)
    return str(portia_dir)


def test_a_table_that_fits_is_sent_exactly_as_the_handler_built_it(meters):
    from portia.agent import handlers

    for name, handler in (
        ("describe_source", handlers.describe_source),
        ("profile_source", handlers.profile_source),
    ):
        result = _call(name, {"source": "meters", "portia_dir": meters})
        assert _text(result) == to_json_compact(handler("meters", meters))


def test_a_table_too_wide_to_list_comes_back_as_its_card(wide_meters):
    from portia.agent import handlers

    for name in ("describe_source", "profile_source"):
        answer = json.loads(_text(_call(name, {"source": "wide", "portia_dir": wide_meters})))
        assert answer["n_columns"] == 261 > handlers.CARD_LISTS_ALL
        assert "columns" not in answer and answer["more"], name
        assert sum(t["n"] for t in answer["types"].values()) == 261
        integers = answer["types"]["integer"]
        assert integers["n"] > handlers.TYPE_EXAMPLES
        assert integers["examples"][:2] == ["reading", "meter_000"]
        # Every flagged column is named, by the rarer side of its flag.
        assert answer["flagged"]["high_null"] == {
            "n": 13,
            "columns": [f"meter_{k:03d}" for k in range(0, 260, 20)],
        }
        # meter_000 is every reading times zero: constant, and named under both.
        assert answer["flagged"]["constant"] == {"n": 1, "columns": ["meter_000"]}
        assert answer["unflagged"] == 261 - 13


def test_a_filter_lists_the_columns_it_matches_and_counts_the_rest(wide_meters):
    from portia.agent import handlers

    args = {"source": "wide", "match": "METER_1*", "portia_dir": wide_meters}
    described = json.loads(_text(_call("describe_source", args)))
    profiled = json.loads(_text(_call("profile_source", args)))

    assert described["columns"]["n"] == profiled["columns"]["n"] == 100
    assert described["columns"]["fields"] == list(handlers.DESCRIBE_FIELDS)
    assert len(described["columns"]["rows"]) == 100 and "more" not in described["columns"]
    assert len(profiled["columns"]["rows"]) == handlers.PROFILE_CAP
    assert profiled["columns"]["shown"] == f"1-{handlers.PROFILE_CAP}"
    assert "50 more" in profiled["columns"]["more"]


def test_a_flag_too_common_to_name_says_how_to_list_it(monkeypatch):
    """The flag rule's count is never the last word: the card says which call names them."""
    from portia.agent import handlers
    from portia.core import listing as listing_module

    monkeypatch.setattr(listing_module, "PER_FLAG_NAMES", 3)
    columns = [
        {"name": f"m{i}", "inferred": "float", "flags": ["high_null"] if i < 10 else []}
        for i in range(30)
    ]
    card = handlers._overview(columns, "meters", measured=True, more="m")

    assert card["flagged"]["high_null"]["n"] == 10
    assert card["flagged"]["high_null"]["listed"] is False
    assert "flag 'high_null'" in card["flagged"]["high_null"]["more"]


def test_a_card_whose_list_does_not_fit_is_sent_as_its_overview(monkeypatch):
    """Two hundred long names with long roles can pass the budget; the overview cannot."""
    from portia.agent import handlers

    names = [f"reading_of_the_building_meter_number_{i:04d}_in_kilowatt_hours" for i in range(200)]
    rows = [
        [n, "float", "measure: " + "x" * 80, ["high_null"] if i < 3 else []]
        for i, n in enumerate(names)
    ]
    card = {
        "source": "meters",
        "path": "data/meters.csv",
        "summary": None,
        "n_rows": 24,
        "n_columns": 200,
        "columns": listing.listing(handlers.DESCRIBE_FIELDS, rows, n=200),
        "notes": listing.listing(("at", "text"), [["2026-10-13", "kept"]], n=1),
    }
    assert len(to_json_compact(card)) > tools.RESULT_BUDGET, "the fixture is not big enough"
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: card)

    result = _call("describe_source", {"source": "meters"})

    assert "is_error" not in result, _text(result)[:300]
    answer = json.loads(_text(result))
    assert list(answer) == [
        "source",
        "path",
        "summary",
        "n_rows",
        "n_columns",
        "types",
        "flagged",
        "unflagged",
        "more",
        "notes",
    ]
    assert answer["types"]["float"]["n"] == 200
    assert answer["flagged"]["high_null"] == {"n": 3, "columns": names[:3]}
    assert answer["unflagged"] == 197


def test_a_filtered_answer_is_not_coarsened():
    from portia.agent import handlers

    filtered = {"source": "meters", "n_columns": 2, "columns": {"n": 0, "rows": []}}
    assert handlers.overview_of(filtered) is filtered


# --- a step over a wide table, shortened, and a write never refused (2026-10-08) ---


@pytest.fixture
def wide_project(tmp_path, monkeypatch):
    """An indexed source wide enough that a `SELECT *` step's report is over the budget.

    Long names rather than thousands of columns: the report's width is names,
    and a few hundred long ones cross the budget without a minute of profiling.
    """
    import pandas as pd

    from portia.catalog import index_source, init_project

    monkeypatch.chdir(tmp_path)
    names = [f"meter_reading_at_building_number_{i:04d}_kwh" for i in range(400)]
    frame = pd.DataFrame(
        {name: [float(i + h) if h % 3 else None for h in range(6)] for i, name in enumerate(names)}
    )
    frame.to_csv("meters.csv", index=False)
    portia_dir = tmp_path / ".portia"
    init_project("hourly readings, one column per building", portia_dir=portia_dir)
    index_source("meters.csv", portia_dir=portia_dir)
    return str(portia_dir)


_WIDE_STEP = {
    "id": "all_meters",
    "op": "sql",
    "inputs": ["meters"],
    "sql": "SELECT * FROM meters",
    "rationale": "Every building's readings, unchanged.",
}


def test_a_step_over_a_wide_table_comes_back_shortened_not_refused(wide_project, tmp_path):
    from portia.agent import handlers

    full = handlers.record_step("specs/wide_a.yaml", dict(_WIDE_STEP), portia_dir=wide_project)
    assert len(to_json_compact(full)) > tools.RESULT_BUDGET, "the fixture is not wide enough"

    result = _call(
        "record_step",
        {"spec_path": "specs/wide.yaml", "step": dict(_WIDE_STEP), "portia_dir": wide_project},
    )

    assert "is_error" not in result, _text(result)[:300]
    answer = json.loads(_text(result))
    assert list(answer)[0] == "shortened"
    assert answer["step_id"] == "all_meters" and answer["outcome"]["n_cols"] == 400
    assert answer["outcome"]["null_rates"]["count"] == 400
    assert (tmp_path / "specs" / "wide.yaml").exists()


def test_running_a_wide_spec_comes_back_shortened(wide_project):
    from portia.agent import handlers

    handlers.record_step("specs/wide.yaml", dict(_WIDE_STEP), portia_dir=wide_project)

    result = _call("run_spec", {"spec_path": "specs/wide.yaml"})

    assert "is_error" not in result, _text(result)[:300]
    answer = json.loads(_text(result))
    assert list(answer)[0] == "shortened"
    (step,) = answer["steps"]
    assert step["id"] == "all_meters" and step["outcome"]["n_cols"] == 400


def test_a_write_whose_report_cannot_fit_is_answered_with_a_receipt(monkeypatch):
    """Never *you have none of it* after a write that happened: that reads as failure,
    and a copilot that believes it records the step twice."""
    written = {
        "spec": "specs/wide.yaml",
        "step_id": "all_meters",
        "n_steps": 1,
        "outcome": {
            "n_rows": 24,
            "n_cols": 400,
            "flags": [],
            "note": "x" * (2 * tools.RESULT_BUDGET),
        },
    }
    monkeypatch.setattr(tools.handlers, "record_step", lambda *a, **k: written)

    result = _call("record_step", {"spec_path": "specs/wide.yaml", "step": dict(_WIDE_STEP)})

    assert "is_error" not in result
    answer = json.loads(_text(result))
    assert list(answer)[0] == "receipt"
    assert "WAS recorded" in answer["receipt"] and "specs/wide.yaml#all_meters" in answer["receipt"]
    assert answer["outcome"] == {"n_rows": 24, "n_cols": 400, "flags": []}
    assert answer["step_id"] == "all_meters"


def test_a_read_too_large_even_shortened_is_still_refused(monkeypatch):
    """The receipt is for writes; a read past the budget keeps the refusal."""
    monkeypatch.setattr(
        tools.handlers,
        "run_spec",
        lambda *a, **k: {"spec": "s", "note": "x" * (2 * tools.RESULT_BUDGET)},
    )

    result = _call("run_spec", {"spec_path": "s"})

    assert result["is_error"] is True and "NOT sent" in _text(result)


def test_a_wide_built_table_reads_back_grouped(monkeypatch):
    columns = [
        {
            "name": f"meter_{i:04d}",
            "inferred": "float",
            "null_rate": 0.01,
            "n_distinct": 9,
            "flags": [],
        }
        for i in range(2000)
    ]
    payload = {"spec": "specs/wide.yaml", "model": "wide", "measured": {"columns": columns}}
    monkeypatch.setattr(tools.handlers, "read_spec", lambda *a, **k: payload)

    result = _call("read_spec", {"spec": "wide", "measured": True})

    assert "is_error" not in result, _text(result)[:300]
    answer = json.loads(_text(result))
    assert list(answer)[0] == "shortened"
    (floats,) = answer["measured"]["columns_by_type"]
    assert floats["n_columns"] == 2000 and floats["first"][0] == "meter_0000"


# --- the graph tools' arguments (2026-10-13) --------------------------------------------


def test_graph_lookup_takes_a_neighbour_or_a_table_to_reach():
    properties = tools.graph_lookup.input_schema["properties"]
    assert {"table", "column", "neighbour", "to"} <= set(properties)
    assert tools.graph_lookup.input_schema["required"] == ["table"]


def test_the_graph_lookup_description_states_the_numbers_the_code_holds():
    from portia.knowledge import query

    assert f"at most {query.ROUTE_HOPS} steps" in tools.graph_lookup.description
    assert f"up to {query.ROUTES} of them" in tools.graph_lookup.description
