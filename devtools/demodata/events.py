"""``events_feed.parquet`` — the third-party event feed. Big, and Parquet on purpose.

Two reasons it is the one Parquet source. It is the table a demo profiles most,
and a per-column read on Parquet is nearly free where a CSV re-parses the whole
file for every column (`docs/DUCKDB_MIGRATION.md` §14) — so the difference is
visible next to `bookings.csv`, which is a comparable size and text. And Parquet
carries its own schema, so this is the one table where a date is a date and a
number is a number: every type problem in this demo is in a CSV, which is the
honest version of why converting is worth it.

Planted problems:

- **``event_id`` is not unique, and that is the feed working correctly.** A
  vendor ships snapshots: roughly ``SNAPSHOT_RATE`` of events appear two to four
  times with a later ``updated`` and a revised rank, attendance and spend. The
  latest snapshot per id is the row you want, which is a window function, not a
  ``DISTINCT``. Declaring a grain of ``event_id`` before that is done fails, and
  should.
- **~8% have no coordinates**, so they cannot be placed against a hotel at all.
- **Cancelled and deleted events are still in the feed.** The real pipeline
  keeps the high-ranked ones (a cancelled stadium show still moves demand
  because the bookings were already made) and drops the rest — a judgment call
  the data cannot make for itself.
- **Conferences with zero predicted spend** are noise the feed carries anyway.
- **``attendance`` is null for ~22%**, and null is not zero.
- **``country`` is ISO2 but not consistently uppercase**, so joining it to
  ``regions.country_iso2`` on the raw value loses the lowercase rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from devtools.demodata.geography import CITIES, KM_PER_DEGREE

SEED = 20260813

#: Distinct events before snapshots are added.
N_EVENTS = 330_000

#: Share of events the feed re-ships as later snapshots.
SNAPSHOT_RATE = 0.18

#: Share of events with no usable coordinates.
MISSING_COORD_RATE = 0.08

#: Share of events with no attendance figure.
MISSING_ATTENDANCE_RATE = 0.22

#: Share of country codes shipped lowercase.
LOWERCASE_ISO_RATE = 0.07

#: How far an event may sit from its city centre, in km. Wider than a hotel's
#: spread — festivals happen outside town.
CITY_SPREAD_KM = 22.0

#: The window the feed covers. Wider than the booking window, because a feed
#: carries history and a forward book does not.
START = pd.Timestamp("2024-01-01")
DAYS = 365 * 4

_CATEGORIES = (
    "conferences",
    "concerts",
    "sports",
    "expos",
    "festivals",
    "community",
    "observances",
    "school-holidays",
)
#: Weighted toward the long tail of low-value categories, so tiering matters.
_CATEGORY_WEIGHTS = (0.10, 0.14, 0.16, 0.05, 0.08, 0.24, 0.13, 0.10)

_STATES = ("active", "canceled", "deleted", "postponed")
_STATE_WEIGHTS = (0.88, 0.06, 0.03, 0.03)

_TITLE_HEADS = (
    "Annual",
    "International",
    "National",
    "Summer",
    "Winter",
    "Global",
    "Regional",
    "Open",
)
_TITLE_TAILS = (
    "Summit",
    "Congress",
    "Festival",
    "Championship",
    "Expo",
    "Forum",
    "Marathon",
    "Fair",
    "Convention",
    "Derby",
)


def events() -> pd.DataFrame:
    """The event feed, snapshots and all."""
    rng = np.random.default_rng(SEED)
    n = N_EVENTS

    city_idx = rng.integers(0, len(CITIES), size=n)
    cities = [CITIES[i] for i in city_idx]

    spread_deg = CITY_SPREAD_KM / KM_PER_DEGREE
    lat = np.array([c.lat for c in cities]) + rng.normal(0, spread_deg, n)
    lon = np.array([c.lon for c in cities]) + rng.normal(0, spread_deg, n)
    missing = rng.random(n) < MISSING_COORD_RATE
    lat = np.where(missing, np.nan, lat.round(6))
    lon = np.where(missing, np.nan, lon.round(6))

    category = rng.choice(_CATEGORIES, size=n, p=_CATEGORY_WEIGHTS)
    # Rank is the feed's own 0-100 impact score, skewed low: most of what a feed
    # carries is not worth a forecast.
    rank = np.clip((rng.beta(2.0, 5.0, size=n) * 100).round(), 0, 100).astype(int)

    start = START + pd.to_timedelta(rng.integers(0, DAYS, size=n), unit="D")
    # Most events are one day; conferences and expos run longer.
    length = np.where(
        np.isin(category, ("conferences", "expos")),
        rng.integers(1, 6, size=n),
        rng.choice([1, 1, 1, 1, 2, 3], size=n),
    )
    end = start + pd.to_timedelta(length - 1, unit="D")

    attendance = (rng.lognormal(7.4, 1.25, size=n)).round().astype("float64")
    attendance[rng.random(n) < MISSING_ATTENDANCE_RATE] = np.nan

    # Spend tracks attendance and rank, then a chunk of conferences report zero.
    spend = (np.nan_to_num(attendance, nan=400.0) * (rank / 100 + 0.15) * 41.0).round(2)
    zero_spend = (category == "conferences") & (rng.random(n) < 0.30)
    spend = np.where(zero_spend, 0.0, spend)

    iso = np.array([c.iso2 for c in cities], dtype=object)
    lowered = rng.random(n) < LOWERCASE_ISO_RATE
    country = np.where(lowered, np.char.lower(iso.astype(str)), iso)

    titles = [
        f"{_TITLE_HEADS[rng.integers(0, len(_TITLE_HEADS))]} "
        f"{cities[i].name} "
        f"{_TITLE_TAILS[rng.integers(0, len(_TITLE_TAILS))]}"
        for i in range(n)
    ]

    frame = pd.DataFrame(
        {
            "event_id": [f"EVT{i:08d}" for i in range(1, n + 1)],
            "title": titles,
            "category": category,
            "state": rng.choice(_STATES, size=n, p=_STATE_WEIGHTS),
            "rank": rank,
            "event_start": start,
            "event_end": end,
            "country": country,
            "attendance": attendance,
            "predicted_accommodation_spend": spend,
            "latitude": lat,
            "longitude": lon,
            "updated": pd.Timestamp("2026-01-05"),
        }
    )

    return _append_snapshots(frame, rng)


def _append_snapshots(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Re-ship some events as later revisions of themselves.

    The revision is a real one — rank, attendance, spend and sometimes state all
    move, and ``updated`` is later. So the duplicate is not noise to be dropped;
    it is the *older* row that has to go, and only ``updated`` says which is
    which. Shuffled at the end so the snapshots are not conveniently adjacent.
    """
    n_snap = int(len(frame) * SNAPSHOT_RATE)
    picked = rng.choice(frame.index, size=n_snap, replace=False)
    snaps = frame.loc[picked].copy()

    snaps["rank"] = np.clip(snaps["rank"] + rng.integers(-12, 13, size=n_snap), 0, 100)
    snaps["attendance"] = (snaps["attendance"] * rng.uniform(0.75, 1.4, size=n_snap)).round()
    snaps["predicted_accommodation_spend"] = (
        snaps["predicted_accommodation_spend"] * rng.uniform(0.8, 1.35, size=n_snap)
    ).round(2)
    # A revision is where a cancellation shows up.
    became_cancelled = rng.random(n_snap) < 0.05
    snaps["state"] = np.where(became_cancelled, "canceled", snaps["state"])
    snaps["updated"] = pd.Timestamp("2026-01-05") + pd.to_timedelta(
        rng.integers(1, 200, size=n_snap), unit="D"
    )

    out = pd.concat([frame, snaps], ignore_index=True)
    return out.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
