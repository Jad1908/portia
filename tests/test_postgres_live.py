"""The PostgreSQL adapter against a real server — or a skip (`docs/CONNECTORS.md` §4, §9).

Everything the connector *decides* is in `test_postgres.py` and runs without a
driver. What is here is the seam between the adapter and a server, and the
three things only a server found on the first drive (§9.3). Opt-in:
``PORTIA_TEST_POSTGRES=<name>`` names a connection in ``connections.yaml``, and
a password comes from ``PGPASSWORD``. A throwaway server is one line::

    docker run -d -e POSTGRES_PASSWORD=portia -e POSTGRES_DB=shop -p 54329:5432 postgres:16

The tests create and drop one schema, ``portia_test``, and touch nothing else.
"""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from portia.checks import join, profiling
from portia.core import cancel
from portia.core.table import Table
from portia.ops.normalize import apply_normalize

SCHEMA = "portia_test"


@pytest.fixture
def orders(postgres_session):
    con = postgres_session
    con.execute(
        f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; "
        f'CREATE TABLE {SCHEMA}.orders ("ORDER_ID" integer, customer uuid, country varchar(20), '
        "amount text, legacy json); "
        f"INSERT INTO {SCHEMA}.orders SELECT i, gen_random_uuid(), "
        "(ARRAY['France', 'FRA ', 'Spain'])[1 + i % 3], "
        "CASE WHEN i % 5 = 0 THEN 'n/a' ELSE (i * 1.5)::text END, '{\"a\": 1}'::json "
        "FROM generate_series(1, 100) i; "
        f"CREATE TABLE {SCHEMA}.customers (id integer, name text); "
        f"INSERT INTO {SCHEMA}.customers SELECT i, 'c' || i FROM generate_series(1, 60) i; "
        f"ANALYZE {SCHEMA}.orders"
    )
    yield Table("orders", f'SELECT * FROM "{con.database}"."{SCHEMA}"."orders"', con)
    con.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def test_a_session_answers_the_six_things_table_asks(postgres_session):
    con = postgres_session
    assert con.execute("SELECT 1 AS n, 'a' AS s").fetchall() == [(1, "a")]
    rel = con.sql("SELECT 1 AS n, 'a'::varchar(3) AS s, 1.5::numeric(10,2) AS d")
    assert rel.columns == ["n", "s", "d"]
    assert rel.types == ["integer", "character varying(3)", "numeric(10,2)"]
    assert con.cursor().execute("SELECT 2").fetchone() == (2,)
    frame = con.execute("SELECT 1 AS n UNION ALL SELECT 2 ORDER BY 1").fetch_df()
    assert isinstance(frame, pd.DataFrame) and list(frame["n"]) == [1, 2]
    assert "connected as" in con.whoami()


def test_a_failed_statement_leaves_the_shared_connection_usable(postgres_session):
    with pytest.raises(Exception, match="no_such_table"):
        postgres_session.execute("SELECT * FROM no_such_table")
    assert postgres_session.cursor().execute("SELECT 1").fetchone() == (1,)


def test_browsing_and_the_free_facts(postgres_session, orders):
    con = postgres_session
    assert con.databases() == [con.database]
    assert SCHEMA in con.schemas(con.database)
    assert ("orders", "table") in con.tables(con.database, SCHEMA)
    qualified = f"{con.database}.{SCHEMA}.orders"
    assert con.columns(qualified)[:2] == [("ORDER_ID", "integer"), ("customer", "uuid")]
    facts = con.table_facts(qualified)
    assert facts["rows"] == 100 and facts["approximate"] == ["rows"]
    assert facts["size"] > 0 and str(facts["mtime"]).startswith("writes:")
    with pytest.raises(ValueError, match="cannot"):
        con.schemas("some_other_database")


def test_a_profile_runs_on_a_uuid_a_bounded_string_and_a_json_column(orders):
    columns = {c["name"]: c for c in profiling.profile(orders)["columns"]}
    assert columns["ORDER_ID"]["n_distinct"] == 100 and columns["ORDER_ID"]["median"] == 50.5
    assert "approximate" not in columns["ORDER_ID"], "percentile_cont is exact here"
    assert "leading_trailing_whitespace" in columns["country"]["flags"]
    assert "mixed_types" in columns["amount"]["flags"]
    assert columns["customer"]["n_distinct"] == 100
    assert columns["legacy"]["n_distinct"] == 1, "json has no equality; it is compared as text"


def test_join_findings_resolves_a_key_typed_in_another_case_and_skips_what_cannot_sort(
    postgres_session, orders
):
    customers = Table(
        "customers",
        f'SELECT * FROM "{postgres_session.database}"."{SCHEMA}"."customers"',
        postgres_session,
    )
    found = join.join_findings(
        orders, customers, left_on=["order_id"], right_on=["ID"], left_columns=["legacy"]
    )
    assert found["report"]["keys"] == {"left": ["ORDER_ID"], "right": ["id"]}
    rows = found["evidence"]["unmatched_left_rows"]
    assert [r["ORDER_ID"] for r in rows] == [61, 62, 63], "ordered by the key, json left out"
    assert set(rows[0]) == {"ORDER_ID", "legacy"}


def test_two_transforms_run_and_their_compiled_form_runs_too(postgres_session, orders):
    done = apply_normalize(
        orders, [{"column": "COUNTRY", "op": "strip"}, {"column": "amount", "op": "to_numeric"}]
    )
    assert done.provenance["transforms"][1]["n_failed"] == 20
    postgres_session.execute(
        f"SET search_path = {SCHEMA}; CREATE TEMP TABLE compiled AS {done.compiled}"
    )
    assert postgres_session.execute("SELECT count(amount) FROM compiled").fetchone() == (80,)
    postgres_session.execute("DROP TABLE compiled; RESET search_path")


def test_a_build_replaces_the_table_it_wrote(postgres_session, orders):
    con, d = postgres_session, postgres_session.dialect
    for query in ("SELECT 1 AS n", "SELECT 1 AS n UNION ALL SELECT 2"):
        for statement in d.write_table([con.database, SCHEMA], d.fold("Built"), query):
            con.execute(statement)
    assert con.execute(f"SELECT count(*) FROM {SCHEMA}.built").fetchone() == (2,)


def test_stop_cancels_the_running_statement_and_the_session_survives(postgres_session):
    scope = cancel.Scope()
    threading.Timer(0.3, scope.cancel).start()
    began = time.monotonic()
    try:
        with pytest.raises(cancel.Cancelled), cancel.scope(scope):
            cancel.watch(postgres_session)
            postgres_session.execute("SELECT pg_sleep(30)")
    finally:
        scope.close()
    assert time.monotonic() - began < 5
    assert postgres_session.execute("SELECT 1").fetchone() == (1,)
