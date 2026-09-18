"""The escape hatch: a transform we didn't prewrite, measured like any other op.

The scenario driving these: the hotel fixture's fatal fan-out has exactly one
correct handling — reduce events to one row per city-date *before* joining — and
no op could express it. A capable model worked that out, said there was no op for
it, and stopped (docs/EVALUATION.md, Run 6). These tests are that gap closing,
and the guard rails that keep closing it from handing back the filesystem
`agent/session.py` withholds.
"""

import json

import pandas as pd
import pytest

from portia.checks.outcome import GRAIN_NOT_UNIQUE, outcome_report
from portia.ops import apply_join, apply_sql
from portia.ops.sql import (
    PROVENANCE_KEYS,
    SqlNotAllowed,
    SqlTableUnknown,
    check_sql,
    rename_tables,
    render_text,
)


@pytest.fixture
def events():
    """Two events in Amsterdam on one day — the planted fan-out."""
    return pd.DataFrame(
        {
            "city_name": ["amsterdam", "amsterdam", "paris"],
            "event_date": ["2026-06-12", "2026-06-12", "2026-06-12"],
            "event_name": ["Canal Festival", "Design Week", "Tech Summit"],
            "expected_attendance": [25000, 12000, 15000],
        }
    )


@pytest.fixture
def bookings():
    return pd.DataFrame(
        {
            "booking_id": ["B0009", "B0001"],
            "city": ["amsterdam", "paris"],
            "stay_date": ["2026-06-12", "2026-06-12"],
            "revenue": [480, 2400],
        }
    )


AGGREGATE = """
    SELECT city_name, event_date,
           COUNT(*) AS n_events,
           SUM(expected_attendance) AS total_attendance
    FROM city_events
    GROUP BY 1, 2
"""


# --- the gap it closes -------------------------------------------------------


def test_the_fan_out_has_a_resolution_now(events, bookings, table):
    """The whole point: aggregate first, and the join no longer multiplies rows.

    Recorded as two steps this is exactly the answer key's correct handling, and
    until this op existed the spec could not express it at all.
    """
    events_t, bookings_t = table(events, "city_events"), table(bookings, "bookings")
    per_city_date = apply_sql({"city_events": events_t}, AGGREGATE, name="per_city_date")
    assert per_city_date.table.count() == 2  # amsterdam and paris, one row each

    joined = apply_join(
        bookings_t,
        per_city_date.table,
        how="left",
        left_on=["city", "stay_date"],
        right_on=["city_name", "event_date"],
    )
    report = outcome_report(
        joined.table,
        inputs={"bookings": bookings_t, "events": per_city_date.table},
        grain=["booking_id"],
    )

    assert joined.table.count() == len(bookings)  # no multiplication
    assert report["grain"]["unique"] is True
    assert GRAIN_NOT_UNIQUE not in report["flags"]
    # nothing double-counted
    assert joined.table.scalar("sum(revenue)") == bookings["revenue"].sum()


def test_without_the_aggregate_the_same_join_still_fans_out(events, bookings, table):
    """The trap is real, not an artifact of the fixture — the control case."""
    bookings_t = table(bookings, "bookings")
    joined = apply_join(
        bookings_t,
        table(events, "city_events"),
        how="left",
        left_on=["city", "stay_date"],
        right_on=["city_name", "event_date"],
    )
    report = outcome_report(joined.table, inputs={"b": bookings_t}, grain=["booking_id"])

    assert joined.table.count() > len(bookings)
    assert GRAIN_NOT_UNIQUE in report["flags"]


# --- provenance --------------------------------------------------------------


def test_provenance_is_serializable_and_declares_what_it_read(events, table):
    result = apply_sql({"city_events": table(events)}, AGGREGATE)
    json.dumps(result.provenance)

    assert set(result.provenance) == set(PROVENANCE_KEYS)
    assert result.provenance["op"] == "sql"
    assert result.provenance["inputs"] == ["city_events"]
    assert result.provenance["input_rows"] == {"city_events": 3}
    assert result.provenance["result_rows"] == 2
    assert result.provenance["columns"] == [
        "city_name",
        "event_date",
        "n_events",
        "total_attendance",
    ]


def test_the_declared_keys_match_a_real_run(events, table):
    """Guards `_EXPECTABLE`: an `expect` block is validated against this set."""
    assert set(apply_sql({"city_events": table(events)}, AGGREGATE).provenance) == set(
        PROVENANCE_KEYS
    )


def test_a_table_that_was_not_declared_is_not_visible(events, bookings, table):
    """An undeclared read is an error, not a silent dependency.

    Without this, the contribution measurement in `checks.outcome` is reporting
    on a set of inputs that isn't the set the query actually used.
    """
    with pytest.raises(Exception, match="(?i)bookings"):
        apply_sql({"city_events": table(events)}, "SELECT * FROM bookings")


def test_render_shows_the_sql_itself(events, table):
    text = render_text(apply_sql({"city_events": table(events)}, AGGREGATE).provenance)
    assert "city_events 3" in text
    assert "→ 2 rows × 4 cols" in text
    assert "GROUP BY 1, 2" in text  # the decision is the query; show it


# --- the sandbox -------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "COPY (SELECT * FROM city_events) TO '/tmp/leak.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "INSTALL httpfs",
        "ATTACH 'other.db' AS other",
        "SELECT 1; DROP TABLE city_events",
        "DROP TABLE city_events",
        "CREATE TABLE t AS SELECT 1",
        "UPDATE city_events SET city_name = 'x'",
        "SET memory_limit = '1GB'",
        "",
    ],
)
def test_anything_that_is_not_a_single_read_is_refused(sql):
    """`session.py` gives the agent no filesystem tools; the hatch must not
    quietly hand them back. Refused before DuckDB is touched."""
    with pytest.raises(SqlNotAllowed):
        check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM city_events",
        "  select city_name from city_events  ",
        "WITH e AS (SELECT * FROM city_events) SELECT * FROM e",
        "SELECT * FROM city_events;",  # one trailing semicolon is fine
        "-- collapse to one row per city-date\nSELECT city_name FROM city_events",
    ],
)
def test_an_ordinary_read_is_allowed(sql):
    check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM 'secrets.csv'",  # no function name for the word list to catch
        "SELECT * FROM read_csv_auto('secrets.csv')",  # not the exact word `read_csv`
    ],
)
def test_external_access_is_off_inside_duckdb_too(events, sql, table):
    """The string check is bypassable by design; this is the half that isn't.

    Both spellings get past `check_sql`'s word list, so what stops them is
    DuckDB's own configuration — which is why the sandbox is a pair and not
    either one alone.
    """
    check_sql(sql)  # the readable half does NOT catch these
    with pytest.raises(Exception, match="(?i)file system operations are disabled"):
        apply_sql({"city_events": table(events)}, sql)


# --- naming an earlier step, and being told when you got it wrong -----------
#
# `docs/PIPELINE.md` §8. The `#` form was documented as interchangeable with a
# bare step id and was normalized in a step's `inputs` and not in its SQL text,
# so a `sql` step chaining by the documented form asked the sandbox for a table
# nobody had registered. These pin the two halves of the fix.


@pytest.mark.parametrize(
    ("sql", "want"),
    [
        # The quoted identifier — the only form that parses, so the common one.
        ('SELECT * FROM "specs/t.yaml#prep"', 'SELECT * FROM "prep"'),
        # Unquoted, which is a parser error before this and a working query after.
        ("SELECT * FROM specs/t.yaml#prep", "SELECT * FROM prep"),
        # Twice in one statement.
        (
            'SELECT a FROM "specs/t.yaml#prep" JOIN "specs/t.yaml#prep" USING (a)',
            'SELECT a FROM "prep" JOIN "prep" USING (a)',
        ),
        # A name this one is a prefix of is not a match.
        ('SELECT * FROM "specs/t.yaml#prepared"', 'SELECT * FROM "specs/t.yaml#prepared"'),
        # Nothing to do.
        ("SELECT a FROM other", "SELECT a FROM other"),
    ],
)
def test_rename_tables_rewrites_identifiers(sql, want):
    assert rename_tables(sql, {"specs/t.yaml#prep": "prep"}) == want


def test_rename_tables_leaves_string_literals_alone():
    """The one distinction it has to get right.

    A double-quoted run is an identifier and a single-quoted run is a literal,
    so the same characters mean two different things three tokens apart.
    """
    sql = "SELECT 'specs/t.yaml#prep' AS note FROM \"specs/t.yaml#prep\""
    assert rename_tables(sql, {"specs/t.yaml#prep": "prep"}) == (
        "SELECT 'specs/t.yaml#prep' AS note FROM \"prep\""
    )


def test_rename_tables_is_a_no_op_when_nothing_changes():
    sql = 'SELECT * FROM "prep"'
    assert rename_tables(sql, {"prep": "prep"}) is sql


def test_an_undeclared_table_is_portias_error_not_duckdbs(events, table):
    """`PIPELINE.md` §8.3.

    The sandbox holds exactly the declared inputs, so "does not exist" always
    means "you named something you did not declare" — and DuckDB's own answer
    ends by suggesting `__portia_raw_city_events`, a staging relation that is
    real, reachable, and something no caller should ever be told to type.
    """
    with pytest.raises(SqlTableUnknown) as raised:
        apply_sql({"city_events": table(events)}, "SELECT * FROM events")
    message = str(raised.value)
    assert "Declared: city_events" in message
    assert "bare id" in message
    assert "__portia_raw" not in message


# --- the remote path (`docs/CONNECTOR.md` §2.8) ---------------------------------


class _RemoteDuckDB:
    """A DuckDB connection that claims to be remote.

    Not a stand-in for a warehouse: it is DuckDB, so the composed statement is
    really executed and really answers. What it lets the tests pin is portia's
    own decision — that a remote input is bound as a CTE and never copied, and
    that an undeclared table is refused before anything runs.
    """

    remote = True

    def __init__(self, inner):
        self._inner = inner
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return self._inner.execute(sql)

    def sql(self, query):
        return self._inner.sql(query)

    def cursor(self):
        return _RemoteDuckDB(self._inner.cursor())

    def interrupt(self):
        self._inner.interrupt()


def _remote_inputs(con):
    from portia.core.table import Table

    remote = _RemoteDuckDB(con)
    con.execute(
        "CREATE TABLE orders AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'b')) v(id, k)"
    )
    con.execute("CREATE TABLE other AS SELECT 9 AS id")
    return remote, {"orders": Table.from_name("orders", remote)}


def test_a_remote_step_runs_where_the_data_is_and_copies_nothing(con):
    from portia.ops import sql as sql_op

    remote, inputs = _remote_inputs(con)
    out = apply_sql(inputs, "SELECT k, count(*) AS n FROM orders GROUP BY k", name="by_k")

    assert out.provenance["result_rows"] == 2 and out.provenance["input_rows"] == {"orders": 3}
    assert out.provenance["columns"] == ["k", "n"]
    assert out.table.query.startswith('WITH "orders" AS (')
    # The compiled form reads the input the way `compose` binds it — quoted. The
    # agent's verbatim text is the provenance's job, not this one's.
    assert out.compiled == 'SELECT k, COUNT(*) AS n FROM "orders" GROUP BY k'
    assert out.provenance["sql"] == "SELECT k, count(*) AS n FROM orders GROUP BY k"
    # Nothing pulled a relation out: every statement was a count or a describe.
    assert not any(".fetch_df" in s for s in remote.executed)
    assert out.table.con is remote, "the result is a handle on the same session"
    assert sql_op.compose(inputs, "WITH x AS (SELECT 1) SELECT * FROM x").startswith(
        'WITH "orders" AS ('
    ), "a statement with its own WITH gets one merged list, not two"


def test_a_remote_step_naming_an_undeclared_table_is_refused_before_it_runs(con):
    from portia.ops.sql import SqlTableUnknown

    remote, inputs = _remote_inputs(con)
    before = len(remote.executed)
    with pytest.raises(SqlTableUnknown, match="names other, which is not a declared input"):
        apply_sql(inputs, "SELECT * FROM orders JOIN other USING (id)")
    assert len(remote.executed) == before, "refused by the parser, never by the warehouse"


def test_the_parse_check_reads_through_ctes_quotes_and_case():
    from portia.ops import sql as sql_op

    sql = 'WITH x AS (SELECT * FROM ORDERS) SELECT * FROM x JOIN "Customers" c ON 1=1, DB.S.T'
    assert sql_op.referenced_tables(sql, dialect="snowflake") == {"ORDERS", "Customers", "DB.S.T"}
    inputs = {"orders": None, "customers": None}
    assert sql_op.undeclared_tables(sql, inputs, dialect="snowflake") == ["DB.S.T"]


def test_a_remote_reference_is_quoted_to_the_exact_cte_spelling(con):
    """The composed statement agrees with itself about case (`CONNECTOR.md` §2.8.1).

    `compose` binds ``"orders"`` quoted; an unquoted ``FROM orders`` folds to
    ``ORDERS`` on Snowflake and misses it — the 2026-09-06 run's death loop, 18
    failures across every spelling the copilot could think of. `_RemoteDuckDB`
    cannot reproduce the fold (DuckDB forgives the mismatch), so what this pins
    is the string the warehouse receives: every reference to a declared input
    arrives quoted, whatever case the agent wrote it in.
    """
    remote, inputs = _remote_inputs(con)
    out = apply_sql(inputs, "SELECT * FROM ORDERS", name="reread")
    assert 'FROM "orders"' in out.table.query and "FROM ORDERS" not in out.table.query
    assert out.provenance["result_rows"] == 3


def test_quoting_declared_inputs_touches_nothing_else():
    from portia.ops import sql as sql_op

    inputs = {"orders": None, "unified_clean": None}
    sql = (
        "WITH cleaned AS (SELECT * FROM orders WHERE k = 'orders') "
        "SELECT unified_clean, cleaned.* FROM cleaned JOIN DB.S.T ON 1=1"
    )
    bound = sql_op.quote_declared(sql, inputs, dialect="snowflake")
    assert 'FROM "orders"' in bound, "the reference inside the statement's own CTE binds"
    assert "'orders'" in bound, "a string literal is somebody's data, never a table"
    assert "FROM cleaned" in bound, "the statement's own CTE is its own to name"
    assert "SELECT unified_clean" in bound, "a column sharing an input's name is a column"
    assert "DB.S.T" in bound, "a qualified name is a warehouse table, not an input"


def test_snowflakes_own_verbs_and_system_functions_are_refused():
    from portia.ops.sql import SqlNotAllowed, check_sql

    with pytest.raises(SqlNotAllowed, match="SYSTEM\\$"):
        check_sql("SELECT SYSTEM$CANCEL_ALL_QUERIES(1)")
    with pytest.raises(SqlNotAllowed, match="PUT"):
        check_sql("SELECT * FROM t WHERE put = 1")
    check_sql("SELECT updated, deleted_at FROM t")  # a column *spelled near* a verb is fine


def test_the_parse_check_and_the_quoted_binding_speak_googlesql():
    """`CONNECTORS.md` §8: a project id with a hyphen is only reachable in backticks, and
    the parse check has to read one as three parts to refuse it as undeclared."""
    from portia.core import dialect as dialects
    from portia.core.table import Table
    from portia.ops import sql as sql_op

    class Quacks:
        dialect = dialects.BIGQUERY
        remote = True

    inputs = {"orders": Table(name="orders", query="SELECT 1 AS id", con=Quacks())}
    sql = "SELECT o.id FROM orders o JOIN `my-proj.raw.customers` c ON o.id = c.id"
    assert sql_op.referenced_tables(sql, dialect="bigquery") == {"orders", "my-proj.raw.customers"}
    assert sql_op.undeclared_tables(sql, inputs, dialect="bigquery") == ["my-proj.raw.customers"]
    bound = sql_op.quote_declared("SELECT * FROM ORDERS o", inputs, dialect="bigquery")
    assert bound == "SELECT * FROM `orders` AS o"
    assert sql_op.compose(inputs, bound).startswith("WITH `orders` AS (SELECT 1 AS id)")
