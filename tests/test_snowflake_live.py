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


def test_browsing_lists_databases(snowflake_session):
    assert snowflake_session.databases(), "a session can always see at least one database"
