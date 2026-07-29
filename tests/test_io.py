"""The canonical loader dispatches by format and refuses the unknown."""

from pathlib import Path

import pandas as pd
import pytest

from portia.checks.profiling import profile_path
from portia.core import store
from portia.core.io import (
    NA_TOKENS,
    load_frame,
    load_table,
    supported_suffixes,
    write_table,
)

MOCK = Path(__file__).resolve().parents[1] / "data" / "mock"


def test_loads_csv(tmp_path):
    p = tmp_path / "t.csv"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(p, index=False)
    df = load_frame(p)
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_unsupported_format_raises_clearly(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_bytes(b"not really excel")
    with pytest.raises(ValueError, match="unsupported data format"):
        load_frame(p)


def test_csv_is_supported():
    assert ".csv" in supported_suffixes()


def test_profile_path_round_trips(tmp_path):
    p = tmp_path / "nums.csv"
    pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]}).to_csv(p, index=False)
    prof = profile_path(p)
    assert prof["source"] == str(p)
    assert prof["columns"][0]["mean"] == 2.5


def test_load_table_is_lazy_and_reads_the_same_rows(tmp_path):
    p = tmp_path / "t.csv"
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(p, index=False)
    con = store.memory()
    try:
        assert load_table(p, con).count() == 3
    finally:
        con.close()


def test_the_two_tiers_agree_on_what_missing_looks_like(tmp_path):
    """`NA_TOKENS` is a copy of pandas' default set, and copies drift.

    Both directions matter. If pandas starts nulling something we don't list,
    DuckDB reads a value where pandas reads a gap; if we list something pandas
    keeps, DuckDB invents a gap. Either way a null rate would depend on which
    reader ran, and null rates are what the copilot decides on.
    """
    con = store.memory()
    try:
        for token in NA_TOKENS:
            p = tmp_path / "na.csv"
            p.write_text(f"k,v\n1,x\n2,{token}\n")
            assert bool(load_frame(p)["v"].isna().iloc[1]), f"pandas keeps {token!r}"
            assert load_table(p, con).scalar("count(v)") == 1, f"duckdb keeps {token!r}"

        for token in ("-", "?", "NIL", "na", "Null", "missing"):
            p = tmp_path / "kept.csv"
            p.write_text(f"k,v\n1,x\n2,{token}\n")
            assert not bool(load_frame(p)["v"].isna().iloc[1]), f"pandas nulls {token!r}"
            assert load_table(p, con).scalar("count(v)") == 2, f"duckdb nulls {token!r}"
    finally:
        con.close()


# --- parquet -----------------------------------------------------------------


def test_parquet_is_supported_on_both_halves(tmp_path):
    """A format you can load and not save is a trap you find at the end of a run."""
    assert ".parquet" in supported_suffixes()
    con = store.memory()
    try:
        source = load_table(MOCK / "messy_customers.csv", con)
        out = write_table(source, tmp_path / "messy.parquet")
        assert out.exists()
        assert load_table(out, con).count() == source.count()
        assert len(load_frame(out)) == 40  # the pandas half works too
    finally:
        con.close()


def test_a_round_trip_through_parquet_keeps_the_schema(tmp_path):
    """The point of converting: the CSV reader stops guessing.

    `signup_amount` is numbers-with-whitespace plus a `pending`, and its
    text-ness *is* the finding. Parquet carries that rather than re-sniffing it.
    """
    con = store.memory()
    try:
        before = load_table(MOCK / "messy_customers.csv", con)
        after = load_table(write_table(before, tmp_path / "m.parquet"), con)
        assert after.dtypes == before.dtypes
        assert after.dtypes["signup_amount"] == "VARCHAR"
    finally:
        con.close()


def test_the_same_evidence_comes_out_of_either_format(tmp_path):
    """Converting must not change what the copilot reads."""
    from portia.checks.profiling import profile

    con = store.memory()
    try:
        csv = load_table(MOCK / "messy_customers.csv", con)
        parquet = load_table(write_table(csv, tmp_path / "m.parquet"), con)
        assert profile(parquet) == profile(csv)
    finally:
        con.close()


def test_writing_an_unsupported_format_says_so(tmp_path):
    con = store.memory()
    try:
        with pytest.raises(ValueError, match="unsupported data format"):
            write_table(load_table(MOCK / "hotels.csv", con), tmp_path / "out.xlsx")
    finally:
        con.close()


def test_a_parquet_source_indexes_like_any_other(tmp_path):
    """Ingest dispatches on the extension, so a project can hold either."""
    con = store.memory()
    try:
        write_table(load_table(MOCK / "otb.csv", con), tmp_path / "otb.parquet")
        store.ingest(con, tmp_path / "otb.parquet", name="otb")
        assert store.table(con, "otb").count() == 14
    finally:
        con.close()
