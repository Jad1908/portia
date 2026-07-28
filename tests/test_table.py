"""A Table is a handle, not data — it stays lazy, and head() is the way out."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import duckdb
import pandas as pd
import pytest

from portia.core.table import Table, quote_ident, quote_literal


@pytest.fixture
def t(con):
    frame = pd.DataFrame(
        {
            "city": ["paris", "paris", "lyon", None],
            "rooms": [10, 20, 30, 40],
        }
    )
    return Table.from_frame(frame, "stays", con)


def test_schema_without_reading_rows(t):
    assert t.columns == ["city", "rooms"]
    assert t.dtypes == {"city": "VARCHAR", "rooms": "BIGINT"}


def test_count_and_scalar(t):
    assert t.count() == 4
    assert t.scalar("sum(rooms)") == 100
    assert t.scalar("count(city)") == 3  # nulls excluded, as SQL counts


def test_row_returns_many_aggregates_in_one_pass(t):
    assert t.row({"n": "count(*)", "n_city": "count(city)", "top": "max(rooms)"}) == {
        "n": 4,
        "n_city": 3,
        "top": 40,
    }


def test_row_of_nothing_is_nothing(t):
    assert t.row({}) == {}


def test_sql_derives_a_new_table(t):
    grouped = t.sql(
        f"SELECT city, count(*) AS n FROM {t.ref} WHERE city IS NOT NULL GROUP BY city",
        name="by_city",
    )
    assert grouped.name == "by_city"
    assert grouped.columns == ["city", "n"]
    assert grouped.count() == 2  # paris, lyon — the null is excluded


def test_derived_tables_chain(t):
    once = t.sql(f"SELECT * FROM {t.ref} WHERE rooms > 10", name="big")
    twice = once.sql(f"SELECT sum(rooms) AS total FROM {once.ref}", name="total")
    assert twice.scalar("any_value(total)") == 90


def test_deriving_computes_nothing(t):
    """The whole point of a handle: a broken query is only a problem when asked.

    If this ever fails, something along the path started materialising eagerly —
    which is the memory ceiling coming back.
    """
    broken = t.sql(f"SELECT no_such_column FROM {t.ref}")
    with pytest.raises(duckdb.BinderException):
        broken.count()


def test_head_is_capped_and_returns_pandas(t):
    head = t.head(2)
    assert isinstance(head, pd.DataFrame)
    assert len(head) == 2
    assert list(head.columns) == ["city", "rooms"]


def test_to_csv_round_trips(t, tmp_path, con):
    out = tmp_path / "stays.csv"
    t.to_csv(out)
    assert pd.read_csv(out).shape == (4, 2)


def test_using_rebinds_to_another_handle(t, con):
    assert t.using(con.cursor()).count() == t.count()


def test_concurrent_queries_each_take_their_own_cursor(t, con):
    """§4's rule, as a test: DuckDB connections are not thread-safe.

    The app runs blocking work through ``asyncio.to_thread``, so this is the
    shape every threaded read has to take. Getting it wrong gives intermittent
    corruption rather than a clean error, which is why it is pinned here.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(lambda _: t.using(con.cursor()).count(), range(64)))
    assert set(counts) == {4}


def test_from_name_reads_an_existing_table(con):
    con.execute("CREATE TABLE nums AS SELECT * FROM range(5) t(i)")
    assert Table.from_name("nums", con).count() == 5


def test_awkward_identifiers_survive(con):
    """A column really can be called ``order``, and a table can have a quote in it."""
    frame = pd.DataFrame({"order": [1, 2], 'we"ird': ["a", "b"]})
    t = Table.from_frame(frame, 'my "table"', con)
    assert t.count() == 2
    assert t.columns == ["order", 'we"ird']
    assert t.sql(f"SELECT * FROM {t.ref}").count() == 2


def test_quoting_escapes():
    assert quote_ident('a"b') == '"a""b"'
    assert quote_literal("it's") == "'it''s'"
