"""The few expressions the checks spell per engine (`core/dialect.py`).

DuckDB's spellings are **executed**, because that is the engine the tests have;
Snowflake's are checked as text, because a stub that answered them would be a
second, wrong Snowflake (`docs/CONNECTOR.md` §6). What matters is that both
dialects answer the same five questions and that a connection which says
nothing is DuckDB.
"""

from __future__ import annotations

import pandas as pd

from portia.checks import profiling
from portia.core import dialect
from portia.core.table import Table


def test_a_connection_that_says_nothing_is_duckdb(con):
    assert dialect.of(con) is dialect.DUCKDB


def test_a_connection_can_carry_its_dialect():
    class Quacks:
        dialect = dialect.SNOWFLAKE

    assert dialect.of(Quacks()) is dialect.SNOWFLAKE


def test_duckdb_filters_execute_as_filter_semantics(table):
    t = table(pd.DataFrame({"n": [1, 2, 3, 4]}))
    d = dialect.DUCKDB
    row = t.row(
        {
            "c": d.count_where("n > 2"),
            "s": d.sum_where("n", "n > 2"),
            "m": d.max_where("n", "n < 3"),
        }
    )
    assert (row["c"], row["s"], row["m"]) == (2, 7, 2)


def test_duckdb_quartiles_are_one_list_valued_aggregate(table):
    """§13 of the migration doc: three quartiles buffer the column once."""
    t = table(pd.DataFrame({"n": [1.0, 2.0, 3.0, 4.0, 5.0]}))
    exprs = dialect.DUCKDB.quartile_exprs('"n"')
    assert list(exprs) == ["quartiles"]
    stats = t.row({f"c0_{k}": v for k, v in exprs.items()})
    assert dialect.DUCKDB.read_quartiles(stats, "c0") == (2.0, 3.0, 4.0)


def test_snowflake_spells_the_same_five_things_without_filter():
    d = dialect.SNOWFLAKE
    assert d.count_where("x > 1") == "count_if(x > 1)"
    assert d.sum_where("v", "x > 1") == "sum(CASE WHEN x > 1 THEN v END)"
    assert d.max_where("v", "x > 1") == "max(CASE WHEN x > 1 THEN v END)"
    assert "FILTER" not in " ".join(d.quartile_exprs('"n"').values())
    assert d.order_by_all(3) == "ORDER BY 1, 2, 3"
    assert d.order_by_all(0) == ""


def test_snowflake_quartiles_are_three_aliases_read_back_in_order():
    d = dialect.SNOWFLAKE
    exprs = d.quartile_exprs('"n"')
    assert list(exprs) == ["q25", "median", "q75"]
    assert all("percentile_cont" in e and "WITHIN GROUP" in e for e in exprs.values())
    stats = {"c4_q25": 1, "c4_median": 2, "c4_q75": 3}
    assert d.read_quartiles(stats, "c4") == (1, 2, 3)


def test_every_dialect_is_reachable_by_name():
    assert set(dialect.BY_NAME) == {"duckdb", "snowflake", "bigquery", "postgres"}


def test_a_profile_under_snowflakes_dialect_writes_no_duckdb_only_sql():
    """The stat query, as text: nothing in it is DuckDB's alone."""
    exprs = profiling._stat_exprs({"n": profiling.FLOAT, "s": profiling.STRING}, dialect.SNOWFLAKE)
    text = " ".join(exprs.values())
    assert "FILTER" not in text and "quantile_cont" not in text
    assert "count_if" in text and "percentile_cont" in text


def test_snowflake_takes_a_numbers_moments_in_floating_point():
    """Snowflake sums a ``NUMBER``'s squares as a 38-digit integer, so
    ``stddev_samp`` over a wide revenue column overflowed and took the whole
    table's profile with it (2026-09-21, a real account). The two sums are cast;
    what picks a value rather than summing stays on the column itself."""
    exprs = profiling._stat_exprs({"REVENUE": profiling.FLOAT}, dialect.SNOWFLAKE)
    assert exprs["c0_mean"] == 'avg(CAST("REVENUE" AS DOUBLE))'
    assert exprs["c0_std"] == 'stddev_samp(CAST("REVENUE" AS DOUBLE))'
    assert exprs["c0_min"] == 'min("REVENUE")' and exprs["c0_max"] == 'max("REVENUE")'
    assert exprs["c0_median"] == 'percentile_cont(0.5) WITHIN GROUP (ORDER BY "REVENUE")'
    # Locally the statement is the one every golden profile was measured with.
    assert profiling._stat_exprs({"REVENUE": profiling.FLOAT})["c0_std"] == 'stddev_samp("REVENUE")'


def test_kind_of_reads_snowflakes_number_by_its_scale():
    assert profiling.kind_of("NUMBER(38,0)") == profiling.INTEGER
    assert profiling.kind_of("NUMBER(10,2)") == profiling.FLOAT
    assert profiling.kind_of("TEXT") == profiling.STRING
    assert profiling.kind_of("TIMESTAMP_NTZ") == profiling.DATETIME
    assert profiling.kind_of("VARIANT") == profiling.OTHER
    # The DuckDB names still map as they did.
    assert profiling.kind_of("BIGINT") == profiling.INTEGER
    assert profiling.kind_of("DECIMAL(18,3)") == profiling.FLOAT
    assert profiling.duckdb_kind is profiling.kind_of


def test_a_table_on_a_dialect_carrying_connection_is_profiled_in_that_dialect(con):
    """The dialect follows the connection, not a global (`dialect.of`)."""
    seen: list[str] = []

    class Recording:
        """A DuckDB connection that says it is Snowflake, and records what it is asked."""

        dialect = dialect.SNOWFLAKE

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql):
            seen.append(sql)
            return self._inner.execute(sql)

        def sql(self, query):
            return self._inner.sql(query)

    con.execute("CREATE TABLE t AS SELECT 1 AS n")
    t = Table.from_name("t", Recording(con))
    try:
        profiling.profile(t)
    except Exception:  # noqa: BLE001 — DuckDB does not know count_if; the text is the point
        pass
    assert any("count_if" in s or "percentile_cont" in s for s in seen)


# --- names typed by the agent against columns named by the engine ---------------


def test_a_name_differing_only_by_case_resolves_to_the_column():
    """`grain: [establishment_id]` against a warehouse that returned ESTABLISHMENT_ID."""
    assert dialect.resolve_columns(["establishment_id", "city"], ["ESTABLISHMENT_ID", "CITY"]) == [
        "ESTABLISHMENT_ID",
        "CITY",
    ]


def test_an_exact_match_wins_and_an_absent_name_is_returned_as_it_came():
    assert dialect.resolve_columns(["Id", "nope"], ["Id", "ID"]) == ["Id", "nope"]


def test_a_name_that_could_mean_two_columns_is_not_resolved():
    """Two columns differing only by case are both quoted; the exact spelling is the only answer."""
    assert dialect.resolve_columns(["id"], ["Id", "ID"]) == ["id"]


def test_folding_is_the_engine_s_own_and_only_snowflake_upper_cases():
    """What an unquoted identifier becomes: DuckDB keeps it, Snowflake upper-cases it,
    BigQuery never folds a dataset or table name."""
    assert dialect.DUCKDB.fold("stg_chicago") == "stg_chicago"
    assert dialect.SNOWFLAKE.fold("stg_chicago") == "STG_CHICAGO"
    assert dialect.BIGQUERY.fold("stg_chicago") == "stg_chicago"
    assert dialect.POSTGRES.fold("STG_Chicago") == "stg_chicago", "PostgreSQL folds the other way"


# --- quoting and casting are the dialect's (`CONNECTORS.md` §3) --------------------


def test_quoting_is_double_quotes_everywhere_but_bigquery_which_reads_them_as_a_string():
    assert dialect.DUCKDB.quote("order") == '"order"' == dialect.SNOWFLAKE.quote("order")
    assert dialect.DUCKDB.quote('a"b') == '"a""b"'
    assert dialect.BIGQUERY.quote("order") == "`order`"
    assert dialect.BIGQUERY.quote("my-proj") == "`my-proj`"
    assert dialect.BIGQUERY.quote("a`b") == "`a\\`b`"
    from portia.core.table import quote_ident

    assert quote_ident("x") == dialect.DUCKDB.quote("x"), "the local spelling is DuckDB's"


def test_a_table_quotes_in_its_connections_dialect(con):
    class Quacks:
        dialect = dialect.BIGQUERY

    t = Table(name="t", query="SELECT 1", con=Quacks())
    assert t.dialect is dialect.BIGQUERY and t.ref == "(SELECT 1) AS `t`"
    assert Table(name="t", query="SELECT 1", con=con).ref == '(SELECT 1) AS "t"'


def test_the_casts_spell_their_types_per_engine():
    assert dialect.DUCKDB.try_cast("x", dialect.DUCKDB.double) == "try_cast(x AS DOUBLE)"
    assert dialect.SNOWFLAKE.text == "VARCHAR"
    assert dialect.BIGQUERY.try_cast("x", dialect.BIGQUERY.double) == "SAFE_CAST(x AS FLOAT64)"
    assert dialect.BIGQUERY.text == "STRING"


def test_bigquery_spells_the_same_things_in_googlesql_and_says_its_quartiles_are_a_sketch():
    d = dialect.BIGQUERY
    assert d.count_where("x > 1") == "COUNTIF(x > 1)"
    assert d.sum_where("v", "x > 1") == "sum(CASE WHEN x > 1 THEN v END)"
    assert d.order_by_all(2) == "ORDER BY 1, 2"
    exprs = d.quartile_exprs("`n`")
    assert list(exprs) == ["quartiles"] and "APPROX_QUANTILES(`n`, 1000)" in exprs["quartiles"]
    assert d.read_quartiles({"c0_quartiles": list(range(1001))}, "c0") == (250, 500, 750)
    assert d.read_quartiles({"c0_quartiles": None}, "c0") == (None, None, None)
    assert d.approximate_quartiles and not dialect.DUCKDB.approximate_quartiles


def test_a_profile_under_bigquerys_dialect_writes_no_other_engines_sql_and_flags_the_sketch():
    exprs = profiling._stat_exprs({"n": profiling.FLOAT, "s": profiling.STRING}, dialect.BIGQUERY)
    text = " ".join(exprs.values())
    assert "FILTER" not in text and "count_if" not in text and "try_cast" not in text
    assert "COUNTIF" in text and "SAFE_CAST" in text and "APPROX_QUANTILES" in text
    assert '"' not in text, "a double-quoted token is a string literal on BigQuery"
    stats = {
        "n_rows": 3,
        "c0_non_null": 3,
        "c0_distinct": 3,
        "c0_min": 1,
        "c0_max": 3,
        "c0_mean": 2.0,
        "c0_std": 1.0,
        "c0_quartiles": list(range(1001)),
    }

    class Silent:
        """Answers the sample query with nothing, and asserts it was quoted GoogleSQL's way."""

        dialect = dialect.BIGQUERY

        def execute(self, sql):
            assert "`n`" in sql and '"n"' not in sql

            class Empty:
                def fetchall(self):
                    return []

            return Empty()

    extras = profiling._column_extras(
        Table(name="t", query="SELECT 1", con=Silent()), "n", profiling.FLOAT, 3, 0
    )
    facts = profiling._table_column(
        "n", 0, profiling.FLOAT, "FLOAT64", stats, 3, extras, dialect.BIGQUERY
    )
    assert facts["approximate"] == ["q25", "median", "q75"] and facts["median"] == 500


def test_kind_of_reads_googlesqls_names():
    assert profiling.kind_of("INT64") == profiling.INTEGER
    assert profiling.kind_of("FLOAT64") == profiling.FLOAT
    assert profiling.kind_of("NUMERIC") == profiling.FLOAT
    assert profiling.kind_of("BIGNUMERIC") == profiling.FLOAT
    assert profiling.kind_of("BOOL") == profiling.BOOLEAN
    assert profiling.kind_of("DATETIME") == profiling.DATETIME
    assert profiling.kind_of("STRING") == profiling.STRING
    assert profiling.kind_of("ARRAY<INT64>") == profiling.OTHER
    assert profiling.kind_of("STRUCT") == profiling.OTHER


# --- PostgreSQL (`CONNECTORS.md` §9) ------------------------------------------------


def test_postgres_keeps_filter_and_exact_quartiles_and_spells_the_rest_its_own_way():
    d = dialect.POSTGRES
    assert d.name == "postgres" and not d.approximate_quartiles
    assert d.quote('a"b') == '"a""b"'
    assert d.count_where("x > 1") == "count(*) FILTER (WHERE x > 1)"
    assert (d.text, d.double) == ("text", "double precision")
    assert d.as_text('"id"') == 'CAST("id" AS text)', "nothing casts to text by itself there"
    assert dialect.DUCKDB.as_text('"id"') == '"id"', "and everywhere else it is left alone"
    (expr,) = d.quartile_exprs('"n"').values()
    assert expr == 'percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY "n")'
    assert d.read_quartiles({"c0_quartiles": [1.0, 2.0, 3.0]}, "c0") == (1.0, 2.0, 3.0)
    assert d.read_quartiles({"c0_quartiles": None}, "c0") == (None, None, None)


def test_the_guarded_cast_is_the_servers_own_parser_from_16_and_a_narrow_pattern_before():
    exact = dialect.POSTGRES.try_cast('"x"', "double precision")
    assert "pg_input_is_valid(CAST(\"x\" AS text), 'double precision')" in exact
    older = dialect.POSTGRES_BEFORE_16.try_cast('"x"', "double precision")
    assert "pg_input_is_valid" not in older and " ~ '" in older
    assert "\\" not in older, "no backslash, so standard_conforming_strings cannot change it"
    assert dialect.POSTGRES_BEFORE_16.name == "postgres", "one dialect to sqlglot and the prompt"
    import pytest

    with pytest.raises(ValueError, match="before PostgreSQL 16"):
        dialect.POSTGRES_BEFORE_16.try_cast('"x"', "date")


def test_the_pattern_admits_only_what_the_cast_cannot_refuse():
    import re

    pattern = re.compile(dialect._PLAIN_NUMBER.replace("[[:space:]]", r"\s"))
    for good in ("1", "-1.5", " 12.50 ", ".5", "1e9", "+3.2E-4"):
        assert pattern.match(good), good
    for bad in ("", "n/a", "1,5", "NaN", "1e999", "0x10", "1.2.3", "9" * 201):
        assert not pattern.match(bad), bad


def test_writing_a_table_is_the_dialects_because_postgres_has_no_create_or_replace():
    home, query = ["shop", "staging"], "SELECT 1 AS n"
    assert dialect.DUCKDB.write_table(home, "t", query) == [
        'CREATE SCHEMA IF NOT EXISTS "shop"."staging"',
        'CREATE OR REPLACE TABLE "shop"."staging"."t" AS SELECT 1 AS n',
    ]
    schema, swap = dialect.POSTGRES.write_table(home, "t", query)
    assert schema == 'CREATE SCHEMA IF NOT EXISTS "staging"', "a session reaches one database"
    assert swap.startswith('DROP TABLE IF EXISTS "shop"."staging"."t"; CREATE TABLE ')
    assert "CASCADE" not in swap, "a view somebody built on it must make the drop fail"


def test_an_ordering_can_leave_out_what_the_engine_cannot_sort():
    assert dialect.DUCKDB.orders_every_type and not dialect.POSTGRES.orders_every_type
    assert dialect.DUCKDB.order_by_all(3, skip=[1]) == "ORDER BY ALL"
    assert dialect.POSTGRES.order_by_all(3, skip=[1]) == "ORDER BY 1, 3"
    assert dialect.POSTGRES.order_by_all(1, skip=[0]) == ""
    assert dialect.SNOWFLAKE.order_by_all(2) == "ORDER BY 1, 2"


def test_kind_of_reads_postgres_names_as_psql_shows_them():
    kinds = {
        "integer": profiling.INTEGER,
        "bigint": profiling.INTEGER,
        "numeric(10,2)": profiling.FLOAT,
        "double precision": profiling.FLOAT,
        "character varying(20)": profiling.STRING,
        "character(3)": profiling.STRING,
        "text": profiling.STRING,
        "uuid": profiling.STRING,
        "boolean": profiling.BOOLEAN,
        "timestamp without time zone": profiling.DATETIME,
        "date": profiling.DATETIME,
        "jsonb": profiling.OTHER,
        "text[]": profiling.OTHER,
    }
    assert {name: profiling.kind_of(name) for name in kinds} == kinds


def test_a_profile_under_postgres_reads_a_string_and_an_untyped_column_as_text():
    exprs = profiling._stat_exprs(
        {"ID": profiling.STRING, "meta": profiling.OTHER, "n": profiling.INTEGER}, dialect.POSTGRES
    )
    assert 'CAST("ID" AS text) <> trim(CAST("ID" AS text))' in exprs["c0_whitespace"]
    assert exprs["c0_distinct"] == 'count(DISTINCT "ID")'
    assert exprs["c1_distinct"] == 'count(DISTINCT CAST("meta" AS text))', "json has no equality"
    assert "try_cast" not in " ".join(exprs.values())
    local = profiling._stat_exprs({"meta": profiling.OTHER}, dialect.DUCKDB)
    assert local["c0_distinct"] == 'count(DISTINCT "meta")', "DuckDB is asked what it always was"


def test_a_query_read_as_a_whole_carries_an_alias_because_postgres_before_16_requires_one(con):
    from portia.core.table import subquery

    assert subquery("SELECT 1") == "(SELECT 1) AS _q"
    t = Table.from_frame(pd.DataFrame({"n": [1, 2, 3]}), "t", con)
    asked: list[str] = []

    class Spy:
        def execute(self, sql):
            asked.append(sql)
            return con.execute(sql)

    assert t.using(Spy()).count() == 3
    assert asked == ['SELECT count(*) FROM (SELECT * FROM "t") AS _q']
