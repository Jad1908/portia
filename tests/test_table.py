"""A Table is a handle, not data — it stays lazy, and head() is the way out."""

from __future__ import annotations

import pathlib
import re
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


def test_copy_to_round_trips(t, tmp_path, con):
    out = tmp_path / "stays.csv"
    t.copy_to(out, options="HEADER, DELIMITER ','")
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


# --- the rule that keeps the migration honest --------------------------------

#: The only places a whole relation is allowed to become a DataFrame, and why.
#: Everything else must go through `Table.head` / `Table.rows`, which are capped.
MATERIALIZERS = {
    "portia/core/table.py": "head() itself — a LIMIT, which is the capped exit",
    "portia/core/io.py": (
        "`load_frame` is *defined* as the whole file in memory — it says so in the "
        "module docstring, and it is the small-read convenience the engine no "
        "longer calls. The Parquet loader reads through DuckDB rather than adding "
        "pyarrow, which is why it shows up here at all when the CSV one does not."
    ),
    "portia/ops/sql.py": (
        "the escape hatch's sandbox boundary: the declared inputs cross into a "
        "connection that holds nothing else, which is what makes the guarantee "
        "independent of reading the query correctly (§6.1). It is also why SQL "
        "steps stay memory-bound, and that is written down rather than hidden."
    ),
}

_PULLS_EVERYTHING = re.compile(r"\.(?:fetch_df|fetchdf|to_df|df)\s*\(\s*\)")


def test_nothing_new_pulls_a_whole_relation_into_memory():
    """§4's rule, as a test instead of a grep someone has to remember to run.

    "Memory scales with the answer" survives exactly as long as no one adds a
    `.df()`. The two exceptions are listed above with their reasons; a third
    needs one too, and needs it argued for before it is added here.
    """
    package = pathlib.Path(__file__).resolve().parents[1] / "portia"
    offenders = [
        f"{rel}:{i}  {line.strip()}"
        for path in sorted(package.rglob("*.py"))
        for rel in [path.relative_to(package.parent).as_posix()]
        if rel not in MATERIALIZERS
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if _PULLS_EVERYTHING.search(line)
    ]
    assert not offenders, (
        "these read an entire relation into memory, which is the ceiling the "
        "migration removed — use Table.head/rows, or add the file to "
        "MATERIALIZERS with the reason:\n  " + "\n  ".join(offenders)
    )


def test_a_timezone_aware_timestamp_survives_both_exits(con):
    """DuckDB needs `pytz` to hand a TIMESTAMPTZ back to python, and says so by
    raising when it meets one. Real data met one on the first try, which broke
    every sample and every preview of that table."""
    con.execute("CREATE TABLE stamps AS SELECT TIMESTAMPTZ '2024-01-01 10:00:00+00' AS t")
    table = Table.from_name("stamps", con)
    assert len(table.rows(1)) == 1  # the evidence exit
    assert len(table.head(1)) == 1  # the rendering exit
