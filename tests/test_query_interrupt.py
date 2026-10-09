"""*Interrupt query*: one data call stopped from its card, and the exchange goes on.

`docs/CONVERSATION.md` §16. A model once wrote a `query_data` that unpivoted two
1,579-column tables and joined them on the meter name without the hour, some
430 billion rows, and nothing could stop that query but the Stop that ended the
whole exchange. These pin the engine's half: a press stops that call and no
other, a call waiting its turn never starts, the exchange's Stop still stops
everything, a step being interrupted writes nothing, and what the copilot reads
in place of the result says why and what portia measured after.
"""

from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from portia.agent import handlers, tools
from portia.catalog import index_source, init_project
from portia.core import cancel
from portia.fixtures import sales_customers, sales_orders

#: A query long enough to still be running when the press lands.
_SLOW = "SELECT count(*) FROM range(20000000000) t(i) WHERE i % 7 = 0"
#: Well inside the time `_SLOW` takes, well past `cancel.INTERRUPT_EVERY`.
_SETTLE = 0.4
_ARGS = {"sql": "SELECT 1", "inputs": ["orders"], "question": "how many?"}
_FACTS = {"inputs": {"orders": {"rows": 8, "from": "counted"}}}


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _slow(*args, **kwargs):
    """A handler whose query runs until something interrupts it."""
    con = duckdb.connect(":memory:")
    cancel.watch(con)
    con.execute(_SLOW).fetchall()
    return {"finished": True}


async def _until(predicate, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, "never happened"
        await asyncio.sleep(0.02)


def _phase(card: str, tool: str = "query_data", args: dict = _ARGS) -> str | None:
    found = tools.data_call(card, tool, args)
    return found["phase"] if found else None


@pytest.fixture
def exchange():
    """One exchange's scope, installed as the window's `Conversation.send` installs it."""
    scope = cancel.Scope()
    with tools.stopping(scope):
        yield scope
    scope.close()


@pytest.fixture
def measured(monkeypatch):
    """What portia measures after a press, canned, so a test is about the press."""
    seen: list[tuple[str, dict]] = []

    def facts(tool, args, **kwargs):
        seen.append((tool, args))
        return dict(_FACTS)

    monkeypatch.setattr(tools.handlers, "interrupted_facts", facts)
    return seen


# --- one call, and only that call --------------------------------------------


def test_a_press_stops_that_call_and_the_exchange_goes_on(monkeypatch, exchange, measured):
    monkeypatch.setattr(tools.handlers, "query_data", _slow)

    async def press():
        call = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_1") == tools.RUNNING)
        await asyncio.sleep(_SETTLE)
        assert tools.interrupt_call("toolu_1", "query_data", _ARGS, reason="Wrong query") == (
            tools.INTERRUPTING
        )
        return await asyncio.wait_for(call, 10)

    result = asyncio.run(press())

    assert not exchange.cancelled
    assert result["is_error"] is True
    text = _text(result)
    assert "interrupted this `query_data` call" in text
    assert "Their reason: Wrong query." in text
    assert "It ran for" in text
    assert '"rows":8' in text
    assert "Stop" not in text.split("This is not the Stop")[0]
    assert measured == [("query_data", _ARGS)]
    logged = tools.take_interruption("toolu_1")
    assert logged["reason"] == "Wrong query" and logged["landed"] is True
    assert logged["measure"] == tools.MEASURED and logged["measured"] == _FACTS
    assert logged["ran"] >= _SETTLE
    assert tools.take_interruption("toolu_1") is None  # taken once


def test_another_call_in_the_same_exchange_is_not_touched(monkeypatch, exchange, measured):
    other = {"source": "orders"}
    monkeypatch.setattr(tools.handlers, "query_data", _slow)
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: {"described": True})

    async def press():
        call = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_1") == tools.RUNNING)
        tools.interrupt_call("toolu_1", "query_data", _ARGS)
        described = await tools.describe_source.handler(other)
        return await asyncio.wait_for(call, 10), described

    interrupted, described = asyncio.run(press())
    assert interrupted["is_error"] is True
    assert "described" in _text(described)


def test_the_exchanges_stop_still_stops_every_call(monkeypatch, exchange, measured):
    monkeypatch.setattr(tools.handlers, "query_data", _slow)

    async def stop():
        call = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_1") == tools.RUNNING)
        exchange.cancel()
        return await asyncio.wait_for(call, 10)

    result = asyncio.run(stop())
    assert "Stop" in _text(result) and "interrupted this" not in _text(result)
    assert measured == []


def test_the_exchanges_stop_wins_over_a_press_on_the_same_call(monkeypatch, exchange, measured):
    monkeypatch.setattr(tools.handlers, "query_data", _slow)

    async def both():
        call = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_1") == tools.RUNNING)
        tools.interrupt_call("toolu_1", "query_data", _ARGS, reason="Takes too long")
        exchange.cancel()
        return await asyncio.wait_for(call, 10)

    assert "interrupted this" not in _text(asyncio.run(both()))


def test_a_call_waiting_its_turn_can_be_interrupted_and_never_starts(
    monkeypatch, exchange, measured
):
    started, release = threading.Event(), threading.Event()
    ran: list[str] = []

    def holds_the_data(*args, **kwargs):
        started.set()
        release.wait(10)
        return {"held": True}

    def must_not_run(*args, **kwargs):
        ran.append("query_data")
        return {}

    monkeypatch.setattr(tools.handlers, "profile_source", holds_the_data)
    monkeypatch.setattr(tools.handlers, "query_data", must_not_run)

    async def press_the_waiting_one():
        first = asyncio.ensure_future(tools.profile_source.handler({"source": "a"}))
        await asyncio.to_thread(started.wait, 5)
        waiting = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_2") == tools.WAITING)
        assert tools.data_call("toolu_1", "profile_source", {"source": "a"})["waiting"] == 1
        tools.interrupt_call("toolu_2", "query_data", _ARGS)
        stopped = await asyncio.wait_for(waiting, 5)
        release.set()
        return stopped, await first

    stopped, first = asyncio.run(press_the_waiting_one())
    assert ran == []
    assert "never started" in _text(stopped)
    assert measured == []  # nothing ran, so nothing is measured
    assert "held" in _text(first)
    assert tools.take_interruption("toolu_2")["ran"] is None


def test_a_second_press_while_portia_measures_skips_the_measuring(monkeypatch, exchange):
    monkeypatch.setattr(tools.handlers, "query_data", _slow)
    monkeypatch.setattr(tools.handlers, "interrupted_facts", lambda *a, **k: _slow())

    async def press_twice():
        call = asyncio.ensure_future(tools.query_data.handler(dict(_ARGS)))
        await _until(lambda: _phase("toolu_1") == tools.RUNNING)
        tools.interrupt_call("toolu_1", "query_data", _ARGS, reason="Takes too long")
        await _until(lambda: _phase("toolu_1") == tools.MEASURING)
        assert tools.interrupt_call("toolu_1", "query_data", _ARGS) == tools.SKIPPING
        return await asyncio.wait_for(call, 10)

    result = asyncio.run(press_twice())
    assert not exchange.cancelled
    assert "skipped what portia measures" in _text(result)
    assert tools.take_interruption("toolu_1")["measure"] == tools.SKIPPED


def test_a_press_before_the_call_reaches_portia_is_held_until_it_does(
    monkeypatch, exchange, measured
):
    """The card is drawn from the model's message; the request can come a moment later."""
    ran: list[str] = []
    monkeypatch.setattr(tools.handlers, "query_data", lambda *a, **k: ran.append("ran") or {})

    assert tools.interrupt_call("toolu_9", "query_data", _ARGS, reason="Wrong query") == (
        tools.PENDING
    )
    result = asyncio.run(tools.query_data.handler(dict(_ARGS)))

    assert ran == []
    assert "Their reason: Wrong query." in _text(result)
    assert "never started" in _text(result)


def test_a_press_held_for_a_call_that_never_came_ends_with_its_exchange(monkeypatch):
    with tools.stopping(cancel.Scope()):
        tools.interrupt_call("toolu_9", "query_data", _ARGS)
    ran: list[str] = []
    monkeypatch.setattr(tools.handlers, "query_data", lambda *a, **k: ran.append("ran") or {})
    with tools.stopping(cancel.Scope()):
        asyncio.run(tools.query_data.handler(dict(_ARGS)))
    assert ran == ["ran"]


def test_a_call_that_finished_first_answers_as_it_would_have(monkeypatch, exchange):
    """A press on a card drawn one refresh ago is an ordinary race, not an error."""
    monkeypatch.setattr(tools.handlers, "query_data", lambda *a, **k: {"answer": 42})
    result = asyncio.run(tools.query_data.handler(dict(_ARGS)))
    assert tools.interrupt_call("toolu_1", "query_data", _ARGS) == tools.PENDING
    assert "42" in _text(result)


# --- record_step: an interrupted step writes nothing ------------------------------


@pytest.fixture
def sales(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sales_orders().to_csv("orders.csv", index=False)
    sales_customers().to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    init_project("order reconciliation", portia_dir=d)
    index_source("orders.csv", portia_dir=d)
    index_source("customers.csv", portia_dir=d)
    return str(d)


def test_an_interrupted_step_writes_nothing_and_says_so(sales, tmp_path, exchange):
    step = {
        "id": "everything",
        "op": "sql",
        "inputs": ["orders"],
        "sql": "SELECT o.customer_id, sum(r.range) AS s FROM orders o, range(400000000) r GROUP BY 1",
        "rationale": "a step that takes long enough to be interrupted",
    }
    args = {"spec_path": "specs/slow.yaml", "step": step, "portia_dir": sales}

    async def press():
        call = asyncio.ensure_future(tools.record_step.handler(args))
        await _until(lambda: _phase("toolu_s", "record_step", args) == tools.RUNNING)
        await asyncio.sleep(_SETTLE)
        tools.interrupt_call("toolu_s", "record_step", args, reason="Takes too long")
        return await asyncio.wait_for(call, 30)

    result = asyncio.run(press())

    text = _text(result)
    assert "Nothing was written" in text
    assert not list(tmp_path.rglob("slow.yaml"))
    # What the step read, measured after: its input counted, its comma join named.
    assert '"orders":{"rows":8,"from":"counted"}' in text
    assert "kind_not_measured" in text


def test_a_step_already_writing_refuses_the_press(monkeypatch, exchange):
    committed, release = threading.Event(), threading.Event()

    def writing(*args, **kwargs):
        cancel.commit()
        committed.set()
        release.wait(10)
        return {"spec": "specs/x.yaml", "step_id": "x", "outcome": {}}

    monkeypatch.setattr(tools.handlers, "record_step", writing)
    args = {"spec_path": "specs/x.yaml", "step": {"id": "x"}}

    async def press_too_late():
        call = asyncio.ensure_future(tools.record_step.handler(args))
        await asyncio.to_thread(committed.wait, 5)
        outcome = tools.interrupt_call("toolu_w", "record_step", args, reason="Wrong query")
        release.set()
        return outcome, await asyncio.wait_for(call, 10)

    outcome, result = asyncio.run(press_too_late())
    assert outcome == tools.TOO_LATE
    assert "is_error" not in result
    assert tools.take_interruption("toolu_w")["landed"] is False


# --- which call a card is about ---------------------------------------------------


def _request_naming(use_id: str):
    """The MCP request's context, as the server sets it, carrying the harness's call id."""
    from mcp.server.lowlevel.server import request_ctx

    meta = SimpleNamespace(model_extra={tools.TOOL_USE_META: use_id})
    return request_ctx.set(SimpleNamespace(meta=meta))


def test_a_card_finds_its_call_by_the_id_the_harness_sends(exchange):
    from mcp.server.lowlevel.server import request_ctx

    first = tools._register("query_data", dict(_ARGS))
    token = _request_naming("toolu_B")
    try:
        second = tools._register("query_data", dict(_ARGS))
    finally:
        request_ctx.reset(token)
    try:
        # Identical arguments; the id says which.
        assert tools.data_call("toolu_B", "query_data", _ARGS) is not None
        assert second.card == "toolu_B" and first.card is None
    finally:
        tools._retire(first)
        tools._retire(second)


def test_without_an_id_identical_calls_are_matched_in_the_order_they_came(exchange):
    first = tools._register("query_data", dict(_ARGS))
    second = tools._register("query_data", dict(_ARGS))
    try:
        tools.data_call("card-1", "query_data", _ARGS)
        tools.data_call("card-2", "query_data", _ARGS)
        assert (first.card, second.card) == ("card-1", "card-2")
        assert tools.data_call("card-1", "query_data", {"other": 1}) is not None  # bound
    finally:
        tools._retire(first)
        tools._retire(second)


def test_every_tool_that_reads_data_is_named_and_carries_its_name():
    """`DATA_TOOLS` is what the window asks; the call sites are what takes turns."""
    source = Path(tools.__file__).read_text(encoding="utf-8")
    sites: dict[str, str | None] = {}
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_evidence"):
            continue
        keywords = {k.arg: k.value for k in node.keywords}
        reads = keywords.get("reads_data")
        if isinstance(reads, ast.Constant) and reads.value is True:
            called = keywords.get("called")
            assert isinstance(called, ast.Tuple), ast.unparse(node)
            sites[ast.literal_eval(called.elts[0])] = ast.unparse(called.elts[1])
    assert set(sites) == tools.DATA_TOOLS
    assert set(sites.values()) == {"args"}


# --- what portia measures after the press ------------------------------------------


def test_the_measuring_counts_the_inputs_and_the_join_exactly(sales):
    sql = "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
    facts = handlers.interrupted_facts(
        "query_data", {"sql": sql, "inputs": ["orders", "customers"]}, portia_dir=sales
    )
    assert facts["inputs"] == {
        "orders": {"rows": 8, "from": handlers.COUNTED},
        "customers": {"rows": 6, "from": handlers.COUNTED},
    }
    (join,) = facts["joins"]
    assert (join["left"], join["right"], join["on"]) == (
        "orders",
        "customers",
        ["customer_id = customer_id"],
    )
    assert join["left_repeats"] == {"min": 1, "max": 2}
    assert join["right_repeats"] == {"min": 1, "max": 2}
    # 1000: 1x1, 1001: 2x2, 1002: 2x1, 1003: 1x1.
    assert join["rows"] == 8
    assert "not_read" not in facts


def test_an_input_that_no_longer_resolves_costs_only_its_own_size(sales):
    facts = handlers.interrupted_facts(
        "query_data",
        {"sql": "SELECT 1 FROM orders", "inputs": ["orders", "gone"]},
        portia_dir=sales,
    )
    assert facts["inputs"]["orders"]["rows"] == 8
    assert facts["inputs"]["gone"] == {"rows": None, "from": handlers.UNRESOLVED}


def test_a_tool_with_no_sql_gets_the_sizes_of_what_it_read(sales):
    facts = handlers.interrupted_facts(
        "join_findings", {"left": "orders", "right": "customers"}, portia_dir=sales
    )
    assert set(facts) == {"inputs"}
    assert facts["inputs"]["customers"]["rows"] == 6


def test_on_a_warehouse_nothing_is_scanned_and_the_catalog_says_the_sizes(
    sales, tmp_path, monkeypatch
):
    """The interrupt was pressed to stop a scan on someone's meter; measuring must not start one."""
    from portia import catalog

    entry = catalog.load_source("orders", portia_dir=sales)
    entry["indexed"] = {**entry["indexed"], "rows": 1234, "approximate": ["rows"]}
    catalog._write(Path(sales) / "sources" / "orders.yaml", entry)

    class Warehouse:
        remote = True

        def execute(self, *args, **kwargs):
            raise AssertionError("scanned the warehouse")

    monkeypatch.setattr(handlers, "connect", lambda: Warehouse())
    sql = "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id"
    facts = handlers.interrupted_facts(
        "query_data", {"sql": sql, "inputs": ["orders", "customers"]}, portia_dir=sales
    )
    assert facts == {
        "scanned": False,
        "inputs": {
            "orders": {"rows": 1234, "from": handlers.FROM_CATALOG, "approximate": True},
            "customers": {"rows": None, "from": handlers.UNKNOWN},
        },
    }
    record = tools.DataCall(
        "query_data",
        {},
        "",
        None,
        cancel.Scope(),
        None,
        0.0,
        interruption=tools.Interruption(
            None, "", 0.0, ran=3.0, measure=tools.MEASURED, measured=facts
        ),
    )
    text = _text(tools._interrupted(record))
    assert "scanned nothing" in text and "They gave no reason" in text
    record.scope.close()
