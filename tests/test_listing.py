"""The list encoder and the flag rule: every cut announced, every flagged column named while it fits."""

from __future__ import annotations

from decimal import Decimal

import pytest

from portia.checks import profiling
from portia.core import listing
from portia.core.listing import CARD_NAMES, PER_FLAG_NAMES, flagged

# --- listing ------------------------------------------------------------------


def test_a_whole_list_is_its_header_once_and_its_rows_as_lists():
    out = listing.listing(["name", "n"], [["a", 1], ["b", 2]], n=2)
    assert out == {"n": 2, "shown": "1-2", "fields": ["name", "n"], "rows": [["a", 1], ["b", 2]]}


def test_a_cut_list_says_which_rows_and_how_to_ask_for_the_rest():
    out = listing.listing(["name"], [["c"], ["d"]], n=40, start=20, more="38 more; narrow it")
    assert out["shown"] == "21-22"
    assert out["more"] == "38 more; narrow it"


def test_a_cut_with_nothing_saying_so_is_refused():
    with pytest.raises(ValueError, match="left out"):
        listing.listing(["name"], [["a"]], n=2)


def test_more_is_left_off_when_nothing_was_cut():
    out = listing.listing(["name"], [["a"]], n=1, more="never read")
    assert "more" not in out


def test_an_empty_list_shows_none():
    assert listing.listing(["name"], [], n=0)["shown"] == listing.NONE_SHOWN
    past_the_end = listing.listing(["name"], [], n=5, start=5, more="ask from 0")
    assert past_the_end["shown"] == listing.NONE_SHOWN


def test_a_row_of_the_wrong_width_is_refused():
    with pytest.raises(ValueError, match="2 values for 1 fields"):
        listing.listing(["name"], [["a", 1]], n=1)


def test_more_rows_than_the_count_is_refused():
    with pytest.raises(ValueError, match="past the 1"):
        listing.listing(["name"], [["a"], ["b"]], n=1)


def test_values_pass_through_the_one_serializer_and_lists_stay_lists():
    out = listing.listing(
        ["flags", "rate", "price", "missing"],
        [[("constant", "high_null"), 0.123456789, Decimal("1.50"), float("nan")]],
        n=1,
    )
    assert out["rows"] == [[["constant", "high_null"], 0.1235, 1.5, None]]


# --- flagged --------------------------------------------------------------------


def _columns(n: int, flags: dict[str, set[int]]) -> list[dict]:
    """``n`` columns ``c000…``; ``flags`` maps each flag to the positions carrying it."""
    return [
        {"name": f"c{i:03d}", "flags": [f for f, at in flags.items() if i in at]} for i in range(n)
    ]


def test_a_flag_few_columns_carry_names_them():
    out = flagged(_columns(10, {"constant": {2, 5}}))
    assert out == {"constant": {"n": 2, "columns": ["c002", "c005"]}}


def test_a_flag_most_columns_carry_names_the_ones_that_do_not():
    out = flagged(_columns(10, {"high_null": set(range(10)) - {3}}))
    assert out == {"high_null": {"n": 9, "all_but": ["c003"]}}


def test_a_flag_every_column_carries_names_nobody():
    out = flagged(_columns(4, {"high_null": set(range(4))}))
    assert out == {"high_null": {"n": 4, "all_but": []}}


def test_a_flag_on_exactly_half_lists_the_columns_that_carry_it():
    out = flagged(_columns(4, {"constant": {0, 1}}))
    assert out == {"constant": {"n": 2, "columns": ["c000", "c001"]}}


def test_a_flag_too_common_on_both_sides_is_a_count():
    width = 2 * PER_FLAG_NAMES + 10
    out = flagged(_columns(width, {"high_null": set(range(PER_FLAG_NAMES + 5))}))
    assert out == {"high_null": {"n": PER_FLAG_NAMES + 5, "listed": False}}


def test_several_flags_each_name_their_own_side():
    out = flagged(
        _columns(20, {"constant": {1}, "high_null": set(range(20)) - {0, 1}, "mixed_types": {7}})
    )
    assert out == {
        "constant": {"n": 1, "columns": ["c001"]},
        "high_null": {"n": 18, "all_but": ["c000", "c001"]},
        "mixed_types": {"n": 1, "columns": ["c007"]},
    }


def test_a_column_carrying_two_flags_is_named_under_both():
    out = flagged(_columns(10, {"constant": {4}, "high_null": {4, 6}}))
    assert out["constant"]["columns"] == ["c004"]
    assert out["high_null"]["columns"] == ["c004", "c006"]


def test_past_the_card_cap_the_longest_lists_become_counts_until_the_rest_fit():
    # Three flags of 90, 80 and 70 names, and one of 75: 315 names, over a cap of 300.
    sizes = {"constant": 90, "high_null": 80, "mixed_types": 70, "high_cardinality": 75}
    width = 1000
    start, flags = 0, {}
    for flag, size in sizes.items():
        flags[flag] = set(range(start, start + size))
        start += size
    assert sum(sizes.values()) > CARD_NAMES
    out = flagged(_columns(width, flags))
    assert out["constant"] == {"n": 90, "listed": False}  # the longest, and only it
    assert [len(out[f].get("columns", [])) for f in ("high_null", "mixed_types")] == [80, 70]
    assert len(out["high_cardinality"]["columns"]) == 75


def test_the_cap_turns_lists_into_counts_one_at_a_time_longest_first():
    flags = {f"f{k}": set(range(k * 100, k * 100 + 99)) for k in range(5)}  # five lists of 99
    out = flagged(_columns(1000, flags))
    # 495 names: two must go, and in a tie the earlier flag goes first.
    assert [out[f].get("listed", True) for f in flags] == [False, False, True, True, True]


def test_flags_follow_the_vocabulary_order_given_and_unknown_flags_come_after():
    columns = [
        {"name": "a", "flags": ["high_cardinality"]},
        {"name": "b", "flags": ["newly_seen"]},
        {"name": "c", "flags": ["constant"]},
    ]
    out = flagged(columns, order=profiling.FLAGS)
    assert list(out) == ["constant", "high_cardinality", "newly_seen"]
    assert list(flagged(columns)) == ["high_cardinality", "newly_seen", "constant"]


def test_columns_without_flags_or_with_none_count_toward_the_total():
    columns = [{"name": "a", "flags": None}, {"name": "b"}, {"name": "c", "flags": ["constant"]}]
    assert flagged(columns) == {"constant": {"n": 1, "columns": ["c"]}}


def test_the_profilers_flags_are_named_in_its_vocabulary_and_in_its_order(table):
    import pandas as pd

    frame = pd.DataFrame(
        {
            "empty": [None, None, None, None],
            "same": ["x", "x", "x", None],
            "mixed": [" 1", "a", "3", None],
        }
    )
    profile = profiling.profile(table(frame, "flags"))
    for column in profile["columns"]:
        assert set(column["flags"]) <= set(profiling.FLAGS)
        positions = [profiling.FLAGS.index(f) for f in column["flags"]]
        assert positions == sorted(positions)


# --- near misses ---------------------------------------------------------------


def test_a_near_miss_differing_only_in_case_comes_first_and_the_rest_are_capped():
    names = ["ORDERS", "orders_eu", "orders_us", "orders_uk", "customers"]
    assert listing.near_misses("orders", names) == ["ORDERS", "orders_eu", "orders_us"]
    assert len(listing.near_misses("orders", names, n=5)) == 5 - 1, "customers is not close"


def test_a_tie_goes_to_the_name_given_first():
    """`difflib` kept the alphabetically last of two equally close names."""
    names = [f"t{i:03d}" for i in range(1, 101)]
    assert listing.near_misses("t07", names)[0] == "t007"
    assert listing.near_misses("t07", ["t057", "t007"]) == ["t057", "t007"], "equally close"


def test_the_closest_still_comes_before_the_first_given():
    assert listing.near_misses("meter_12", ["meter_1", "meter_12a", "meter_123"]) == [
        "meter_12a",
        "meter_123",
        "meter_1",
    ]


def test_a_pattern_matches_without_case_and_no_pattern_matches_everything():
    assert listing.matches("METER_01", "meter_*")
    assert listing.matches("meter_01", " Meter_0? ")
    assert not listing.matches("site_01", "meter_*")
    assert listing.matches("anything", None) and listing.matches("anything", "")
    assert (
        listing.is_pattern("north_*") and listing.is_pattern("t0?") and listing.is_pattern("[ab]")
    )
    assert not listing.is_pattern("timestamp")


def test_nothing_close_is_an_empty_list():
    assert listing.near_misses("portia_columns", [f"account_{i}" for i in range(80)]) == []
