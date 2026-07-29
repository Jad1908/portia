"""The canonical loader dispatches by format and refuses the unknown."""

import pandas as pd
import pytest

from portia.checks.profiling import profile_path
from portia.core import store
from portia.core.io import NA_TOKENS, load_frame, load_table, supported_suffixes


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
