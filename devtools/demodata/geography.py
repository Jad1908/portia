"""The one place a city's name, country and coordinates are written down.

Both `properties` and `events` need to put things on a map, and they have to
agree: an event 800 m from a hotel is the whole point of the bridge, so the two
builders cannot each invent their own coordinates. They draw from this table and
jitter around it.

The country list is the seventeen the real feed carries, which is what makes the
`regions` lookup a real bridge rather than decoration — see
`devtools/demodata/regions.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    """A place, its country as the *feed* spells it, and where it is."""

    name: str
    country_name: str
    iso2: str
    lat: float
    lon: float


# Real coordinates, so a 15 km radius means something on a map and two hotels in
# the same city genuinely fall inside each other's catchment.
CITIES: tuple[City, ...] = (
    City("Paris", "FRANCE", "FR", 48.8566, 2.3522),
    City("Lyon", "FRANCE", "FR", 45.7640, 4.8357),
    City("Nice", "FRANCE", "FR", 43.7102, 7.2620),
    City("London", "UNITED KINGDOM", "GB", 51.5072, -0.1276),
    City("Manchester", "UNITED KINGDOM", "GB", 53.4808, -2.2426),
    City("Edinburgh", "UNITED KINGDOM", "GB", 55.9533, -3.1883),
    City("Berlin", "GERMANY", "DE", 52.5200, 13.4050),
    City("Munich", "GERMANY", "DE", 48.1351, 11.5820),
    City("Frankfurt", "GERMANY", "DE", 50.1109, 8.6821),
    City("Amsterdam", "THE NETHERLANDS", "NL", 52.3676, 4.9041),
    City("Rotterdam", "THE NETHERLANDS", "NL", 51.9244, 4.4777),
    City("Barcelona", "SPAIN", "ES", 41.3874, 2.1686),
    City("Madrid", "SPAIN", "ES", 40.4168, -3.7038),
    City("Milan", "ITALY", "IT", 45.4642, 9.1900),
    City("Rome", "ITALY", "IT", 41.9028, 12.4964),
    City("New York", "UNITED STATES OF AMERICA", "US", 40.7128, -74.0060),
    City("Chicago", "UNITED STATES OF AMERICA", "US", 41.8781, -87.6298),
    City("Los Angeles", "UNITED STATES OF AMERICA", "US", 34.0522, -118.2437),
    City("Miami", "UNITED STATES OF AMERICA", "US", 25.7617, -80.1918),
    City("Toronto", "CANADA", "CA", 43.6532, -79.3832),
    City("Vancouver", "CANADA", "CA", 49.2827, -123.1207),
    City("Sao Paulo", "BRAZIL", "BR", -23.5505, -46.6333),
    City("Dubai", "UNITED ARAB EMIRATES", "AE", 25.2048, 55.2708),
    City("Abu Dhabi", "UNITED ARAB EMIRATES", "AE", 24.4539, 54.3773),
    City("Riyadh", "SAUDI ARABIA", "SA", 24.7136, 46.6753),
    City("Istanbul", "TURKEY", "TR", 41.0082, 28.9784),
    City("Singapore", "SINGAPORE", "SG", 1.3521, 103.8198),
    City("Bangkok", "THAILAND", "TH", 13.7563, 100.5018),
    City("Hong Kong", "HONG KONG, SAR", "HK", 22.3193, 114.1694),
    City("Shanghai", "MAINLAND CHINA", "CN", 31.2304, 121.4737),
    City("Seoul", "KOREA(SOUTH)- REPUBLIC", "KR", 37.5665, 126.9780),
    City("Tokyo", "JAPAN", "JP", 35.6762, 139.6503),
    City("Sydney", "AUSTRALIA", "AU", -33.8688, 151.2093),
    City("Melbourne", "AUSTRALIA", "AU", -37.8136, 144.9631),
    City("Jakarta", "INDONESIA", "ID", -6.2088, 106.8456),
)

#: Roughly 1 degree of latitude in km — enough to jitter a coordinate by a
#: believable few kilometres without pulling in a projection library.
KM_PER_DEGREE = 111.0
