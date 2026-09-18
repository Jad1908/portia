"""The BigQuery adapter against a real project — or a skip (`docs/CONNECTORS.md` §4).

Everything the connector *decides* is in `test_connectors.py` and runs without
a driver. What is here is the seam between the adapter and Google: whether the
questions it asks are the ones BigQuery answers. The fixture spends the named
connection's bytes, which is why it is opt-in by environment variable.
"""

from __future__ import annotations

import pandas as pd

from portia.checks import profiling
from portia.core.table import Table


def test_a_session_answers_the_six_things_table_asks(bigquery_session):
    con = bigquery_session
    rows = con.execute("SELECT 1 AS n, 'a' AS s").fetchall()
    assert rows == [(1, "a")]
    rel = con.sql("SELECT 1 AS n, 'a' AS s")
    assert rel.columns == ["n", "s"] and rel.types == ["INT64", "STRING"]
    sibling = con.cursor()
    assert sibling.execute("SELECT 2").fetchone() == (2,)
    frame = con.execute("SELECT 1 AS n UNION ALL SELECT 2 ORDER BY 1").fetch_df()
    assert isinstance(frame, pd.DataFrame) and list(frame["n"]) == [1, 2]
    assert "connected as" in con.whoami()


def test_a_profile_runs_in_googlesql_and_says_its_quartiles_are_approximate(bigquery_session):
    t = Table(
        name="t",
        query="SELECT n, CAST(n AS STRING) AS s FROM UNNEST([1, 2, 3, 4, 5]) AS n",
        con=bigquery_session,
    )
    profile = profiling.profile(t)
    n = next(c for c in profile["columns"] if c["name"] == "n")
    assert n["n_distinct"] == 5 and n["min"] == 1 and n["max"] == 5
    assert n["approximate"] == ["q25", "median", "q75"]
    s = next(c for c in profile["columns"] if c["name"] == "s")
    assert s["kind"] == profiling.STRING


def test_browsing_lists_the_connections_project(bigquery_session):
    assert bigquery_session.databases()[0] == bigquery_session.project
