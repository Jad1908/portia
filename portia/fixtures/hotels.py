"""A three-table forecasting project — the fixture that tests *derivation*.

The sales pair tests whether the engine reports a join correctly. This one tests
something harder: whether the **agent** can work out how sources relate when
nobody tells it. The project brief that accompanies it (see
``tests/fixtures/hotels.answers.yaml``) describes a hotel revenue-forecasting
problem and deliberately says **nothing** about keys, joins, or granularity.

The relationships are inferable but not stated:

- ``otb`` (on-the-books bookings) knows only ``hotel_id``. It has no city.
- ``hotels`` carries ``hotel_id`` **and** ``city`` — so it is the bridge.
- ``city_events`` is keyed on ``city`` + ``event_date``.

So reaching events from bookings is two hops, and the second key never appears
in the fact table. An agent that only inspects ``otb`` and ``city_events`` finds
no join at all.

Planted traps, in rough order of how badly they bite:

- **Fan-out that silently double-counts revenue.** Amsterdam has *two* events on
  2026-06-12. Joining ``otb`` to ``city_events`` on city+date multiplies those
  booking rows, inflating revenue — the numbers still look plausible, which is
  what makes it dangerous. This one is visible in the raw data.
- **City spelling doesn't match.** ``hotels`` says ``"Paris"``; ``city_events``
  says ``" paris"`` for one row (case + leading space). A naive join drops it.
  The columns are also *named* differently (``city`` vs ``city_name``).
- **A fan-out that only appears after cleaning.** Once the Paris spelling is
  normalized, Paris *also* has two events that day. So cleaning creates a new
  problem, and an agent that normalizes and immediately joins double-counts.
  Re-checking after an action is the only way to catch it — which is exactly
  what the two traps together are here to measure, separately.
- **A city with events but no hotels** (Lyon) — events you cannot use.
- **A hotel with no bookings** (H005, newly opened) — drops on an inner join.
- **An orphan booking** referencing ``H999``, absent from ``hotels``.
- **Revenue outliers** — two bookings at ~20x the typical rate (a group booking
  and a data-entry error), so *some* judgement about filtering is invited but
  never forced.
"""

from __future__ import annotations

import pandas as pd


def hotels() -> pd.DataFrame:
    """The property dimension — and the only bridge from bookings to cities."""
    return pd.DataFrame(
        {
            "hotel_id": ["H001", "H002", "H003", "H004", "H005"],
            "hotel_name": [
                "Grand Riverside",
                "Le Petit Nord",
                "Marina Bay Suites",
                "Old Town Inn",
                "Canal View",
            ],
            "city": ["Paris", "Paris", "Barcelona", "Amsterdam", "Amsterdam"],
            "country": ["FR", "FR", "ES", "NL", "NL"],
            "rooms": [220, 85, 310, 64, 120],
        }
    )


def otb() -> pd.DataFrame:
    """On-the-books bookings. Knows the hotel, but nothing about geography."""
    return pd.DataFrame(
        {
            "booking_id": [f"B{n:04d}" for n in range(1, 15)],
            "hotel_id": [
                "H001",
                "H001",
                "H001",
                "H002",
                "H002",
                "H003",
                "H003",
                "H003",
                "H004",
                "H004",
                "H999",  # orphan: no such hotel
                "H001",
                "H003",
                "H002",
            ],
            "stay_date": [
                "2026-06-12",
                "2026-06-12",
                "2026-06-13",
                "2026-06-12",
                "2026-06-14",
                "2026-06-12",
                "2026-06-13",
                "2026-06-13",
                "2026-06-12",
                "2026-06-15",
                "2026-06-12",
                "2026-06-20",
                "2026-06-21",
                "2026-06-22",
            ],
            "rooms_sold": [12, 8, 15, 4, 6, 22, 18, 9, 3, 5, 7, 11, 25, 2],
            "revenue": [
                2400.0,
                1600.0,
                3000.0,
                760.0,
                1140.0,
                4840.0,
                3960.0,
                1980.0,
                480.0,
                800.0,
                1400.0,
                # outliers: a corporate block booking and a data-entry slip
                52000.0,
                61500.0,
                380.0,
            ],
        }
    )


def city_events() -> pd.DataFrame:
    """External events, at city granularity. Different key name, messy values."""
    return pd.DataFrame(
        {
            "city_name": [
                "Paris",
                " paris",  # same city, different spelling — a naive join drops it
                "Barcelona",
                "Amsterdam",
                "Amsterdam",  # second Amsterdam event, same day -> plain fan-out
                "Lyon",  # a city with no hotels
                "Barcelona",
            ],
            "event_date": [
                "2026-06-12",
                "2026-06-12",  # second Paris event, same day -> fan-out *after* cleaning
                "2026-06-12",
                "2026-06-12",
                "2026-06-12",
                "2026-06-12",
                "2026-06-13",
            ],
            "event_name": [
                "Tech Summit",
                "Marathon",
                "Mobile Congress",
                "Canal Festival",
                "Design Week",
                "Food Fair",
                "Mobile Congress",
            ],
            "expected_attendance": [15000, 40000, 90000, 25000, 12000, 8000, 90000],
        }
    )
