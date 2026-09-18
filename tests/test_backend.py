"""One active backend per process, set explicitly (`core/backend.py`)."""

from __future__ import annotations

import pandas as pd
import pytest

from portia import pipeline, spec
from portia.core import backend, dialect
from portia.core.io import connect, is_table_ref, source_query, source_table, table_query
from portia.core.table import Table


def test_the_default_is_duckdb_in_memory():
    active = backend.active()
    assert active is backend.LOCAL
    assert active.kind == "duckdb" and not active.remote and not active.agent_writes
    assert active.dialect is dialect.DUCKDB


def test_using_installs_for_a_block_and_restores():
    other = backend.Backend(kind="test", dialect=dialect.SNOWFLAKE, open=lambda: None, remote=True)
    with backend.using(other) as installed:
        assert installed is other and backend.active() is other
    assert backend.active() is backend.LOCAL


def test_connect_asks_the_active_backend():
    opened = []

    def open_one():
        opened.append(1)
        return backend.LOCAL.open()

    fake = backend.Backend(kind="duckdb", dialect=dialect.DUCKDB, open=open_one)
    with backend.using(fake):
        con = connect()
    assert opened == [1]
    con.close()


def test_remote_is_read_off_the_connection_not_the_process(con):
    class Remote:
        remote = True

    assert backend.is_remote(Remote()) and not backend.is_remote(con)


# --- a source that names a table ---------------------------------------------


def test_a_table_ref_is_a_dict_with_one_key():
    assert is_table_ref({"table": "DB.S.T"}) and not is_table_ref("data/orders.csv")


def test_table_query_quotes_every_part():
    assert table_query("SALES.PUBLIC.ORDERS") == 'SELECT * FROM "SALES"."PUBLIC"."ORDERS"'
    assert table_query("orders") == 'SELECT * FROM "orders"'


def test_source_query_dispatches_and_a_path_still_reads_its_file(tmp_path):
    csv = tmp_path / "orders.csv"
    csv.write_text("a\n1\n")
    assert source_query({"table": "x.y"}) == 'SELECT * FROM "x"."y"'
    assert "read_csv" in source_query("orders.csv", base=tmp_path)
    assert str(csv.resolve()) in source_query("orders.csv", base=tmp_path)
    assert "orders.csv'" in source_query("orders.csv", absolute=False)


def test_source_table_names_a_table_the_connection_can_see(con):
    Table.from_frame(pd.DataFrame({"id": [1, 2]}), "orders", con)
    t = source_table({"table": "orders"}, con, name="orders")
    assert t.count() == 2 and t.name == "orders"
    assert source_table({"table": "main.orders"}, con).name == "orders"


def test_a_spec_may_name_a_table_as_a_source(con):
    """`run_spec` reads a `{table: …}` source through the same dispatch."""
    Table.from_frame(pd.DataFrame({"id": [1, 2, 3], "k": ["a", "b", "b"]}), "orders", con)
    doc = {
        "version": 1,
        "sources": {"orders": {"table": "orders"}},
        "steps": [
            {
                "id": "keep",
                "op": "sql",
                "inputs": ["orders"],
                "sql": "SELECT * FROM orders WHERE k = 'b'",
            }
        ],
    }
    results = spec.run_spec(doc, con=con)
    assert results[-1].provenance["result_rows"] == 2


def test_the_sources_file_creates_a_view_over_a_named_table():
    sql = pipeline.compile_sources({"orders": {"table": "SALES.PUBLIC.ORDERS"}, "f": "data/f.csv"})
    assert 'CREATE OR REPLACE VIEW "orders" AS\nSELECT * FROM "SALES"."PUBLIC"."ORDERS";' in sql
    assert "read_csv('data/f.csv'" in sql


def test_a_spec_fingerprint_moves_when_a_source_becomes_a_table():
    as_file = {"sources": {"o": "o.csv"}, "steps": []}
    as_table = {"sources": {"o": {"table": "DB.S.O"}}, "steps": []}
    assert pipeline.fingerprint(as_file) != pipeline.fingerprint(as_table)


@pytest.mark.parametrize("ref", [{"table": "a.b"}, "a/b.csv"])
def test_source_table_and_source_query_agree(ref, con, tmp_path):
    if isinstance(ref, str):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b.csv").write_text("x\n1\n")
    t = source_table(ref, con, base=tmp_path)
    assert t.query == source_query(ref, base=tmp_path)


def test_a_table_reference_and_the_compiled_sources_are_quoted_in_the_dialect_they_run_on():
    """`CONNECTORS.md` §8: the deliverable runs where it was written for, and BigQuery
    reads a double-quoted token as a string literal."""
    assert table_query("my-proj.raw.orders", dialect=dialect.BIGQUERY) == (
        "SELECT * FROM `my-proj`.`raw`.`orders`"
    )
    sql = pipeline.compile_sources({"o": {"table": "my-proj.raw.orders"}}, dialect=dialect.BIGQUERY)
    assert "CREATE OR REPLACE VIEW `o` AS\nSELECT * FROM `my-proj`.`raw`.`orders`;" in sql
    remote = backend.Backend(
        kind="bigquery", dialect=dialect.BIGQUERY, open=lambda: None, remote=True
    )
    with backend.using(remote):
        assert pipeline.compile_sources({"o": {"table": "p.d.t"}}).count("`") == 8
    assert '"' in pipeline.compile_sources({"o": {"table": "p.d.t"}}), "local is DuckDB's"


def test_parallel_queries_is_read_off_the_connection_and_defaults_to_one(con):
    class Wide:
        parallel_queries = 8

    class Odd:
        parallel_queries = 0

    assert backend.parallel_queries(Wide()) == 8
    assert backend.parallel_queries(con) == 1
    assert backend.parallel_queries(Odd()) == 1
