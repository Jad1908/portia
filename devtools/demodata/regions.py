"""``regions.csv`` — the country-name to ISO2 bridge. Small, and load-bearing.

This is the demo's stand-in for the mapping file the real pipeline flattens out
of JSON. It matters because `properties` cannot be joined to `events` directly:
properties spells a country as a name (``THE NETHERLANDS``), the event feed
spells it as ISO2 (``NL``), and the only thing that connects them is this table.

**The planted problem is what it leaves out.** The lookup is keyed on an
uppercase, trimmed country name, and `properties` carries variants that are not
in here at all — ``HOLLAND``, ``UAE``, ``USA``, ``UK``, ``KOREA (SOUTH)``. An
inner join through this table drops those hotels without saying anything, which
is the quiet kind of loss the copilot is supposed to surface rather than the
loud kind that errors.
"""

from __future__ import annotations

import pandas as pd

from devtools.demodata.geography import CITIES

#: Continental grouping, for the column that makes this look like a real
#: reference table rather than a two-column crib.
_REGION_OF: dict[str, str] = {
    "FR": "EMEA",
    "GB": "EMEA",
    "DE": "EMEA",
    "NL": "EMEA",
    "ES": "EMEA",
    "IT": "EMEA",
    "TR": "EMEA",
    "AE": "EMEA",
    "SA": "EMEA",
    "US": "AMER",
    "CA": "AMER",
    "BR": "AMER",
    "SG": "APAC",
    "TH": "APAC",
    "HK": "APAC",
    "CN": "APAC",
    "KR": "APAC",
    "JP": "APAC",
    "AU": "APAC",
    "ID": "APAC",
}

#: Countries the reference table knows but no hotel and no event sits in. A
#: lookup that covers exactly the rows that use it is a lookup somebody built
#: for this demo; a real one carries the rest of the world too.
_UNUSED = (
    ("BELGIUM", "BE", "EMEA"),
    ("PORTUGAL", "PT", "EMEA"),
    ("SWEDEN", "SE", "EMEA"),
    ("NORWAY", "NO", "EMEA"),
    ("DENMARK", "DK", "EMEA"),
    ("IRELAND", "IE", "EMEA"),
    ("POLAND", "PL", "EMEA"),
    ("GREECE", "GR", "EMEA"),
    ("MEXICO", "MX", "AMER"),
    ("ARGENTINA", "AR", "AMER"),
    ("CHILE", "CL", "AMER"),
    ("INDIA", "IN", "APAC"),
    ("VIETNAM", "VN", "APAC"),
    ("MALAYSIA", "MY", "APAC"),
    ("NEW ZEALAND", "NZ", "APAC"),
)


def regions() -> pd.DataFrame:
    """The canonical country name -> ISO2 mapping, uppercase and trimmed."""
    seen: dict[str, tuple[str, str]] = {}
    for city in CITIES:
        seen[city.country_name] = (city.iso2, _REGION_OF[city.iso2])
    rows = [(name, iso2, region) for name, (iso2, region) in seen.items()]
    rows.extend(_UNUSED)
    rows.sort()
    return pd.DataFrame(rows, columns=["country_name", "country_iso2", "region"])
