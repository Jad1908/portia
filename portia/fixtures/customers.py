"""Mock customer table. One builder, one job: plant known traps.

Sole purpose: prove the building blocks work as intended (see the working
agreement — real data is reserved for the agentic loop, not for unit tests).
Seeded, so re-generating yields byte-identical files.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def messy_customers(n: int = 40, seed: int = 7) -> pd.DataFrame:
    """A dirty customer table. Planted traps, by column:

    - customer_id          : unique, non-null  -> possible_key
    - name                 : free text         -> high_cardinality
    - country              : low cardinality   -> categorical
    - signup_amount        : numbers as text, whitespace, 'N/A'/'pending' -> numeric_stored_as_text
    - notes                : >50% missing       -> high_null
    - source               : one value          -> constant
    - legacy_col           : entirely empty      -> all_null
    - mixed_ref            : ints and strings    -> mixed_types
    """
    rng = np.random.default_rng(seed)
    countries = ["DE", "FR", "US", "UK"]
    names = [f"Firm {chr(65 + (i % 26))}{i}" for i in range(n)]
    # A couple of repeated names so the column is high-cardinality but *not* a
    # key — real customer tables have the odd duplicate.
    names[1] = names[2] = names[0]

    amounts = [
        (f" {round(float(rng.uniform(10, 9999)), 2)} " if i % 5 == 0
         else f"{round(float(rng.uniform(10, 9999)), 2)}")
        for i in range(n)
    ]
    amounts[3] = amounts[2]  # one repeated amount -> not unique
    # A little non-numeric contamination. Two effects, both intended: it keeps
    # the column as text through a CSV round-trip (pandas won't auto-parse a
    # column with 'N/A' in it), so the numeric_stored_as_text trap survives to
    # disk; and it's a realistic mess a copilot should ask about.
    amounts[11], amounts[27] = "N/A", "pending"

    return pd.DataFrame(
        {
            "customer_id": range(1000, 1000 + n),
            "name": names,
            "country": rng.choice(countries, size=n).tolist(),
            # numbers stored as strings, some with stray whitespace
            "signup_amount": amounts,
            # >50% missing
            "notes": [f"note {i}" if i % 3 == 0 else None for i in range(n)],
            "source": ["import_v2"] * n,
            "legacy_col": [None] * n,
            # mixed python types in one object column
            "mixed_ref": [i if i % 2 == 0 else f"ref-{i}" for i in range(n)],
        }
    )
