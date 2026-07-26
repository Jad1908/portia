"""The escape hatch — a transform we didn't prewrite, authored as DuckDB SQL.

`join` and `normalize` cover what we anticipated. The hotel fixture proves that
isn't enough: correctly handling its fatal fan-out means reducing events to one
row per city-date *before* joining, and there is no aggregate op. A capable model
worked that out unaided, said there was no op for it, and stopped
(docs/EVALUATION.md, Run 6). Verification turns wrong answers into blocks; this
is what unblocks them.

**Why SQL and not captured Python** (decided 2026-07-25, docs/BACKLOG.md): the
spec's whole claim is being reviewable in a pull request, and a 40-line pandas
function embedded in YAML is not. Arbitrary Python also hands back the filesystem
and network that `agent/session.py` deliberately withholds. SQL's semantics are
stable across versions where pandas' are not, and it is the only option that
survives the pandas → DuckDB → Snowflake seam instead of needing a rewrite per
step. The cost is real — stats-heavy transforms will be awkward — **and that
friction is the instrument**: an expressive hatch is a worse measuring device,
because what the agent strains to express is what tells us which op to promote.

**The line this moves.** An agent authoring transforms is close to an agent
authoring analysis, which this project forbids. What preserves the guarantee is
that a custom step is captured verbatim, measured by the same harness as every
other op, and is a *step* rather than a hidden reasoning act. So the rule
tightens rather than bends: **the agent may author a transform; it may never
author a number.**
"""

from __future__ import annotations

import re

import pandas as pd

from portia.ops.base import OpResult

#: Every field this op reports — see ``ops.join.PROVENANCE_KEYS`` for why.
#: `tests/test_ops_sql.py` asserts this matches a real run.
#:
#: Deliberately thinner than `join`'s. A join knows what it dropped because it
#: knows what a key is; arbitrary SQL does not, and inventing a "rows_dropped"
#: for it would be a number the engine cannot stand behind. What a SQL step
#: predicts is its shape — and `checks.outcome` still measures the table it
#: produced, which is where the real safety net lives.
PROVENANCE_KEYS = frozenset(
    {"op", "sql", "inputs", "input_rows", "result_rows", "columns", "flags"}
)

#: A SQL step must be exactly one read. Everything else — writing a file,
#: reading one, attaching a database, installing an extension, or chaining a
#: second statement after the SELECT — is refused before DuckDB ever sees it.
#: This is the same boundary `session.py` draws by giving the agent no
#: filesystem tools; the hatch must not quietly hand it back.
STATEMENT_START = ("select", "with")

#: Refused outright, anywhere in the statement. `enable_external_access=False`
#: already stops most of these inside DuckDB — this is the readable half of the
#: pair, so a rejected step can say *why* rather than surfacing a DuckDB error.
FORBIDDEN = (
    "attach",
    "copy",
    "export",
    "force",
    "install",
    "load",
    "read_csv",
    "read_json",
    "read_parquet",
    "set",
)

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_WORD = re.compile(r"[a-z_][a-z0-9_]*")


class SqlNotAllowed(ValueError):
    """The statement is not a single read. Raised before DuckDB is touched."""


def check_sql(sql: str) -> None:
    """Refuse anything that isn't one plain SELECT, with a reason a human reads.

    Deliberately conservative and deliberately dumb: it works on the stripped
    text rather than a parse tree, so it errs toward refusing something valid
    rather than admitting something clever. `enable_external_access=False` in
    :func:`apply_sql` is the half that doesn't rely on reading the string right.
    """
    stripped = _COMMENT.sub(" ", sql).strip().rstrip(";")
    if not stripped:
        raise SqlNotAllowed("the sql is empty")

    words = _WORD.findall(stripped.lower())
    if not words or words[0] not in STATEMENT_START:
        raise SqlNotAllowed(
            f"a sql step must be a single SELECT (or WITH … SELECT), not {words[0].upper()!r}"
            if words
            else "a sql step must be a single SELECT (or WITH … SELECT)"
        )
    if ";" in stripped:
        raise SqlNotAllowed("one statement per step — remove the ';' and everything after it")

    forbidden = sorted(set(words) & set(FORBIDDEN))
    if forbidden:
        raise SqlNotAllowed(
            f"{', '.join(w.upper() for w in forbidden)} is not allowed in a sql step — "
            "a step reads the tables it declares in 'inputs' and nothing else"
        )


def apply_sql(frames: dict[str, pd.DataFrame], sql: str) -> OpResult:
    """Run one SELECT over the named frames, returning the table + provenance.

    ``frames`` is the step's *declared* inputs, and only those are registered:
    naming a table the step didn't declare is an error rather than a silent
    dependency. That declaration is what lets `checks.outcome` still report
    which input contributed nothing — the measurement Run 2 didn't have.
    """
    import duckdb

    check_sql(sql)

    # Read-only by construction: an in-memory database with no external access,
    # so `COPY … TO`, `read_csv()` and extension installs fail inside DuckDB even
    # if `check_sql` were fooled into passing them through.
    con = duckdb.connect(":memory:", config={"enable_external_access": False})
    try:
        for name, frame in frames.items():
            con.register(name, frame)
        out = con.execute(sql).fetch_df()
    finally:
        con.close()

    provenance = {
        "op": "sql",
        "sql": sql.strip(),
        "inputs": sorted(frames),
        "input_rows": {name: int(len(f)) for name, f in frames.items()},
        "result_rows": int(len(out)),
        "columns": [str(c) for c in out.columns],
        "flags": [],
    }
    return OpResult(frame=out, provenance=provenance)


def render_text(provenance: dict) -> str:
    """Human-readable SQL step summary, for the CLI."""
    rows = ", ".join(f"{name} {n}" for name, n in provenance["input_rows"].items())
    lines = [
        f"sql over {rows} rows → {provenance['result_rows']} rows "
        f"× {len(provenance['columns'])} cols",
    ]
    lines += [f"    {line}" for line in provenance["sql"].splitlines()]
    return "\n".join(lines)
