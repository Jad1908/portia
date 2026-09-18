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
