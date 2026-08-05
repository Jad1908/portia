"""`column_overlap` — do two columns share values, and how far.

The measurement the knowledge graph's `OVERLAPS` edge carries. No database
needed: this is a check like any other, and it lives in `checks/join.py` because
comparing two sets of values at scale is what that module already does.

The case worth reading is `test_a_true_zero_on_two_columns_that_mean_the_same
_thing` — `France` against `FRA`. The number is right and the number is
misleading, which is the whole reason §4.4 puts the agent's reason on the edge.
"""

from __future__ import annotations

import pandas as pd
import pytest

from portia.checks.join import column_overlap, render_overlap


def test_overlap_is_not_symmetric_and_both_numbers_are_reported(table):
    """§4.3 — 98% of orders' ids may exist in customers while 40% of customers
    appear in orders. Two different questions, one edge, neither ranked."""
    orders = table(pd.DataFrame({"customer_id": ["C1", "C1", "C2"]}), "orders")
    customers = table(pd.DataFrame({"id": ["C1", "C2", "C3", "C4"]}), "customers")

    overlap = column_overlap(orders, "customer_id", customers, "id")
    assert overlap["left_coverage"] == 1.0
    assert overlap["right_coverage"] == 0.5
    assert overlap["n_shared_values"] == 2
    assert overlap["n_right_only_values"] == 2


def test_a_true_zero_on_two_columns_that_mean_the_same_thing(table):
    """§4.4 — the most misleading fact in a project, measured correctly.

    Nothing here is wrong: these columns share no values. They are also the same
    thing after a mapping, and that reading is the agent's, which is why the
    edge carries the reason it was asked for.
    """
    names = table(pd.DataFrame({"country_name": ["France", "Germany"]}), "names")
    codes = table(pd.DataFrame({"country_code": ["FRA", "DEU"]}), "codes")

    overlap = column_overlap(names, "country_name", codes, "country_code")
    assert overlap["n_shared_values"] == 0
    assert overlap["left_coverage"] == 0.0
    # …and it is a *comparable* zero, which is what separates it from the case
    # below: these two could have matched and didn't.
    assert overlap["comparable_types"] is True


def test_different_types_are_reported_rather_than_crashing(table):
    """A zero between text and a number says nothing about the values.

    `checks/join.py` compares as text when the kinds differ, precisely so the
    report cannot raise — the same care, reached through the same code.
    """
    text = table(pd.DataFrame({"k": ["1", "2"]}), "as_text")
    numbers = table(pd.DataFrame({"k": [1, 2]}), "as_numbers")

    overlap = column_overlap(text, "k", numbers, "k")
    assert overlap["comparable_types"] is False
    assert overlap["left"]["type"] == "string"
    assert overlap["right"]["type"] == "numeric"


def test_nulls_are_counted_and_never_match(table):
    left = table(pd.DataFrame({"k": ["a", None, "b"]}), "with_nulls")
    right = table(pd.DataFrame({"k": ["a", "b"]}), "without")

    overlap = column_overlap(left, "k", right, "k")
    assert overlap["left"]["n_null"] == 1
    assert overlap["left"]["n_rows"] == 3
    assert overlap["left_coverage"] == 0.6667


def test_a_missing_column_says_which_side(table):
    left = table(pd.DataFrame({"k": ["a"]}), "l")
    right = table(pd.DataFrame({"k": ["a"]}), "r")
    with pytest.raises(ValueError, match="right table is missing"):
        column_overlap(left, "k", right, "nope")


def test_rendering_states_numbers_and_never_a_verdict(table):
    left = table(pd.DataFrame({"k": ["a", "b"]}), "l")
    right = table(pd.DataFrame({"k": ["a"]}), "r")
    line = render_overlap(column_overlap(left, "k", right, "k"))
    assert "1 shared value(s)" in line
    assert "50% of left rows" in line
    # No "good", "weak", "candidate" — the check reports and the agent judges.
    assert not any(word in line.lower() for word in ("good", "weak", "best", "likely"))
