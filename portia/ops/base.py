"""Shared types for the execution layer."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class OpResult:
    """The output of an operation: the data, and the provenance of producing it.

    ``frame`` is the produced table. ``provenance`` is the JSON-serializable drop
    report — always produced, never suppressed.
    """

    frame: pd.DataFrame
    provenance: dict
