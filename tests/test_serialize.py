"""Evidence must be plain JSON — whichever tier produced the value."""

from __future__ import annotations

import datetime as dt
import json
import math
import uuid
from decimal import Decimal

import numpy as np
import pytest

from portia.core.serialize import round_float, to_json, to_jsonable


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (np.int64(7), 7),
        (np.float64(1.23456789), 1.2346),
        (np.bool_(False), False),
        (float("nan"), None),
        (float("inf"), None),
        # DuckDB's own scalars, which the pandas-era coercion never anticipated.
        (Decimal("1.5"), 1.5),
        (Decimal("2.345678"), 2.3457),
        (dt.date(2026, 6, 12), "2026-06-12"),
        (dt.datetime(2026, 6, 12, 9, 30), "2026-06-12 09:30:00"),
        (uuid.UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
    ],
)
def test_coerces_to_json_values(value, expected):
    assert to_jsonable(value) == expected


def test_a_decimal_stays_a_number_rather_than_becoming_a_string():
    """The str() fallback is right for a date and wrong for a price."""
    assert isinstance(to_jsonable(Decimal("1.5")), float)


def test_a_decimal_nan_is_null_like_every_other_nan():
    assert to_jsonable(Decimal("NaN")) is None
    assert to_jsonable(Decimal("Infinity")) is None


def test_everything_it_returns_is_valid_json():
    values = [Decimal("1.5"), dt.date(2026, 6, 12), np.int64(3), float("nan"), "x", True]
    encoded = json.dumps([to_jsonable(v) for v in values], allow_nan=False)
    assert json.loads(encoded) == [1.5, "2026-06-12", 3, None, "x", True]


def test_round_float_is_the_one_rounding_rule():
    assert round_float(1.23456789) == 1.2346
    assert round_float(np.float64(2.0)) == 2.0


def test_to_json_is_readable_and_keeps_unicode():
    assert to_json({"city": "Zürich"}) == '{\n  "city": "Zürich"\n}'


def test_nan_never_reaches_a_json_document():
    assert not math.isnan(0.0)
    assert json.loads(to_json({"rate": to_jsonable(float("nan"))})) == {"rate": None}
