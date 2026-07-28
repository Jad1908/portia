"""Post-conditions on a produced table — the check Run 2 didn't have.

The scenario each of these is built around: the copilot lowercased one side of a
join key and not the other, the join matched nothing, and because it had
predicted the row count correctly the only post-hoc check in the system reported
clean. It shipped a training table whose event columns were null in every row
(docs/EVALUATION.md). These tests are the measurement that makes that loud.
"""

import json

import pandas as pd
import pytest

from portia.checks.outcome import (
    ALL_NULL_COLUMN,
    BLOCKING_FLAGS,
    EMPTY_OUTPUT,
    GRAIN_COLUMNS_MISSING,
    GRAIN_EXAMPLES,
    GRAIN_NOT_UNIQUE,
    NO_CONTRIBUTION,
    outcome_report,
    render_outcome,
)
from portia.ops import apply_join


@pytest.fixture
def bookings():
    return pd.DataFrame(
        {
            "hotel_id": ["H001", "H001", "H002"],
            "stay_date": ["2026-06-12", "2026-06-13", "2026-06-12"],
            "revenue": [100, 120, 90],
        }
    )


@pytest.fixture
def events():
    """City-level events. Amsterdam has two on the same day — the planted fan-out."""
    return pd.DataFrame(
        {
            "hotel_id": ["H001", "H001", "H002"],
            "stay_date": ["2026-06-12", "2026-06-12", "2026-06-13"],
            "event_name": ["Canal Festival", "Design Week", "Marathon"],
        }
    )


def _report(table, left, right, *, how, keys, grain=None):
    """Run a real join and measure it, the way `spec._run_step` does."""
    lt, rt = table(left, "left"), table(right, "right")
    out = apply_join(lt, rt, how=how, on=keys)
    return outcome_report(
        out.table,
        inputs={"left": lt, "right": rt},
        keys={"left": keys, "right": keys},
        grain=grain,
    )


# --- the Run 2 failure -------------------------------------------------------


def test_a_source_that_contributed_nothing_is_named(bookings, table):
    """The whole point: a left join onto keys that match nothing.

    Row count is exactly what a correct prediction would have said, and every
    column from the right side is null. `expect`/drift is silent here; this is
    the only thing that isn't.
    """
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    report = _report(table, bookings, unmatched, how="left", keys=["hotel_id"])
    json.dumps(report)

    assert report["n_rows"] == 3  # a perfectly plausible-looking table
    assert report["contribution"]["right"]["contributed"] is False
    assert report["all_null_columns"] == ["event_name"]
    assert NO_CONTRIBUTION in report["flags"]
    assert ALL_NULL_COLUMN in report["flags"]


def test_a_contributing_source_is_not_flagged(bookings, events, table):
    report = _report(table, bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert report["contribution"]["right"]["contributed"] is True
    assert not set(report["flags"]) & BLOCKING_FLAGS


def test_partial_nulls_never_block(bookings, events, table):
    """A left join leaving *some* rows unmatched is ordinary, not a failure.

    The blocking set holds zeros only. Deciding whether 33% coverage is
    acceptable needs the goal, which the engine doesn't have.
    """
    report = _report(table, bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert 0 < report["null_rates"]["event_name"] < 1
    assert not set(report["flags"]) & BLOCKING_FLAGS


def test_a_column_that_was_already_all_null_does_not_block(bookings, table):
    """Otherwise every join carrying an empty source column is a false alarm.

    Only a column that went in with data and came out empty is a consequence of
    *this* step.
    """
    right = pd.DataFrame({"hotel_id": ["H001", "H002"], "note": [None, None]})
    report = _report(table, bookings, right, how="left", keys=["hotel_id"])

    assert report["all_null_columns"] == ["note"]  # still reported as a fact
    assert report["newly_all_null_columns"] == []  # but it isn't news
    assert ALL_NULL_COLUMN not in report["flags"]


def test_an_empty_result_reports_only_that(bookings, table):
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    report = _report(table, bookings, unmatched, how="inner", keys=["hotel_id"])

    assert report["empty"] is True
    # nothing else is measurable on an empty frame, so it doesn't pile on flags
    assert report["flags"] == [EMPTY_OUTPUT]


def test_a_key_only_lookup_is_not_accused_of_contributing_nothing(bookings, table):
    """It contributes by filtering. There are no non-key columns to judge."""
    allowed = pd.DataFrame({"hotel_id": ["H001", "H002"]})
    report = _report(table, bookings, allowed, how="inner", keys=["hotel_id"])

    assert report["contribution"]["right"]["contributed"] is None
    assert NO_CONTRIBUTION not in report["flags"]


def test_colliding_column_names_are_traced_back_to_their_side(table):
    """pandas suffixes a collision `_x`/`_y`; contribution has to follow that."""
    left = pd.DataFrame({"k": [1, 2], "note": ["a", "b"]})
    right = pd.DataFrame({"k": [1, 2], "note": [None, None]})
    report = _report(table, left, right, how="left", keys=["k"])

    assert report["contribution"]["left"]["columns_in_output"] == ["note_x"]
    assert report["contribution"]["left"]["contributed"] is True
    assert report["contribution"]["right"]["columns_in_output"] == ["note_y"]
    assert report["contribution"]["right"]["contributed"] is False


# --- the grain claim ---------------------------------------------------------


def test_the_fan_out_is_caught_after_the_join(bookings, events, table):
    """The fatal trap: two events in one city on one day double-count revenue.

    The result looks entirely plausible — it's a few percent off, not obviously
    broken. The agent claims the grain; code measures whether it holds.
    """
    report = _report(
        table,
        bookings,
        events,
        how="left",
        keys=["hotel_id", "stay_date"],
        grain=["hotel_id", "stay_date"],
    )
    json.dumps(report)

    grain = report["grain"]
    assert grain["unique"] is False
    assert grain["n_duplicated_keys"] == 1
    assert grain["max_multiplicity"] == 2
    assert grain["examples"][0] == {"hotel_id": "H001", "stay_date": "2026-06-12", "n_rows": 2}
    assert GRAIN_NOT_UNIQUE in report["flags"]


def test_a_grain_that_holds_is_reported_clean(bookings, events, table):
    report = _report(
        table, bookings, events, how="left", keys=["hotel_id", "stay_date"], grain=["hotel_id"]
    )
    # one row per hotel is *not* true here — hotel H001 has two stay dates
    assert report["grain"]["unique"] is False

    single = outcome_report(
        table(bookings, "g1"),
        inputs={"b": table(bookings, "g1b")},
        grain=["hotel_id", "stay_date"],
    )
    assert single["grain"]["unique"] is True
    assert single["grain"]["n_distinct"] == 3
    assert not single["flags"]


def test_an_unmeasurable_grain_claim_blocks_rather_than_passing_quietly(bookings, table):
    """A claim about a column that doesn't exist must not read as 'verified'."""
    report = outcome_report(
        table(bookings, "g2"), inputs={"b": table(bookings, "g2b")}, grain=["city"]
    )

    assert report["grain"]["measurable"] is False
    assert report["grain"]["missing_columns"] == ["city"]
    assert GRAIN_COLUMNS_MISSING in report["flags"]


def test_no_grain_claimed_means_nothing_measured(bookings, table):
    report = outcome_report(table(bookings, "g3"), inputs={"b": table(bookings, "g3b")})
    assert report["grain"] is None
    assert not report["flags"]


def test_nulls_in_the_grain_key_are_counted_not_dropped(table):
    frame = pd.DataFrame({"k": [None, None, "a"], "v": [1, 2, 3]})
    report = outcome_report(table(frame, "g4"), inputs={"f": table(frame, "g4b")}, grain=["k"])
    assert report["grain"]["n_duplicated_keys"] == 1  # the two nulls are a group


# --- the layer's own rules ---------------------------------------------------


def test_every_blocking_flag_is_a_zero_condition_not_a_threshold():
    """Guard on the line in `CLAUDE.md`: code owns facts, never what matters.

    A tunable number in the blocking set is code deciding what counts as bad,
    which is exactly the deterministic-planner mistake this project reversed.
    """
    assert BLOCKING_FLAGS == {
        EMPTY_OUTPUT,
        ALL_NULL_COLUMN,
        NO_CONTRIBUTION,
        GRAIN_NOT_UNIQUE,
        GRAIN_COLUMNS_MISSING,
    }


def test_the_report_never_ranks_or_recommends(bookings, events, table):
    report = _report(table, bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert not {"score", "priority", "impact", "severity", "recommendation"} & set(report)


def test_render_is_readable_and_lives_outside_the_check(bookings, table):
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    text = render_outcome(_report(table, bookings, unmatched, how="left", keys=["hotel_id"]))
    assert "contributed no values" in text
    assert NO_CONTRIBUTION in text


# --- the SQL implementation --------------------------------------------------


def _measure(table, frame, inputs, tag, **kw):
    """Measure a produced frame against the inputs that produced it."""
    tables = {n: table(f, f"{tag}_{n}") for n, f in inputs.items()}
    return outcome_report(table(frame, f"{tag}_out"), inputs=tables, **kw)


def test_the_two_implementations_agree_on_a_plain_join(con, table):
    left = pd.DataFrame({"k": [1, 2, 3], "a": ["x", "y", None]})
    right = pd.DataFrame({"k": [1, 2], "b": [10, 20]})
    merged = left.merge(right, on="k", how="left")
    report = _measure(
        table, merged, {"l": left, "r": right}, "plain", keys={"l": ["k"], "r": ["k"]}
    )
    assert report["n_rows"] == 3
    assert report["contribution"]["r"]["contributed"] is True
    assert report["flags"] == []


def test_the_suffix_trap_survives_the_swap(con, table):
    """§6.2: attribution runs on `_x`/`_y`, and it is what fires the blocking flag.

    Both sides have `name`; the right side's copy is entirely null in the output,
    so `r` contributed nothing and the step must be refused.
    """
    left = pd.DataFrame({"k": [1, 2], "name": ["a", "b"]})
    right = pd.DataFrame({"k": [1, 2], "name": [None, None]})
    merged = pd.DataFrame(
        {"k": [1, 2], "name_x": ["a", "b"], "name_y": [None, None]},
    )
    report = _measure(
        table, merged, {"l": left, "r": right}, "suffix", keys={"l": ["k"], "r": ["k"]}
    )
    assert report["contribution"]["l"]["columns_in_output"] == ["name_x"]
    assert report["contribution"]["r"]["columns_in_output"] == ["name_y"]
    assert report["contribution"]["r"]["contributed"] is False
    assert NO_CONTRIBUTION in report["flags"]


def test_an_empty_output_blocks_and_measures_nothing_else(con, table):
    left = pd.DataFrame({"k": [1], "a": ["x"]})
    empty = left.iloc[:0]
    report = _measure(table, empty, {"l": left}, "empty", grain=["k"])
    assert report["flags"] == [EMPTY_OUTPUT]
    assert report["grain"] is None  # a grain claim over zero rows is vacuously true


def test_a_column_that_arrived_full_and_left_empty_blocks(con, table):
    left = pd.DataFrame({"k": [1, 2], "a": ["x", "y"]})
    out = pd.DataFrame({"k": [1, 2], "a": [None, None]})
    report = _measure(table, out, {"l": left}, "wentnull", keys={"l": ["k"]})
    assert report["newly_all_null_columns"] == ["a"]
    assert ALL_NULL_COLUMN in report["flags"]


def test_a_column_that_was_already_empty_is_reported_but_does_not_block(con, table):
    left = pd.DataFrame({"k": [1, 2], "a": [None, None]})
    report = _measure(table, left, {"l": left}, "wasnull", keys={"l": ["k"]})
    assert report["all_null_columns"] == ["a"]
    assert report["newly_all_null_columns"] == []
    assert ALL_NULL_COLUMN not in report["flags"]


def test_a_grain_that_holds(con, table):
    frame = pd.DataFrame({"k": [1, 2, 3], "v": ["a", "b", "c"]})
    report = _measure(table, frame, {"l": frame}, "grainok", grain=["k"])
    assert report["grain"]["unique"] is True
    assert report["grain"]["n_distinct"] == 3
    assert report["grain"]["max_multiplicity"] == 1
    assert report["flags"] == []


def test_a_grain_that_does_not_hold_names_its_worst_offenders(con, table):
    frame = pd.DataFrame({"k": [1, 1, 1, 2, 2, 3], "v": list("abcdef")})
    report = _measure(table, frame, {"l": frame}, "grainbad", grain=["k"])
    assert report["grain"]["unique"] is False
    assert report["grain"]["n_duplicated_keys"] == 2
    assert report["grain"]["max_multiplicity"] == 3
    assert report["grain"]["examples"][0] == {"k": 1, "n_rows": 3}
    assert GRAIN_NOT_UNIQUE in report["flags"]


def test_a_null_in_the_grain_key_is_counted_not_dropped(con, table):
    """SQL groups NULLs together, which is what pandas' `dropna=False` asked for."""
    frame = pd.DataFrame({"k": [None, None, 1], "v": list("abc")})
    report = _measure(table, frame, {"l": frame}, "grainnull", grain=["k"])
    assert report["grain"]["n_distinct"] == 2
    assert report["grain"]["n_duplicated_keys"] == 1


def test_a_grain_naming_a_column_that_is_not_there(con, table):
    frame = pd.DataFrame({"k": [1, 2]})
    report = _measure(table, frame, {"l": frame}, "grainmissing", grain=["nope"])
    assert report["grain"]["measurable"] is False
    assert report["grain"]["missing_columns"] == ["nope"]
    assert GRAIN_COLUMNS_MISSING in report["flags"]


def test_only_the_worst_grain_offenders_are_shown_but_all_are_counted(con, table):
    frame = pd.DataFrame({"k": [i for i in range(8) for _ in range(2)]})
    report = _measure(table, frame, {"l": frame}, "grainmany", grain=["k"])
    assert report["grain"]["n_duplicated_keys"] == 8
    assert len(report["grain"]["examples"]) == GRAIN_EXAMPLES


def test_a_composite_grain(con, table):
    frame = pd.DataFrame({"a": ["x", "x", "y"], "b": [1, 1, 2]})
    report = _measure(table, frame, {"l": frame}, "graincomp", grain=["a", "b"])
    assert report["grain"]["examples"] == [{"a": "x", "b": 1, "n_rows": 2}]


def test_a_key_only_input_is_not_accused_of_contributing_nothing(con, table):
    left = pd.DataFrame({"k": [1, 2], "a": ["x", "y"]})
    lookup = pd.DataFrame({"k": [1, 2]})
    report = _measure(
        table, left, {"l": left, "lk": lookup}, "keyonly", keys={"l": ["k"], "lk": ["k"]}
    )
    assert report["contribution"]["lk"]["contributed"] is None
    assert NO_CONTRIBUTION not in report["flags"]
