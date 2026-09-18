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
