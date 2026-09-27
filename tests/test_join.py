"""The join check must predict, from keys alone, exactly what a join would do.

Numbers are hand-computed against the sales fixture (see its docstring):
orders(left) ⋈ customers(right) on customer_id.
"""

import json

import pandas as pd
import pytest

from portia.checks.join import flags_for, join_report, render_text
from portia.core.serialize import to_json
from portia.core.table import Table
from portia.fixtures import city_events, hotels, reservations, sales_customers, sales_orders


@pytest.fixture
def report(table) -> dict:
    return join_report(table(sales_orders()), table(sales_customers()), on="customer_id")


def test_relationship_is_many_to_many(report):
    # 1001 duplicated in customers AND has two orders -> many:many.
    assert report["relationship"] == "many:many"


def test_result_rows_by_join_type(report):
    # inner = Σ mult_left*mult_right over shared keys
    #       = 1000:1*1 + 1001:2*2 + 1002:2*1 + 1003:1*1 = 8
    assert report["joins"]["inner"]["result_rows"] == 8
    assert report["joins"]["left"]["result_rows"] == 10  # + 2 unmatched left
    assert report["joins"]["right"]["result_rows"] == 9  # + 1 unmatched right
    assert report["joins"]["outer"]["result_rows"] == 11  # + both


def test_inner_join_silently_drops_left_rows(report):
    # order 9005 (customer 7777, orphan) + order 9006 (null key) = 2 dropped.
    inner = report["joins"]["inner"]
    assert inner["left_dropped"] == 2
    assert inner["right_dropped"] == 1  # customer 1004 has no orders
    assert "left_rows_dropped" in report["flags"]


def test_fan_out_detected(report):
    assert report["fan_out"]["max_left_to_right"] == 2
    assert "fan_out" in report["flags"]


#: A lookup: every event names one venue, and V1 hosts three of them. Event 5's
#: venue is missing from the dimension, so a left join keeps it with no match.
EVENTS = pd.DataFrame({"event_id": [1, 2, 3, 4, 5], "venue_id": ["V1", "V1", "V1", "V2", "V3"]})
VENUES = pd.DataFrame({"venue_id": ["V1", "V2"], "capacity": [500, 80]})


def test_a_lookup_does_not_fan_out(table):
    """Each event comes out once, so nothing multiplies it; V1 repeating is the lookup working.

    Until 2026-09-27 the flag read either side's key multiplicity, and V1's three
    events raised it on a join whose result holds every event exactly once.
    """
    report = join_report(table(EVENTS), table(VENUES), on="venue_id")
    assert report["relationship"] == "many:1"
    assert report["fan_out"]["max_right_to_left"] == 3  # what used to raise the flag
    assert report["fan_out"]["extra_left_copies"] == 0
    assert report["fan_out"]["extra_right_copies"] == 2  # V1 three times: two past the first
    assert "fan_out" not in report["flags"]


def test_the_same_pair_the_other_way_round_fans_out(table):
    report = join_report(table(VENUES), table(EVENTS), on="venue_id")
    assert report["relationship"] == "1:many"
    assert report["fan_out"]["extra_left_copies"] == 2
    assert "fan_out" in report["flags"]


def test_extra_copies_are_what_a_join_adds_to_the_side_it_keeps(report):
    """Row for row: a left join returns the left's rows plus its extra copies, a right join the right's."""
    fan, joins = report["fan_out"], report["joins"]
    assert joins["left"]["result_rows"] == report["left"]["n_rows"] + fan["extra_left_copies"]
    assert joins["right"]["result_rows"] == report["right"]["n_rows"] + fan["extra_right_copies"]
    assert (fan["extra_left_copies"], fan["extra_right_copies"]) == (2, 3)


@pytest.mark.parametrize(
    "how,fans", [("inner", False), ("left", False), ("right", True), ("outer", True)]
)
def test_a_join_type_reads_fan_out_for_the_side_it_keeps(table, how, fans):
    """A right join keeps the venues, and V1 comes out three times: that is its fan-out."""
    report = join_report(table(EVENTS), table(VENUES), on="venue_id")
    assert ("fan_out" in flags_for(report, how)) is fans
    assert flags_for(report) == report["flags"]


def test_the_extra_copies_are_the_rows_that_double_count(table, con):
    """The hotel fixture's planted fan-out, built and counted, against what the check said.

    Paris (once its spelling is cleaned) and Amsterdam each have two events on
    2026-06-12, so four bookings match two events. The check counts four extra
    copies without building anything; the built join has four more rows than
    bookings, and its revenue is exactly those four bookings' revenue too high.
    """
    res, hot, evt = table(reservations()), table(hotels()), table(city_events())
    bookings = Table(
        "bookings",
        f"SELECT r.*, lower(h.city) AS city FROM ({res.query}) r "
        f"LEFT JOIN ({hot.query}) h USING (hotel_id)",
        con,
    )
    events = Table(
        "events",
        f"SELECT lower(trim(city_name)) AS city, event_date AS stay_date, event_name "
        f"FROM ({evt.query})",
        con,
    )
    report = join_report(bookings, events, on=["city", "stay_date"])
    assert report["fan_out"]["extra_left_copies"] == 4
    assert "fan_out" in report["flags"]

    built = con.execute(
        f"SELECT count(*) - count(DISTINCT b.booking_id), sum(b.revenue) "
        f"FROM ({bookings.query}) b LEFT JOIN ({events.query}) e USING (city, stay_date)"
    ).fetchone()
    doubled = con.execute(
        f"SELECT sum(revenue) FROM ({bookings.query}) "
        f"WHERE booking_id IN ('B0001', 'B0002', 'B0004', 'B0009')"
    ).fetchone()[0]
    total = con.execute(f"SELECT sum(revenue) FROM ({res.query})").fetchone()[0]
    assert built[0] == report["fan_out"]["extra_left_copies"]
    assert built[1] - total == doubled == 5240


def test_null_keys_flagged(report):
    assert report["left"]["n_null_keys"] == 1
    assert "null_keys" in report["flags"]


def test_overlap_and_samples(report):
    ov = report["overlap"]
    assert ov["n_shared_keys"] == 4
    assert ov["n_left_only_keys"] == 1 and ov["n_right_only_keys"] == 1
    assert ov["left_coverage"] == 0.75  # 6 of 8 order rows match
    assert to_jsonable_ok(ov["sample_left_only"])  # 7777 present, JSON-safe


def to_jsonable_ok(values) -> bool:
    json.dumps(values)  # must not raise
    return len(values) == 1


def test_key_dtype_mismatch_flagged(table):
    # '123' (string) never matches 123 (int) — silent zero-match bug.
    left = pd.DataFrame({"k": ["1", "2", "3"], "v": [1, 2, 3]})
    right = pd.DataFrame({"k": [1, 2, 3], "w": [9, 8, 7]})
    rep = join_report(table(left), table(right), on="k")
    assert rep["key_dtype_match"] is False
    assert "key_dtype_mismatch" in rep["flags"]


def test_clean_one_to_one(table):
    left = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"id": [1, 2, 3], "b": [10, 20, 30]})
    rep = join_report(table(left), table(right), on="id")
    assert rep["relationship"] == "1:1"
    assert rep["joins"]["inner"]["result_rows"] == 3
    assert rep["flags"] == []


def test_different_key_names(table):
    left = pd.DataFrame({"cust": [1, 2], "a": ["x", "y"]})
    right = pd.DataFrame({"customer_id": [1, 2], "b": [10, 20]})
    rep = join_report(table(left), table(right), left_on="cust", right_on="customer_id")
    assert rep["joins"]["inner"]["result_rows"] == 2


def test_a_key_typed_in_another_case_is_the_column_the_engine_named(table):
    left = pd.DataFrame({"CUSTOMER_ID": [1, 2], "a": [1, 2]})
    right = pd.DataFrame({"customer_id": [1, 2], "b": [10, 20]})
    rep = join_report(table(left), table(right), on="customer_id")
    assert rep["keys"] == {"left": ["CUSTOMER_ID"], "right": ["customer_id"]}
    assert rep["joins"]["inner"]["result_rows"] == 2


def test_missing_key_raises(table):
    left = pd.DataFrame({"id": [1]})
    right = pd.DataFrame({"other": [1]})
    with pytest.raises(ValueError, match="missing key column"):
        join_report(table(left), table(right), on="id")


def test_report_is_json_serializable_and_renders(report):
    assert json.loads(to_json(report))["relationship"] == "many:many"
    assert "many:many" in render_text(report)


# --- measured against tables, not frames -------------------------------------


def _pair(table, left, right, lname="l", rname="r"):
    return table(left, lname), table(right, rname)


def test_a_mismatched_key_is_reported_rather_than_raised(table):
    """DuckDB implements `BIGINT = VARCHAR` by casting and *throwing* on a value
    that won't convert. A check that crashes cannot report the mismatch."""
    lt, rt = _pair(table, pd.DataFrame({"k": [9000, 9001]}), pd.DataFrame({"k": ["H001", "H002"]}))
    report = join_report(lt, rt, on="k")
    assert report["key_dtype_match"] is False
    assert "key_dtype_mismatch" in report["flags"] and "no_matches" in report["flags"]
    assert report["joins"]["inner"]["result_rows"] == 0
    # the samples come back in their own type, not the text the comparison used
    assert report["overlap"]["sample_left_only"] == [9000, 9001]


def test_an_int_key_still_matches_a_float_key(table):
    """Both are 'numeric', so they are compared as numbers — as pandas aligns them."""
    lt, rt = _pair(table, pd.DataFrame({"k": [1, 2]}), pd.DataFrame({"k": [1.0, 2.0]}))
    report = join_report(lt, rt, on="k")
    assert report["key_dtype_match"] is True
    assert report["joins"]["inner"]["result_rows"] == 2


def test_a_composite_key_is_evidence_as_a_list(table):
    lt, rt = _pair(
        table,
        pd.DataFrame({"a": ["x"], "b": ["1"]}),
        pd.DataFrame({"a": ["y"], "b": ["2"]}),
    )
    report = join_report(lt, rt, on=["a", "b"])
    assert report["overlap"]["sample_left_only"] == [["x", "1"]]


def test_a_missing_key_column_says_which(table):
    lt, rt = _pair(table, pd.DataFrame({"a": [1]}), pd.DataFrame({"a": [1]}))
    with pytest.raises(ValueError, match="missing key column"):
        join_report(lt, rt, on="nope")


def test_an_empty_side_reports_zeroes_not_an_error(table):
    lt, rt = _pair(
        table, pd.DataFrame({"k": pd.Series([], dtype="int64")}), pd.DataFrame({"k": [1]})
    )
    report = join_report(lt, rt, on="k")
    assert report["joins"]["inner"]["result_rows"] == 0
    assert report["overlap"]["left_coverage"] == 0.0
    assert "no_matches" in report["flags"]


def test_a_fan_out_is_counted_never_built(con):
    """The claim the module docstring makes, at a size pandas could not merge."""
    con.execute("CREATE TABLE big_l AS SELECT i % 1000 AS k FROM range(200000) t(i)")
    con.execute("CREATE TABLE big_r AS SELECT i % 1000 AS k FROM range(200000) t(i)")
    report = join_report(Table.from_name("big_l", con), Table.from_name("big_r", con), on="k")
    assert report["joins"]["inner"]["result_rows"] == 1000 * 200 * 200
    assert report["fan_out"]["max_left_to_right"] == 200
