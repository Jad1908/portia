"""join_findings surfaces facts + example rows — no ranking, no recommendation."""

import json

import pandas as pd
import pytest

from portia.checks.join import SAMPLE_ROW_COLUMNS, join_findings
from portia.fixtures import sales_customers, sales_orders


@pytest.fixture
def findings(table) -> dict:
    return join_findings(table(sales_orders()), table(sales_customers()), on="customer_id")


def test_shape_is_facts_only(findings):
    # Only the report + row evidence. Nothing that ranks or recommends.
    assert set(findings) == {"report", "evidence"}
    blob = json.dumps(findings)
    for judgment_word in ("suggested", "severity", "recommend", "impact_rows", "decision"):
        assert judgment_word not in blob


def test_carries_the_full_report(findings):
    assert findings["report"]["relationship"] == "many:many"


def test_unmatched_left_rows_are_real_examples(findings):
    # order 9005 references customer 7777, which isn't in customers.
    ids = [r["order_id"] for r in findings["evidence"]["unmatched_left_rows"]]
    assert 9005 in ids


def test_null_key_rows_surfaced(findings):
    ids = [r["order_id"] for r in findings["evidence"]["null_key_left_rows"]]
    assert 9006 in ids  # the order with a null customer_id


def test_fan_out_examples_point_at_the_duplicated_key(findings):
    fan = findings["evidence"]["fan_out_examples"]
    keys = [e["key"] for e in fan]
    assert 1001.0 in keys  # duplicated in customers AND has two orders
    example = next(e for e in fan if e["key"] == 1001.0)
    assert example["n_left"] == 2 and example["n_right"] == 2


def test_json_serializable(findings):
    json.dumps(findings)  # must not raise


def test_clean_join_has_empty_evidence(table):
    import pandas as pd

    left = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "y", "z"]})
    right = pd.DataFrame({"id": [1, 2, 3], "b": [10, 20, 30]})
    ev = join_findings(table(left), table(right), on="id")["evidence"]
    assert ev["unmatched_left_rows"] == []
    assert ev["fan_out_examples"] == []


# --- the width of an example row ---------------------------------------------
#
# The rows were always capped at three and the call was refused anyway: on the
# 191-column AQN extract each example row was ~6,200 characters, so six of them
# were 37,754 of a 51,402-character payload and the agent received none of it.
# No golden fixture is wider than five columns, so nothing above exercises this.


@pytest.fixture
def wide(table):
    """A left table wide enough for the cap to bite, keyed late in the schema.

    `country` sits at position 20 of 22 on purpose: `SELECT *` returns schema
    order, and on the real extract the join key was field 133 of 191 — the one
    column that explains the unmatch was the hardest thing in the payload to
    find.
    """
    left = pd.DataFrame(
        {**{f"c{i:02d}": ["x", "y"] for i in range(20)}, "country": ["FRANCE", "SPAIN"]}
    )
    right = pd.DataFrame({"country": ["France"], "place": ["Paris"]})
    return table(left, "wide_left"), table(right, "wide_right")


def test_an_example_row_carries_its_key_first_then_a_capped_few(wide):
    left, right = wide
    findings = join_findings(left, right, on="country")

    columns = findings["evidence"]["example_row_columns"]["left"]
    assert columns[0] == "country", "the key explains the unmatch; it leads"
    assert len(columns) == 1 + SAMPLE_ROW_COLUMNS
    assert columns[1:] == ["c00", "c01", "c02", "c03", "c04"], "schema order after the key"

    for row in findings["evidence"]["unmatched_left_rows"]:
        assert list(row) == columns, "every row the same columns, so they read as a table"


def test_example_row_columns_says_what_you_got(wide):
    """21 columns in, 6 out. Three rows of six look like a six-column table."""
    left, right = wide
    findings = join_findings(left, right, on="country")

    assert len(left.columns) == 21
    assert findings["evidence"]["example_row_columns"]["left"] == list(
        findings["evidence"]["unmatched_left_rows"][0]
    )


def test_naming_columns_overrides_the_cap_and_still_prepends_the_key(wide):
    left, right = wide
    findings = join_findings(left, right, on="country", left_columns=["c19", "country", "c00"])

    # 'country' named explicitly is not printed twice, and the two sides are
    # named separately because they are two schemas.
    assert findings["evidence"]["example_row_columns"]["left"] == ["country", "c19", "c00"]
    assert findings["evidence"]["example_row_columns"]["right"] == ["country", "place"]


def test_a_column_that_is_not_there_is_refused_with_the_near_miss(wide):
    left, right = wide
    with pytest.raises(ValueError, match="countr"):
        join_findings(left, right, on="country", left_columns=["countr"])


def test_a_narrow_table_is_unchanged_by_the_cap(findings):
    """Under the cap nothing is dropped, so the fixtures above still see it all."""
    assert findings["evidence"]["example_row_columns"]["left"] == [
        "customer_id",
        "order_id",
        "amount",
    ]


def test_the_example_rows_quote_a_key_as_the_table_spells_it_not_as_it_was_typed(con):
    """The report resolved ``ID`` to ``id`` and the rows below it quoted ``"ID"``.

    DuckDB matches a quoted name without case, so nothing here could fail on it;
    PostgreSQL answered *no such column*. The SQL is what is checked, because the
    SQL is what another engine reads.
    """
    from portia.core.table import Table

    left = Table.from_frame(pd.DataFrame({"ORDER_ID": [1, 2, 3]}), "l", con)
    right = Table.from_frame(pd.DataFrame({"id": [1]}), "r", con)
    asked: list[str] = []

    class Spy:
        def __getattr__(self, name):
            return getattr(con, name)

        def execute(self, sql):
            asked.append(sql)
            return con.execute(sql)

    found = join_findings(left.using(Spy()), right.using(Spy()), left_on="order_id", right_on="ID")
    assert [r["ORDER_ID"] for r in found["evidence"]["unmatched_left_rows"]] == [2, 3]
    assert not [sql for sql in asked if '"ID"' in sql or '"order_id"' in sql]


# --- predicted nulls ----------------------------------------------------------

#: Every join type, as the op spells it and as SQL does.
_SQL = {"inner": "INNER JOIN", "left": "LEFT JOIN", "right": "RIGHT JOIN", "outer": "FULL JOIN"}


def _built_nulls(con, left, right, lkeys, rkeys, lcols, rcols) -> dict:
    """Build each join and count, per carried column, the rows where it is null: the truth."""
    match = " AND ".join(f'l."{a}" = r."{b}"' for a, b in zip(lkeys, rkeys, strict=True))
    out = {}
    for how, sql in _SQL.items():
        counts = [f'count(*) FILTER (WHERE l."{c}" IS NULL)' for c in lcols] + [
            f'count(*) FILTER (WHERE r."{c}" IS NULL)' for c in rcols
        ]
        row = con.execute(
            f"SELECT count(*), {', '.join(counts)} FROM ({left.query}) l {sql} ({right.query}) r "
            f"ON {match}"
        ).fetchone()
        out[how] = {
            "result_rows": row[0],
            "left": dict(zip(lcols, row[1 : 1 + len(lcols)], strict=True)),
            "right": dict(zip(rcols, row[1 + len(lcols) :], strict=True)),
        }
    return out


def test_a_left_join_says_how_many_rows_it_leaves_without_an_event(table):
    """The backlog's case, as numbers: most bookings have no event, and nothing is dropped."""
    bookings = table(
        pd.DataFrame({"booking_id": range(500), "city": ["Paris"] * 160 + ["Lyon"] * 340})
    )
    events = table(pd.DataFrame({"city": ["Paris"], "event_name": ["Tech Summit"]}))
    found = join_findings(bookings, events, on="city")
    assert found["report"]["joins"]["left"] == {
        "result_rows": 500,
        "left_dropped": 0,
        "right_dropped": 0,
    }
    left_join = found["evidence"]["predicted_nulls"]["left"]
    assert left_join["result_rows"] == 500
    assert left_join["kept_unmatched"] == {"left": 340, "right": 0}
    assert left_join["right"] == {"event_name": 340}


def test_a_null_already_there_is_repeated_as_the_join_repeats_its_row(table):
    """Two events for one key, one of them unnamed: that null comes out once per booking."""
    bookings = table(pd.DataFrame({"booking_id": [1, 2, 3], "city": ["Paris", "Paris", "Rome"]}))
    events = table(pd.DataFrame({"city": ["Paris", "Paris"], "event_name": ["Tech Summit", None]}))
    predicted = join_findings(bookings, events, on="city")["evidence"]["predicted_nulls"]
    # inner: 2 bookings x 2 events, one of each pair unnamed. left: those 2, and Rome with none.
    assert predicted["inner"]["right"] == {"event_name": 2}
    assert predicted["left"]["right"] == {"event_name": 3}


def test_predicted_nulls_cover_the_example_columns(wide):
    """Keys are left out: a key's nulls are the unmatched counts, and a shared key is coalesced."""
    found = join_findings(*wide, on="country")
    evidence = found["evidence"]
    predicted = evidence["predicted_nulls"]["left"]
    assert len(predicted["left"]) == SAMPLE_ROW_COLUMNS
    for side in ("left", "right"):
        keys = found["report"]["keys"][side]
        assert list(predicted[side]) == [
            c for c in evidence["example_row_columns"][side] if c not in keys
        ]


@pytest.mark.parametrize("seed", range(12))
def test_predicted_nulls_are_what_the_built_join_holds(con, seed):
    """Against every join type actually built, on random tables with every awkward case in them.

    Duplicated keys on both sides, null keys on both, nulls in carried columns on
    matched and unmatched rows, keys found on one side only, a composite key.
    """
    import random

    rng = random.Random(seed)

    def rows(n, values):
        return [
            (
                rng.choice([*values, None]),
                rng.choice(["a", "b", None]),
                rng.choice([1, 2, None]),
                rng.choice(["x", None]),
            )
            for _ in range(n)
        ]

    left_rows = rows(rng.randint(0, 30), range(6))
    right_rows = rows(rng.randint(0, 30), range(3, 9))
    for name, data in (("pl", left_rows), ("pr", right_rows)):
        con.execute(f"CREATE OR REPLACE TABLE {name} (k INTEGER, k2 VARCHAR, v INTEGER, w VARCHAR)")
        if data:
            con.executemany(f"INSERT INTO {name} VALUES (?, ?, ?, ?)", data)
    from portia.core.table import Table

    left = Table.from_name("pl", con)
    right = Table.from_name("pr", con)
    for lkeys, rkeys in ((["k"], ["k"]), (["k", "k2"], ["k", "k2"])):
        found = join_findings(left, right, left_on=lkeys, right_on=rkeys)
        predicted = found["evidence"]["predicted_nulls"]
        lcols = [c for c in found["evidence"]["example_row_columns"]["left"] if c not in lkeys]
        rcols = [c for c in found["evidence"]["example_row_columns"]["right"] if c not in rkeys]
        truth = _built_nulls(con, left, right, lkeys, rkeys, lcols, rcols)
        for how, measured in truth.items():
            assert predicted[how]["result_rows"] == measured["result_rows"], how
            assert predicted[how]["left"] == measured["left"], how
            assert predicted[how]["right"] == measured["right"], how
