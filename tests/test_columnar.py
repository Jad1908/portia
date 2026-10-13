"""A report too wide to send whole, at a coarser grain, with nothing quietly dropped.

`core/columnar.py` was first an encoding, per-column evidence as a header and a
tab-separated line each, for the two column rungs. Those answer with cards now
(`core/listing.py`, 2026-10-13) and the encoding went with them. What is left
is what `read_spec`, `record_step` and `run_spec` still send past the budget: a
built table's columns grouped by type, and a report's long runs given as their
count and their ends. The tests that matter pin that every column and every
item is still counted.
"""

from __future__ import annotations

from portia.core import columnar
from portia.core.serialize import to_json

# --- the grouped form, for a table too wide to list (2026-10-08) -------------------------


def _profiled(name: str, inferred: str, **facts) -> dict:
    """One record in the shape `profile_source` sends."""
    return {"name": name, "inferred": inferred, "role": None, "flags": [], **facts}


def _wide() -> dict:
    """A datetime, 2,000 numeric columns, two text columns, and three empty ones."""
    columns = [_profiled("read_at", "datetime", null_rate=0.0, n_distinct=48)]
    columns += [
        _profiled(
            f"meter_{i:04d}",
            "float",
            null_rate=round(i / 2000, 4),
            n_distinct=i + 1,
            min=-i,
            max=i * 10,
            flags=["high_null"] if i >= 1000 else [],
        )
        for i in range(2000)
    ]
    columns += [_profiled(f"site_{i}", "categorical", null_rate=0.0, n_distinct=3) for i in (1, 2)]
    columns += [_profiled(f"empty_{i}", "empty", null_rate=1.0, flags=["all_null"]) for i in "abc"]
    return {"source": "wide.csv", "n_rows": 48, "n_cols": len(columns), "columns": columns}


_PROFILE_FIELDS = {
    "typed_by": ("inferred",),
    "tallied": ("role", "flags"),
    "spread": ("null_rate", "n_distinct"),
    "lowest": ("min",),
    "highest": ("max",),
}


def test_every_column_is_counted_in_exactly_one_group():
    """What makes the grouped form a complete answer at a coarser grain, rather
    than the silently short one the budget exists to stop."""
    payload = _wide()
    groups = columnar.by_type(payload, records="columns", **_PROFILE_FIELDS)["columns_by_type"]

    assert sum(g["n_columns"] for g in groups) == len(payload["columns"])
    assert [g["inferred"] for g in groups] == ["datetime", "float", "categorical", "empty"]


def test_the_head_is_kept_and_the_records_are_replaced_by_their_groups():
    grouped = columnar.by_type(_wide(), records="columns", **_PROFILE_FIELDS)
    assert list(grouped) == ["source", "n_rows", "n_cols", "columns_by_type"]


def test_a_small_group_is_listed_record_by_record_as_it_came():
    payload = _wide()
    groups = columnar.by_type(payload, records="columns", **_PROFILE_FIELDS)["columns_by_type"]
    text = next(g for g in groups if g["inferred"] == "categorical")

    assert text["columns"] == payload["columns"][2001:2003]


def test_a_group_one_past_the_listed_size_is_summarised():
    records = [_profiled(f"c{i}", "integer") for i in range(columnar.LISTED_GROUP + 1)]
    listed = columnar.by_type({"columns": records[:-1]}, records="columns", typed_by=("inferred",))
    summarised = columnar.by_type({"columns": records}, records="columns", typed_by=("inferred",))

    assert "columns" in listed["columns_by_type"][0]
    assert "columns" not in summarised["columns_by_type"][0]


def test_a_big_group_names_its_ends_in_table_order_and_ranks_nothing():
    """The first and last names in the file, never the most anything."""
    groups = columnar.by_type(_wide(), records="columns", **_PROFILE_FIELDS)["columns_by_type"]
    floats = next(g for g in groups if g["inferred"] == "float")

    assert floats["first"] == [f"meter_{i:04d}" for i in range(columnar.GROUP_EXAMPLES)]
    assert floats["last"] == [f"meter_{i:04d}" for i in range(1995, 2000)]
    assert floats["n_columns"] == 2000


def test_a_tally_counts_each_value_and_leaves_out_the_nulls():
    records = [
        _profiled(f"c{i}", "float", role="measure" if i < 3 else None, flags=flags)
        for i, flags in enumerate([["constant"]] * 2 + [["high_null", "constant"]] * 10)
    ]
    (group,) = columnar.by_type(
        {"columns": records}, records="columns", typed_by=("inferred",), tallied=("role", "flags")
    )["columns_by_type"]

    assert group["role"] == {"measure": 3}
    assert group["flags"] == {"constant": 12, "high_null": 10}
    assert list(group["flags"]) == ["constant", "high_null"], "order of first appearance"


def test_a_spread_is_interpolated_as_the_profilers_quartiles_are():
    """The same 2,000 numbers profiled as a column give the same five, so a
    spread and a column's quartiles mean one thing."""
    from portia.checks import profiling
    from portia.core.io import connect
    from portia.core.table import Table

    groups = columnar.by_type(_wide(), records="columns", **_PROFILE_FIELDS)["columns_by_type"]
    floats = next(g for g in groups if g["inferred"] == "float")
    as_column = Table("n_distinct", "SELECT range + 1 AS v FROM range(2000)", connect())
    (measured,) = profiling.profile(as_column)["columns"]

    assert floats["n_distinct"] == {
        "min": measured["min"],
        "q25": measured["q25"],
        "median": measured["median"],
        "q75": measured["q75"],
        "max": measured["max"],
    }


def test_a_range_is_the_lowest_and_highest_value_any_column_holds():
    groups = columnar.by_type(_wide(), records="columns", **_PROFILE_FIELDS)["columns_by_type"]
    floats = next(g for g in groups if g["inferred"] == "float")

    assert (floats["min"], floats["max"]) == (-1999, 19990)


def test_a_field_no_record_carries_a_number_for_is_left_out_of_the_summary():
    """A datetime column has no `min` in a profile, so its group has no range to report."""
    records = [_profiled(f"t{i}", "datetime", null_rate=0.0) for i in range(12)]
    (group,) = columnar.by_type({"columns": records}, records="columns", **_PROFILE_FIELDS)[
        "columns_by_type"
    ]

    assert "min" not in group and "max" not in group
    assert group["null_rate"]["max"] == 0.0


def test_a_table_nobody_profiled_groups_on_its_declared_type():
    """`describe_source` on a scoped warehouse table sends `dtype`, not `inferred`."""
    records = [
        {"name": f"c{i}", "role": None, "dtype": "NUMBER(38,0)", "flags": []} for i in range(3)
    ]
    (group,) = columnar.by_type(
        {"columns": records}, records="columns", typed_by=("inferred", "dtype")
    )["columns_by_type"]

    assert group["dtype"] == "NUMBER(38,0)" and group["n_columns"] == 3


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
