"""portia's own records as tables: `portia_columns` and `portia_tables` through `query_data`.

Built from the catalog and handed to the SQL sandbox as one more declared input
(`agent/reserved.py`). The project here is tiny and local; the warehouse is a
connection that only says it is remote, which is all the refusal reads.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import pandas as pd
import pytest

from portia import catalog
from portia.agent import handlers, prompts, reserved, tools
from portia.ops import sql as sql_op


@pytest.fixture
def shop(tmp_path, monkeypatch) -> str:
    """Two files, one with an upper-case column, a group, and a model built from one."""
    monkeypatch.chdir(tmp_path)
    pd.DataFrame(
        {"ORDER_ID": [1, 2, 3, 4], "amount": [1.5, 2.5, 4.0, None], "shop": ["a", "a", "a", "a"]}
    ).to_csv("orders.csv", index=False)
    pd.DataFrame({"id": [1, 2], "name": ["x", "y"]}).to_csv("customers.csv", index=False)
    d = tmp_path / ".portia"
    catalog.init_project("a shop", portia_dir=d)
    catalog.index_source("orders.csv", portia_dir=d)
    catalog.index_source("customers.csv", portia_dir=d)
    catalog.set_group("sales", sources=["orders"], portia_dir=d)
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


def _ask(sql: str, inputs: list[str], d: str, **kwargs) -> dict:
    return handlers.query_data(sql, inputs, "what does the catalog hold?", portia_dir=d, **kwargs)


def _records(answer: dict) -> list[dict]:
    """An answer's rows as records: its ``fields`` once, each row a list (`core/listing.py`)."""
    return [dict(zip(answer["fields"], row, strict=True)) for row in answer["rows"]]


def test_portia_columns_is_one_row_per_column_with_its_facts(shop):
    answer = _ask(
        "SELECT column_name, type, flags, null_rate, n_distinct, min, max, top, top_freq "
        "FROM portia_columns WHERE table_name = 'orders' ORDER BY position",
        ["portia_columns"],
        shop,
    )
    rows = {row["column_name"]: row for row in _records(answer)}
    assert list(rows) == ["ORDER_ID", "amount", "shop"]
    assert rows["ORDER_ID"]["type"] == "integer" and rows["ORDER_ID"]["max"] == 4
    assert rows["amount"]["null_rate"] == 0.25 and rows["amount"]["min"] == 1.5
    assert rows["shop"]["top"] == "a" and rows["shop"]["top_freq"] == 4
    assert "constant" in rows["shop"]["flags"]


def test_a_flag_is_asked_with_list_contains_across_every_table(shop):
    answer = _ask(
        "SELECT table_name, kind, column_name FROM portia_columns "
        "WHERE list_contains(flags, 'constant') ORDER BY table_name, position",
        ["portia_columns"],
        shop,
    )
    assert [(r["table_name"], r["kind"], r["column_name"]) for r in _records(answer)] == [
        ("big_orders", "model", "shop"),
        ("orders", "source", "shop"),
    ]


def test_portia_tables_is_one_row_per_source_and_model(shop):
    catalog.set_interpretation("customers", summary="One row per customer.", portia_dir=shop)
    answer = _ask(
        "SELECT * FROM portia_tables ORDER BY kind DESC, table_name", ["portia_tables"], shop
    )
    rows = {row["table_name"]: row for row in _records(answer)}
    assert answer["fields"] == [name for name, _ in reserved.TABLE_FIELDS]
    assert rows["orders"]["kind"] == "source" and rows["orders"]["path"] == "orders.csv"
    assert rows["orders"]["n_rows"] == 4 and rows["orders"]["n_columns"] == 3
    assert rows["orders"]["groups"] == ["sales"] and rows["customers"]["groups"] == []
    assert rows["orders"]["read"] is False and rows["orders"]["summary"] is None, "drafted only"
    assert rows["customers"]["read"] is True
    assert rows["customers"]["summary"] == "One row per customer."
    assert rows["big_orders"]["kind"] == "model"
    assert rows["big_orders"]["path"] == "specs/big_orders.yaml"
    assert rows["big_orders"]["n_rows"] == 2
    assert all(row["stale"] is False for row in rows.values())
    assert rows["orders"]["indexed_at"]


def test_they_name_things_as_information_schema_does(shop):
    """`table_name` and `column_name`, which a model already knows (2026-10-13).

    With `name` in both, two Haiku drives guessed `source` and then
    `column_name`, a failed query each time.
    """
    answer = _ask(
        "SELECT t.kind, count(c.column_name) AS n FROM portia_tables t "
        "JOIN portia_columns c USING (table_name) GROUP BY ALL ORDER BY 1",
        ["portia_tables", "portia_columns"],
        shop,
    )
    assert _records(answer) == [{"kind": "model", "n": 3}, {"kind": "source", "n": 3 + 2}]


def test_a_changed_file_is_stale_and_so_is_what_is_built_from_it(shop):
    with open("orders.csv", "a", encoding="utf-8") as f:
        f.write("5,9.0,a\n")
    later = os.stat("orders.csv").st_mtime + 5
    os.utime("orders.csv", (later, later))
    answer = _ask("SELECT table_name, stale FROM portia_tables ORDER BY 1", ["portia_tables"], shop)
    assert {r["table_name"]: r["stale"] for r in _records(answer)} == {
        "big_orders": True,
        "customers": False,
        "orders": True,
    }


def test_the_catalog_and_the_data_meet_in_one_query_locally(shop):
    answer = _ask(
        "SELECT t.n_rows AS recorded, (SELECT count(*) FROM orders) AS counted "
        "FROM portia_tables t WHERE t.table_name = 'orders'",
        ["portia_tables", "orders"],
        shop,
    )
    assert _records(answer) == [{"recorded": 4, "counted": 4}]


def test_a_reserved_name_is_matched_the_way_the_engine_folds_it(shop):
    answer = _ask("SELECT count(*) AS n FROM PORTIA_COLUMNS", ["PORTIA_COLUMNS"], shop)
    assert _records(answer) == [{"n": 3 + 2 + 3}]


def test_a_chart_reads_them_too(shop):
    chart = handlers.plot_data(
        "SELECT table_name, n_columns FROM portia_tables",
        ["portia_tables"],
        "how wide is each table?",
        "widths",
        {"mark": "bar", "encoding": {"x": {"field": "table_name"}, "y": {"field": "n_columns"}}},
        portia_dir=shop,
    )
    assert chart["n_rows"] == 3


def test_data_type_finds_the_all_null_columns_the_engine_stores_as_text(shop):
    """The real case (2026-10-14): an UNPIVOT refused DOUBLE beside VARCHAR, and the
    VARCHAR columns were the empty meters. `type` says `empty`; the engine's type is
    what the copilot asked for, by `information_schema`'s name for it."""
    pd.DataFrame(
        {"reading_at": ["2024-01-01 00:00", "2024-01-01 01:00"], "m1": [1.5, 2.5], "m2": [None] * 2}
    ).to_csv("meters.csv", index=False)
    catalog.index_source("meters.csv", portia_dir=shop)
    answer = _ask(
        "SELECT table_name, column_name FROM portia_columns WHERE data_type = 'VARCHAR'",
        ["portia_columns"],
        shop,
    )
    assert {"table_name": "meters", "column_name": "m2"} in _records(answer)
    typed = _ask(
        "SELECT column_name, type, data_type FROM portia_columns "
        "WHERE table_name = 'meters' ORDER BY position",
        ["portia_columns"],
        shop,
    )
    assert [(r["type"], r["data_type"]) for r in _records(typed)] == [
        ("datetime", "TIMESTAMP"),
        ("float", "DOUBLE"),
        ("empty", "VARCHAR"),
    ]


def test_a_models_columns_carry_their_storage_type_too(shop):
    answer = _ask(
        "SELECT column_name, data_type FROM portia_columns "
        "WHERE table_name = 'big_orders' ORDER BY position",
        ["portia_columns"],
        shop,
    )
    assert [(r["column_name"], r["data_type"]) for r in _records(answer)] == [
        ("ORDER_ID", "BIGINT"),
        ("amount", "DOUBLE"),
        ("shop", "VARCHAR"),
    ]


def test_a_table_nobody_profiled_has_its_type_and_no_flags(shop):
    entry = catalog.load_source("customers", portia_dir=shop)
    entry["columns"] = [
        {"name": c["name"], "role": None, "dtype": "VARCHAR"} for c in entry["columns"]
    ]
    catalog._write(catalog.Path(shop) / "sources" / "customers.yaml", entry)
    answer = _ask(
        "SELECT type, flags, null_rate FROM portia_columns WHERE table_name = 'customers'",
        ["portia_columns"],
        shop,
    )
    assert _records(answer) == [{"type": "VARCHAR", "flags": None, "null_rate": None}] * 2


# --- named where a source belongs, or read and not declared -------------------------


@pytest.mark.parametrize("name", ["portia_columns", "PORTIA_TABLES", "portia_result_bcdf"])
def test_one_of_portias_own_tables_is_not_a_source_and_says_where_it_is_read(shop, name):
    """A Haiku drive described `portia_columns` and was told every source's name (2026-10-13)."""
    with pytest.raises(ValueError) as described:
        handlers.describe_source(name, shop)
    with pytest.raises(ValueError) as read:
        handlers._table(name, shop)
    for refused in (described, read):
        assert str(refused.value) == prompts.error("reserved_is_not_a_source", table=name)


@pytest.mark.parametrize("name", ["Portia_Columns", "portia_result_bcdf"])
def test_one_of_portias_tables_read_and_not_declared_is_told_to_declare_it(shop, name):
    """The same Haiku drive failed twice on the sandbox's generic words.

    Never added to 'inputs' for it: a query reads exactly what it declares.
    """
    with pytest.raises(ValueError) as refused:
        _ask(f"SELECT count(*) AS n FROM orders, {name}", ["orders"], shop)
    assert str(refused.value) == prompts.error("reserved_not_declared", table=name)


def test_any_other_undeclared_table_keeps_the_sandboxs_own_words(shop):
    with pytest.raises(ValueError, match="reads only what it declares in 'inputs'"):
        _ask("SELECT count(*) AS n FROM orders, customers", ["orders"], shop)


# --- a warehouse project --------------------------------------------------------------


class _Remote:
    """A connection that says it is a warehouse; asking it anything fails the test."""

    remote = True

    def execute(self, *a, **k):
        raise AssertionError("the warehouse was asked")


def test_on_a_warehouse_portias_records_and_the_warehouse_are_never_one_query(shop, monkeypatch):
    monkeypatch.setattr(handlers, "connect", lambda: _Remote())
    with pytest.raises(ValueError) as refused:
        _ask("SELECT * FROM portia_tables, orders", ["portia_tables", "orders"], shop)
    assert str(refused.value) == prompts.error(
        "reserved_with_warehouse", reserved="portia_tables", tables="orders"
    )


def test_on_a_warehouse_a_record_read_and_not_declared_is_told_to_ask_alone(shop, monkeypatch):
    """Adding it to 'inputs' would only meet the refusal above, so this says what that says."""
    monkeypatch.setattr(handlers, "connect", lambda: _Remote())
    monkeypatch.setattr(handlers, "_table", lambda name, portia_dir, con: object())

    def undeclared(tables, sql, *, name):
        raise sql_op.SqlTableUnknown("the sandbox's own words", ["portia_tables"])

    monkeypatch.setattr(sql_op, "apply_sql", undeclared)
    with pytest.raises(ValueError) as refused:
        _ask("SELECT * FROM portia_tables, orders", ["orders"], shop)
    assert str(refused.value) == prompts.error(
        "reserved_with_warehouse", reserved="portia_tables", tables="orders"
    )


def test_on_a_warehouse_a_query_of_portias_records_alone_runs_here(shop, monkeypatch):
    def no_warehouse():
        raise AssertionError("a catalog-only query opened the warehouse")

    monkeypatch.setattr(handlers, "connect", no_warehouse)
    answer = _ask("SELECT count(*) AS n FROM portia_tables", ["portia_tables"], shop)
    assert _records(answer) == [{"n": 3}]


# --- the registry ------------------------------------------------------------------


def test_a_name_registered_later_is_an_input_the_same_way(shop, monkeypatch):
    """The mechanism a chat's kept results use (`portia/results.py`)."""
    monkeypatch.setattr(reserved, "_registry", list(reserved._registry))
    seen = []

    def stored(name: str, request: reserved.Request):
        seen.append((name, request.portia_dir))
        rows = [{"n": int(name[1:]), "label": "kept"}]
        return reserved.create(request.con, name, [("n", "INTEGER"), ("label", "VARCHAR")], rows)

    reserved.register(re.compile(r"q\d+"), stored)
    answer = _ask("SELECT n, label FROM q7", ["q7"], shop)
    assert _records(answer) == [{"n": 7, "label": "kept"}]
    assert seen == [("q7", shop)]
    assert reserved.find("q") is None and reserved.find("orders") is None


def test_registering_a_name_again_replaces_it(monkeypatch):
    monkeypatch.setattr(reserved, "_registry", list(reserved._registry))
    before = len(reserved._registry)
    reserved.register("scratch", lambda name, request: None)  # type: ignore[arg-type,return-value]
    reserved.register("scratch", lambda name, request: None)  # type: ignore[arg-type,return-value]
    assert len(reserved._registry) == before + 1


def test_the_columns_the_description_lists_are_the_tables_columns(shop):
    description = next(t for t in tools.ALL_TOOLS if t.name == "query_data").description
    for table, fields in (
        (reserved.COLUMNS, reserved.COLUMN_FIELDS),
        (reserved.TABLES, reserved.TABLE_FIELDS),
    ):
        listed = reserved.vocabulary()[table]
        assert listed in description
        answer = _ask(f"SELECT * FROM {table} LIMIT 0", [table], shop)
        assert answer["fields"] == [name for name, _ in fields] == listed.split(", ")


def test_an_empty_project_has_empty_tables_with_their_columns(tmp_path):
    d = tmp_path / ".portia"
    catalog.init_project("nothing yet", portia_dir=d)
    answer = _ask("SELECT * FROM portia_columns", ["portia_columns"], str(d))
    assert answer["n_rows"] == 0
    assert answer["fields"] == [name for name, _ in reserved.COLUMN_FIELDS]


def test_through_the_tool_a_reserved_input_carries_no_stale_notice(shop):
    tool = next(t for t in tools.ALL_TOOLS if t.name == "query_data")
    args = {
        "question": "q",
        "sql": "SELECT count(*) AS n FROM portia_columns",
        "inputs": ["portia_columns"],
        "portia_dir": shop,
    }
    result = asyncio.run(tool.handler(args))
    assert "is_error" not in result
    payload = json.loads(result["content"][0]["text"])
    assert "stale" not in payload and _records(payload) == [{"n": 8}]


def test_an_interrupted_query_counts_a_reserved_input_too(shop):
    facts = handlers.interrupted_facts(
        "query_data", {"sql": "SELECT * FROM portia_tables", "inputs": ["portia_tables"]}, shop
    )
    assert facts["inputs"] == {"portia_tables": {"rows": 3, "from": handlers.COUNTED}}


def test_portia_tables_holds_every_note_a_card_leaves_out(shop):
    """A card carries the latest notes (`handlers.CARD_NOTES`); the rest are here (2026-10-13)."""
    for i in range(handlers.CARD_NOTES + 2):
        catalog.set_interpretation("orders", note=f"note {i}", portia_dir=shop)
    con = reserved.connection()
    (table,) = reserved.tables(["portia_tables"], shop, con).values()
    ((texts,),) = table.sql(
        f"SELECT list_transform(notes, n -> n.text) FROM {table.ref} WHERE table_name = 'orders'"
    ).rows(1)
    assert texts == [f"note {i}" for i in range(handlers.CARD_NOTES + 2)], "oldest first"
    card = handlers.describe_source("orders", shop)
    assert card["notes"]["n"] == handlers.CARD_NOTES + 2
    assert card["notes"]["shown"] == f"3-{handlers.CARD_NOTES + 2}" and card["notes"]["more"]


def test_portia_columns_holds_the_mean_and_the_quartiles(shop):
    """Stored at indexing since 2026-10-13 (`catalog.LATER_FACTS`), and a column here."""
    con = reserved.connection()
    (table,) = reserved.tables(["portia_columns"], shop, con).values()
    ((q25, median, q75, mean),) = table.sql(
        f"SELECT q25, median, q75, mean FROM {table.ref} "
        "WHERE table_name = 'orders' AND column_name = 'amount'"
    ).rows(1)
    assert (q25, median, q75, mean) == (2.0, 2.5, 3.25, 2.6667), "rounded as the profile rounds"
