"""``properties.csv`` — the hotel dimension, and the demo's dirtiest table.

It is the bridge in exactly the way `docs/../portia/fixtures/hotels.py` describes
in miniature: bookings know a property code and nothing about geography, events
know a country and coordinates, and only this table carries both. Everything
planted here therefore costs something downstream rather than being cosmetic.

Planted problems, roughly in the order they bite:

- **The code is not unique.** ``DUPLICATE_CODES`` properties appear twice, as a
  re-exported snapshot with a later ``last_updated`` and a drifted room count.
  A declared grain of ``property_code`` is not unique, which is one of the few
  things allowed to stop the loop (`checks.outcome.BLOCKING_FLAGS`), and it is
  the first thing a profile shows.
- **``country_iso`` is null for every French property**, and ~60% null overall.
  This is the real finding from the pipeline this demo mirrors, and it is why
  the `regions` lookup has to exist: the column that looks like the join key is
  the one you cannot use.
- **``country_name`` has variants the lookup does not carry** — ``HOLLAND``,
  ``UAE``, ``USA``, ``UK``, ``KOREA (SOUTH)`` — plus case and whitespace noise.
  ``UPPER(TRIM(...))`` fixes the noise and does nothing for the variants, so a
  step that looks like it worked still drops those hotels.
- **~14% have no coordinates**, so the distance bridge silently loses them on an
  inner join. Nothing errors; the hotel count just gets smaller.
- **``room_count`` is text.** Most rows are plain integers, but the export
  writes anything over a thousand rooms as ``1,200``, and one separator in one
  row makes the whole column a string on both tiers. `core/io` keeps that
  reportable on purpose rather than sniffing it away, so it lands as a profile
  fact and not as a silent cast — and the rows it costs you are the *largest*
  properties, which is the worst subset to quietly drop from a room-night sum.
- **``opened_on`` mixes date formats** (ISO and ``DD/MM/YYYY``), same effect.
- **``property_type`` is not all hotels** — offices and residences are in the
  export and must be filtered, which the real pipeline does too.
- **``loyalty_tier`` is ~99.6% null**, a column that is present in the schema and
  carries almost nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from devtools.demodata.geography import CITIES, KM_PER_DEGREE

SEED = 20260812
N_PROPERTIES = 12_000

#: Properties re-exported as a second, later snapshot row under the same code.
DUPLICATE_CODES = 240

#: Share of properties whose coordinates never made it into the golden record.
MISSING_COORD_RATE = 0.14

#: Share of *non-French* properties missing the ISO code. France is missing it
#: entirely and separately — see the module docstring.
MISSING_ISO_RATE = 0.55

#: Share of properties large enough to be written with a thousands separator.
#: Convention hotels are genuinely this big, and the export formats anything
#: over a thousand rooms as ``1,200`` — which is what turns the whole column
#: into text. Tied to the *value* rather than sprinkled at random, because that
#: is how a source system actually produces this: it is a formatting rule, so
#: whether a row is text is a fact about the row.
LARGE_PROPERTY_RATE = 0.06

#: The room count above which the export writes a thousands separator.
SEPARATOR_THRESHOLD = 1_000

#: Share of open dates written ``DD/MM/YYYY`` instead of ISO.
NON_ISO_DATE_RATE = 0.06

#: Share of rows carrying a loyalty tier at all.
LOYALTY_TIER_RATE = 0.004

#: How far a property may sit from its city centre, in km.
CITY_SPREAD_KM = 9.0

_BRANDS = (
    "Nordis Collection",
    "Nordis Express",
    "Meridian Court",
    "Meridian Court Suites",
    "Halcyon Resorts",
    "Kestrel Inns",
    "Atlas Grand",
    "Verano Residences",
)

#: Weighted so hotels dominate but the export is not clean.
_TYPES = ("HOTEL", "HOTEL", "HOTEL", "HOTEL", "RESORT", "APARTHOTEL", "OFFICE", "RESIDENCE")

_STATUSES = ("open", "open", "open", "open", "open", "closed", "pipeline")

_LOYALTY_TIERS = ("BRONZE", "SILVER", "GOLD", "PLATINUM")

#: How a source system may spell a country it already has a canonical name for.
#: The first four are *unmappable* by the `regions` lookup — that is the point.
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "THE NETHERLANDS": ("HOLLAND",),
    "UNITED ARAB EMIRATES": ("UAE",),
    "UNITED STATES OF AMERICA": ("USA",),
    "UNITED KINGDOM": ("UK",),
    "KOREA(SOUTH)- REPUBLIC": ("KOREA (SOUTH)",),
}

#: Share of rows in an aliased country that use the unmappable alias.
ALIAS_RATE = 0.18

#: Share of country names given cosmetic case/whitespace damage. Fixable with
#: ``UPPER(TRIM(...))``, unlike the aliases above.
COUNTRY_NOISE_RATE = 0.12

_NAME_PREFIXES = (
    "Grand",
    "Old Town",
    "Riverside",
    "Central",
    "Harbour",
    "Park",
    "Garden",
    "Metro",
    "Royal",
    "City",
)
_NAME_SUFFIXES = ("Hotel", "Inn", "Suites", "Lodge", "House", "Place", "Tower", "Residence")


def _format_rooms(value: int) -> str:
    """The export's own formatting rule: a separator above the threshold."""
    return f"{value:,}" if value >= SEPARATOR_THRESHOLD else str(value)


def _noisy_country(name: str, rng: np.random.Generator) -> str:
    """Case and whitespace damage — the kind ``UPPER(TRIM())`` actually fixes."""
    style = rng.integers(0, 4)
    if style == 0:
        return f" {name}"
    if style == 1:
        return f"{name} "
    if style == 2:
        return name.title()
    return name.lower()


def properties() -> pd.DataFrame:
    """The property dimension, as a golden-record export would hand it over."""
    rng = np.random.default_rng(SEED)
    n = N_PROPERTIES

    city_idx = rng.integers(0, len(CITIES), size=n)
    cities = [CITIES[i] for i in city_idx]

    codes = [f"HN{i:05d}" for i in range(1, n + 1)]

    names = [
        f"{_NAME_PREFIXES[rng.integers(0, len(_NAME_PREFIXES))]} "
        f"{_NAME_SUFFIXES[rng.integers(0, len(_NAME_SUFFIXES))]}"
        for _ in range(n)
    ]

    # Coordinates: jitter around the city centre, then blank some out entirely.
    spread_deg = CITY_SPREAD_KM / KM_PER_DEGREE
    lat = np.array([c.lat for c in cities]) + rng.normal(0, spread_deg, n)
    lon = np.array([c.lon for c in cities]) + rng.normal(0, spread_deg, n)
    missing_coords = rng.random(n) < MISSING_COORD_RATE
    lat = np.where(missing_coords, np.nan, lat.round(6))
    lon = np.where(missing_coords, np.nan, lon.round(6))

    # Country name: alias for some, cosmetic noise for others.
    country_name = []
    for city in cities:
        name = city.country_name
        aliases = _COUNTRY_ALIASES.get(name)
        if aliases is not None and rng.random() < ALIAS_RATE:
            name = aliases[rng.integers(0, len(aliases))]
        elif rng.random() < COUNTRY_NOISE_RATE:
            name = _noisy_country(name, rng)
        country_name.append(name)

    # The ISO column that looks like the join key and is not one: absent for
    # every French property, and mostly absent everywhere else.
    iso = np.array([c.iso2 for c in cities], dtype=object)
    is_france = np.array([c.iso2 == "FR" for c in cities])
    drop_iso = is_france | (rng.random(n) < MISSING_ISO_RATE)
    iso = np.where(drop_iso, None, iso)

    rooms = np.where(
        rng.random(n) < LARGE_PROPERTY_RATE,
        rng.integers(SEPARATOR_THRESHOLD, 2_400, size=n),
        rng.integers(28, 640, size=n),
    )
    room_count = [_format_rooms(int(v)) for v in rooms]

    opened = pd.to_datetime("1985-01-01") + pd.to_timedelta(
        rng.integers(0, 365 * 40, size=n), unit="D"
    )
    non_iso = rng.random(n) < NON_ISO_DATE_RATE
    opened_on = [
        d.strftime("%d/%m/%Y") if flag else d.strftime("%Y-%m-%d")
        for d, flag in zip(opened, non_iso, strict=True)
    ]

    tiers = np.where(
        rng.random(n) < LOYALTY_TIER_RATE,
        rng.choice(_LOYALTY_TIERS, size=n),
        None,
    )

    frame = pd.DataFrame(
        {
            "property_code": codes,
            "property_name": names,
            "brand": rng.choice(_BRANDS, size=n),
            "property_type": rng.choice(_TYPES, size=n),
            "city": [c.name for c in cities],
            "country_name": country_name,
            "country_iso": iso,
            "latitude": lat,
            "longitude": lon,
            "room_count": room_count,
            "opened_on": opened_on,
            "status": rng.choice(_STATUSES, size=n),
            "loyalty_tier": tiers,
            "last_updated": pd.Timestamp("2026-07-01"),
        }
    )

    return _append_snapshot_duplicates(frame, rng)


def _append_snapshot_duplicates(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Re-export some properties as a later snapshot under the same code.

    This is what makes ``property_code`` non-unique, and it is deliberately not
    an exact duplicate: the room count has drifted and ``last_updated`` is
    later, so "just deduplicate" is a decision about *which row wins* rather
    than a tidy-up. Both rows are real; only one is current.
    """
    picked = rng.choice(frame.index, size=DUPLICATE_CODES, replace=False)
    dupes = frame.loc[picked].copy()
    drift = rng.integers(-15, 16, size=len(dupes))
    dupes["room_count"] = [
        _format_rooms(max(int(str(v).replace(",", "")) + d, 10))
        for v, d in zip(dupes["room_count"], drift, strict=True)
    ]
    dupes["last_updated"] = pd.Timestamp("2026-07-28")
    out = pd.concat([frame, dupes], ignore_index=True)
    return out.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
