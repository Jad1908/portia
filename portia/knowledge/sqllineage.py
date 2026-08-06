"""Where a `sql` step's output columns came from — the only module here that reads SQL.

`docs/SQL_LINEAGE.md`. `join` and `normalize` name their output columns in
plain fields, so `build.py` can walk them with no parser. A `sql` step declares
*table* names and a block of text, and the text is the only thing that says what
came out. Until this module existed the walk simply stopped there, and the cost
was not the lineage — it was that the model lost **every** Column node, which
took the harmonized table out of the measured half of the graph entirely.

**Parsing is not running.** Nothing here opens a connection, reads a file or
touches data, so `build.py`'s guarantee is unchanged: a graph can be built for a
project whose data has moved, and it says the same thing it said when the data
was there.

**It knows nothing about the graph.** It is handed the step's SQL and each
declared input's column names, and it hands back, per output column, the input
columns it came from. Turning those into nodes and edges — and deciding what a
`DERIVES_FROM` edge's rank is — stays in `build.py` beside the same decision for
the other two ops. This module reports one structural fact about each path,
:attr:`Origin.transformed`, and invents nothing else.

**The schema is why this is cheap here.** Resolving `select *`, or an
unqualified column across two joined tables, needs to know what columns each
input has — normally the hard part. `build.py` is already carrying exactly that
when it reaches the step.

**Conservative on purpose.** If any one of an output column's origins cannot be
matched to a declared input's column, the whole column is reported with *no*
origins rather than with the ones that did resolve. A half-traced column would
put a guess in the store as structure, which is the failure `KNOWLEDGE_GRAPH.md`
§1.1 is about; an empty answer is read by `build.py` as *nobody could say*, which
is true.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How much of a parser error to keep. The reason string ends up in
#: `BuildResult.unresolved` and in a CLI line, and sqlglot's messages carry a
#: whole statement when they fail late.
MAX_REASON = 160


class LineageUnreadable(ValueError):
    """The SQL could not be read. The message is a reason a human reads."""


@dataclass(frozen=True)
class Origin:
    """One input column an output column's values came from.

    ``transformed`` is read off the parse tree, not guessed: it is False only
    when the value travelled from that input column to the output as a bare
    column reference the whole way — through any number of CTEs and sub-selects
    — and True the moment a function, an operator, a cast or a `CASE` is in the
    path. It is the closest structural equivalent of the question `build.py`
    asks of a `normalize` step: did this step *change* the values, or carry them.
    """

    table: str
    column: str
    transformed: bool


def column_origins(sql: str, inputs: dict[str, list[str]]) -> dict[str, list[Origin]]:
    """Each output column of ``sql``, and the input columns underneath it.

    ``inputs`` maps each declared input's name to its column names — the schema
    the parser resolves against. A column with an empty list is one whose
    derivation could not be named: ``count(*)``, a literal, anything with no
    input column beneath it. That is a real answer and `build.py` records it as
    one; it is not the same as the whole step failing, which raises.
    """
    exp, lineage, qualify, parse_one = _sqlglot()

    schema = {table: dict.fromkeys(columns, "VARCHAR") for table, columns in inputs.items()}
    try:
        statement = parse_one(sql, read=_DIALECT)
    except Exception as exc:
        raise LineageUnreadable(f"the sql could not be parsed: {_short(exc)}") from exc
    try:
        qualified = qualify(statement, schema=schema, dialect=_DIALECT)
    except Exception as exc:
        raise LineageUnreadable(
            f"the sql could not be resolved against its inputs: {_short(exc)}"
        ) from exc

    names = [select.alias_or_name for select in qualified.selects]
    if any(name == "*" or not name for name in names):
        # `select *` over a table the schema does not hold expands to nothing,
        # and sqlglot reports the star itself as the output column. Emitting a
        # column called `*` would be worse than saying we could not tell.
        raise LineageUnreadable("the sql selects '*' from a table whose columns are unknown")

    origins = {}
    for name in names:
        try:
            root = lineage(name, sql, schema=schema, dialect=_DIALECT)
        except Exception as exc:
            raise LineageUnreadable(f"the sql could not be traced: {_short(exc)}") from exc
        origins[name] = _origins(exp, root, inputs)
    return origins


#: DuckDB is what `ops/sql.py` runs the step on, so it is what the step was
#: written against — reading it as anything else would accept queries the engine
#: rejects and reject queries it accepts.
_DIALECT = "duckdb"


def _origins(exp, root, inputs: dict[str, list[str]]) -> list[Origin]:
    """The leaves of one column's lineage, matched back to the declared inputs."""
    found: list[Origin] = []
    for leaf, unchanged in _leaves(exp, root, True):
        # A leaf standing on a `Table` is a real column of a real input. One
        # standing on anything else is a literal or an aggregate over no column
        # — there is nothing to point an edge at.
        if not isinstance(leaf.source, exp.Table):
            return []
        table, column = leaf.source.name, leaf.name.rsplit(".", 1)[-1]
        if column not in (inputs.get(table) or ()):
            return []  # cannot be matched to a declared input — say nothing
        found.append(Origin(table, column, not unchanged))
    return found


def _leaves(exp, node, unchanged: bool) -> list:
    """Every leaf under ``node``, each with whether its path was a bare reference.

    The walk carries the answer down rather than computing it at the bottom,
    because "was this value changed on the way here" is a property of the whole
    path and one function call anywhere along it settles it.
    """
    if not node.downstream:
        return [(node, unchanged)]
    inner = node.expression
    inner = inner.this if isinstance(inner, exp.Alias) else inner
    carried = unchanged and isinstance(inner, exp.Column)
    return [leaf for child in node.downstream for leaf in _leaves(exp, child, carried)]


def _sqlglot():
    """The parser, imported here and nowhere else.

    An optional dependency of the `graph` extra (`KNOWLEDGE_GRAPH.md` §6.6): a
    project without it builds exactly the graph it built before this module
    existed, and is told which columns that cost it. The coarse answer
    `SQL_LINEAGE.md` §3 could not find an honest home for as a design stage is
    this — what happens when the parser is absent.
    """
    try:
        from sqlglot import exp, parse_one
        from sqlglot.lineage import lineage
        from sqlglot.optimizer.qualify import qualify
    except ImportError as exc:  # pragma: no cover - exercised by monkeypatch
        raise LineageUnreadable(
            "sqlglot is not installed, and it is what reads columns out of a sql step "
            "— install portia's 'graph' extra"
        ) from exc
    return exp, lineage, qualify, parse_one


def _short(exc: Exception) -> str:
    """A parser's complaint, on one line and short enough to read in a list."""
    text = " ".join(str(exc).split())
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1] + "…"
