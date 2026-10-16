"""What the equality joins in a SELECT produce, read off its parse and counted.

**Measured after a query was interrupted, never before one runs**
(`docs/CONVERSATION.md` §16). A planner's estimate was measured and found useless
for this, and a time limit would stop correct hour-long queries, so nothing here
predicts or limits anything: when the user interrupts a data call on its card,
these are the facts the copilot reads in place of the result. The query that
started it, a 1,579-column table UNPIVOTed twice and joined on the meter name
without the hour, would have made 430 billion rows; this is the check that says
so in a number.

Two halves, the module's seam:

- :func:`read` is a **parse**. It finds each join whose two sides are named (a
  declared input, or a CTE of the same statement) and whose condition is column
  equalities only (``ON a.k = b.k AND …`` or ``USING``), and says of every other
  join why it was not read, as a code from a closed list. It runs nothing.
- :func:`measure` **counts**, on a connection holding the declared inputs by
  name: the statement's own CTEs are put back in front of portia's query, each
  side is reduced to one row per key, and the two are full-joined on the key.
  Since a key is distinct on each side after that, every number is a sum over
  keys and the join itself is never built: how many rows each side has, how
  many distinct keys, how often a key repeats at least and at most, how many
  keys are on both sides, and the exact number of rows the join produces for
  its kind. ``checks/join.py``'s module docstring has the same formula.

Facts only. Nothing here says a join is wrong, too big or worth changing; the
copilot reads the numbers beside the user's reason and decides.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from typing import Any

#: Why a join was not measured. A closed list of codes, each said in words to the
#: copilot by `prompts/errors/interrupted_measured.md`, so no sentence about a
#: join lives here.
SIDE_NOT_NAMED = "side_not_named"
CONDITION_NOT_EQUALITIES = "condition_not_equalities"
COLUMN_SIDE_UNCLEAR = "column_side_unclear"
KIND_NOT_MEASURED = "kind_not_measured"
NO_PARSER = "no_parser"
NOT_PARSED = "not_parsed"
MEASURE_FAILED = "measure_failed"

#: The join kinds :func:`measure` counts, as this module names them.
INNER, LEFT, RIGHT, FULL, CROSS = "inner", "left", "right", "full", "cross"

#: How much of a join's text a report quotes, so the copilot can tell which one
#: it is about without the statement being sent back whole.
TEXT_CHARS = 160

#: The two sides reduced to one row per key, and their full join. Named so that
#: no CTE of the agent's own can collide with them.
_LEFT, _RIGHT = "__portia_left_keys", "__portia_right_keys"

_DIALECT = "duckdb"


@dataclass(frozen=True)
class Join:
    """One join :func:`read` found: its two sides and what it matches on.

    ``left``/``right`` name a side as the statement does (a declared input or a
    CTE), or are ``None`` for a sub-select, which :func:`measure` cannot count
    by name. ``left_on``/``right_on`` are the keys, SQL ready over that side: a
    column, or an expression of one column (a cast, a date function), as
    written.

    **The ``base`` fields are the declared table underneath each side**
    (2026-10-15), for a caller that keeps the join as a fact about tables
    (`agent/handlers._keep_joins`): a CTE or a sub-select that reads one table
    and passes the key through is that table. ``*_base_on`` is the key over the
    base table's columns, ``*_columns`` the column under each key, and
    ``*_converted`` the expression where the key is not the bare column (``""``
    where it is). ``None`` base: the side is no one table.
    """

    text: str
    kind: str
    left: str | None
    right: str | None
    left_on: tuple[str, ...]
    right_on: tuple[str, ...]
    left_base: str | None = None
    right_base: str | None = None
    left_columns: tuple[str, ...] = ()
    right_columns: tuple[str, ...] = ()
    left_converted: tuple[str, ...] = ()
    right_converted: tuple[str, ...] = ()


def read(sql: str, names: Collection[str]) -> tuple[list[Join], list[dict[str, str]]]:
    """The joins in ``sql`` that can be measured, and each one that cannot, with why.

    ``names`` are the declared inputs; a side is *named* when it is one of them
    or a CTE of the statement, compared without case as DuckDB does. Every
    ``SELECT`` in the statement is read, those inside its CTEs included, so a
    join written inside a CTE is found where it is.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return [], [{"join": _clip(sql), "because": NO_PARSER}]
    try:
        statement = sqlglot.parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001 - a statement the parser refuses is reported, not raised
        return [], [{"join": _clip(sql), "because": NOT_PARSED}]

    ctes = {cte.alias_or_name.lower(): cte.this for cte in statement.find_all(exp.CTE)}
    inputs = {name.lower() for name in names}
    known = set(ctes) | inputs
    joins: list[Join] = []
    unread: list[dict[str, str]] = []
    for select in statement.find_all(exp.Select):
        sides = _sides(select, known, ctes, exp)
        for at, join in enumerate(_arg(select, "joins") or [], start=1):
            found = _one_join(join, at, sides, exp)
            if isinstance(found, Join):
                found = _with_bases(found, sides, at, join, inputs, ctes, exp)
                if found not in joins:
                    joins.append(found)
            else:
                unread.append({"join": _clip(_text(sides, at, join)), "because": found})
    return joins, unread


def measure(con: Any, sql: str, joins: list[Join]) -> list[dict[str, Any]]:
    """Count what each of ``joins`` produces, on ``con``, which holds the inputs by name.

    The statement's own ``WITH`` is put back in front, so a side that is a CTE is
    the CTE the query ran. A join that fails to count (two key types DuckDB will
    not compare) is reported with :data:`MEASURE_FAILED` and the rest go on; an
    interrupt is not a failure and is raised, so a press that skips the
    measuring stops all of it.
    """
    import duckdb

    ctes = _ctes(sql)
    out = []
    for join in joins:
        if join.left is None or join.right is None:
            # A sub-select has no name to count it by here.
            out.append({"join": join.text, "because": SIDE_NOT_NAMED})
            continue
        try:
            out.append(_counted(con, ctes, join))
        except duckdb.InterruptException:
            raise
        except duckdb.Error as exc:
            out.append(
                {"join": join.text, "because": MEASURE_FAILED, "error": _clip(str(exc).strip())}
            )
    return out


# --- reading ------------------------------------------------------------------


def _arg(node: Any, name: str) -> Any:
    """An argument of a parse node by name, under sqlglot's newer spelling or its older one.

    sqlglot 30 calls ``FROM`` and ``WITH`` ``from_`` and ``with_``; 25, the
    extras' floor, did not.
    """
    value = node.args.get(f"{name}_")
    return value if value is not None else node.args.get(name)


@dataclass(frozen=True)
class _Side:
    """One table in a ``FROM … JOIN …`` chain: what it is called here, its name if named,
    and the ``SELECT`` under it when it is a CTE or a sub-select."""

    alias: str
    name: str | None
    text: str
    select: Any = None


def _sides(select: Any, known: set[str], ctes: dict, exp: Any) -> list[_Side]:
    """The ``FROM`` table and each joined one, in order, each with its name if it has one."""
    tables = []
    from_ = _arg(select, "from")
    if from_ is not None:
        tables.append(from_.this)
    tables += [join.this for join in _arg(select, "joins") or []]
    sides = []
    for table in tables:
        named = (
            isinstance(table, exp.Table)
            and not table.args.get("db")
            and not table.args.get("catalog")
            and table.name.lower() in known
        )
        under = None
        if named:
            under = ctes.get(table.name.lower())
        elif isinstance(table, exp.Subquery):
            under = table.this
        sides.append(
            _Side(
                alias=str(table.alias_or_name).lower(),
                name=table.this.sql(dialect=_DIALECT) if named else None,
                text=table.sql(dialect=_DIALECT),
                select=under,
            )
        )
    return sides


def _one_join(join: Any, at: int, sides: list[_Side], exp: Any) -> Join | str:
    """The join at position ``at`` of the chain, or the code saying why it is not read."""
    kind = _kind(join)
    if kind is None:
        return KIND_NOT_MEASURED
    if at >= len(sides):
        return SIDE_NOT_NAMED
    right = sides[at]
    if right.name is None and right.select is None:
        return SIDE_NOT_NAMED
    if kind == CROSS:
        if right.name is None:
            return SIDE_NOT_NAMED
        if at != 1 or sides[0].name is None:
            return SIDE_NOT_NAMED if sides[0].name is None else COLUMN_SIDE_UNCLEAR
        return Join(_clip(_text(sides, at, join)), CROSS, sides[0].name, right.name, (), ())
    using = join.args.get("using")
    if using:
        # Which earlier table a USING column belongs to is the first table that
        # has it, which a parse without the schema cannot tell past one table.
        if at != 1:
            return COLUMN_SIDE_UNCLEAR
        left = sides[0]
        if left.name is None or right.name is None:
            return SIDE_NOT_NAMED
        keys = tuple(column.sql(dialect=_DIALECT) for column in using)
        return Join(_clip(_text(sides, at, join)), kind, left.name, right.name, keys, keys)
    return _on(join, at, sides, kind, exp)


def _on(join: Any, at: int, sides: list[_Side], kind: str, exp: Any) -> Join | str:
    """A join read off its ``ON``: every part an equality of two columns, one per side."""
    on = join.args.get("on")
    if on is None:
        return KIND_NOT_MEASURED
    parts = list(on.flatten()) if isinstance(on, exp.And) else [on]
    earlier = {side.alias: side for side in sides[:at]}
    right = sides[at]
    left: _Side | None = None
    left_on: list[str] = []
    right_on: list[str] = []
    for part in parts:
        if not isinstance(part, exp.EQ):
            return CONDITION_NOT_EQUALITIES
        # A key is a column, or an expression of one column: a cast, a date
        # function. A date join is written that way far more often than not
        # (2026-10-15: every one in thirteen sessions).
        a, b = _key(part.left, exp), _key(part.right, exp)
        if a is None or b is None:
            return CONDITION_NOT_EQUALITIES
        qualified = (str(a[0].table).lower(), str(b[0].table).lower())
        if right.alias == qualified[1] and qualified[0] in earlier:
            mine, theirs, other = b, a, earlier[qualified[0]]
        elif right.alias == qualified[0] and qualified[1] in earlier:
            mine, theirs, other = a, b, earlier[qualified[1]]
        else:
            return COLUMN_SIDE_UNCLEAR
        if left is not None and other.alias != left.alias:
            # Keys from two earlier tables: the left side is a join already
            # made, which no single name stands for.
            return COLUMN_SIDE_UNCLEAR
        left = other
        right_on.append(mine[1])
        left_on.append(theirs[1])
    if left is None:
        return CONDITION_NOT_EQUALITIES
    if (left.name is None and left.select is None) or (right.name is None and right.select is None):
        return SIDE_NOT_NAMED
    return Join(
        _clip(_text(sides, at, join)), kind, left.name, right.name, tuple(left_on), tuple(right_on)
    )


def _key(node: Any, exp: Any) -> tuple[Any, str] | None:
    """A join key: ``(the one column under it, the key as SQL over that side)``, or ``None``.

    A bare column, or an expression of exactly one column with no aggregate, no
    window and no sub-select: ``CAST(w.DATE AS DATE)``, ``lower(c.name)``. The
    SQL drops the column's table qualifier, so it runs against the side alone.
    """
    if isinstance(node, exp.Column):
        return node, node.this.sql(dialect=_DIALECT)
    if node.find(exp.AggFunc, exp.Window, exp.Subquery, exp.Select) is not None:
        return None
    columns = list(node.find_all(exp.Column))
    if len(columns) != 1:
        return None
    bare = node.copy()
    for column in bare.find_all(exp.Column):
        column.set("table", None)
    return columns[0], bare.sql(dialect=_DIALECT)


#: How many CTEs deep :func:`_base` follows a key looking for the table under it.
BASE_DEPTH = 4


def _with_bases(
    found: Join, sides: list[_Side], at: int, join: Any, inputs: set[str], ctes: dict, exp: Any
) -> Join:
    """``found`` with the declared table under each side, where one is (:class:`Join`)."""
    by_name = {s.name: s for s in sides if s.name is not None}
    right = sides[at]
    left = next(
        (s for s in sides[:at] if s.name is not None and s.name == found.left),
        None,
    )
    if left is None:
        # A sub-select on the left: the earlier side the condition named.
        left = next((s for s in sides[:at] if s.name is None and s.select is not None), None)
    del by_name
    out = {}
    for prefix, side, keys in (("left", left, found.left_on), ("right", right, found.right_on)):
        resolved = _base(side, keys, inputs, ctes, exp) if side is not None else None
        if resolved is not None:
            base, columns, converted = resolved
            out.update(
                {
                    f"{prefix}_base": base,
                    f"{prefix}_columns": columns,
                    f"{prefix}_converted": converted,
                }
            )
    return replace(found, **out)


def _base(side: _Side, keys: tuple[str, ...], inputs: set[str], ctes: dict, exp: Any):
    """The declared table under one side, its key columns, and each key's conversion.

    ``(table, columns, converted)`` or ``None``. A declared input is itself. A
    CTE or sub-select is its one table when it reads exactly one, joins nothing,
    and passes each key through, bare or under a cast; followed a few CTEs deep.
    A side that filters, deduplicates or aggregates still is that table: the join
    is between the tables, and how the query trimmed one side is the query's.
    """
    parsed = []
    for key in keys:
        try:
            node = _parse_expr(key)
        except Exception:  # noqa: BLE001 - a key the parser will not re-read is not traced
            return None
        found = _key(node, exp)
        if found is None:
            return None
        column, _ = found
        parsed.append((column.name, "" if isinstance(node, exp.Column) else key))
    name, select = side.name, side.select
    for _ in range(BASE_DEPTH):
        if name is not None and name.lower() in inputs and select is None:
            return _unquote(name), tuple(c for c, _ in parsed), tuple(k for _, k in parsed)
        if not isinstance(select, exp.Select) or _arg(select, "joins"):
            return None
        from_ = _arg(select, "from")
        source = from_.this if from_ is not None else None
        traced = []
        for column, converted in parsed:
            step = _through(select, column, converted, exp)
            if step is None:
                return None
            traced.append(step)
        parsed = traced
        if isinstance(source, exp.Table) and not source.args.get("db"):
            name = source.this.sql(dialect=_DIALECT)
            select = ctes.get(source.name.lower()) if source.name.lower() not in inputs else None
            if select is None and source.name.lower() not in inputs:
                return None
        elif isinstance(source, exp.Subquery):
            name, select = None, source.this
        else:
            return None
    return None


def _through(select: Any, column: str, converted: str, exp: Any) -> tuple[str, str] | None:
    """``column`` of this ``SELECT``'s output, as a column of its ``FROM``, with its conversion.

    A ``*`` passes every column through. A projection named ``column`` passes a
    bare column through under another name, or a converted one when the key
    above it was bare. Two conversions stacked are not traced.
    """
    for projection in select.expressions:
        if isinstance(projection, exp.Star) or (
            isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
        ):
            return column, converted
        if projection.alias_or_name.lower() != column.lower():
            continue
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        found = _key(inner, exp)
        if found is None:
            return None
        under, sql = found
        if isinstance(inner, exp.Column):
            if not converted:
                return under.name, ""
            node = _parse_expr(converted)
            for col in node.find_all(exp.Column):
                col.replace(exp.column(under.name, quoted=True))
            return under.name, node.sql(dialect=_DIALECT)
        return (under.name, sql) if not converted else None
    return None


def _parse_expr(text: str) -> Any:
    import sqlglot

    return sqlglot.parse_one(text, read=_DIALECT)


def _unquote(name: str) -> str:
    return name[1:-1] if len(name) > 1 and name[0] == name[-1] == '"' else name


def _kind(join: Any) -> str | None:
    """This module's name for a join's kind, or ``None`` for one it does not count.

    NATURAL, ASOF and POSITIONAL are methods; SEMI and ANTI keep one side's rows
    and are not a product of the two; a comma join's condition is in ``WHERE``,
    which is not read.
    """
    if join.args.get("method"):
        return None
    kind = str(join.args.get("kind") or "").upper()
    side = str(join.args.get("side") or "").upper()
    if kind == "CROSS":
        return CROSS
    if kind not in ("", "INNER", "OUTER"):
        return None
    if not join.args.get("on") and not join.args.get("using"):
        return None
    return {"": INNER, "LEFT": LEFT, "RIGHT": RIGHT, "FULL": FULL}.get(side)


def _text(sides: list[_Side], at: int, join: Any) -> str:
    """The join as written: the table before it, then the join itself."""
    return f"{sides[at - 1].text if at - 1 < len(sides) else ''} {join.sql(dialect=_DIALECT)}"


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= TEXT_CHARS else f"{flat[: TEXT_CHARS - 1]}…"


def _ctes(sql: str) -> list[str]:
    """Each CTE of the statement, as ``name AS (body)``, ready to go in front of another query."""
    import sqlglot
    from sqlglot import exp

    statement = sqlglot.parse_one(sql, read=_DIALECT)
    with_ = _arg(statement, "with")
    if with_ is None:
        return []
    return [cte.sql(dialect=_DIALECT) for cte in with_.expressions if isinstance(cte, exp.CTE)]


# --- counting -----------------------------------------------------------------


def _counted(con: Any, ctes: list[str], join: Join) -> dict[str, Any]:
    if join.kind == CROSS:
        return _crossed(con, ctes, join)
    n = len(join.left_on)
    left_keys = ", ".join(f"{col} AS k{i}" for i, col in enumerate(join.left_on))
    right_keys = ", ".join(f"{col} AS k{i}" for i, col in enumerate(join.right_on))
    keyed = [
        *ctes,
        f"{_LEFT} AS (SELECT {left_keys}, count(*) AS n FROM {join.left} GROUP BY ALL)",
        f"{_RIGHT} AS (SELECT {right_keys}, count(*) AS n FROM {join.right} GROUP BY ALL)",
    ]
    match = " AND ".join(f"l.k{i} = r.k{i}" for i in range(n))
    left_keyed = " AND ".join(f"l.k{i} IS NOT NULL" for i in range(n))
    right_keyed = " AND ".join(f"r.k{i} IS NOT NULL" for i in range(n))
    both = "l.n IS NOT NULL AND r.n IS NOT NULL"
    counts = {
        "left_rows": "sum(l.n)",
        "right_rows": "sum(r.n)",
        "left_keys": f"count(l.n) FILTER (WHERE {left_keyed})",
        "right_keys": f"count(r.n) FILTER (WHERE {right_keyed})",
        "left_min": f"min(l.n) FILTER (WHERE {left_keyed})",
        "left_max": f"max(l.n) FILTER (WHERE {left_keyed})",
        "right_min": f"min(r.n) FILTER (WHERE {right_keyed})",
        "right_max": f"max(r.n) FILTER (WHERE {right_keyed})",
        "keys_on_both": f"count(*) FILTER (WHERE {both})",
        "matched": f"sum(CAST(l.n AS HUGEINT) * r.n) FILTER (WHERE {both})",
        "left_matched": f"sum(l.n) FILTER (WHERE {both})",
        "right_matched": f"sum(r.n) FILTER (WHERE {both})",
    }
    row = con.execute(
        f"WITH {', '.join(keyed)} SELECT {', '.join(counts.values())} "
        f"FROM {_LEFT} l FULL OUTER JOIN {_RIGHT} r ON {match}"
    ).fetchone()
    got = {name: int(value or 0) for name, value in zip(counts, row, strict=True)}
    left_unmatched = got["left_rows"] - got["left_matched"]
    right_unmatched = got["right_rows"] - got["right_matched"]
    rows = got["matched"]
    if join.kind in (LEFT, FULL):
        rows += left_unmatched
    if join.kind in (RIGHT, FULL):
        rows += right_unmatched
    return {
        "join": join.text,
        "kind": join.kind,
        "left": join.left,
        "right": join.right,
        "on": [f"{a} = {b}" for a, b in zip(join.left_on, join.right_on, strict=True)],
        "left_rows": got["left_rows"],
        "right_rows": got["right_rows"],
        "left_keys": got["left_keys"],
        "right_keys": got["right_keys"],
        "keys_on_both": got["keys_on_both"],
        "left_repeats": {"min": got["left_min"], "max": got["left_max"]},
        "right_repeats": {"min": got["right_min"], "max": got["right_max"]},
        "rows": rows,
    }


def _crossed(con: Any, ctes: list[str], join: Join) -> dict[str, Any]:
    """A cross join produces every left row with every right row: the two counts, multiplied."""
    head = f"WITH {', '.join(ctes)} " if ctes else ""
    left_rows, right_rows = con.execute(
        f"{head}SELECT (SELECT count(*) FROM {join.left}), (SELECT count(*) FROM {join.right})"
    ).fetchone()
    return {
        "join": join.text,
        "kind": CROSS,
        "left": join.left,
        "right": join.right,
        "left_rows": int(left_rows),
        "right_rows": int(right_rows),
        "rows": int(left_rows) * int(right_rows),
    }
