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

from portia.checks.outcome import GRAIN_NOT_UNIQUE, outcome_report_table
from portia.ops import apply_join, apply_sql
from portia.ops.sql import PROVENANCE_KEYS, SqlNotAllowed, check_sql, render_text


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
    report = outcome_report_table(
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
    report = outcome_report_table(joined.table, inputs={"b": bookings_t}, grain=["booking_id"])

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
