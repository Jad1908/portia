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


def _report(left, right, *, how, keys, grain=None):
    """Run a real join and measure it, the way `spec._run_step` does."""
    out = apply_join(left, right, how=how, on=keys)
    return outcome_report(
        out.frame,
        inputs={"left": left, "right": right},
        keys={"left": keys, "right": keys},
        grain=grain,
    )


# --- the Run 2 failure -------------------------------------------------------


def test_a_source_that_contributed_nothing_is_named(bookings):
    """The whole point: a left join onto keys that match nothing.

    Row count is exactly what a correct prediction would have said, and every
    column from the right side is null. `expect`/drift is silent here; this is
    the only thing that isn't.
    """
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    report = _report(bookings, unmatched, how="left", keys=["hotel_id"])
    json.dumps(report)

    assert report["n_rows"] == 3  # a perfectly plausible-looking table
    assert report["contribution"]["right"]["contributed"] is False
    assert report["all_null_columns"] == ["event_name"]
    assert NO_CONTRIBUTION in report["flags"]
    assert ALL_NULL_COLUMN in report["flags"]


def test_a_contributing_source_is_not_flagged(bookings, events):
    report = _report(bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert report["contribution"]["right"]["contributed"] is True
    assert not set(report["flags"]) & BLOCKING_FLAGS


def test_partial_nulls_never_block(bookings, events):
    """A left join leaving *some* rows unmatched is ordinary, not a failure.

    The blocking set holds zeros only. Deciding whether 33% coverage is
    acceptable needs the goal, which the engine doesn't have.
    """
    report = _report(bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert 0 < report["null_rates"]["event_name"] < 1
    assert not set(report["flags"]) & BLOCKING_FLAGS


def test_a_column_that_was_already_all_null_does_not_block(bookings):
    """Otherwise every join carrying an empty source column is a false alarm.

    Only a column that went in with data and came out empty is a consequence of
    *this* step.
    """
    right = pd.DataFrame({"hotel_id": ["H001", "H002"], "note": [None, None]})
    report = _report(bookings, right, how="left", keys=["hotel_id"])

    assert report["all_null_columns"] == ["note"]  # still reported as a fact
    assert report["newly_all_null_columns"] == []  # but it isn't news
    assert ALL_NULL_COLUMN not in report["flags"]


def test_an_empty_result_reports_only_that(bookings):
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    report = _report(bookings, unmatched, how="inner", keys=["hotel_id"])

    assert report["empty"] is True
    # nothing else is measurable on an empty frame, so it doesn't pile on flags
    assert report["flags"] == [EMPTY_OUTPUT]


def test_a_key_only_lookup_is_not_accused_of_contributing_nothing(bookings):
    """It contributes by filtering. There are no non-key columns to judge."""
    allowed = pd.DataFrame({"hotel_id": ["H001", "H002"]})
    report = _report(bookings, allowed, how="inner", keys=["hotel_id"])

    assert report["contribution"]["right"]["contributed"] is None
    assert NO_CONTRIBUTION not in report["flags"]


def test_colliding_column_names_are_traced_back_to_their_side():
    """pandas suffixes a collision `_x`/`_y`; contribution has to follow that."""
    left = pd.DataFrame({"k": [1, 2], "note": ["a", "b"]})
    right = pd.DataFrame({"k": [1, 2], "note": [None, None]})
    report = _report(left, right, how="left", keys=["k"])

    assert report["contribution"]["left"]["columns_in_output"] == ["note_x"]
    assert report["contribution"]["left"]["contributed"] is True
    assert report["contribution"]["right"]["columns_in_output"] == ["note_y"]
    assert report["contribution"]["right"]["contributed"] is False


# --- the grain claim ---------------------------------------------------------


def test_the_fan_out_is_caught_after_the_join(bookings, events):
    """The fatal trap: two events in one city on one day double-count revenue.

    The result looks entirely plausible — it's a few percent off, not obviously
    broken. The agent claims the grain; code measures whether it holds.
    """
    report = _report(
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


def test_a_grain_that_holds_is_reported_clean(bookings, events):
    report = _report(
        bookings, events, how="left", keys=["hotel_id", "stay_date"], grain=["hotel_id"]
    )
    # one row per hotel is *not* true here — hotel H001 has two stay dates
    assert report["grain"]["unique"] is False

    single = outcome_report(bookings, inputs={"b": bookings}, grain=["hotel_id", "stay_date"])
    assert single["grain"]["unique"] is True
    assert single["grain"]["n_distinct"] == 3
    assert not single["flags"]


def test_an_unmeasurable_grain_claim_blocks_rather_than_passing_quietly(bookings):
    """A claim about a column that doesn't exist must not read as 'verified'."""
    report = outcome_report(bookings, inputs={"b": bookings}, grain=["city"])

    assert report["grain"]["measurable"] is False
    assert report["grain"]["missing_columns"] == ["city"]
    assert GRAIN_COLUMNS_MISSING in report["flags"]


def test_no_grain_claimed_means_nothing_measured(bookings):
    report = outcome_report(bookings, inputs={"b": bookings})
    assert report["grain"] is None
    assert not report["flags"]


def test_nulls_in_the_grain_key_are_counted_not_dropped():
    frame = pd.DataFrame({"k": [None, None, "a"], "v": [1, 2, 3]})
    report = outcome_report(frame, inputs={"f": frame}, grain=["k"])
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


def test_the_report_never_ranks_or_recommends(bookings, events):
    report = _report(bookings, events, how="left", keys=["hotel_id", "stay_date"])
    assert not {"score", "priority", "impact", "severity", "recommendation"} & set(report)


def test_render_is_readable_and_lives_outside_the_check(bookings):
    unmatched = pd.DataFrame({"hotel_id": ["ZZZ"], "event_name": ["Nothing"]})
    text = render_outcome(_report(bookings, unmatched, how="left", keys=["hotel_id"]))
    assert "contributed no values" in text
    assert NO_CONTRIBUTION in text
