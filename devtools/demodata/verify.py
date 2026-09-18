"""Measure every planted problem: ``python -m devtools.demodata.verify [directory]``.

A demo dataset whose traps have quietly stopped working is worse than no demo
dataset, because you find out on camera. Each builder's docstring *claims*
something is in the data; this measures whether it still is, reading the files
through `portia.core.io` exactly as the engine would.

It **prints measurements and does not judge them** — no pass/fail column, no
threshold. The numbers are the point: they are what you say out loud while
recording, and re-running this after a regeneration is how you find out whether
the sentence you rehearsed is still true. A seed change or an edit to a builder
moves them, and nothing here pretends otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

from devtools.demodata import DEFAULT_DIR
from portia.core.io import connect, load_table

#: ``(source, what it shows, SQL)``. ``{src}`` is replaced by the source's
#: ``SELECT``, so these read the files the way the engine does rather than
#: naming a reader themselves.
CHECKS: tuple[tuple[str, str, str], ...] = (
    # -- properties ---------------------------------------------------------
    (
        "properties",
        "rows / distinct property_code",
        "SELECT count(*), count(DISTINCT property_code) FROM {src}",
    ),
    (
        "properties",
        "rows with no coordinates",
        "SELECT count(*) FILTER (WHERE latitude IS NULL) FROM {src}",
    ),
    (
        "properties",
        "French rows / with country_iso",
        "SELECT count(*), count(country_iso) FROM {src} WHERE upper(trim(country_name)) = 'FRANCE'",
    ),
    (
        "properties",
        "rows with no country_iso",
        "SELECT count(*) FILTER (WHERE country_iso IS NULL) FROM {src}",
    ),
    (
        "properties",
        "room_count is text",
        "SELECT column_type FROM (DESCRIBE SELECT * FROM {src}) WHERE column_name = 'room_count'",
    ),
    (
        "properties",
        "room_counts with a separator",
        "SELECT count(*) FROM {src} WHERE room_count LIKE '%,%'",
    ),
    (
        "properties",
        "opened_on in DD/MM/YYYY",
        "SELECT count(*) FROM {src} WHERE opened_on LIKE '%/%'",
    ),
    (
        "properties",
        "non-hotel property types",
        "SELECT count(*) FROM {src} WHERE property_type <> 'HOTEL'",
    ),
    ("properties", "loyalty_tier present", "SELECT count(loyalty_tier), count(*) FROM {src}"),
    # -- events -------------------------------------------------------------
    (
        "events_feed",
        "rows / distinct event_id",
        "SELECT count(*), count(DISTINCT event_id) FROM {src}",
    ),
    (
        "events_feed",
        "rows with no coordinates",
        "SELECT count(*) FILTER (WHERE latitude IS NULL) FROM {src}",
    ),
    (
        "events_feed",
        "rows with no attendance",
        "SELECT count(*) FILTER (WHERE attendance IS NULL) FROM {src}",
    ),
    (
        "events_feed",
        "country shipped lowercase",
        "SELECT count(*) FROM {src} WHERE country <> upper(country)",
    ),
    (
        "events_feed",
        "conferences with zero spend",
        "SELECT count(*) FROM {src} WHERE category = 'conferences' AND predicted_accommodation_spend = 0",
    ),
    (
        "events_feed",
        "cancelled / deleted rows",
        "SELECT count(*) FROM {src} WHERE state IN ('canceled', 'deleted')",
    ),
    # -- bookings -----------------------------------------------------------
    (
        "bookings",
        "rows / distinct booking_id",
        "SELECT count(*), count(DISTINCT booking_id) FROM {src}",
    ),
    ("bookings", "distinct currencies", "SELECT count(DISTINCT currency) FROM {src}"),
    (
        "bookings",
        "distinct channel spellings",
        "SELECT count(DISTINCT channel), count(DISTINCT upper(channel)) FROM {src}",
    ),
    (
        "bookings",
        "stay_date is text",
        "SELECT column_type FROM (DESCRIBE SELECT * FROM {src}) WHERE column_name = 'stay_date'",
    ),
    (
        "bookings",
        "stay_date in DD/MM/YYYY",
        "SELECT count(*) FROM {src} WHERE stay_date LIKE '%/%'",
    ),
    (
        "bookings",
        "booked after the stay",
        "SELECT count(*) FROM {src} WHERE stay_date NOT LIKE '%/%' AND booked_on > CAST(stay_date AS TIMESTAMP)",
    ),
    ("bookings", "negative revenue rows", "SELECT count(*) FROM {src} WHERE room_revenue < 0"),
    (
        "bookings",
        "rows with no rooms_sold",
        "SELECT count(*) FILTER (WHERE rooms_sold IS NULL) FROM {src}",
    ),
    (
        "bookings",
        "max vs p99 revenue",
        "SELECT round(max(room_revenue)), round(quantile_cont(room_revenue, 0.99)) FROM {src}",
    ),
    # -- rates --------------------------------------------------------------
    (
        "rates_daily",
        "occupancy range per source_system",
        "SELECT source_system, round(min(occupancy), 3), round(max(occupancy), 1) FROM {src} GROUP BY 1 ORDER BY 1",
    ),
    (
        "rates_daily",
        "mean of the mixed occupancy column",
        "SELECT round(avg(occupancy), 3) FROM {src}",
    ),
    (
        "rates_daily",
        "rows with no avg_rate",
        "SELECT count(*) FILTER (WHERE avg_rate IS NULL) FROM {src}",
    ),
    (
        "rates_daily",
        "rows with no comp_rate",
        "SELECT count(*) FILTER (WHERE comp_rate IS NULL) FROM {src}",
    ),
)

#: Checks that need two sources at once — the joins that are the whole point.
CROSS_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "bookings referencing a property that does not exist",
        """SELECT count(*) FROM {bookings} b
           LEFT JOIN (SELECT DISTINCT property_code AS c FROM {properties}) p ON b.property_code = p.c
           WHERE p.c IS NULL""",
    ),
    (
        "rates.hotel_ref values shared with properties.property_code (raw)",
        """SELECT count(*) FROM (SELECT DISTINCT hotel_ref AS v FROM {rates_daily}) a
           INNER JOIN (SELECT DISTINCT property_code AS v FROM {properties}) b USING (v)""",
    ),
    (
        "...and after upper() + removing the dash",
        """SELECT count(*) FROM (SELECT DISTINCT upper(replace(hotel_ref, '-', '')) AS v FROM {rates_daily}) a
           INNER JOIN (SELECT DISTINCT property_code AS v FROM {properties}) b USING (v)""",
    ),
    (
        "country names no regions row maps",
        """SELECT count(*) FROM {properties} p
           LEFT JOIN {regions} r ON upper(trim(p.country_name)) = r.country_name
           WHERE r.country_name IS NULL""",
    ),
)


def _sources(directory: Path) -> tuple[dict[str, str], object]:
    """Every demo file as ``name -> SELECT``, on one shared connection."""
    con = connect()
    data_dir = directory / "data"
    tables = {p.stem: load_table(p, con) for p in sorted(data_dir.iterdir())}
    return {name: f"({t.query})" for name, t in tables.items()}, con


def main(directory: Path = DEFAULT_DIR) -> None:
    src, con = _sources(directory)

    print(f"\n{'source':<14} {'measurement':<44} value")
    print("-" * 92)
    for source, label, sql in CHECKS:
        if source not in src:
            print(f"{source:<14} {label:<44} (file not written)")
            continue
        rows = con.sql(sql.format(src=src[source])).fetchall()
        print(f"{source:<14} {label:<44} {_render(rows)}")

    print(f"\n{'across sources':<59} value")
    print("-" * 92)
    for label, sql in CROSS_CHECKS:
        rows = con.sql(sql.format(**src)).fetchall()
        print(f"{label:<59} {_render(rows)}")
    print()


def _render(rows: list) -> str:
    """One row of scalars on a line; anything longer, one group per line."""
    if len(rows) == 1:
        return " / ".join(_scalar(v) for v in rows[0])
    return ("\n" + " " * 60).join(" / ".join(_scalar(v) for v in row) for row in rows)


def _scalar(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR)
