"""The canonical loader dispatches by format and refuses the unknown."""

import pandas as pd
import pytest

from portia.checks.profiling import profile_path
from portia.core.io import load_frame, supported_suffixes


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
