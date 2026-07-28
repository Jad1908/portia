"""The profiler must report the traps the fixtures deliberately plant."""

import json
from pathlib import Path

import pandas as pd
import pytest

from portia.checks.profiling import (
    null_rates,
    null_rates_table,
    profile_frame,
    profile_table,
)
from portia.core import store
from portia.core.io import load_frame
from portia.core.serialize import to_json
from portia.core.table import Table
from portia.fixtures import messy_customers

MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


@pytest.fixture(scope="module")
def profile() -> dict:
    return profile_frame(messy_customers())


def _col(profile: dict, name: str) -> dict:
    return next(c for c in profile["columns"] if c["name"] == name)


def test_shape(profile):
    assert profile["n_rows"] == 40
    assert profile["n_cols"] == 8


def test_possible_key(profile):
    # possible_key is a pure structural fact (unique & non-null). mixed_ref
    # qualifies too — but the profiler also flags *why* it's a poor key choice,
    # leaving the judgement to the copilot rather than hiding the column.
    assert profile["candidate_keys"] == ["customer_id", "mixed_ref"]
    assert "possible_key" in _col(profile, "customer_id")["flags"]
    mixed = _col(profile, "mixed_ref")
    assert {"possible_key", "mixed_types"} <= set(mixed["flags"])


def test_all_null_column(profile):
    legacy = _col(profile, "legacy_col")
    assert legacy["n_null"] == 40
    assert "all_null" in legacy["flags"]


def test_constant_column(profile):
    source = _col(profile, "source")
    assert source["n_distinct"] == 1
    assert "constant" in source["flags"]


def test_high_null(profile):
    notes = _col(profile, "notes")
    assert notes["null_rate"] >= 0.5
    assert "high_null" in notes["flags"]


def test_numeric_stored_as_text(profile):
    amt = _col(profile, "signup_amount")
    assert amt["inferred"] in {"text", "categorical"}  # not parsed as numeric
    assert "numeric_stored_as_text" in amt["flags"]
    assert "leading_trailing_whitespace" in amt["flags"]


def test_mixed_types(profile):
    assert "mixed_types" in _col(profile, "mixed_ref")["flags"]


def test_high_cardinality_text(profile):
    name = _col(profile, "name")
    assert name["inferred"] == "text"
    assert "high_cardinality" in name["flags"]


def test_numeric_stats_present():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})
    col = profile_frame(df)["columns"][0]
    assert col["min"] == 1.0 and col["max"] == 4.0 and col["mean"] == 2.5


def test_numeric_describe_stats():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    col = profile_frame(df)["columns"][0]
    assert col["q25"] == 2.0 and col["median"] == 3.0 and col["q75"] == 4.0
    assert col["std"] is not None
    assert "top" not in col  # numeric columns report quartiles, not a modal value


def test_std_is_none_for_single_value():
    col = profile_frame(pd.DataFrame({"x": [5]}))["columns"][0]
    assert col["std"] is None  # undefined, and must stay JSON-valid (not NaN)


def test_categorical_top_value():
    col = profile_frame(pd.DataFrame({"c": ["a", "a", "b"]}))["columns"][0]
    assert col["top"] == "a" and col["top_freq"] == 2
    assert "median" not in col  # non-numeric columns don't get quartiles


def test_output_is_json_serializable(profile):
    # The profile is what the agent will see; it must round-trip through JSON.
    assert json.loads(to_json(profile)) == json.loads(json.dumps(json.loads(to_json(profile))))


# --- the SQL implementation --------------------------------------------------


@pytest.fixture
def con():
    c = store.memory()
    yield c
    c.close()


def _table(con, frame, name="t"):
    return Table.from_frame(frame, name, con)


def test_the_two_implementations_agree_on_the_fixtures(con):
    """The migration's whole promise, stated directly rather than only in golden files.

    Everything except the two documented divergences: backend type names, and a
    date column that DuckDB types and pandas does not (`docs/DUCKDB_MIGRATION.md`
    §6.3). If a third one appears, it belongs in that document before it belongs
    in this list.
    """
    for name in ("messy_customers", "sales_customers", "sales_orders", "hotels", "city_events"):
        path = MOCK / f"{name}.csv"
        store.ingest(con, path)
        frame_profile = profile_frame(load_frame(path))
        table_profile = profile_table(store.table(con, name))
        for a, b in zip(frame_profile["columns"], table_profile["columns"], strict=True):
            a.pop("dtype")
            b.pop("dtype")
            if a["name"] == "event_date":
                continue  # the accepted date divergence, pinned in its own test
            assert a == b, f"{name}.{a['name']}"
        assert frame_profile["candidate_keys"] == table_profile["candidate_keys"]
        assert frame_profile["n_rows"] == table_profile["n_rows"]


def test_a_date_column_is_typed_and_says_so(con):
    """The accepted divergence, pinned so it stays deliberate."""
    store.ingest(con, MOCK / "otb.csv")
    col = _col(profile_table(store.table(con, "otb")), "stay_date")
    assert col["dtype"] == "DATE"
    assert col["inferred"] == "datetime"
    # ISO text, not a widened numpy timestamp — evidence carries python values.
    assert col["samples"] == ["2026-06-12", "2026-06-13", "2026-06-14"]


def test_an_empty_table_profiles_without_dividing_by_zero(con):
    frame = pd.DataFrame({"a": pd.Series([], dtype="int64"), "b": pd.Series([], dtype=str)})
    profile = profile_table(_table(con, frame))
    assert profile["n_rows"] == 0
    assert [c["flags"] for c in profile["columns"]] == [["all_null"], ["all_null"]]
    assert all(c["null_rate"] == 0.0 and c["samples"] == [] for c in profile["columns"])


def test_booleans_get_neither_quartiles_nor_a_modal_value(con):
    col = _col(profile_table(_table(con, pd.DataFrame({"ok": [True, False, True]}))), "ok")
    assert col["inferred"] == "boolean"
    assert "median" not in col and "top" not in col


def test_a_single_row_has_no_sample_standard_deviation(con):
    col = _col(profile_table(_table(con, pd.DataFrame({"x": [1.5]}))), "x")
    assert col["std"] is None
    assert col["mean"] == 1.5


def test_decimals_are_numbers_in_the_evidence_not_strings(con):
    """DuckDB returns DECIMAL as `decimal.Decimal`; the str() fallback would lie."""
    con.execute("CREATE TABLE prices AS SELECT * FROM (VALUES (1.5), (2.5)) v(amount)")
    col = _col(profile_table(Table.from_name("prices", con)), "amount")
    assert col["inferred"] == "float"
    assert col["mean"] == 2.0
    assert col["samples"] == [1.5, 2.5]


def test_the_modal_value_breaks_ties_by_value(con):
    """`hotels.city` is Paris 2, Amsterdam 2 — an undefined answer is not a fact."""
    store.ingest(con, MOCK / "hotels.csv")
    assert _col(profile_table(store.table(con, "hotels")), "city")["top"] == "Amsterdam"
    assert _col(profile_frame(load_frame(MOCK / "hotels.csv")), "city")["top"] == "Amsterdam"


def test_samples_are_distinct_and_ordered(con):
    """Both halves of the decision behind SAMPLE_VALUES, on a low-cardinality column."""
    store.ingest(con, MOCK / "messy_customers.csv")
    for profile in (
        profile_table(store.table(con, "messy_customers")),
        profile_frame(load_frame(MOCK / "messy_customers.csv")),
    ):
        assert _col(profile, "country")["samples"] == ["DE", "FR", "UK"]
        # leading whitespace sorts first, which is where a sample earns its keep
        assert _col(profile, "signup_amount")["samples"][0].startswith(" ")


def test_null_rates_agree_across_implementations(con):
    store.ingest(con, MOCK / "messy_customers.csv")
    frame = load_frame(MOCK / "messy_customers.csv")
    assert null_rates_table(store.table(con, "messy_customers")) == null_rates(frame)


def test_null_rates_of_an_empty_table_are_zero_not_undefined(con):
    t = _table(con, pd.DataFrame({"a": pd.Series([], dtype="int64")}))
    assert null_rates_table(t) == {"a": 0.0}


def test_a_column_that_is_partly_numeric_is_mixed(con):
    """`mixed_types`, redefined for a typed store (§6.3)."""
    t = _table(con, pd.DataFrame({"ref": ["1", "2", "abc", "4"]}))
    assert "mixed_types" in _col(profile_table(t), "ref")["flags"]


def test_a_column_that_is_entirely_numeric_or_entirely_text_is_not_mixed(con):
    numeric = _table(con, pd.DataFrame({"a": ["1", "2", "3"]}), "numeric_text")
    text = _table(con, pd.DataFrame({"a": ["x", "y", "z"]}), "plain_text")
    assert "mixed_types" not in _col(profile_table(numeric), "a")["flags"]
    assert "mixed_types" not in _col(profile_table(text), "a")["flags"]
