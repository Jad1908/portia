"""`checks/sql_joins` — what the equality joins of an interrupted query produce.

Measured after the user interrupts a data call (`docs/CONVERSATION.md` §16): the
query that asked for it unpivoted two wide tables and joined them on the meter
alone, and these numbers are what says so. Every count here is checked against
arithmetic done by hand on a table small enough to do it, because a measurement
that is off by the null keys or by the join's kind is a number the copilot will
repeat.
"""

from __future__ import annotations

import duckdb
import pytest

from portia.checks import sql_joins

pytest.importorskip("sqlglot", reason="reading a join needs the parser")


@pytest.fixture
def con():
    """``a`` and ``b`` keyed by ``k``: 1 twice and 2 three times on the left, 1 once,
    2 twice and 3 once on the right, and a null key on each side; plus a wide
    ``meters`` table of 3 hours by 2 meters, for the unpivot."""
    db = duckdb.connect(":memory:")
    db.execute(
        "CREATE VIEW a AS SELECT * FROM (VALUES (1, 'x'), (1, 'y'), (2, 'z'), (2, 'w'), "
        "(2, 'v'), (NULL, 'n')) t(k, va)"
    )
    db.execute(
        "CREATE VIEW b AS SELECT * FROM (VALUES (1, 10), (2, 20), (2, 21), (3, 30), "
        "(NULL, 0)) t(k, vb)"
    )
    db.execute(
        "CREATE VIEW meters AS SELECT * FROM (VALUES (1, 0.1, 0.2), (2, 0.3, 0.4), "
        "(3, 0.5, 0.6)) t(hour, m1, m2)"
    )
    yield db
    db.close()


def _measured(con, sql, names=("a", "b", "meters")):
    joins, unread = sql_joins.read(sql, names)
    return sql_joins.measure(con, sql, joins), unread


def test_an_equality_join_between_two_inputs_is_counted_exactly(con):
    sql = "SELECT * FROM a JOIN b ON a.k = b.k"
    (got,), unread = _measured(con, sql)

    assert unread == []
    assert got["kind"] == sql_joins.INNER
    assert (got["left"], got["right"]) == ("a", "b")
    assert got["on"] == ["k = k"]
    assert (got["left_rows"], got["right_rows"]) == (6, 5)
    assert (got["left_keys"], got["right_keys"], got["keys_on_both"]) == (2, 3, 2)
    assert got["left_repeats"] == {"min": 2, "max": 3}
    assert got["right_repeats"] == {"min": 1, "max": 2}
    # key 1: 2 x 1, key 2: 3 x 2; the nulls match nothing.
    assert got["rows"] == 2 * 1 + 3 * 2
    assert got["rows"] == con.execute(sql.replace("*", "count(*)")).fetchone()[0]


def test_the_rows_follow_the_kind_of_join(con):
    """The 8 matched rows, plus what each kind keeps unmatched: the left's null-key
    row (1), the right's key 3 and its null-key row (2), or all three."""
    for kind, rows in (("LEFT", 8 + 1), ("RIGHT", 8 + 2), ("FULL", 8 + 1 + 2)):
        sql = f"SELECT * FROM a {kind} JOIN b ON a.k = b.k"
        (got,), _ = _measured(con, sql)
        assert got["kind"] == kind.lower()
        assert got["rows"] == rows
        assert got["rows"] == con.execute(sql.replace("*", "count(*)", 1)).fetchone()[0]


def test_a_side_that_is_a_cte_is_the_cte_the_query_ran(con):
    """The motivating case in miniature: two unpivots joined on the meter only."""
    sql = (
        "WITH e AS (UNPIVOT meters ON COLUMNS(* EXCLUDE (hour)) INTO NAME meter VALUE kwh), "
        "g AS (UNPIVOT meters ON COLUMNS(* EXCLUDE (hour)) INTO NAME meter VALUE m3) "
        "SELECT e.meter, sum(e.kwh * g.m3) FROM e JOIN g ON e.meter = g.meter GROUP BY 1"
    )
    (got,), unread = _measured(con, sql)

    assert unread == []
    assert (got["left"], got["right"]) == ("e", "g")
    assert (got["left_rows"], got["right_rows"]) == (6, 6)
    assert got["left_keys"] == got["keys_on_both"] == 2
    # Each meter repeats once per hour on both sides: 3 x 3 rows per meter.
    assert got["left_repeats"] == got["right_repeats"] == {"min": 3, "max": 3}
    assert got["rows"] == 2 * 3 * 3


def test_using_is_read_as_the_same_column_on_both_sides(con):
    (got,), unread = _measured(con, "SELECT * FROM a JOIN b USING (k)")
    assert unread == []
    assert got["on"] == ["k = k"]
    assert got["rows"] == 8


def test_an_alias_and_a_composite_key_are_read(con):
    sql = "SELECT * FROM a AS l JOIN a AS r ON l.k = r.k AND l.va = r.va"
    (got,), _ = _measured(con, sql)
    assert (got["left"], got["right"]) == ("a", "a")
    assert got["on"] == ["k = k", "va = va"]
    assert got["rows"] == 5  # every non-null row matches itself once


def test_a_cross_join_is_the_two_counts_multiplied(con):
    (got,), _ = _measured(con, "SELECT * FROM a CROSS JOIN b")
    assert got["kind"] == sql_joins.CROSS
    assert got["rows"] == 6 * 5


@pytest.mark.parametrize(
    ("sql", "because"),
    [
        ("SELECT * FROM a JOIN (SELECT * FROM b) s ON a.k = s.k", sql_joins.SIDE_NOT_NAMED),
        ("SELECT * FROM a JOIN other ON a.k = other.k", sql_joins.SIDE_NOT_NAMED),
        ("SELECT * FROM a JOIN b ON lower(a.va) = b.vb", sql_joins.CONDITION_NOT_EQUALITIES),
        ("SELECT * FROM a JOIN b ON a.k = b.k AND a.k > 1", sql_joins.CONDITION_NOT_EQUALITIES),
        ("SELECT * FROM a JOIN b ON k = vb", sql_joins.COLUMN_SIDE_UNCLEAR),
        ("SELECT * FROM a NATURAL JOIN b", sql_joins.KIND_NOT_MEASURED),
        ("SELECT * FROM a, b WHERE a.k = b.k", sql_joins.KIND_NOT_MEASURED),
        ("SELECT * FROM a SEMI JOIN b ON a.k = b.k", sql_joins.KIND_NOT_MEASURED),
        (
            "SELECT * FROM a JOIN b USING (k) JOIN meters USING (hour)",
            sql_joins.COLUMN_SIDE_UNCLEAR,
        ),
    ],
)
def test_a_join_it_cannot_read_is_said_with_why_and_not_measured(con, sql, because):
    joins, unread = sql_joins.read(sql, ("a", "b", "meters"))
    assert because in {u["because"] for u in unread}
    assert all(u["join"] for u in unread)
    assert len(sql_joins.measure(con, sql, joins)) == len(joins)


def test_a_statement_with_no_join_reads_as_nothing_to_measure():
    assert sql_joins.read("SELECT count(*) FROM a", ("a",)) == ([], [])


def test_a_statement_the_parser_refuses_is_said_so():
    joins, unread = sql_joins.read("SELEC nonsense FROM", ("a",))
    assert joins == [] and unread[0]["because"] == sql_joins.NOT_PARSED


def test_a_join_that_cannot_be_counted_is_reported_and_the_rest_go_on(con):
    con.execute("CREATE VIEW c AS SELECT * FROM (VALUES ('one'), ('two')) t(k)")
    sql = "SELECT * FROM a JOIN c ON a.k = c.k JOIN b ON a.k = b.k"
    joins, _ = sql_joins.read(sql, ("a", "b", "c"))
    measured = sql_joins.measure(con, sql, joins)
    assert measured[0]["because"] == sql_joins.MEASURE_FAILED
    assert measured[1]["rows"] == 8


def test_an_interrupt_while_counting_is_raised_not_reported(con):
    """A second press skips the measuring: an interrupt is not a failed join."""
    import threading
    import time

    con.execute("CREATE VIEW big AS SELECT i % 3 AS k FROM range(200000000) t(i)")
    sql = "SELECT * FROM big x JOIN big y ON x.k = y.k"
    joins, _ = sql_joins.read(sql, ("big",))
    threading.Thread(target=lambda: (time.sleep(0.2), con.interrupt()), daemon=True).start()
    with pytest.raises(duckdb.InterruptException):
        sql_joins.measure(con, sql, joins)
