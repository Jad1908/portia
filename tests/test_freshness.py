"""Stale is loud: an answer naming a table that changed since it was indexed says so first.

`catalog.freshness` answers in codes, for a file (a stat) and a warehouse table
(one metadata query, believed for a minute); `agent/freshness.py` words the
codes and finds the tables a call's arguments name; `tools._evidence` puts the
list first in every tool's answer. The warehouse is faked: a session that
counts the questions it is asked.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from portia import catalog
from portia.agent import freshness, handlers, prompts, tools


@pytest.fixture(autouse=True)
def _nothing_remembered():
    catalog.forget_facts()
    yield
    catalog.forget_facts()


@pytest.fixture
def shop(tmp_path, monkeypatch) -> str:
    """Two files indexed, and a model reading one of them. One column is upper case."""
    monkeypatch.chdir(tmp_path)
    pd.DataFrame({"ORDER_ID": [1, 2, 3], "amount": [1.5, 2.5, 4.0]}).to_csv(
        "orders.csv", index=False
    )
    pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}).to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    catalog.init_project("a shop", portia_dir=d)
    catalog.index_source("orders.csv", portia_dir=d)
    catalog.index_source("customers.csv", portia_dir=d)
    handlers.record_step(
        "specs/big_orders.yaml",
        {
            "id": "big",
            "op": "sql",
            "inputs": ["orders"],
            "sql": "SELECT * FROM orders WHERE amount > 2",
        },
        portia_dir=str(d),
    )
    return str(d)


def _touch(path: str, extra: str) -> None:
    """Change a file after it was indexed: more bytes and a later mtime."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(extra)
    later = os.stat(path).st_mtime + 5
    os.utime(path, (later, later))


def _entry(d: str, name: str) -> dict:
    return catalog.load_catalog(d)["sources"][name]


# --- a file -----------------------------------------------------------------


def test_a_file_unchanged_since_it_was_indexed_is_fresh(shop):
    assert catalog.freshness("orders", _entry(shop, "orders"), portia_dir=shop) is None


def test_a_file_changed_after_it_was_indexed_says_what_changed(shop):
    before = _entry(shop, "orders")
    _touch("orders.csv", "4,9.0\n")
    fresh = catalog.freshness("orders", before, portia_dir=shop)
    assert fresh is not None
    assert fresh["changed"] == catalog.FILE_CHANGED and fresh["path"] == "orders.csv"
    assert fresh["was"]["size"] < fresh["now"]["size"]
    assert fresh["indexed"] == before["indexed"]["at"]


def test_a_file_that_is_gone_says_so(shop):
    os.remove("orders.csv")
    fresh = catalog.freshness("orders", _entry(shop, "orders"), portia_dir=shop)
    assert fresh is not None and fresh["changed"] == catalog.FILE_GONE


def test_an_entry_with_nothing_recorded_cannot_tell(shop):
    entry = {**_entry(shop, "orders"), "indexed": None}
    fresh = catalog.freshness("orders", entry, portia_dir=shop)
    assert fresh == {"source": "orders", "changed": None, "unknown": True, "why": "no_marker"}


# --- a warehouse table --------------------------------------------------------


class _Warehouse:
    """A session answering `table_facts`, counting how often it is asked."""

    remote = True

    def __init__(self, facts: dict | Exception):
        self.facts = facts
        self.asked = 0

    def table_facts(self, qualified: str) -> dict:
        self.asked += 1
        if isinstance(self.facts, Exception):
            raise self.facts
        return dict(self.facts)


_INDEXED = {"size": 100, "mtime": "2026-10-01T09:00:00", "rows": 3, "at": "2026-10-01T10:00:00"}


def _remote(kind: str = "table", **indexed) -> dict:
    return {
        "source": "SALES.PUBLIC.ORDERS",
        catalog.REMOTE: {"database": "SALES", "schema": "PUBLIC", "table": "ORDERS", "kind": kind},
        "indexed": {**_INDEXED, **indexed},
    }


def test_a_warehouse_table_is_asked_once_and_believed_for_a_while(monkeypatch):
    con = _Warehouse({**_INDEXED, "at": "now"})
    assert catalog.freshness("orders", _remote(), con=con) is None
    assert catalog.freshness("orders", _remote(), con=con) is None
    assert con.asked == 1, "a burst of calls asks the warehouse once"

    con.facts = {**_INDEXED, "mtime": "2026-10-13T08:00:00"}
    assert catalog.freshness("orders", _remote(), con=con) is None, "still within the minute"
    clock = catalog.time.monotonic() + catalog.WAREHOUSE_FACTS_S + 1
    monkeypatch.setattr(catalog.time, "monotonic", lambda: clock)
    fresh = catalog.freshness("orders", _remote(), con=con)
    assert con.asked == 2
    assert fresh == {
        "source": "orders",
        "changed": catalog.TABLE_CHANGED,
        "table": "SALES.PUBLIC.ORDERS",
        "indexed": _INDEXED["at"],
    }


def test_remote_changed_on_request_still_asks_every_time():
    con = _Warehouse(dict(_INDEXED))
    entry = _remote()
    assert catalog.remote_changed(entry, con) is False
    con.facts = {**_INDEXED, "size": 200}
    assert catalog.remote_changed(entry, con) is True and con.asked == 2


def test_a_view_has_no_marker_and_the_warehouse_is_not_asked():
    con = _Warehouse(dict(_INDEXED))
    fresh = catalog.freshness("orders", _remote(kind="view"), con=con)
    assert fresh == {"source": "orders", "changed": None, "unknown": True, "why": "no_marker"}
    assert catalog.freshness("orders", _remote(mtime=None), con=con)["why"] == "no_marker"
    assert con.asked == 0


def test_a_table_the_warehouse_cannot_find_is_gone_and_a_failure_cannot_tell():
    gone = catalog.freshness("orders", _remote(), con=_Warehouse(ValueError("no table")))
    assert gone is not None and gone["changed"] == catalog.TABLE_GONE
    catalog.forget_facts()
    down = catalog.freshness("orders", _remote(), con=_Warehouse(RuntimeError("timeout")))
    assert down == {"source": "orders", "changed": None, "unknown": True, "why": "unreachable"}


def test_without_a_connection_only_a_remembered_answer_is_used():
    assert catalog.freshness("orders", _remote())["why"] == catalog.NOT_ASKED
    catalog.remember_facts("SALES.PUBLIC.ORDERS", {**_INDEXED, "size": 5})
    assert catalog.freshness("orders", _remote())["changed"] == catalog.TABLE_CHANGED


def test_what_portia_asks_anyway_refreshes_what_it_remembers(tmp_path):
    """A re-scope records new facts; an older remembered answer must not contradict them."""
    import duckdb

    from tests.test_remote_catalog import _Warehouse as Fake

    inner = duckdb.connect()
    inner.execute("CREATE SCHEMA sales")
    inner.execute("CREATE TABLE sales.orders AS SELECT 1 AS id")
    warehouse = Fake(inner)
    d = tmp_path / ".portia"
    catalog.init_project("w", portia_dir=d)
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=d)
    inner.execute("INSERT INTO sales.orders VALUES (2)")
    catalog.scope_table("memory.sales.orders", warehouse, portia_dir=d)
    entry = catalog.load_catalog(d)["sources"]["orders"]
    assert catalog.freshness("orders", entry, portia_dir=d) is None


# --- the notice, worded -----------------------------------------------------------


def test_a_change_is_worded_from_the_prompts_with_what_to_do(shop):
    _touch("orders.csv", "4,9.0\n")
    (notice,) = freshness.notices({"source": "orders"}, shop)
    assert notice["source"] == "orders"
    assert notice["changed"].startswith("orders.csv was modified on")
    assert "bytes then" in notice["changed"]
    assert notice["do"] == prompts.notice("stale_do")
    assert "read_by" not in notice


def test_a_fresh_table_and_an_unknown_name_say_nothing(shop):
    assert freshness.notices({"source": "orders"}, shop) == []
    assert freshness.notices({"inputs": ["portia_columns", "nope"]}, shop) == []
    assert freshness.notices({}, shop) == []


def test_a_model_is_as_fresh_as_the_sources_it_reads(shop):
    _touch("orders.csv", "4,9.0\n")
    (notice,) = freshness.notices({"inputs": ["big_orders"]}, shop)
    assert notice["source"] == "orders" and notice["read_by"] == ["big_orders"]
    (step,) = freshness.notices({"source": "specs/big_orders.yaml#big"}, shop)
    assert step["read_by"] == ["big_orders"]
    (spec,) = freshness.notices({"spec_path": "specs/big_orders.yaml"}, shop)
    assert spec["source"] == "orders"


def test_a_source_named_and_read_by_a_named_model_is_listed_once_as_named(shop):
    _touch("orders.csv", "4,9.0\n")
    notices = freshness.notices({"left": "big_orders", "right": "ORDERS"}, shop)
    assert [n["source"] for n in notices] == ["orders"]
    assert "read_by" not in notices[0], "named itself, under its case-folded name"


def test_every_naming_argument_is_read():
    args = {
        "source": "a",
        "table": "b",
        "to": "c",
        "neighbour": "k",
        "left": "d",
        "right": "e",
        "inputs": ["f", "specs/g.yaml#step"],
        "pairs": [{"left": "h", "right": "a"}],
        "spec": "i",
        "spec_path": "specs/j.yaml",
        "question": "not a table",
    }
    assert freshness.named(args) == [
        ("a", False),
        ("b", False),
        ("c", False),
        ("k", False),
        ("d", False),
        ("e", False),
        ("f", False),
        ("specs/g.yaml", True),
        ("h", False),
        ("i", True),
        ("specs/j.yaml", True),
    ]


def test_a_warehouse_view_is_named_as_unknown_in_the_notice(tmp_path, monkeypatch):
    d = tmp_path / ".portia"
    catalog.init_project("w", portia_dir=d)
    (d / "sources").mkdir(parents=True, exist_ok=True)
    catalog._write(d / "sources" / "orders.yaml", _remote(kind="view"))
    catalog._register(d, "orders")
    monkeypatch.setattr(freshness, "connect", lambda: _Warehouse(dict(_INDEXED)))
    (notice,) = freshness.notices({"source": "orders"}, d)
    assert notice == {
        "source": "orders",
        "changed": None,
        "unknown": True,
        "why": prompts.notice("stale_no_marker"),
    }


# --- first in every answer ----------------------------------------------------------


def _call(name: str, args: dict) -> dict:
    tool = next(t for t in tools.ALL_TOOLS if t.name == name)
    return asyncio.run(tool.handler(args))


def _head(result: dict) -> dict:
    """The answer's JSON head: the whole of a JSON answer, the first line of a columnar one."""
    text = result["content"][0]["text"]
    return json.loads(text.split("\n", 1)[0])


def test_every_kind_of_answer_names_the_changed_table_first(shop):
    _touch("orders.csv", "4,9.0\n")
    query = {"question": "q", "sql": "SELECT count(*) AS n FROM orders", "inputs": ["orders"]}
    for name, args in [
        ("describe_source", {"source": "orders"}),
        ("profile_source", {"source": "orders"}),
        ("query_data", query),
        (
            "join_findings",
            {"left": "orders", "right": "customers", "left_on": ["ORDER_ID"], "right_on": ["id"]},
        ),
        ("read_spec", {"spec": "big_orders"}),
        ("set_interpretation", {"source": "orders", "note": "Amounts are in euros."}),
    ]:
        result = _call(name, {**args, "portia_dir": shop})
        assert "is_error" not in result, (name, result)
        head = _head(result)
        assert list(head)[0] == "stale", name
        assert [n["source"] for n in head["stale"]] == ["orders"], name


def test_an_answer_about_fresh_tables_carries_no_stale_key(shop):
    result = _call("describe_source", {"source": "customers", "portia_dir": shop})
    assert "stale" not in _head(result)


def test_a_coarsened_answer_still_starts_with_stale(shop, monkeypatch):
    """A card whose column list is over the budget is sent as its overview (2026-10-13)."""
    from portia.core import listing

    _touch("orders.csv", "4,9.0\n")
    rows = [[f"c{i:04d}_{'x' * 80}", "integer", "r" * 60, ["constant"]] for i in range(200)]
    wide = {
        "source": "orders",
        "path": "orders.csv",
        "summary": None,
        "n_rows": 3,
        "n_columns": 200,
        "columns": listing.listing(("name", "type", "role", "flags"), rows, n=200),
    }
    monkeypatch.setattr(tools.handlers, "describe_source", lambda *a, **k: wide)
    result = _call("describe_source", {"source": "orders", "portia_dir": shop})
    text = result["content"][0]["text"]
    assert "is_error" not in result
    assert text.startswith('{"stale":[{"source":"orders"')
    assert '"types"' in text and '"columns":{' not in text


def test_a_check_that_fails_says_so_rather_than_failing_the_answer(shop, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(freshness, "notices", broken)
    result = _call("describe_source", {"source": "orders", "portia_dir": shop})
    (notice,) = _head(result)["stale"]
    assert notice["unknown"] is True and "disk went away" in notice["why"]


# --- as a column holds it ------------------------------------------------------------


def test_statuses_ask_no_warehouse_and_a_model_follows_its_sources(shop):
    _touch("orders.csv", "4,9.0\n")
    cat = catalog.load_catalog(shop)
    models = catalog.load_models(shop)
    sources = {**cat["sources"], "remote": _remote()}
    out = freshness.statuses(shop, sources, models)
    assert out == {"orders": True, "customers": False, "remote": None, "big_orders": True}


def test_moment_reads_both_kinds_of_timestamp():
    from portia.core.present import moment

    assert moment("2026-10-13T09:12:44+02:00") == "2026-10-13 09:12"
    assert moment(None) == "—" and moment("whenever") == "whenever"
    assert len(moment(Path(".").stat().st_mtime)) == len("2026-10-13 09:12")


# --- a model built locally is as fresh as its spec (2026-10-13) ----------------------


def _edit_spec(old: str, new: str) -> None:
    path = Path("specs/big_orders.yaml")
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_a_recorded_model_is_fresh_against_its_own_spec(shop):
    entry = catalog.load_models(shop)["big_orders"]
    assert entry["built"][catalog.BUILT_FROM] and "table" not in entry
    assert catalog.freshness("big_orders", entry, portia_dir=shop) is None
    assert freshness.notices({"source": "big_orders"}, shop) == []


def test_a_spec_edited_by_hand_makes_the_model_stale_until_it_is_recorded_again(shop):
    _edit_spec("amount > 2", "amount > 3")
    fresh = catalog.freshness(
        "big_orders", catalog.load_models(shop)["big_orders"], portia_dir=shop
    )
    assert fresh is not None and fresh["changed"] == catalog.SPEC_CHANGED
    assert fresh["spec"] == "specs/big_orders.yaml"

    (notice,) = freshness.notices({"source": "big_orders"}, shop)
    assert notice["source"] == "big_orders" and "specs/big_orders.yaml" in notice["changed"]
    assert notice["do"] == prompts.notice("stale_do_rebuild")
    out = freshness.statuses(shop, catalog.load_catalog(shop)["sources"], catalog.load_models(shop))
    assert out["big_orders"] is True and out["orders"] is False

    handlers.record_step(
        "specs/big_orders.yaml",
        {"id": "big", "op": "sql", "inputs": ["orders"], "sql": "SELECT * FROM orders"},
        supersedes="big",
        portia_dir=shop,
    )
    assert freshness.notices({"source": "big_orders"}, shop) == []


def test_a_model_entry_from_before_the_fingerprint_follows_its_sources_only(shop):
    entry = catalog.load_models(shop)["big_orders"]
    entry["built"].pop(catalog.BUILT_FROM)
    _edit_spec("amount > 2", "amount > 3")
    assert catalog.freshness("big_orders", entry, portia_dir=shop) is None
