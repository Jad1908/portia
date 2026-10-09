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


def _call(name: str, args: dict) -> dict:
    tool = next(t for t in tools.ALL_TOOLS if t.name == name)
    return asyncio.run(tool.handler(args))


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def test_a_per_column_payload_reaches_the_model_as_a_table(monkeypatch):
    payload = {
        "source": "golden.csv",
        "n_rows": 2,
        "columns": [{"name": "a", "top": "x"}, {"name": "b", "min": 1, "max": 9}],
    }
    monkeypatch.setattr(tools.handlers, "profile_source", lambda *a, **k: payload)

    text = _text(_call("profile_source", {"source": "golden.csv"}))

    assert "## 1 of 2 columns" in text
    assert "name\ttop" in text and "name\tmin\tmax" in text
    assert json.loads(text.splitlines()[0]) == {"source": "golden.csv", "n_rows": 2}


def test_the_other_rung_that_returns_columns_is_a_table_too(monkeypatch):
    """`describe_source` was the larger consumer of the two, by a whole session.

    31 calls and 101,601 characters in the AQN build run, against
    `profile_source`'s 40,247 — and no single call was ever refused, which is why
    it took counting the log to notice.
    """
    payload = {
        "source": "golden.csv",
        "summary": "hotels",
        "columns": [
            {"name": "code", "role": "identifier", "inferred": "text", "flags": []},
            {"name": "city", "role": "attribute", "inferred": "categorical", "flags": []},
        ],
    }
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: payload)

    text = _text(_call("describe_source", {"source": "golden.csv"}))

    assert "## 2 of 2 columns" in text
    assert "name\trole\tinferred\tflags" in text
    assert json.loads(text.splitlines()[0]) == {
        "source": "golden.csv",
        "summary": "hotels",
    }


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
    """`columnar` and compact JSON do not cost the same per record, so the
    payload has to be encoded before it can be judged."""
    records = {"columns": [{"name": f"col_{i:04d}", "blob": "x" * 20} for i in range(800)]}

    as_json = _evidence(lambda: records)  # 40,013 characters
    as_table = _evidence(lambda: records, encode=tools._as_columns)  # 24,035

    assert as_json["is_error"] is True
    assert "is_error" not in as_table, "the same dict fits through the cheaper encoder"


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


# --- a table too wide to list comes back grouped (2026-10-08) -------------------------

#: A datetime, 2,000 numeric columns, two text columns and three empty ones: the
#: shape of a wide meter export, where `describe_source` and `profile_source`
#: both used to be refused and the copilot could only name columns blind.
_N_WIDE = 2006


def _wide(*, profiled: bool) -> dict:
    """A wide table as `describe_source` (``profiled=False``) or `profile_source` sends it."""

    def column(name: str, inferred: str, flags: list[str], **facts) -> dict:
        measured = facts if profiled else {}
        return {"name": name, "role": None, "inferred": inferred, "flags": flags, **measured}

    columns = [column("read_at", "datetime", [], null_rate=0.0, n_distinct=8760, samples=[])]
    columns += [
        column(
            f"meter_{i:04d}",
            "float",
            ["high_null"] if i % 25 == 0 else [],
            null_rate=0.6 if i % 25 == 0 else 0.01,
            n_distinct=900 + i,
            min=0.0,
            max=float(i),
            mean=1.5,
            samples=[0.1, 0.2, 0.3],
        )
        for i in range(2000)
    ]
    columns += [
        column(f"site_{i}", "categorical", [], null_rate=0.0, n_distinct=4, top="north")
        for i in (1, 2)
    ]
    columns += [column(f"spare_{i}", "empty", ["all_null"], null_rate=1.0) for i in (1, 2, 3)]
    head = {"source": "data/meters.csv", "summary": "hourly readings"}
    if profiled:
        head |= {"n_rows": 8760, "n_cols": len(columns)}
    return {**head, "columns": columns}


def _grouped(result: dict) -> dict:
    assert "is_error" not in result, _text(result)[:300]
    assert len(_text(result)) <= tools.RESULT_BUDGET
    return json.loads(_text(result))


def test_a_table_too_wide_to_describe_comes_back_grouped(monkeypatch):
    payload = _wide(profiled=False)
    assert len(tools._as_columns(payload)) > tools.RESULT_BUDGET, "the fixture is not wide enough"
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: payload)

    answer = _grouped(_call("describe_source", {"source": "meters"}))

    assert list(answer)[0] == "grouped", "the answer says it is grouped before anything else"
    assert f"{_N_WIDE:,} columns" in answer["grouped"]
    assert answer["summary"] == "hourly readings"
    groups = answer["columns_by_type"]
    assert sum(g["n_columns"] for g in groups) == _N_WIDE
    assert [g["inferred"] for g in groups] == ["datetime", "float", "categorical", "empty"]


def test_a_grouped_description_carries_no_statistics(monkeypatch):
    """L2 is the rung of meaning: roles and flags are tallied, nothing is measured."""
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: _wide(profiled=False))

    groups = _grouped(_call("describe_source", {"source": "meters"}))["columns_by_type"]
    floats = next(g for g in groups if g["inferred"] == "float")

    assert floats["flags"] == {"high_null": 80}
    assert floats["role"] == {}
    assert not {"null_rate", "n_distinct", "min", "max"} & set(floats)


def test_a_table_too_wide_to_profile_comes_back_grouped_with_its_spreads(monkeypatch):
    monkeypatch.setattr(tools.handlers, "profile_source", lambda *a, **k: _wide(profiled=True))

    answer = _grouped(_call("profile_source", {"source": "meters"}))

    assert answer["n_cols"] == _N_WIDE
    groups = answer["columns_by_type"]
    assert sum(g["n_columns"] for g in groups) == _N_WIDE
    floats = next(g for g in groups if g["inferred"] == "float")
    assert floats["null_rate"]["max"] == 0.6 and floats["null_rate"]["median"] == 0.01
    assert floats["n_distinct"]["min"] == 900 and floats["n_distinct"]["max"] == 2899
    assert (floats["min"], floats["max"]) == (0.0, 1999.0)
    assert floats["first"][0] == "meter_0000" and floats["last"][-1] == "meter_1999"
    # A type with a few columns is listed in the tool's own per-column shape.
    sites = next(g for g in groups if g["inferred"] == "categorical")
    assert [c["name"] for c in sites["columns"]] == ["site_1", "site_2"]
    assert sites["columns"][0]["top"] == "north"


def test_named_columns_too_many_to_send_are_refused_not_grouped(monkeypatch):
    """Naming columns is already the narrower question; past the budget it is
    refused as it always was."""
    monkeypatch.setattr(tools.handlers, "profile_source", lambda *a, **k: _wide(profiled=True))

    named = [f"meter_{i:04d}" for i in range(2000)]
    result = _call("profile_source", {"source": "meters", "columns": named})

    assert result["is_error"] is True
    assert "NOT sent" in _text(result)


def test_a_grouped_answer_still_over_the_budget_is_refused_with_the_full_size():
    """The grouped form is a coarser answer, not a truncation: if it does not fit
    either, the refusal is the one there always was."""
    payload = {**_wide(profiled=False), "summary": "x" * (tools.RESULT_BUDGET + 1)}
    full = len(tools._as_columns(payload))

    result = _evidence(lambda: payload, encode=tools._as_columns, coarser=tools._described_by_type)

    assert result["is_error"] is True
    assert f"{full:,} characters" in _text(result)


@pytest.fixture
def meters(tmp_path):
    """A real indexed table with a type big enough to be summarised when grouped."""
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


def test_a_table_that_fits_is_sent_exactly_as_before(meters):
    from portia.agent import handlers

    for name, handler in (
        ("describe_source", handlers.describe_source),
        ("profile_source", handlers.profile_source),
    ):
        result = _call(name, {"source": "meters", "portia_dir": meters})
        assert _text(result) == tools._as_columns(handler("meters", meters))


def test_the_grouped_forms_read_the_fields_the_handlers_send(meters):
    """The two partials name fields; a renamed field in a handler would leave a
    group silently without it, so this reads real handler output."""
    from portia.agent import handlers

    described = tools._described_by_type(handlers.describe_source("meters", meters))
    profiled = tools._profiled_by_type(handlers.profile_source("meters", meters))

    def floats(answer: dict) -> dict:
        return next(g for g in answer["columns_by_type"] if g["inferred"] == "float")

    assert {"n_columns", "first", "last", "role", "flags"} == set(floats(described)) - {"inferred"}
    assert {"null_rate", "n_distinct", "min", "max"} <= set(floats(profiled))
    assert floats(profiled)["n_columns"] == 12
    for answer in (described, profiled):
        assert sum(g["n_columns"] for g in answer["columns_by_type"]) == 15
