"""Normalize/coerce columns — the resolution to what the profiler flags.

Where `checks.profiling` *reports* `numeric_stored_as_text`, whitespace, or a key
dtype that blocks a join, this op *fixes* it — deterministically, and with a
provenance record of exactly what changed and, crucially, **what failed to
convert**. Coercion that silently turns unparseable values into nulls is the
classic footgun; here every failure is counted and sampled (never silent).

Transforms (per column): ``strip``, ``lower``, ``to_numeric``, ``to_string``.

Each transform rewrites one column of a relation, so a chain of them is a chain
of projections and nothing is materialized. The counts are the only work: one
aggregate per transform, measured *before* the column is replaced, because
"how many values changed" is a question about the column that is about to stop
existing.
"""

from __future__ import annotations

from portia.core.serialize import to_jsonable
from portia.core.table import Table, quote_ident
from portia.ops.base import OpResult, named_from

SAMPLE_FAILED = 5

#: Every field this op reports — see ``ops.join.PROVENANCE_KEYS`` for why.
#: `tests/test_ops_normalize.py` asserts this matches a real run.
PROVENANCE_KEYS = frozenset({"op", "input_rows", "transforms", "flags"})

#: The transforms a step may ask for. Declared next to the dispatch so callers
#: can validate a step *before* it is written to a spec, rather than discovering
#: a typo when the spec is re-run months later. `tests/test_ops_normalize.py`
#: asserts this matches what `_expression` actually accepts.
TRANSFORM_OPS = frozenset({"strip", "lower", "to_numeric", "to_string"})

#: What a value becomes, per transform. ``{col}`` is the quoted column.
#:
#: ``strip`` and ``lower`` read the column as text first, because that is what
#: pandas' ``astype("string")`` did and a spec may legitimately strip a column
#: the sniffer decided was numeric. ``try_cast`` is the whole point of
#: ``to_numeric``: it yields NULL rather than raising, so the failures can be
#: counted and shown instead of aborting the step.
_EXPRESSIONS = {
    "strip": "trim(CAST({col} AS VARCHAR))",
    "lower": "lower(CAST({col} AS VARCHAR))",
    "to_numeric": "try_cast({col} AS DOUBLE)",
    "to_string": "CAST({col} AS VARCHAR)",
}


def apply_normalize(table: Table, transforms: list[dict], *, name: str | None = None) -> OpResult:
    """Apply an ordered list of column transforms, returning the new table +
    a provenance report. Each transform is ``{"column": ..., "op": ...}``."""
    out = table
    # The compiled chain starts from the input's *name* and wraps the same way the
    # executed one does, so N transforms give N nested projections either way.
    compiled = named_from(table)
    records = []
    for t in transforms:
        col, op = t["column"], t["op"]
        if col not in out.columns:
            raise ValueError(f"normalize: no such column {col!r}")
        if op not in _EXPRESSIONS:
            raise ValueError(f"normalize: unknown transform op {op!r}")

        expression = _EXPRESSIONS[op].format(col=quote_ident(col))
        records.append({"column": col, "op": op, **_measure(out, col, op, expression)})
        if op == "to_numeric" and t.get("fill") is not None:
            expression = f"coalesce({expression}, {float(t['fill'])})"
        compiled = _project(out.columns, col, expression, compiled)
        out = _replace(out, col, expression, name or table.name)

    # A normalize with no transforms is a pass-through, and `SELECT *` says so
    # more honestly in a file than an empty projection would.
    compiled = compiled if transforms else f"SELECT * FROM {compiled}"

    provenance = {
        "op": "normalize",
        "input_rows": table.count(),
        "transforms": records,
        "flags": ["coercion_failures"] if any(r.get("n_failed", 0) for r in records) else [],
    }
    return OpResult(table=out, provenance=provenance, compiled=compiled)


def _project(columns: list[str], column: str, expression: str, from_item: str) -> str:
    """One column rewritten in place, keeping the order — the SELECT, as text.

    Shared by the executed and the compiled chain; only ``from_item`` differs
    (a nested sub-query vs. the input's name). See `ops.base.OpResult.compiled`.
    """
    select = ", ".join(
        f"{expression} AS {quote_ident(c)}" if c == column else quote_ident(c) for c in columns
    )
    return f"SELECT {select} FROM {from_item}"


def _replace(table: Table, column: str, expression: str, name: str) -> Table:
    """The same relation with one column rewritten, in place, keeping the order."""
    query = _project(table.columns, column, expression, f"({table.query})")
    return Table(name=name, query=query, con=table.con)


def _measure(table: Table, column: str, op: str, expression: str) -> dict:
    """What this transform did, counted before the column is replaced."""
    q = quote_ident(column)
    if op == "to_numeric":
        failed = f"{q} IS NOT NULL AND {expression} IS NULL"
        counts = table.row(
            {
                "n_converted": f"count({expression})",
                "n_failed": f"count(*) FILTER (WHERE {failed})",
            }
        )
        record: dict = {
            "n_converted": int(counts["n_converted"]),
            "n_failed": int(counts["n_failed"]),
        }
        if record["n_failed"]:
            record["sample_failed"] = _sample_failed(table, q, failed)
        return record

    if op == "to_string":
        # Every non-null value is restated as text; nulls stay null.
        return {"n_changed": int(table.scalar(f"count({q})"))}

    # strip / lower: changed means "was not already in that form", nulls excluded.
    text = f"CAST({q} AS VARCHAR)"
    changed = f"{text} IS NOT NULL AND {text} <> {expression}"
    return {"n_changed": int(table.scalar(f"count(*) FILTER (WHERE {changed})"))}


def _sample_failed(table: Table, quoted: str, failed: str) -> list:
    """Values that would not convert. Ordered, for the reason samples always are."""
    rows = table.sql(f"SELECT {quoted} FROM {table.ref} WHERE {failed} ORDER BY 1").rows(
        SAMPLE_FAILED
    )
    return [to_jsonable(v) for (v,) in rows]


def render_text(provenance: dict) -> str:
    """Human-readable normalize report for the CLI."""
    lines = [f"normalize  ({provenance['input_rows']} rows)"]
    for r in provenance["transforms"]:
        line = f"  {r['column']}: {r['op']}"
        if r["op"] == "to_numeric":
            line += f"  converted {r['n_converted']}, failed {r['n_failed']}"
            if r.get("sample_failed"):
                line += f"  e.g. {r['sample_failed']}"
        else:
            line += f"  changed {r.get('n_changed', 0)}"
        lines.append(line)
    if provenance["flags"]:
        lines.append(f"  ⚑ {', '.join(provenance['flags'])}")
    return "\n".join(lines)
