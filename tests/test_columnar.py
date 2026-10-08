"""Per-column evidence as a table — smaller, and with nothing quietly changed.

The encoding exists because a profile the copilot needed was refused for size
(`docs/EVALUATION.md` → "The AQN build run", defect 1). So the tests that matter
are the ones pinning that it is *only* an encoding: every value still reaches the
model, and the two cases where a table format can lie about real data — a value
containing the delimiter, and a null against an empty string — are pinned
against values taken from that run.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from portia.core import columnar
from portia.core.serialize import to_json


def _blocks(rendered: str) -> list[list[str]]:
    """Each `## …` block as its lines, header first."""
    out: list[list[str]] = []
    for line in rendered.splitlines():
        if line.startswith("## "):
            out.append([])
        elif out and line:
            out[-1].append(line)
    return out


def test_the_head_keeps_everything_that_is_not_the_record_list():
    payload = {"source": "golden.csv", "n_rows": 34214, "columns": [{"name": "a"}]}
    head = json.loads(columnar.render(payload, records="columns").splitlines()[0])
    assert head == {"source": "golden.csv", "n_rows": 34214}


def test_records_are_grouped_by_which_fields_they_carry():
    """Three field sets, three blocks — and no cell blank because a field is absent.

    The grouping is derived rather than named here on purpose: `checks/profiling`
    gives a numeric column quartiles, a text column a modal value and a boolean
    neither, and an encoder that hardcoded those three would drop any field added
    there.
    """
    payload = {
        "columns": [
            {"name": "price", "min": 1, "max": 9},
            {"name": "city", "top": "Paris"},
            {"name": "flag"},
            {"name": "qty", "min": 0, "max": 5},
        ]
    }
    blocks = _blocks(columnar.render(payload, records="columns"))
    assert [b[0] for b in blocks] == ["name\tmin\tmax", "name\ttop", "name"]
    assert [len(b) - 1 for b in blocks] == [2, 1, 1]
    for block in blocks:
        width = len(block[0].split("\t"))
        assert all(len(row.split("\t")) == width for row in block[1:])


def test_a_block_heading_counts_against_the_whole_list():
    payload = {"columns": [{"a": 1}, {"a": 2}, {"b": 3}]}
    rendered = columnar.render(payload, records="columns")
    headings = [line for line in rendered.splitlines() if line.startswith("## ")]
    assert headings == ["## 2 of 3 columns", "## 1 of 3 columns"]


#: Real values from the 191-column source in the AQN build run. Each one breaks a
#: naive table encoding in a different way.
@pytest.mark.parametrize(
    "value",
    [
        "\x07\tB&B HOTEL BREMEN ALTSTADT",  # a tab, mid-value
        "\tDARWIN AIRPORT RESORT OPERATING COMPANY",  # a leading tab
        "A&L ROYAUME UNI, EUROPE CENTR",  # a comma
        '"No. 32, Tianzhu Town Fuqian Street, Shunyi District"',  # commas and quotes
        "line\nbreak",
        "back\\slash",
        "\\N",  # the null token, as data
    ],
)
def test_a_value_that_contains_a_delimiter_stays_one_cell(value):
    payload = {"columns": [{"name": "c", "top": value}]}
    rows = _blocks(columnar.render(payload, records="columns"))[0]
    assert len(rows) == 2, "the value broke the block onto extra lines"
    assert len(rows[1].split("\t")) == 2, "the value split into extra cells"


def test_a_value_that_is_literally_the_null_token_is_not_read_as_null():
    payload = {"columns": [{"real_null": None, "the_token": columnar.NULL}]}
    cells = _blocks(columnar.render(payload, records="columns"))[0][1].split("\t")
    assert cells[0] == columnar.NULL
    assert cells[1] != cells[0], "a value of '\\N' is indistinguishable from a null"


def test_a_null_is_not_an_empty_string():
    """`core/present.py` settled this for the human surfaces; the model gets it too.

    `checks/profiling` leaves `std` as None when DuckDB returns none, and
    `agent/handlers.profile_source` sets `role` to None for every step-output
    profile, so a present-but-null field is reachable rather than theoretical.
    """
    payload = {"columns": [{"std": None, "role": ""}]}
    cells = _blocks(columnar.render(payload, records="columns"))[0][1].split("\t")
    assert cells == [columnar.NULL, ""]


def test_a_list_cell_survives_a_value_containing_the_list_separator():
    """Joining on a comma cannot say whether this is one sample or two."""
    samples = ["A&L ROYAUME UNI, EUROPE CENTR", "PARIS"]
    payload = {"columns": [{"samples": samples}]}
    cell = _blocks(columnar.render(payload, records="columns"))[0][1]
    assert json.loads(cell) == samples


def test_numpy_scalars_are_coerced_like_everywhere_else():
    """`to_jsonable`'s job, not a second implementation of it (CLAUDE.md)."""
    payload = {"columns": [{"n": np.int64(7), "rate": np.float64(1.23456789), "xs": [np.int64(1)]}]}
    cells = _blocks(columnar.render(payload, records="columns"))[0][1].split("\t")
    assert cells == ["7", "1.2346", "[1]"]


def test_an_empty_record_list_still_renders_its_head():
    rendered = columnar.render({"source": "empty.csv", "columns": []}, records="columns")
    assert json.loads(rendered.strip()) == {"source": "empty.csv"}


def test_every_value_still_reaches_the_model():
    """The claim the whole change rests on: this is an encoding, not a summary."""
    payload = {
        "source": "golden.csv",
        "n_rows": 3,
        "columns": [
            {"name": "a", "null_rate": 0.5, "samples": ["x", "y"], "flags": ["high_null"]},
            {"name": "b", "min": 1, "max": 2, "samples": [1, 2], "flags": []},
        ],
    }
    rendered = columnar.render(payload, records="columns")
    for record in payload["columns"]:
        for key, value in record.items():
            assert key in rendered
            for token in value if isinstance(value, list) else [value]:
                assert str(token) in rendered


def test_the_table_is_materially_smaller_than_the_json_it_replaces():
    """The success criterion is a character count; keep it from silently regressing."""
    payload = {
        "source": "wide.csv",
        "columns": [
            {
                "name": f"column_{i}",
                "dtype": "VARCHAR",
                "inferred": "text",
                "n_null": i,
                "null_rate": 0.1,
                "n_distinct": 40,
                "distinct_rate": 0.9,
                "samples": ["a", "b", "c"],
                "top": "a",
                "top_freq": 4,
                "flags": ["high_cardinality"],
                "role": "identifier",
            }
            for i in range(60)
        ],
    }
    rendered = columnar.render(payload, records="columns")
    assert len(rendered) < len(to_json(payload)) / 2


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
