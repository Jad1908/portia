"""Shared types for the execution layer."""

from __future__ import annotations

from dataclasses import dataclass

from portia.core.table import Table


@dataclass
class OpResult:
    """The output of an operation: the data, and the provenance of producing it.

    ``table`` is the produced table — a lazy handle, not rows, so a step that
    multiplies 2M rows by 40 costs the same to *produce* as one that filters.
    ``provenance`` is the JSON-serializable drop report — always produced, never
    suppressed.
    """

    table: Table
    provenance: dict
