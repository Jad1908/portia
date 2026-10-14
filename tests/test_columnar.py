"""A report too wide to send whole, at a coarser grain, with nothing quietly dropped.

`core/columnar.py` was first an encoding, per-column evidence as a header and a
tab-separated line each, for the two column rungs. Those answer with cards now
(`core/listing.py`, 2026-10-13) and the encoding went with them, and so did the
grouped form `read_spec` sent a built table's columns in. What is left is what
`run_spec` still sends past the budget: a report's long runs given as their
count and their ends. The tests that matter pin that every item is still
counted.
"""

from __future__ import annotations

from portia.core import columnar
from portia.core.serialize import to_json

# --- a report whose width is the table's, shortened (2026-10-08) -------------


def _outcome(n: int) -> dict:
    """An outcome report over ``n`` columns, the shape `record_step` sends."""
    names = [f"meter_{i:04d}" for i in range(n)]
    return {
        "n_rows": 24,
        "n_cols": n,
        "null_rates": {name: round(0.01 * (i % 7), 2) or 0.5 for i, name in enumerate(names)},
        "all_null_columns": [],
        "contribution": {"meters": {"columns_in_output": names, "n_columns": n}},
        "flags": [],
    }


def test_a_long_run_of_names_is_its_count_and_its_ends():
    short = columnar.shorten(_outcome(1579))
    run = short["contribution"]["meters"]["columns_in_output"]

    assert run == {
        "count": 1579,
        "first": [f"meter_{i:04d}" for i in range(5)],
        "last": [f"meter_{i:04d}" for i in range(1574, 1579)],
    }
    assert short["contribution"]["meters"]["n_columns"] == 1579


def test_a_long_map_of_numbers_keeps_its_ends_and_their_spread():
    rates = columnar.shorten(_outcome(1579))["null_rates"]

    assert rates["count"] == 1579
    assert list(rates["first"]) == [f"meter_{i:04d}" for i in range(5)]
    assert rates["first"]["meter_0001"] == 0.01
    assert set(rates["spread"]) == set(columnar.SPREAD)
    assert rates["spread"]["max"] == 0.5


def test_what_is_short_or_not_a_run_is_kept_as_it_was():
    """Ten names are cheaper than their summary, and a list of records is not a run."""
    report = _outcome(10)
    report["steps"] = [{"id": f"s{i}", "flags": []} for i in range(30)]

    assert columnar.shorten(report) == report


def test_shortening_is_small_enough_and_says_nothing_new():
    report = _outcome(1579)
    short = columnar.shorten(report)

    assert len(to_json(short)) < len(to_json(report)) / 20
    assert {k: short[k] for k in ("n_rows", "n_cols", "flags")} == {
        k: report[k] for k in ("n_rows", "n_cols", "flags")
    }


def test_a_spread_is_interpolated_as_the_profilers_quartiles_are():
    """The same 2,000 numbers profiled as a column give the same five, so a
    spread and a column's quartiles mean one thing."""
    from portia.checks import profiling
    from portia.core.io import connect
    from portia.core.table import Table

    numbers = {f"meter_{i:04d}": i + 1 for i in range(2000)}
    as_column = Table("n_distinct", "SELECT range + 1 AS v FROM range(2000)", connect())
    (measured,) = profiling.profile(as_column)["columns"]

    assert columnar.shorten(numbers)["spread"] == {
        "min": measured["min"],
        "q25": measured["q25"],
        "median": measured["median"],
        "q75": measured["q75"],
        "max": measured["max"],
    }
