"""A two-table pair for exercising the join check.

Hand-built (not random) so the join report's numbers are exactly assertable.
Join is ``orders`` (left) to ``customers`` (right) on ``customer_id``. Planted
traps, all of which the report must surface:

- customer 1001 appears **twice** in customers (a duplicate key / data-quality
  bug) and has two orders -> many:many, row **fan-out**.
- order 9005 references customer 7777, absent from customers -> **unmatched
  left**, silently dropped by an inner join.
- order 9006 has a **null** customer_id -> unmatched, dropped by inner.
- customer 1004 has no orders -> **unmatched right** (matters for right/outer).
"""

from __future__ import annotations

import pandas as pd


def sales_customers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [1000, 1001, 1001, 1002, 1003, 1004],
            "name": ["Alpha", "Bravo", "Bravo-dup", "Charlie", "Delta", "Echo"],
        }
    )


def sales_orders() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007],
            "customer_id": [1000, 1001, 1001, 1002, 1002, 7777, None, 1003],
            "amount": [50, 20, 30, 40, 45, 15, 10, 25],
        }
    )
