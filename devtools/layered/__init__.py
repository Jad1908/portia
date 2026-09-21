"""A small layered project — for looking at the canvas, not for stressing the engine.

`devtools/demodata` is the other generated project and it is a different thing:
three million rows, planted data problems, built so a *person* can drive the
copilot through it on camera. This one is a few hundred rows and has no problems
planted in it at all. It exists so the **project graph** can be assessed — which
is a question about *shape*, and the demo project has none: one table built from
three files is a picture with nothing in it to select, filter or trace.

So everything here is chosen to make one part of the canvas answerable:

===================================== ========================================
what it is                            what it is for
===================================== ========================================
three layers, ``stg`` → ``int`` →     that layers group and never rank, and
``mart``                              that depth is derived from what specs read
a **diamond** — two ``int`` models     that selecting one table pulls its whole
read one ``stg``, one ``mart`` reads   ancestry in, and that two selections
both                                   share an upstream without drawing it twice
a source read by **two** models        that hiding one model keeps a file its
                                       sibling still needs
a deliberately **long table name**     that a card truncates rather than wraps
a spec with **one** step and one with  that a card's height follows its rows
**six**
a **ghost step** in ``int_order_       that a step nothing reads is visible as
totals``                               such — see `docs/PIPELINE.md` §6
===================================== ========================================

**Deterministic**, like `demodata`: the same call writes byte-identical files, so
a screenshot taken today still matches the project tomorrow. Written to
``sandbox/layered`` by default, which is gitignored — the generator is tracked,
the output is not.

Run it with ``python -m devtools.layered``.
"""

from __future__ import annotations

import csv
from pathlib import Path

import yaml

from portia import catalog, spec

#: Where it lands. Under `sandbox/`, which is gitignored whole — this is a
#: throwaway project you regenerate, not something to keep.
DEFAULT_ROOT = Path("sandbox/layered")

#: What the project says it is. The app refuses to open a workspace without one,
#: and half the copilot's system prompt does not exist until it is written.
BRIEF = (
    "A small orders project, three layers deep. Customers place orders; orders have "
    "line items; products and regions are reference data. It exists to look at, not "
    "to learn anything from — there are no data problems planted in it."
)

#: Row counts. Small on purpose: this project is regenerated and re-run often
#: while looking at the screen, so a build has to finish in about a second.
CUSTOMERS, PRODUCTS, ORDERS, ITEMS_PER_ORDER, REGIONS = 60, 12, 200, 3, 5


def _rows(root: Path) -> None:
    """Five CSVs, generated from index arithmetic so nothing here is random."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)

    _write(
        data / "regions.csv",
        ["region_id", "region_name", "country"],
        [[i, f"region-{i}", "FR" if i % 2 else "DE"] for i in range(1, REGIONS + 1)],
    )
    _write(
        data / "customers.csv",
        ["customer_id", "customer_name", "region_id", "signed_up_on"],
        [
            [i, f"customer-{i:03d}", i % REGIONS + 1, f"2024-{i % 12 + 1:02d}-01"]
            for i in range(1, CUSTOMERS + 1)
        ],
    )
    _write(
        data / "products.csv",
        ["product_id", "product_name", "category", "list_price"],
        [
            [i, f"product-{i:02d}", ["tools", "parts", "kits"][i % 3], 10 + i * 5]
            for i in range(1, PRODUCTS + 1)
        ],
    )
    _write(
        data / "orders.csv",
        ["order_id", "customer_id", "ordered_on", "status"],
        [
            [
                i,
                i % CUSTOMERS + 1,
                f"2025-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
                "shipped" if i % 4 else "cancelled",
            ]
            for i in range(1, ORDERS + 1)
        ],
    )
    _write(
        data / "order_items.csv",
        ["order_item_id", "order_id", "product_id", "quantity", "unit_price"],
        [
            [
                (order - 1) * ITEMS_PER_ORDER + line,
                order,
                (order + line) % PRODUCTS + 1,
                line + 1,
                10 + ((order + line) % PRODUCTS + 1) * 5,
            ]
            for order in range(1, ORDERS + 1)
            for line in range(ITEMS_PER_ORDER)
        ],
    )


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


# --- the specs ---------------------------------------------------------------
#
# One spec per table, `layer` naming the tier. Steps are mostly `sql`, which is
# the shortest way to write a real step; one `join` is in there because a join
# step carries its keys and its `_x`/`_y` rename, and the card's Reads section
# reads those differently from a SQL step's parsed lineage.


def _specs() -> dict[str, dict]:
    specs: dict[str, dict] = {}

    for name, source, columns in (
        ("customers", "customers.csv", "customer_id, customer_name, region_id, signed_up_on"),
        ("products", "products.csv", "product_id, product_name, category, list_price"),
        ("regions", "regions.csv", "region_id, region_name, country"),
    ):
        specs[f"stg_{name}"] = {
            "version": 1,
            "layer": "staging",
            "sources": {name: f"data/{source}"},
            "steps": [
                {
                    "id": f"stg_{name}",
                    "op": "sql",
                    "inputs": [name],
                    "sql": f"select {columns} from {name}",
                    "rationale": f"Pass {name} through unchanged — the staging tier is a rename.",
                    "expect": {},
                }
            ],
        }

    # Six steps, so a card is tall beside its one-step neighbours.
    specs["stg_orders"] = {
        "version": 1,
        "layer": "staging",
        "sources": {"orders": "data/orders.csv"},
        "steps": [
            {
                "id": "orders_typed",
                "op": "sql",
                "inputs": ["orders"],
                "sql": (
                    "select order_id, customer_id, cast(ordered_on as date) as ordered_on, "
                    "status from orders"
                ),
                "rationale": "Cast the order date, which arrives as text.",
                "expect": {},
            },
            {
                "id": "orders_shipped_only",
                "op": "sql",
                "inputs": ["orders_typed"],
                "sql": "select * from orders_typed where status = 'shipped'",
                "rationale": "Cancelled orders are not demand.",
                "expect": {},
            },
            {
                "id": "orders_with_month",
                "op": "sql",
                "inputs": ["orders_shipped_only"],
                "sql": (
                    "select *, strftime(ordered_on, '%Y-%m') as order_month "
                    "from orders_shipped_only"
                ),
                "rationale": "Month is the grain everything downstream aggregates to.",
                "expect": {},
            },
            {
                "id": "orders_with_year",
                "op": "sql",
                "inputs": ["orders_with_month"],
                "sql": "select *, year(ordered_on) as order_year from orders_with_month",
                "rationale": "Year, for the annual cut.",
                "expect": {},
            },
            {
                "id": "orders_ranked_within_customer",
                "op": "sql",
                "inputs": ["orders_with_year"],
                "sql": (
                    "select *, row_number() over (partition by customer_id order by ordered_on) "
                    "as order_seq from orders_with_year"
                ),
                "rationale": "Which order this is for that customer.",
                "expect": {},
            },
            {
                "id": "stg_orders",
                "op": "sql",
                "inputs": ["orders_ranked_within_customer"],
                "sql": "select * from orders_ranked_within_customer",
                "rationale": "The staging table.",
                "expect": {},
            },
        ],
    }

    specs["stg_order_items"] = {
        "version": 1,
        "layer": "staging",
        "sources": {"order_items": "data/order_items.csv"},
        "steps": [
            {
                "id": "stg_order_items",
                "op": "sql",
                "inputs": ["order_items"],
                "sql": (
                    "select order_item_id, order_id, product_id, quantity, unit_price, "
                    "quantity * unit_price as line_total from order_items"
                ),
                "rationale": "Line total is arithmetic on two columns that arrived.",
                "expect": {},
            }
        ],
    }

    # **The ghost step.** `items_summed_wrong` is read by nothing and is not the
    # last step, so it is compiled into the `.sql` as a CTE nothing selects from —
    # and it is still executed on every run. It is here on purpose: the demo
    # project grew four of these by accident and there was no way to see it.
    specs["int_order_totals"] = {
        "version": 1,
        "layer": "intermediate",
        "sources": {},
        "steps": [
            {
                "id": "items_summed_wrong",
                "op": "sql",
                "inputs": ["stg_order_items"],
                "sql": "select order_id, sum(quantity) as total from stg_order_items group by 1",
                "rationale": "First attempt — summed quantity rather than value. Superseded.",
                "expect": {},
            },
            {
                "id": "items_summed",
                "op": "sql",
                "inputs": ["stg_order_items"],
                "sql": (
                    "select order_id, sum(line_total) as order_total, count(*) as n_lines "
                    "from stg_order_items group by 1"
                ),
                "rationale": "Order value is the sum of its line totals.",
                "expect": {},
            },
            {
                "id": "int_order_totals",
                "op": "join",
                "left": "stg_orders",
                "right": "items_summed",
                "keys": ["order_id"],
                "how": "inner",
                "rationale": "Attach each order's value to the order.",
                "expect": {},
            },
        ],
    }

    specs["int_customer_region"] = {
        "version": 1,
        "layer": "intermediate",
        "sources": {},
        "steps": [
            {
                "id": "int_customer_region",
                "op": "sql",
                "inputs": ["stg_customers", "stg_regions"],
                "sql": (
                    "select c.customer_id, c.customer_name, r.region_name, r.country "
                    "from stg_customers c left join stg_regions r on c.region_id = r.region_id"
                ),
                "rationale": "Give each customer its region's name and country.",
                "expect": {},
            }
        ],
    }

    specs["mart_customer_orders"] = {
        "version": 1,
        "layer": "mart",
        "sources": {},
        "steps": [
            {
                "id": "mart_customer_orders",
                "op": "sql",
                "inputs": ["int_order_totals", "int_customer_region"],
                "sql": (
                    "select r.customer_id, r.customer_name, r.region_name, r.country, "
                    "count(*) as n_orders, sum(t.order_total) as revenue "
                    "from int_order_totals t join int_customer_region r "
                    "on t.customer_id = r.customer_id group by 1, 2, 3, 4"
                ),
                "rationale": "One row per customer: how much they ordered, and where they are.",
                "expect": {},
            }
        ],
    }

    # The long name, for the card's truncation.
    specs["mart_product_performance_by_region_and_month"] = {
        "version": 1,
        "layer": "mart",
        "sources": {},
        "steps": [
            {
                "id": "items_with_region",
                "op": "sql",
                "inputs": ["stg_order_items", "int_order_totals", "int_customer_region"],
                "sql": (
                    "select i.product_id, r.region_name, t.order_month, i.line_total "
                    "from stg_order_items i "
                    "join int_order_totals t on i.order_id = t.order_id "
                    "join int_customer_region r on t.customer_id = r.customer_id"
                ),
                "rationale": "Put a region and a month on every line item.",
                "expect": {},
            },
            {
                "id": "mart_product_performance_by_region_and_month",
                "op": "sql",
                "inputs": ["items_with_region", "stg_products"],
                "sql": (
                    "select p.product_name, p.category, i.region_name, i.order_month, "
                    "sum(i.line_total) as revenue from items_with_region i "
                    "join stg_products p on i.product_id = p.product_id group by 1, 2, 3, 4"
                ),
                "rationale": "Revenue per product, per region, per month.",
                "expect": {},
            },
        ],
    }
    return specs


def build(root: str | Path = DEFAULT_ROOT, *, index: bool = True) -> Path:
    """Write the data, the specs and the brief. Returns the project root.

    ``index`` profiles the sources into the catalog, which is what the app needs
    before a card can say which columns came along an arrow — an unindexed project
    draws every arrow and no column detail, which is itself worth looking at once.
    """
    root = Path(root).expanduser().resolve()
    _rows(root)

    for name, doc in _specs().items():
        # Placed by the engine's own rule, so the fixture shows the tree a real
        # project gets: a spec that declares a layer lands in that layer's folder
        # (`spec.spec_path`), exactly as its compiled `.sql` does.
        path = spec.spec_path(name, layer=doc.get("layer"), root=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    portia_dir = root / catalog.DEFAULT_DIR
    catalog.init_project(BRIEF, portia_dir=portia_dir)
    if index:
        for source in sorted((root / "data").glob("*.csv")):
            catalog.index_source(source, portia_dir=portia_dir)
    return root
