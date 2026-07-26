"""The execution layer — operations that actually transform data.

Distinct from ``checks`` (read-only diagnosis): an op *produces* a table. Every
op returns an :class:`OpResult` — the output frame plus a JSON-serializable
**provenance** report that is always produced, never suppressed (the drop report
is the default output, per docs/brief.md).

Like the checks layer, ops sit behind one interface so pandas → DuckDB/Snowflake
stays a swap, not a rewrite.
"""

from portia.ops.base import OpResult
from portia.ops.join import apply_join
from portia.ops.normalize import apply_normalize
from portia.ops.sql import SqlNotAllowed, apply_sql

__all__ = ["OpResult", "SqlNotAllowed", "apply_join", "apply_normalize", "apply_sql"]
