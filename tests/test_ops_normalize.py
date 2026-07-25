"""normalize must fix columns and report exactly what it changed / failed."""

import pandas as pd
import pytest
from pandas.api import types as ptypes

from portia.fixtures import messy_customers
from portia.ops import apply_normalize
from portia.spec import run_spec


def _rec(result, column):
    return next(r for r in result.provenance["transforms"] if r["column"] == column)


def test_to_numeric_reports_failures_not_silently():
    df = pd.DataFrame({"amt": ["1", " 2 ", "N/A", "4"]})
    res = apply_normalize(df, [{"column": "amt", "op": "to_numeric"}])
    assert ptypes.is_numeric_dtype(res.frame["amt"])
    assert list(res.frame["amt"].dropna()) == [1.0, 2.0, 4.0]  # whitespace parsed too
    rec = _rec(res, "amt")
    assert rec["n_converted"] == 3 and rec["n_failed"] == 1
    assert rec["sample_failed"] == ["N/A"]
    assert "coercion_failures" in res.provenance["flags"]


def test_to_numeric_with_fill():
    df = pd.DataFrame({"amt": ["1", "N/A", "3"]})
    res = apply_normalize(df, [{"column": "amt", "op": "to_numeric", "fill": 0}])
    assert list(res.frame["amt"]) == [1.0, 0.0, 3.0]


def test_strip_counts_changes():
    df = pd.DataFrame({"c": ["a ", " b", "c", "d "]})
    res = apply_normalize(df, [{"column": "c", "op": "strip"}])
    assert list(res.frame["c"]) == ["a", "b", "c", "d"]
    assert _rec(res, "c")["n_changed"] == 3


def test_lower_counts_changes():
    df = pd.DataFrame({"c": ["A", "Bc", "d"]})
    res = apply_normalize(df, [{"column": "c", "op": "lower"}])
    assert list(res.frame["c"]) == ["a", "bc", "d"]
    assert _rec(res, "c")["n_changed"] == 2


def test_to_string_changes_dtype():
    df = pd.DataFrame({"k": [1, 2, 3]})
    res = apply_normalize(df, [{"column": "k", "op": "to_string"}])
    assert ptypes.is_string_dtype(res.frame["k"])
    assert list(res.frame["k"]) == ["1", "2", "3"]


def test_does_not_mutate_input():
    df = pd.DataFrame({"c": ["a ", "b "]})
    apply_normalize(df, [{"column": "c", "op": "strip"}])
    assert list(df["c"]) == ["a ", "b "]  # original untouched


def test_unknown_op_and_missing_column_raise():
    df = pd.DataFrame({"c": ["a"]})
    with pytest.raises(ValueError, match="unknown transform op"):
        apply_normalize(df, [{"column": "c", "op": "titlecase"}])
    with pytest.raises(ValueError, match="no such column"):
        apply_normalize(df, [{"column": "nope", "op": "strip"}])


def test_on_messy_fixture_flags_the_bad_values():
    # messy_customers.signup_amount is numeric-stored-as-text with 'N/A'/'pending'.
    res = apply_normalize(messy_customers(), [{"column": "signup_amount", "op": "to_numeric"}])
    rec = _rec(res, "signup_amount")
    assert rec["n_failed"] == 2
    assert set(rec["sample_failed"]) == {"N/A", "pending"}


def test_normalize_then_join_in_a_spec(tmp_path):
    # Keys differ only by case -> they won't join until lowercased. The spec
    # normalizes both sides, then joins them.
    pd.DataFrame({"k": ["a1", "a2", "x9"], "v": [1, 2, 3]}).to_csv(tmp_path / "l.csv", index=False)
    pd.DataFrame({"k": ["A1", "A2", "Z"], "w": [9, 8, 7]}).to_csv(tmp_path / "r.csv", index=False)
    spec = {
        "version": 1,
        "sources": {"left": "l.csv", "right": "r.csv"},
        "steps": [
            {
                "id": "l_norm",
                "op": "normalize",
                "input": "left",
                "transforms": [{"column": "k", "op": "lower"}],
            },
            {
                "id": "r_norm",
                "op": "normalize",
                "input": "right",
                "transforms": [{"column": "k", "op": "lower"}],
            },
            {
                "id": "joined",
                "op": "join",
                "left": "l_norm",
                "right": "r_norm",
                "keys": ["k"],
                "how": "inner",
                "expect": {"result_rows": 2, "left_dropped": 1, "right_dropped": 1},
            },
        ],
    }
    results = run_spec(spec, base_dir=tmp_path)
    joined = results[-1]
    assert joined.provenance["result_rows"] == 2  # a1, a2 match after lowercasing
    assert joined.has_drift is False


def test_provenance_keys_declaration_matches_reality():
    """See the twin in test_ops_join.py — the declaration must not rot."""
    from portia.ops.normalize import PROVENANCE_KEYS

    result = apply_normalize(messy_customers(), [{"column": "signup_amount", "op": "strip"}])
    assert set(result.provenance) == set(PROVENANCE_KEYS)


def test_transform_ops_declaration_matches_the_dispatch():
    """Callers validate steps against this list, so it must not drift."""
    import pytest as _pytest

    from portia.ops.normalize import TRANSFORM_OPS

    df = messy_customers()
    for op in TRANSFORM_OPS:  # every declared op is accepted
        apply_normalize(df, [{"column": "signup_amount", "op": op}])
    with _pytest.raises(ValueError, match="unknown transform op"):
        apply_normalize(df, [{"column": "signup_amount", "op": "nope"}])
