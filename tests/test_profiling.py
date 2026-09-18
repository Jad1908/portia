"""The profiler must report the traps the fixtures deliberately plant."""

import json

import pandas as pd
import pytest

from portia.checks.profiling import null_rates, profile
from portia.core.serialize import to_json
from portia.core.table import Table
from portia.fixtures import messy_customers


@pytest.fixture
def messy(table) -> dict:
    """The messy fixture, profiled — the traps it plants are the subject here."""
    return profile(table(messy_customers(), "messy_customers"))


def _col(report: dict, name: str) -> dict:
    return next(c for c in report["columns"] if c["name"] == name)


def test_shape(messy):
    assert messy["n_rows"] == 40
    assert messy["n_cols"] == 8


def test_nothing_nominates_a_key(messy):
    """Uniqueness is reported as numbers and never as a verdict.

    `possible_key` and `candidate_keys` are retired. `customer_id` is still
    measurably unique and non-null here — the profile just says so with
    `n_distinct`, `n_rows` and `null_rate` instead of with a word that claims
    keyness the check cannot establish at arity 1.
    """
    assert "candidate_keys" not in messy
    cid = _col(messy, "customer_id")
    assert cid["n_distinct"] == messy["n_rows"] and cid["null_rate"] == 0.0
    for col in messy["columns"]:
        assert "possible_key" not in col["flags"]


def test_all_null_column(messy):
    legacy = _col(messy, "legacy_col")
    assert legacy["n_null"] == 40
    assert "all_null" in legacy["flags"]


def test_constant_column(messy):
    source = _col(messy, "source")
    assert source["n_distinct"] == 1
    assert "constant" in source["flags"]


def test_high_null(messy):
    notes = _col(messy, "notes")
    assert notes["null_rate"] >= 0.5
    assert "high_null" in notes["flags"]


def test_numeric_stored_as_text(messy):
    amt = _col(messy, "signup_amount")
    assert amt["inferred"] in {"text", "categorical"}  # not parsed as numeric
    assert "numeric_stored_as_text" in amt["flags"]
    assert "leading_trailing_whitespace" in amt["flags"]


def test_mixed_types(messy):
    assert "mixed_types" in _col(messy, "mixed_ref")["flags"]


def test_high_cardinality_text(messy):
    name = _col(messy, "name")
    assert name["inferred"] == "text"
    assert "high_cardinality" in name["flags"]


def test_numeric_stats_present(table):
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})
    col = profile(table(df))["columns"][0]
    assert col["min"] == 1.0 and col["max"] == 4.0 and col["mean"] == 2.5


def test_numeric_describe_stats(table):
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    col = profile(table(df))["columns"][0]
    assert col["q25"] == 2.0 and col["median"] == 3.0 and col["q75"] == 4.0
    assert col["std"] is not None
    assert "top" not in col  # numeric columns report quartiles, not a modal value


def test_std_is_none_for_single_value(table):
    col = profile(table(pd.DataFrame({"x": [5]})))["columns"][0]
    assert col["std"] is None  # undefined, and must stay JSON-valid (not NaN)


def test_categorical_top_value(table):
    col = profile(table(pd.DataFrame({"c": ["a", "a", "b"]})))["columns"][0]
    assert col["top"] == "a" and col["top_freq"] == 2
    assert "median" not in col  # non-numeric columns don't get quartiles


def test_output_is_json_serializable(messy):
    # The profile is what the agent will see; it must round-trip through JSON.
    assert json.loads(to_json(messy)) == json.loads(json.dumps(json.loads(to_json(messy))))


# --- against the real ingest path --------------------------------------------

# The cross-implementation parity that used to live here is now the golden files'
# job (`tests/test_golden.py`): they hold what the pandas engine emitted before
# the migration, so comparing against them is a stronger claim than comparing two
# implementations that both still exist. What stays here is behaviour.


def test_a_date_column_is_typed_and_says_so(ingested):
    """The one accepted divergence from the frozen evidence, pinned so it stays deliberate."""
    col = _col(profile(ingested("reservations")), "stay_date")
    assert col["dtype"] == "DATE"
    assert col["inferred"] == "datetime"
    # ISO text, not a widened numpy timestamp — evidence carries python values.
    assert col["samples"] == ["2026-06-12", "2026-06-13", "2026-06-14"]


def test_an_empty_table_profiles_without_dividing_by_zero(table):
    frame = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype=str)})
    report = profile(table(frame))
    assert report["n_rows"] == 0
    assert [c["flags"] for c in report["columns"]] == [["all_null"], ["all_null"]]
    assert all(c["null_rate"] == 0.0 and c["samples"] == [] for c in report["columns"])


def test_booleans_get_neither_quartiles_nor_a_modal_value(table):
    col = _col(profile(table(pd.DataFrame({"ok": [True, False, True]}))), "ok")
    assert col["inferred"] == "boolean"
    assert "median" not in col and "top" not in col


def test_a_single_row_has_no_sample_standard_deviation(table):
    col = _col(profile(table(pd.DataFrame({"x": [1.5]}))), "x")
    assert col["std"] is None
    assert col["mean"] == 1.5


def test_decimals_are_numbers_in_the_evidence_not_strings(con):
    """DuckDB returns DECIMAL as `decimal.Decimal`; the str() fallback would lie."""
    con.execute("CREATE TABLE prices AS SELECT * FROM (VALUES (1.5), (2.5)) v(amount)")
    col = _col(profile(Table.from_name("prices", con)), "amount")
    assert col["inferred"] == "float"
    assert col["mean"] == 2.0
    assert col["samples"] == [1.5, 2.5]


def test_the_modal_value_breaks_ties_by_value(ingested):
    """`hotels.city` is Paris 2, Amsterdam 2 — an undefined answer is not a fact."""
    assert _col(profile(ingested("hotels")), "city")["top"] == "Amsterdam"


def test_samples_are_distinct_and_ordered(ingested):
    """Both halves of the decision behind SAMPLE_VALUES, on a low-cardinality column."""
    report = profile(ingested("messy_customers"))
    assert _col(report, "country")["samples"] == ["DE", "FR", "UK"]
    # leading whitespace sorts first, which is where a sample earns its keep
    assert _col(report, "signup_amount")["samples"][0].startswith(" ")


def test_n_a_is_a_missing_value_on_this_tier_too(ingested):
    """`signup_amount` holds one `N/A`; DuckDB left alone would call it a value."""
    assert _col(profile(ingested("messy_customers")), "signup_amount")["n_null"] == 1


def test_null_rates_of_an_empty_table_are_zero_not_undefined(table):
    assert null_rates(table(pd.DataFrame({"a": pd.Series([], dtype="int64")}))) == {"a": 0.0}


def test_a_column_that_is_partly_numeric_is_mixed(table):
    """`mixed_types`, redefined for a typed store (§6.3)."""
    assert (
        "mixed_types"
        in _col(profile(table(pd.DataFrame({"ref": ["1", "2", "abc", "4"]}))), "ref")["flags"]
    )


def test_a_column_that_is_entirely_numeric_or_entirely_text_is_not_mixed(table):
    numeric = table(pd.DataFrame({"a": ["1", "2", "3"]}), "numeric_text")
    text = table(pd.DataFrame({"a": ["x", "y", "z"]}), "plain_text")
    assert "mixed_types" not in _col(profile(numeric), "a")["flags"]
    assert "mixed_types" not in _col(profile(text), "a")["flags"]


# --- parsing a re-scanning file once ----------------------------------------


def test_a_csv_is_parsed_once_and_profiles_to_the_same_numbers(tmp_path):
    """The optimisation is only legitimate if nothing it measures moves.

    A profile is one scan plus *two queries per column* (`_table_samples`,
    `_table_top`), and on a reader that re-parses its file that is a full parse
    per column. Measured on a real 191-column 40 MB CSV: 108 s, against 1.57 s
    for the same rows and columns as Parquet. `profile_path` parses once first —
    and this pins that the parse is all that changed.
    """
    import pandas as pd

    from portia.checks.profiling import profile, profile_path
    from portia.core.io import connect, load_table

    frame = pd.DataFrame(
        {
            "id": range(60),
            "grp": [f"g{i % 7}" for i in range(60)],
            "amount": [i * 1.5 for i in range(60)],
            "empty": [None] * 60,
        }
    )
    path = tmp_path / "wide.csv"
    frame.to_csv(path, index=False)

    con = connect()
    try:
        lazy = profile(load_table(path, con))
    finally:
        con.close()
    parsed_once = profile_path(path)
    parsed_once.pop("source")

    assert parsed_once == lazy


def test_only_a_rescanning_format_is_parsed_up_front():
    """Parquet is columnar: its per-column reads are already nearly free, so it
    stays lazy and the file is never copied. Registering that is the format's
    job, in the one module where a reader is named — so it is a property of the
    suffix and needs no file to answer."""
    from portia.core.io import rescans

    assert rescans("anything.csv") is True
    assert rescans("anything.parquet") is False


def test_the_parsed_copy_does_not_outlive_the_profile(tmp_path):
    """It lives on the connection `profile_path` opens and closes, so nothing is
    left behind on disk or in a database portia keeps — there isn't one."""
    import pandas as pd

    from portia.checks.profiling import profile_path

    pd.DataFrame({"a": [1, 2, 3]}).to_csv(tmp_path / "t.csv", index=False)
    profile_path(tmp_path / "t.csv")

    assert [p.name for p in tmp_path.iterdir()] == ["t.csv"]


# --- several columns at once, on a connection that says it can -------------------


class _Wide:
    """A DuckDB connection claiming it can take several queries at once.

    The shape a warehouse session has (`core/backend.parallel_queries`): the
    width is an attribute on the connection, and `cursor()` hands out a sibling.
    Every real DuckDB connection is width one, so this is the only way to drive
    the concurrent path without a warehouse.
    """

    parallel_queries = 4

    def __init__(self, con) -> None:
        self._con = con
        self.threads: set[int] = set()

    def cursor(self):
        return _Wide(self._con.cursor())

    def __getattr__(self, name):
        import threading

        self.threads.add(threading.get_ident())
        return getattr(self._con, name)


def test_a_wide_connection_profiles_the_same_numbers(con):
    """Width changes how the per-column questions are *asked*, never the answer."""
    frame = messy_customers()
    Table.from_frame(frame, "messy", con)
    one = profile(Table(name="messy", query='SELECT * FROM "messy"', con=con))
    wide = profile(Table(name="messy", query='SELECT * FROM "messy"', con=_Wide(con)))
    assert wide == one


def test_a_wide_connection_asks_on_more_than_one_thread(con):
    import threading

    frame = messy_customers()
    Table.from_frame(frame, "messy", con)
    wide = _Wide(con)
    seen: set[int] = set()
    original = wide.cursor

    def cursor():
        sibling = original()
        seen.add(threading.get_ident())

        class Recording(_Wide):
            def __getattr__(inner, name):
                seen.add(threading.get_ident())
                return getattr(sibling._con, name)

        return Recording(sibling._con)

    wide.cursor = cursor
    profile(Table(name="messy", query='SELECT * FROM "messy"', con=wide))
    assert len(seen - {threading.get_ident()}) >= 2


def test_width_one_never_opens_a_sibling(con):
    class Narrow(_Wide):
        parallel_queries = 1

        def cursor(self):
            raise AssertionError("width one runs on the caller's own connection")

    Table.from_frame(messy_customers(), "messy", con)
    profile(Table(name="messy", query='SELECT * FROM "messy"', con=Narrow(con)))
