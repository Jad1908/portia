"""``bookings.csv`` — stays already booked. The big fact table, and the one that
carries the problem no amount of data can settle.

Planted problems:

- **``room_revenue`` is in four currencies and nothing converts them.** There is
  no FX table in this project, on purpose. Summing the column is wrong, and no
  check can tell you what the right answer is, because the right answer is not
  in the data — it depends on which rate, as of when, and for what. This is the
  one trap designed to be *unsolvable by inspection*: it is what a question to
  the human is for.
- **~0.6% of ``property_code`` values are not in `properties`.** Codes from a
  divested portfolio that the booking system still emits. An inner join drops
  them and reports a smaller number that looks fine.
- **``stay_date`` mixes ISO and ``DD/MM/YYYY``**, which makes the column text on
  both tiers — and ``08/03/2026`` is a real ambiguity, not just a format one.
- **``booked_on`` is after ``stay_date`` for ~0.3% of rows**, which is
  impossible and no schema forbids.
- **Cancellations are in the same table as bookings**, as negative revenue with
  a ``cancelled`` flag, so a naive ``sum`` nets them off and a naive ``count``
  does not.
- **``DUPLICATE_ROWS`` rows are exact duplicates** — a file loaded twice. They
  are exact, so they cannot be told apart by content, only counted.
- **``channel`` is spelled inconsistently** (``OTA``/``ota``/``Ota``), so a
  ``GROUP BY`` produces three rows for one channel.
- **Revenue outliers**: corporate blocks and a scatter of decimal-point slips.
- **``rooms_sold`` is null for ~1.5%** and zero for a few, which are different
  things and both survive into an average.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from devtools.demodata.properties import N_PROPERTIES

SEED = 20260814
N_BOOKINGS = 2_000_000

#: Exact duplicate rows, as a file loaded twice would produce.
DUPLICATE_ROWS = 5_000

#: Share of rows referencing a property that is not in the dimension.
ORPHAN_RATE = 0.006

#: Share of stay dates written ``DD/MM/YYYY`` instead of ISO.
NON_ISO_DATE_RATE = 0.04

#: Share of rows booked *after* the stay they refer to.
IMPOSSIBLE_DATE_RATE = 0.003

#: Share of rows that are cancellations, carried as negative revenue.
CANCELLED_RATE = 0.045

#: Share of rows with no room count.
MISSING_ROOMS_RATE = 0.015

#: Share of rows that are corporate block bookings — real, large, and worth
#: asking about rather than filtering on sight.
BLOCK_RATE = 0.004

#: Share of rows whose revenue has a misplaced decimal point.
DECIMAL_SLIP_RATE = 0.0004

#: The booking window: history plus a forward book, around a "today" of
#: 2026-08-12. A stay in 2027 that is already booked is the normal case
#: for this kind of table, not an error.
STAY_START = pd.Timestamp("2025-01-01")
STAY_DAYS = 910

#: How far ahead of the stay a booking is typically made.
LEAD_DAYS_MEAN = 34

_CURRENCIES = ("EUR", "USD", "GBP", "AED")
_CURRENCY_WEIGHTS = (0.44, 0.33, 0.15, 0.08)

#: The same four channels, spelled the way four source systems spell them.
_CHANNELS = (
    "DIRECT",
    "Direct",
    "direct",
    "OTA",
    "ota",
    "Ota",
    "GDS",
    "gds",
    "CORP",
    "Corp",
)
_CHANNEL_WEIGHTS = (0.16, 0.06, 0.03, 0.28, 0.09, 0.04, 0.14, 0.04, 0.12, 0.04)

_RATE_PLANS = ("BAR", "CORP-NEG", "PKG-BB", "ADV-PURCH", "GROUP", "MEMBER")


def bookings() -> pd.DataFrame:
    """Stays already booked, at booking granularity."""
    rng = np.random.default_rng(SEED)
    n = N_BOOKINGS

    codes = _property_codes(rng, n)

    stay = STAY_START + pd.to_timedelta(rng.integers(0, STAY_DAYS, size=n), unit="D")
    lead = rng.exponential(LEAD_DAYS_MEAN, size=n).round().astype(int)
    booked = stay - pd.to_timedelta(lead, unit="D")
    # A booking made after the stay it refers to. Impossible, and present.
    impossible = rng.random(n) < IMPOSSIBLE_DATE_RATE
    booked = np.where(
        impossible, stay + pd.to_timedelta(rng.integers(1, 40, size=n), unit="D"), booked
    )
    booked = pd.to_datetime(booked)

    rooms = rng.integers(1, 5, size=n).astype("float64")
    blocks = rng.random(n) < BLOCK_RATE
    rooms = np.where(blocks, rng.integers(15, 90, size=n), rooms)

    nightly = rng.lognormal(5.05, 0.5, size=n)
    revenue = (rooms * nightly).round(2)

    slips = rng.random(n) < DECIMAL_SLIP_RATE
    revenue = np.where(slips, (revenue * 100).round(2), revenue)

    cancelled = rng.random(n) < CANCELLED_RATE
    revenue = np.where(cancelled, -revenue, revenue)

    rooms = np.where(rng.random(n) < MISSING_ROOMS_RATE, np.nan, rooms)

    frame = pd.DataFrame(
        {
            "booking_id": [f"BK{i:09d}" for i in range(1, n + 1)],
            "property_code": codes,
            "stay_date": _mixed_format_dates(stay, rng),
            "booked_on": booked.strftime("%Y-%m-%d"),
            "rooms_sold": rooms,
            "room_revenue": revenue,
            "currency": rng.choice(_CURRENCIES, size=n, p=_CURRENCY_WEIGHTS),
            "channel": rng.choice(_CHANNELS, size=n, p=_CHANNEL_WEIGHTS),
            "rate_plan": rng.choice(_RATE_PLANS, size=n),
            "cancelled": cancelled,
        }
    )

    return _append_exact_duplicates(frame, rng)


def _property_codes(rng: np.random.Generator, n: int) -> np.ndarray:
    """Property codes, mostly real, some from a portfolio we no longer own.

    Built from `properties.N_PROPERTIES` rather than by loading that table, so
    generating one source never depends on generating another. Bookings are
    skewed across the estate — a handful of properties carry a lot of volume,
    which is what a real book looks like and what makes a per-property profile
    interesting.
    """
    weights = rng.pareto(1.6, size=N_PROPERTIES) + 1.0
    weights /= weights.sum()
    idx = rng.choice(N_PROPERTIES, size=n, p=weights) + 1
    codes = np.char.add("HN", np.char.zfill(idx.astype(str), 5))

    orphans = rng.random(n) < ORPHAN_RATE
    # Codes above the dimension's range: a divested portfolio, still emitting.
    orphan_idx = rng.integers(N_PROPERTIES + 500, N_PROPERTIES + 900, size=n)
    orphan_codes = np.char.add("HN", np.char.zfill(orphan_idx.astype(str), 5))
    return np.where(orphans, orphan_codes, codes)


def _mixed_format_dates(stay: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """ISO for most rows, ``DD/MM/YYYY`` for a minority — so the column is text.

    Only the minority is re-formatted: ``strftime`` over two million rows twice
    is the slowest thing in this module, and there is no reason to pay for it on
    rows that end up ISO anyway.
    """
    out = stay.strftime("%Y-%m-%d").to_numpy(dtype=object)
    swap = rng.random(len(stay)) < NON_ISO_DATE_RATE
    out[swap] = stay[swap].strftime("%d/%m/%Y").to_numpy(dtype=object)
    return out


def _append_exact_duplicates(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Append byte-identical rows, as a re-run of yesterday's load would.

    Exact, including ``booking_id`` — which is what makes them findable at all.
    A duplicate that differed somewhere would be a different problem, and this
    demo already has that one in `properties` and `events`.
    """
    picked = rng.choice(frame.index, size=DUPLICATE_ROWS, replace=False)
    out = pd.concat([frame, frame.loc[picked]], ignore_index=True)
    return out.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
