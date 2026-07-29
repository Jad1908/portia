"""Shared foundation seams every tool builds on: loading, serialization, display.

Nothing project-specific lives here — just the things every surface needs to do
the same way (get a table from a path; emit compact JSON evidence; render a
measured value for a human).

`table` and `store` are the scale tier: a :class:`Table` is a handle rather than
data, and the store is where a project's ingested sources live
(`docs/DUCKDB_MIGRATION.md`).
"""

from portia.core.io import find_data_files, load_frame, load_table, supported_suffixes
from portia.core.present import as_yaml, count, format_rate, inline, scalar
from portia.core.serialize import round_float, to_json, to_jsonable
from portia.core.table import Table

__all__ = [
    "load_frame",
    "load_table",
    "Table",
    "find_data_files",
    "supported_suffixes",
    "round_float",
    "to_json",
    "to_jsonable",
    "format_rate",
    "count",
    "inline",
    "scalar",
    "as_yaml",
]
