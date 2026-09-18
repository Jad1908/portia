"""``rates_daily.csv`` — a rate-shopping feed, and the demo's best single trap.

**Its key does not match anything, and it is the same key.** The property is
``hn-00001`` here and ``HN00001`` in `properties`: lowercase, with a dash. So a
measured overlap between ``rates_daily.hotel_ref`` and
``properties.property_code`` is **exactly zero shared values** — and zero is the
answer the knowledge graph warns about in `docs/KNOWLEDGE_GRAPH.md` §4.4, where
a measured zero means *no shared values* and never *unrelated*. ``France`` vs
``FRA``, with the two columns obviously about the same thing to anyone reading
their names.

It is worth having in a demo because both readings are visible at once: the
profile shows two columns with the same cardinality, the same shape and no
values in common. Nothing deterministic can decide that; normalizing one side is
a judgment call, and once it is made the join is perfect.

The other planted problems:

- **``occupancy`` is on two different scales.** ``pms`` reports a fraction
  (0-1), ``channel_manager`` reports a percentage (0-100), and the mean of the
  mixed column is meaningless while sitting inside a plausible range. The
  ``source_system`` column is the only thing that explains it.
- **``avg_rate`` is null for ~9%** — days the shopper did not run.
- **``comp_rate`` is null far more often** (~35%), because a competitor set is
  not always resolvable.
- Coverage is partial: only ``COVERED_SHARE`` of properties are shopped at all,
  so a join to `properties` legitimately misses most of the estate, and that is
  not an error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from devtools.demodata.properties import N_PROPERTIES

SEED = 20260815
N_RATES = 900_000

#: Share of the estate the rate shopper covers.
COVERED_SHARE = 0.35

#: Share of rows with no shopped rate.
MISSING_RATE_SHARE = 0.09

#: Share of rows with no competitor-set rate.
MISSING_COMP_RATE = 0.35

#: Share of rows coming from the channel manager, which reports occupancy as a
#: percentage where the PMS reports a fraction.
CHANNEL_MANAGER_SHARE = 0.4

RATE_START = pd.Timestamp("2025-06-01")
RATE_DAYS = 640


def rates() -> pd.DataFrame:
    """Daily shopped rates, keyed the way the vendor keys them."""
    rng = np.random.default_rng(SEED)
    n = N_RATES

    covered = int(N_PROPERTIES * COVERED_SHARE)
    idx = rng.integers(1, covered + 1, size=n)
    # The vendor's own spelling of a key everyone else writes as HN00001.
    hotel_ref = np.char.add("hn-", np.char.zfill(idx.astype(str), 5))

    rate_date = RATE_START + pd.to_timedelta(rng.integers(0, RATE_DAYS, size=n), unit="D")

    avg_rate = rng.lognormal(5.1, 0.42, size=n).round(2)
    avg_rate = np.where(rng.random(n) < MISSING_RATE_SHARE, np.nan, avg_rate)

    comp = (avg_rate * rng.uniform(0.82, 1.24, size=n)).round(2)
    comp = np.where(rng.random(n) < MISSING_COMP_RATE, np.nan, comp)

    from_channel_manager = rng.random(n) < CHANNEL_MANAGER_SHARE
    fraction = np.clip(rng.beta(5, 2, size=n), 0, 1)
    occupancy = np.where(
        from_channel_manager,
        (fraction * 100).round(1),
        fraction.round(4),
    )

    return pd.DataFrame(
        {
            "hotel_ref": hotel_ref,
            "rate_date": rate_date.strftime("%Y-%m-%d"),
            "avg_rate": avg_rate,
            "comp_rate": comp,
            "occupancy": occupancy,
            "source_system": np.where(from_channel_manager, "channel_manager", "pms"),
        }
    )
