"""The profiler must report the traps the fixtures deliberately plant."""

import json

import pandas as pd
import pytest

from portia.checks.profiling import profile_frame
from portia.core.serialize import to_json
from portia.fixtures import messy_customers


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


def test_output_is_json_serializable(profile):
    # The profile is what the agent will see; it must round-trip through JSON.
    assert json.loads(to_json(profile)) == json.loads(json.dumps(json.loads(to_json(profile))))
