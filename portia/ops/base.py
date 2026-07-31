"""Shared types for the execution layer."""

from __future__ import annotations

from dataclasses import dataclass

from portia.core.table import Table, quote_ident


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
    #: The same SELECT as ``table.query``, but reading its inputs **by name**
    #: instead of by nested sub-query — the form that compiles to a CTE and
    #: therefore to a readable ``.sql`` file (`docs/PIPELINE.md` §3).
    #:
    #: Produced by the *same* builder that produced ``table.query``, called a
    #: second time with a different FROM item. That is deliberate and it is the
    #: whole safety argument: compilation is not a second implementation that can
    #: drift from execution, it is one implementation parameterized on how its
    #: inputs are named. `tests/test_compile.py` still pins them together by
    #: running both and comparing the tables.
    compiled: str = ""


def named_from(table: Table, alias: str = "") -> str:
    """The FROM item that reads ``table`` by its **name** — the compiled form.

    The counterpart of :attr:`Table.ref`, which reads it as a nested sub-query.
    An op builds its SQL once with `ref` (to execute now, against tables that
    exist on a connection) and once with this (to be written into a file, where
    the name will be a CTE or a real table).
    """
    ref = quote_ident(table.name)
    return f"{ref} AS {quote_ident(alias)}" if alias else ref
