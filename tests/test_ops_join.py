"""apply_join must materialize the table the report predicted."""

import pandas as pd
import pytest

from portia.fixtures import sales_customers, sales_orders
from portia.ops import apply_join


@pytest.mark.parametrize(
    "how,expected_rows", [("inner", 8), ("left", 10), ("right", 9), ("outer", 11)]
)
def test_result_matches_prediction(how, expected_rows, table):
    res = apply_join(table(sales_orders()), table(sales_customers()), how=how, on="customer_id")
    assert res.table.count() == expected_rows
    assert res.provenance["result_rows"] == expected_rows
    assert res.provenance["predicted_rows"] == expected_rows
    assert res.provenance["matches_prediction"] is True


def test_left_join_keeps_every_left_row(table):
    res = apply_join(table(sales_orders()), table(sales_customers()), how="left", on="customer_id")
    # all 8 order_ids survive a left join (fan-out can only add rows)
    assert set(res.table.head(100)["order_id"]) == set(sales_orders()["order_id"])
    assert res.provenance["left_dropped"] == 0


def test_provenance_is_json_serializable(table):
    import json

    res = apply_join(table(sales_orders()), table(sales_customers()), how="inner", on="customer_id")
    assert json.loads(json.dumps(res.provenance))["op"] == "join"


def test_bad_how_raises():
    with pytest.raises(ValueError, match="how must be one of"):
        apply_join(pd.DataFrame({"k": [1]}), pd.DataFrame({"k": [1]}), how="cross", on="k")


def test_provenance_keys_declaration_matches_reality(table):
    """The declaration a spec's `expect` is validated against must not rot.

    `agent.handlers` rejects an expectation on a field this op never reports, so
    a stale declaration would either allow a forever-drifting expectation or
    reject a valid one. Both are silent; this test isn't.
    """
    from portia.ops.join import PROVENANCE_KEYS

    result = apply_join(
        table(sales_orders()), table(sales_customers()), on="customer_id", how="left"
    )
    assert set(result.provenance) == set(PROVENANCE_KEYS)
