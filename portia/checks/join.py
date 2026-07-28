"""Join/merge check — the unsuppressable drop report.

Diagnoses what a join between two tables *would* do, without materializing it.
The result size, dropped rows, and fan-out are computed from the **key columns
alone** (set operations + multiplicity counts), so the report is honest at scale:
we can say a join explodes 50M rows to 2B without ever building it (docs/PLAN.md,
"schemas + samples, never full data").

That claim was aspirational while the implementation was pandas — the counts were
honest, but getting them still meant holding both frames in memory. The SQL
implementation is the one that delivers it: an 80M-row fan-out is *counted* in a
GROUP BY and a sum, and never built.

This is diagnosis only — read-only, mutates nothing. It surfaces **facts** for a
reasoning agent to judge; it never ranks, prioritizes, or recommends — that is
the agent's job, with context the engine can't have (see CLAUDE.md, facts vs
judgment). `join_findings` layers row-level examples on top of the report so the
agent can weigh materiality from real rows, not just counts.

The exact result-row formula, per join type, from key multiplicities:
    inner  = Σ_{k in shared}  mult_left[k] * mult_right[k]
    left   = inner + (left rows whose key is unmatched or null)
    right  = inner + (right rows whose key is unmatched or null)
    outer  = inner + both of the above

**Two implementations, one set of rules**, as in `checks.profiling`: everything
that turns measurements into a report — `_assemble`, `_relationship`, `_flags` —
takes plain numbers and is shared, so the tiers cannot drift into two subtly
different reports.
"""

from __future__ import annotations

from typing import Any

from portia.checks.profiling import BOOLEAN, DATETIME, NUMERIC_KINDS, duckdb_kind
from portia.core.serialize import round_float, to_jsonable
from portia.core.table import Table, quote_ident

SAMPLE_KEYS = 5  # example unmatched keys shown per side
SAMPLE_ROWS = 3  # example rows shown per anomaly in join_findings
LOW_COVERAGE = 0.5  # left match rate below this -> "low_overlap"


def join_report(
    left: Table,
    right: Table,
    on: str | list[str] | None = None,
    *,
    left_on: str | list[str] | None = None,
    right_on: str | list[str] | None = None,
) -> dict:
    """:func:`join_report`, measured in SQL. Nothing is materialized.

    Both tables must live on the same connection — a join is between two things
    the database can see at once.
    """
    lkeys, rkeys = _resolve_keys(on, left_on, right_on)
    _require_columns(left.columns, lkeys, "left")
    _require_columns(right.columns, rkeys, "right")

    L = _table_side(left, lkeys)
    R = _table_side(right, rkeys)
    comparable = L["kinds"] == R["kinds"]

    exprs = _overlap_exprs()
    select = ", ".join(exprs.values())
    row = left.con.execute(
        f"{_key_ctes(left, lkeys, right, rkeys, comparable)} SELECT {select} "
        f"FROM l FULL OUTER JOIN r ON {_match(len(lkeys))}"
    ).fetchone()

    return _assemble(
        lkeys,
        rkeys,
        L,
        R,
        {name: int(value or 0) for name, value in zip(exprs, row, strict=True)},
        sample_left_only=_unmatched_keys(left, lkeys, right, rkeys, comparable),
        sample_right_only=_unmatched_keys(right, rkeys, left, lkeys, comparable),
    )


# --- the rules, shared by both implementations ------------------------------


def _assemble(
    lkeys: list[str],
    rkeys: list[str],
    L: dict,
    R: dict,
    ov: dict,
    *,
    sample_left_only: list,
    sample_right_only: list,
) -> dict:
    """One report, from measurements taken either way."""
    # A left row is dropped by an inner join if its key is null or matches nothing.
    dropped_left = L["null_rows"] + ov["left_only_rows"]
    dropped_right = R["null_rows"] + ov["right_only_rows"]
    inner_rows, matched_left, matched_right = (
        ov["inner_rows"],
        ov["matched_left"],
        ov["matched_right"],
    )

    report: dict[str, Any] = {
        "keys": {"left": lkeys, "right": rkeys},
        "left": _side_summary(L),
        "right": _side_summary(R),
        "key_dtypes": {"left": L["kinds"], "right": R["kinds"]},
        "key_dtype_match": L["kinds"] == R["kinds"],
        "relationship": _relationship(L["unique"], R["unique"]),
        "overlap": {
            "n_shared_keys": ov["n_shared_keys"],
            "n_left_only_keys": ov["n_left_only_keys"],
            "n_right_only_keys": ov["n_right_only_keys"],
            "left_coverage": round_float(matched_left / L["n_rows"]) if L["n_rows"] else 0.0,
            "right_coverage": round_float(matched_right / R["n_rows"]) if R["n_rows"] else 0.0,
            "sample_left_only": sample_left_only,
            "sample_right_only": sample_right_only,
        },
        "fan_out": {
            "max_left_to_right": ov["max_left_to_right"],
            "max_right_to_left": ov["max_right_to_left"],
            "result_per_matched_left": round_float(inner_rows / matched_left)
            if matched_left
            else 0.0,
        },
        # Row conservation across every join type — the drop report. left/right
        # dropped = distinct rows from that side that don't survive the join.
        "joins": {
            "inner": {
                "result_rows": inner_rows,
                "left_dropped": dropped_left,
                "right_dropped": dropped_right,
            },
            "left": {
                "result_rows": inner_rows + dropped_left,
                "left_dropped": 0,
                "right_dropped": dropped_right,
            },
            "right": {
                "result_rows": inner_rows + dropped_right,
                "left_dropped": dropped_left,
                "right_dropped": 0,
            },
            "outer": {
                "result_rows": inner_rows + dropped_left + dropped_right,
                "left_dropped": 0,
                "right_dropped": 0,
            },
        },
    }
    report["flags"] = _flags(
        report,
        dropped_left=dropped_left,
        dropped_right=dropped_right,
        inner_rows=inner_rows,
        null_keys=L["null_rows"] + R["null_rows"],
        max_fanout=max(ov["max_left_to_right"], ov["max_right_to_left"]),
    )
    return report


def _side_summary(side: dict) -> dict:
    return {
        "n_rows": side["n_rows"],
        "n_null_keys": side["null_rows"],
        "n_distinct_keys": side["n_distinct"],
        "n_duplicated_keys": side["n_duplicated"],
        "max_key_multiplicity": side["max_mult"],
        "unique_keys": side["unique"],
    }


def _relationship(left_unique: bool, right_unique: bool) -> str:
    if left_unique and right_unique:
        return "1:1"
    if left_unique:
        return "1:many"
    if right_unique:
        return "many:1"
    return "many:many"


def _flags(report, *, dropped_left, dropped_right, inner_rows, null_keys, max_fanout) -> list[str]:
    flags: list[str] = []
    if not report["key_dtype_match"]:
        flags.append("key_dtype_mismatch")  # most severe: likely zero real matches
    if inner_rows == 0:
        flags.append("no_matches")
    if report["relationship"] == "many:many":
        flags.append("many_to_many")
    if dropped_left > 0:
        flags.append("left_rows_dropped")
    if max_fanout > 1:
        flags.append("fan_out")
    if null_keys > 0:
        flags.append("null_keys")
    if report["overlap"]["left_coverage"] < LOW_COVERAGE:
        flags.append("low_overlap")
    if dropped_right > 0:
        flags.append("right_rows_dropped")
    return flags


def _resolve_keys(on, left_on, right_on) -> tuple[list[str], list[str]]:
    if on is not None:
        keys = [on] if isinstance(on, str) else list(on)
        return keys, keys
    if left_on is not None and right_on is not None:
        lk = [left_on] if isinstance(left_on, str) else list(left_on)
        rk = [right_on] if isinstance(right_on, str) else list(right_on)
        if len(lk) != len(rk):
            raise ValueError(f"left_on ({lk}) and right_on ({rk}) must have equal length")
        return lk, rk
    raise ValueError("provide `on`, or both `left_on` and `right_on`")


def _require_columns(columns, keys: list[str], side: str) -> None:
    missing = [k for k in keys if k not in list(columns)]
    if missing:
        raise ValueError(f"{side} table is missing key column(s): {missing}")


def _key_value(key) -> Any:
    """One key as evidence: a scalar for a single key, a **list** for a composite.

    A list rather than pandas' stringified tuple. ``"('H001', '2026-06-12')"`` is
    a repr of an implementation detail — it is not JSON, the components cannot be
    read out of it, and SQL has no reason to produce it. Changed deliberately
    when the SQL tier landed; `docs/DUCKDB_MIGRATION.md` §6.3 records it with the
    other evidence changes.
    """
    if isinstance(key, tuple):
        return [to_jsonable(v) for v in key]
    return to_jsonable(key)


# --- the SQL implementation -------------------------------------------------


def _key_kind(dtype: str) -> str:
    """Coarse structural kind for key comparison. int vs float both 'numeric'
    (they join fine); string vs numeric do not (the '123' != 123 silent miss)."""
    kind = duckdb_kind(dtype)
    if kind in NUMERIC_KINDS:
        return "numeric"
    if kind in (BOOLEAN, DATETIME):
        return kind
    return "string"


def _table_side(table: Table, keys: list[str]) -> dict:
    quoted = [quote_ident(k) for k in keys]
    not_null = " AND ".join(f"{q} IS NOT NULL" for q in quoted)
    grouped = (
        f"SELECT count(*) AS n FROM ({table.query}) WHERE {not_null} GROUP BY {', '.join(quoted)}"
    )
    n_rows, null_rows = table.con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE NOT ({not_null})) FROM ({table.query})"
    ).fetchone()
    n_distinct, n_duplicated, max_mult = table.con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE n > 1), coalesce(max(n), 0) FROM ({grouped})"
    ).fetchone()
    dtypes = table.dtypes
    return {
        "n_rows": int(n_rows),
        "null_rows": int(null_rows),
        "n_distinct": int(n_distinct),
        "n_duplicated": int(n_duplicated),
        "max_mult": int(max_mult),
        "unique": int(max_mult) <= 1,
        "kinds": [_key_kind(dtypes[k]) for k in keys],
    }


def _key_exprs(keys: list[str], comparable: bool, qualifier: str = "") -> list[str]:
    """The key columns as the join compares them, optionally table-qualified.

    When the two sides' kinds already agree, the raw columns — DuckDB matches
    ``BIGINT`` to ``DOUBLE`` exactly as pandas aligns int and float indexes.

    When they do **not** agree, both sides are read as text. Not cosmetic:
    DuckDB implements ``BIGINT = VARCHAR`` by casting the text to a number and
    *raising* when it won't convert, so a report on a mismatched key would crash
    rather than report the mismatch — which is the one thing it most needs to
    say. Comparing as text can never raise, and it agrees with what DuckDB's own
    join does in the cases where that join doesn't blow up.
    """
    prefix = f"{qualifier}." if qualifier else ""
    return [
        f"CAST({prefix}{quote_ident(k)} AS VARCHAR)"
        if not comparable
        else f"{prefix}{quote_ident(k)}"
        for k in keys
    ]


#: The whole join diagnosis, as aggregates over one ``FULL OUTER JOIN`` of the two
#: sides' key multiplicities. This is the query the module docstring's claim rests
#: on: an 80M-row fan-out is `sum(ln * rn)`, so it is *counted* rather than built.
#: Named rather than positional so adding a measurement can't silently shift the
#: meaning of the one next to it.
_SHARED = "l.ln IS NOT NULL AND r.rn IS NOT NULL"
_LEFT_ONLY = "r.rn IS NULL"
_RIGHT_ONLY = "l.ln IS NULL"


def _overlap_exprs() -> dict[str, str]:
    return {
        "inner_rows": f"coalesce(sum(l.ln * r.rn) FILTER (WHERE {_SHARED}), 0)",
        "matched_left": f"coalesce(sum(l.ln) FILTER (WHERE {_SHARED}), 0)",
        "matched_right": f"coalesce(sum(r.rn) FILTER (WHERE {_SHARED}), 0)",
        "n_shared_keys": f"count(*) FILTER (WHERE {_SHARED})",
        "n_left_only_keys": f"count(*) FILTER (WHERE {_LEFT_ONLY})",
        "n_right_only_keys": f"count(*) FILTER (WHERE {_RIGHT_ONLY})",
        "left_only_rows": f"coalesce(sum(l.ln) FILTER (WHERE {_LEFT_ONLY}), 0)",
        "right_only_rows": f"coalesce(sum(r.rn) FILTER (WHERE {_RIGHT_ONLY}), 0)",
        "max_left_to_right": f"coalesce(max(r.rn) FILTER (WHERE {_SHARED}), 0)",
        "max_right_to_left": f"coalesce(max(l.ln) FILTER (WHERE {_SHARED}), 0)",
    }


def _match(n_keys: int) -> str:
    return " AND ".join(f"l.lk{i} = r.rk{i}" for i in range(n_keys))


def _key_ctes(left: Table, lkeys: list[str], right: Table, rkeys: list[str], comparable) -> str:
    """``WITH l AS (…), r AS (…)`` — each side reduced to one row per distinct key.

    This is the whole reason the report scales: after these, everything downstream
    is arithmetic over *keys*, and the number of keys is the answer's size rather
    than the input's.
    """
    return (
        f"WITH l AS ({_key_counts(left, lkeys, comparable, 'lk', 'ln')}), "
        f"r AS ({_key_counts(right, rkeys, comparable, 'rk', 'rn')})"
    )


def _key_counts(table: Table, keys: list[str], comparable: bool, prefix: str, count: str) -> str:
    exprs = _key_exprs(keys, comparable)
    select = ", ".join(f"{e} AS {prefix}{i}" for i, e in enumerate(exprs))
    not_null = " AND ".join(f"{e} IS NOT NULL" for e in exprs)
    ordinals = ", ".join(str(i + 1) for i in range(len(keys)))
    return (
        f"SELECT {select}, count(*) AS {count} FROM ({table.query}) "
        f"WHERE {not_null} GROUP BY {ordinals}"
    )


def _anti_join_where(this: Table, keys: list[str], other: Table, other_keys: list[str], comparable):
    """``WHERE`` clause selecting this side's rows whose key is absent from the other."""
    mine = _key_exprs(keys, comparable, "__t")
    theirs = _key_exprs(other_keys, comparable, "__o")
    not_null = " AND ".join(f"{e} IS NOT NULL" for e in mine)
    match = " AND ".join(f"{t} = {m}" for t, m in zip(theirs, mine, strict=True))
    return f"{not_null} AND NOT EXISTS (SELECT 1 FROM ({other.query}) AS __o WHERE {match})"


def _unmatched_keys(this: Table, keys: list[str], other: Table, other_keys: list[str], comparable):
    """Distinct keys on this side that match nothing on the other, smallest first.

    The values come back in **their own type**, not the text the comparison used,
    so a numeric key still reads as a number in the evidence.
    """
    quoted = ", ".join(quote_ident(k) for k in keys)
    where = _anti_join_where(this, keys, other, other_keys, comparable)
    rows = this.con.execute(
        f"SELECT {quoted} FROM ({this.query}) AS __t WHERE {where} "
        f"GROUP BY {quoted} ORDER BY ALL LIMIT {SAMPLE_KEYS}"
    ).fetchall()
    return [_key_value(row[0] if len(row) == 1 else tuple(row)) for row in rows]


def join_findings(
    left: Table,
    right: Table,
    on: str | list[str] | None = None,
    *,
    left_on: str | list[str] | None = None,
    right_on: str | list[str] | None = None,
) -> dict:
    """:func:`join_findings`, measured in SQL."""
    lkeys, rkeys = _resolve_keys(on, left_on, right_on)
    report = join_report(left, right, on=on, left_on=left_on, right_on=right_on)
    comparable = report["key_dtype_match"]

    evidence = {
        "unmatched_left_rows": _unmatched_rows(left, lkeys, right, rkeys, comparable),
        "unmatched_right_rows": _unmatched_rows(right, rkeys, left, lkeys, comparable),
        "null_key_left_rows": _null_key_rows(left, lkeys),
        "null_key_right_rows": _null_key_rows(right, rkeys),
        "fan_out_examples": _table_fan_out(left, lkeys, right, rkeys, comparable),
    }
    return {"report": report, "evidence": evidence}


def _rows_as_records(con, sql: str, columns: list[str]) -> list[dict]:
    return [
        {col: to_jsonable(value) for col, value in zip(columns, row, strict=True)}
        for row in con.execute(sql).fetchall()
    ]


def _unmatched_rows(this: Table, keys: list[str], other: Table, other_keys: list[str], comparable):
    where = _anti_join_where(this, keys, other, other_keys, comparable)
    sql = f"SELECT * FROM ({this.query}) AS __t WHERE {where} ORDER BY ALL LIMIT {SAMPLE_ROWS}"
    return _rows_as_records(this.con, sql, this.columns)


def _null_key_rows(this: Table, keys: list[str]) -> list[dict]:
    any_null = " OR ".join(f"{quote_ident(k)} IS NULL" for k in keys)
    sql = f"SELECT * FROM ({this.query}) AS __t WHERE {any_null} ORDER BY ALL LIMIT {SAMPLE_ROWS}"
    return _rows_as_records(this.con, sql, this.columns)


def _table_fan_out(left: Table, lkeys, right: Table, rkeys, comparable) -> list[dict]:
    """Shared keys duplicated on either side — the source of row multiplication.

    Worst first, ties broken by the key so the answer is the same every run.
    """
    keyout = ", ".join(f"l.lk{i}" for i in range(len(lkeys)))
    rows = left.con.execute(
        f"{_key_ctes(left, lkeys, right, rkeys, comparable)} "
        f"SELECT {keyout}, l.ln, r.rn FROM l JOIN r ON {_match(len(lkeys))} "
        f"WHERE l.ln > 1 OR r.rn > 1 "
        f"ORDER BY l.ln * r.rn DESC, {keyout} LIMIT {SAMPLE_KEYS}"
    ).fetchall()
    n = len(lkeys)
    return [
        {
            "key": _key_value(row[0] if n == 1 else tuple(row[:n])),
            "n_left": int(row[n]),
            "n_right": int(row[n + 1]),
        }
        for row in rows
    ]


def render_text(report: dict) -> str:
    """Human-readable rendering for playing with the check."""
    lk = ", ".join(report["keys"]["left"])
    rk = ", ".join(report["keys"]["right"])
    key_desc = lk if lk == rk else f"{lk} = {rk}"
    lines = [
        f"join on [{key_desc}]  —  {report['relationship']}",
        f"  left  {report['left']['n_rows']} rows, "
        f"{report['left']['n_distinct_keys']} distinct keys "
        f"({report['left']['n_null_keys']} null)",
        f"  right {report['right']['n_rows']} rows, "
        f"{report['right']['n_distinct_keys']} distinct keys "
        f"({report['right']['n_null_keys']} null)",
        "",
        f"  key coverage: {report['overlap']['left_coverage']:.0%} of left, "
        f"{report['overlap']['right_coverage']:.0%} of right match",
        f"  fan-out: 1 left row -> up to {report['fan_out']['max_left_to_right']} right",
        "",
        "  result rows / dropped, by join type:",
    ]
    for jt, j in report["joins"].items():
        lines.append(
            f"    {jt:<6} {j['result_rows']:>6} rows   "
            f"(left dropped {j['left_dropped']}, right dropped {j['right_dropped']})"
        )
    if report["overlap"]["sample_left_only"]:
        lines.append(f"  keys only in left:  {report['overlap']['sample_left_only']}")
    if report["overlap"]["sample_right_only"]:
        lines.append(f"  keys only in right: {report['overlap']['sample_right_only']}")
    if report["flags"]:
        lines.append("")
        lines.append(f"  ⚑ {', '.join(report['flags'])}")
    return "\n".join(lines)


def render_findings(findings: dict) -> str:
    """Human-readable findings for the CLI: the report, then example rows."""
    lines = [render_text(findings["report"]), ""]
    ev = findings["evidence"]
    for title, key in [
        ("unmatched left rows", "unmatched_left_rows"),
        ("unmatched right rows", "unmatched_right_rows"),
        ("null-key left rows", "null_key_left_rows"),
        ("null-key right rows", "null_key_right_rows"),
    ]:
        if ev[key]:
            lines.append(f"  {title} (sample):")
            lines += [f"    {row}" for row in ev[key]]
    if ev["fan_out_examples"]:
        lines.append(f"  fan-out keys (n_left × n_right): {ev['fan_out_examples']}")
    return "\n".join(lines)
