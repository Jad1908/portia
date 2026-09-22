"""The adapter against a real account — skips without ``PORTIA_TEST_CONNECTION``.

`docs/CONNECTOR.md` §6: the seam worth testing live is whether the questions
the adapter asks are the ones Snowflake answers. Everything here spends the
named connection's credits and may open a browser, which is why the fixture is
opt-in. Nothing here reads a table of the user's: the data is ``SELECT 1``.
"""

from __future__ import annotations

import pandas as pd

from portia.checks import profiling
from portia.core import backend, dialect
from portia.core.table import Table


def test_a_session_answers_the_six_things_table_asks(snowflake_session):
    con = snowflake_session
    assert dialect.of(con) is dialect.SNOWFLAKE and backend.is_remote(con)

    result = con.execute("SELECT 1 AS n, 'x' AS s")
    assert result.fetchone() == (1, "x")

    relation = con.sql("SELECT 1 AS n, 'x' AS s, CURRENT_TIMESTAMP() AS t")
    assert relation.columns == ["N", "S", "T"]
    assert relation.types[0].startswith("NUMBER") and relation.types[1] == "TEXT"

    frame = con.execute("SELECT 1 AS n").fetch_df()
    assert isinstance(frame, pd.DataFrame) and list(frame.columns) == ["N"]

    sibling = con.cursor()
    assert sibling.execute("SELECT 2").fetchone() == (2,)


def test_a_profile_runs_in_snowflakes_dialect(snowflake_session):
    t = Table(
        name="probe",
        query="SELECT * FROM VALUES (1, 'a'), (2, 'b'), (3, ' b') AS v(n, s)",
        con=snowflake_session,
    )
    profile = profiling.profile(t)
    by_name = {c["name"]: c for c in profile["columns"]}
    assert profile["n_rows"] == 3
    assert by_name["N"]["median"] == 2 and by_name["N"]["q25"] == 1.5
    assert by_name["S"]["n_distinct"] == 3


#: Two million generated rows of about a million, at scale 10. Snowflake keeps
#: each as the integer 10^16, so the squares sum to about 2 * 10^38, past the
#: 38 digits a ``NUMBER`` holds. No table is read and the scan is generated.
_WIDE_NUMBER = (
    "SELECT (1000000 + seq4())::NUMBER(38,10) AS revenue FROM TABLE(GENERATOR(ROWCOUNT => 2000000))"
)


def test_a_wide_number_overflows_snowflakes_own_stddev(snowflake_session):
    """The premise, kept as a test: if Snowflake ever stops overflowing here,
    `Snowflake.as_float` is a cast with no reason left and this says so."""
    import pytest

    with pytest.raises(Exception, match="out of representable range"):
        snowflake_session.execute(f"SELECT stddev_samp(revenue) FROM ({_WIDE_NUMBER})").fetchone()


def test_a_profile_survives_a_number_whose_squares_pass_38_digits(snowflake_session):
    """One such column cost a real table its whole profile (2026-09-21): every
    column rides in one statement, and ``stddev_samp`` over a ``NUMBER`` sums
    squares in fixed point (`core/dialect.Snowflake.as_float`)."""
    profile = profiling.profile(Table(name="wide", query=_WIDE_NUMBER, con=snowflake_session))
    (column,) = profile["columns"]
    assert profile["n_rows"] == 2_000_000
    assert column["min"] == 1_000_000 and column["max"] == 2_999_999
    assert column["mean"] == 1_999_999.5
    assert 577_000 < column["std"] < 578_000  # a uniform spread of 2M: 2e6 / sqrt(12)


def test_browsing_lists_databases(snowflake_session):
    assert snowflake_session.databases(), "a session can always see at least one database"
