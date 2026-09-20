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

**Two paths since 2026-09-04** (`docs/CONNECTOR.md` §2.8). On a local
connection the declared inputs are copied into a locked in-memory database,
because that is the one isolation that does not depend on reading the query
correctly (§6.1 of the migration doc). On a warehouse connection that copy is
the pull the connector forbids, so the query runs where the data is with its
inputs bound as CTEs, the **role** is what holds, and a parse of the statement
(`referenced_tables`) is what turns an undeclared table into a sentence rather
than a warehouse error. `check_sql` runs on both.
"""

from __future__ import annotations

import re
from typing import Any

from portia.core import backend, cancel
from portia.core import dialect as dialects
from portia.core.table import Table, quote_ident, subquery
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
    # Snowflake's spellings of the same refusals (`docs/CONNECTOR.md` §2.8):
    # a stage read or write, a procedure, and every statement that changes
    # something. None can occur inside a SELECT except as an identifier, and
    # an identifier spelled like a verb is a name worth quoting anyway.
    "alter",
    "call",
    "delete",
    "drop",
    "grant",
    "insert",
    "merge",
    "put",
    "remove",
    "revoke",
    "truncate",
    "unload",
    "update",
    # BigQuery's (`docs/CONNECTORS.md` §8): ``EXTERNAL_QUERY`` reaches another
    # database through a federated connection, and ``LOAD DATA`` / ``EXPORT
    # DATA`` are already caught by the words they start with.
    "external_query",
)

#: Snowflake's ``SYSTEM$…`` functions — cancel a query, ask for a token, reach
#: an external function. None of them reads a table. Matched as text rather than
#: as a word because ``$`` is not a word character.
SYSTEM_FUNCTIONS = "system$"

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_WORD = re.compile(r"[a-z_][a-z0-9_]*")


class SqlNotAllowed(ValueError):
    """The statement is not a single read. Raised before DuckDB is touched."""


class SqlTableUnknown(ValueError):
    """The query named a table the step did not declare in ``inputs``.

    Its own class because it is the one DuckDB failure that is always the
    caller's mistake and always has the same fix — declare it, or spell it the
    way it was declared. Everything else the sandbox raises is about the query
    itself and is passed through.
    """


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
    if SYSTEM_FUNCTIONS in stripped.lower():
        forbidden.append(SYSTEM_FUNCTIONS)
    if forbidden:
        raise SqlNotAllowed(
            f"{', '.join(w.upper() for w in forbidden)} is not allowed in a sql step — "
            "a step reads the tables it declares in 'inputs' and nothing else"
        )


# --- the remote path: the role holds, the parser explains (`CONNECTOR.md` §2.8) --


def referenced_tables(sql: str, *, dialect: str = "duckdb") -> set[str]:
    """Every table the statement names, as written, minus the CTEs it defines itself.

    A parse, because only a parser can tell ``FROM orders`` from ``'orders'`` in
    a string or ``orders`` as a column. ``sqlglot`` comes with the ``snowflake``
    extra (and the ``graph`` one); without either the remote path cannot check
    a statement, and says so rather than running it unchecked.

    A qualified name comes back qualified (``DB.S.T``), which is what makes it
    refusable: a declared input is always a bare name.
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:  # pragma: no cover - the extra pins it
        raise RuntimeError(
            "checking which tables a statement names needs sqlglot — `uv sync --extra snowflake`"
        ) from exc
    parsed = sqlglot.parse_one(sql, read=dialect)
    defined = {cte.alias_or_name for cte in parsed.find_all(exp.CTE)}
    found = set()
    for table in parsed.find_all(exp.Table):
        parts = [p for p in (table.catalog, table.db, table.name) if p]
        qualified = ".".join(parts)
        if qualified and table.name not in defined:
            found.add(qualified)
    return found


def undeclared_tables(sql: str, inputs: dict[str, Table], *, dialect: str = "duckdb") -> list[str]:
    """The tables the statement names that are not declared inputs, sorted.

    Compared case-insensitively, because an unquoted identifier folds on the
    warehouse (upper on Snowflake) and a step that declares ``orders`` and
    writes ``FROM ORDERS`` is reading the table it declared.
    """
    declared = {name.lower() for name in inputs}
    return sorted(t for t in referenced_tables(sql, dialect=dialect) if t.lower() not in declared)


def compose(inputs: dict[str, Table], sql: str) -> str:
    """The statement with its declared inputs bound as CTEs, under the names it declared.

    This is how a remote step sees exactly its inputs without copying them
    (§2.8): each input's own query becomes a named block, and the agent's SQL
    reads the names. If the statement opens with its own ``WITH``, the two lists
    are joined into one — a statement may not have two.
    """
    ctes = ", ".join(
        f"{table.dialect.quote(name)} AS ({table.query})" for name, table in inputs.items()
    )
    body = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()
    if body[:4].lower() == "with" and (len(body) == 4 or not _IDENT_CHARS.match(body[4])):
        return f"WITH {ctes}, {body[4:].lstrip()}"
    return f"WITH {ctes} {body}"


def quote_declared(sql: str, inputs: dict[str, Table], *, dialect: str = "duckdb") -> str:
    """The statement with every reference to a declared input spelled exactly as
    :func:`compose` binds it — quoted, case kept.

    **This is where chaining on a warehouse was broken** *(2026-09-06, the first
    real Snowflake drive, `CONNECTOR.md` §2.8.1)*. `compose` binds a CTE under
    the quoted declared name (``"chicago_clean"``) and the agent's SQL reads it
    unquoted (``FROM chicago_clean``). DuckDB compares an unquoted identifier
    case-insensitively, so every fixture passed; Snowflake **folds** it to upper
    case first, missed the quoted lower-case CTE, fell through to the schema and
    answered ``Object 'CHICAGO_CLEAN' does not exist`` — a raw error about a
    spelling the agent never wrote. Step ids and model names are lower case by
    convention, so every chained input was unreachable, while raw sources worked
    only because warehouse tables happen to be upper case. The copilot's fallback
    was `PIPELINE.md` §8.4's: it re-inlined its whole preparation into one step.

    A parse rather than :func:`rename_tables`' string walk, on purpose: the
    declared names here are plain words a column can share (``rename_tables``
    gets away with a dumb walk because its targets contain ``/`` and ``#``), and
    only a parser can tell ``FROM unified_clean`` from ``SELECT unified_clean``.
    sqlglot is already a hard requirement of this path — `referenced_tables`
    refuses to run without it — so the "must work without the extra" argument
    that keeps `rename_tables` dumb does not apply. Matching is case-insensitive,
    the same comparison `undeclared_tables` makes: inside a step there is nothing
    an input's name could mean *except* the input. Qualified names and the
    statement's own CTEs are left alone, and the result is regenerated in the
    connection's dialect — the agent's verbatim text stays in the spec and the
    provenance; this spelling is for the warehouse and the compiled model.
    """
    import sqlglot
    from sqlglot import exp

    declared = {name.lower(): name for name in inputs}
    parsed = sqlglot.parse_one(sql, read=dialect)
    defined = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}
    for table in parsed.find_all(exp.Table):
        if table.db or table.catalog or table.name.lower() in defined:
            continue
        exact = declared.get(table.name.lower())
        if exact is not None:
            table.set("this", exp.to_identifier(exact, quoted=True))
    return parsed.sql(dialect=dialect)


def _apply_remote(inputs: dict[str, Table], sql: str, *, name: str, con: Any) -> OpResult:
    """`apply_sql` where the data cannot be copied: the query runs where the data is.

    Nothing is materialized and there is no second connection. What holds the
    line is the **role** the session runs as, which is the user's configuration
    and the guarantee; what makes a mistake readable is the parse check, which
    refuses a table the step did not declare before the warehouse is asked.
    The result is a lazy handle like every other op's, so producing it costs
    the counts in the provenance and nothing else.
    """
    dialect = dialects.of(con).name
    strays = undeclared_tables(sql, inputs, dialect=dialect)
    if strays:
        raise SqlTableUnknown(_undeclared(strays, inputs))
    # `compiled` is the quoted form too, not only the query: `pipeline.py` wraps
    # each step's compiled SQL in the same quoted CTE names `compose` uses, so a
    # `models/*.sql` carrying the unquoted reference would ship the exact
    # mismatch this line exists to remove.
    bound = quote_declared(sql, inputs, dialect=dialect)
    table = Table(name=name, query=compose(inputs, bound), con=con)
    provenance = {
        "op": "sql",
        "sql": sql.strip(),
        "inputs": sorted(inputs),
        "input_rows": {input_name: t.count() for input_name, t in inputs.items()},
        "result_rows": table.count(),
        "columns": list(table.columns),
        "flags": [],
    }
    return OpResult(table=table, provenance=provenance, compiled=bound)


def _undeclared(strays: list[str], inputs: dict[str, Table]) -> str:
    declared = ", ".join(sorted(inputs)) or "(none)"
    return (
        f"the query names {', '.join(strays)}, which is not a declared input. "
        f"A sql step reads only what it declares in 'inputs'. Declared: {declared}."
    )


#: Characters that continue an identifier, for the word-boundary check in
#: :func:`rename_tables`. `#`, `/` and `.` are in here because the name being
#: replaced is a `<spec path>#<step id>` reference, and without them
#: ``specs/t.yaml#a`` would match inside ``specs/t.yaml#ab``.
_IDENT_CHARS = re.compile(r"[A-Za-z0-9_$#./]")


def rename_tables(sql: str, renames: dict[str, str]) -> str:
    """Rewrite table names in a statement, leaving string literals alone.

    **String-level, like :func:`check_sql`, and for the same reason.** A parse
    tree would be more precise and `sqlglot` can build one — but it is an
    optional dependency of the `graph` extra (`KNOWLEDGE_GRAPH.md` §6.6), and a
    step that chains correctly with the extra installed and fails without it
    would be the worst possible version of `PIPELINE.md` §8. So this is
    deliberately dumb in the same way its neighbour is: it knows about quoting
    and about word boundaries, and about nothing else.

    What it has to get right is exactly one distinction. In DuckDB a
    double-quoted run is an **identifier** and a single-quoted run is a
    **literal**, so ``"specs/t.yaml#prep"`` is a table to rename and
    ``'specs/t.yaml#prep'`` is a string somebody selected and must not be
    touched. Everything else is a bare token, and a bare token can only be an
    identifier — the names being replaced contain ``/`` and ``#``, which is why
    the copilot that wrote one unquoted got a parser error rather than a wrong
    answer.

    Identity renames are dropped, so passing a map that changes nothing returns
    the statement unchanged rather than rebuilding it.
    """
    renames = {old: new for old, new in renames.items() if old != new}
    if not renames or not sql:
        return sql
    # Longest first: a step id can be a prefix of another one, and the boundary
    # check below only protects the *end* of a bare token.
    targets = sorted(renames, key=len, reverse=True)

    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            end = _skip_quoted(sql, i, "'")
            out.append(sql[i:end])
            i = end
            continue
        if ch == '"':
            end = _skip_quoted(sql, i, '"')
            inner = sql[i + 1 : end - 1].replace('""', '"')
            out.append(f'"{renames[inner]}"' if inner in renames else sql[i:end])
            i = end
            continue
        for old in targets:
            if sql.startswith(old, i) and _bounded(sql, i, i + len(old)):
                out.append(renames[old])
                i += len(old)
                break
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _skip_quoted(sql: str, start: int, quote: str) -> int:
    """Index just past the quoted run beginning at ``start``.

    Doubling is the escape in both flavours (`''` and `""`), and an unterminated
    quote runs to the end — this is not a validator, and `check_sql` plus DuckDB
    are the two things that decide whether the statement is legal.
    """
    i = start + 1
    while i < len(sql):
        if sql[i] == quote:
            if i + 1 < len(sql) and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return len(sql)


def _bounded(sql: str, start: int, end: int) -> bool:
    """Whether ``sql[start:end]`` is a whole token rather than part of one."""
    before = sql[start - 1] if start else ""
    after = sql[end] if end < len(sql) else ""
    return not _IDENT_CHARS.match(before or " ") and not _IDENT_CHARS.match(after or " ")


def _unknown_table(exc: Exception, inputs: dict[str, Table]) -> str:
    """What to say when the query named a table the sandbox does not hold.

    Written inline and kept short, like every other refusal in this module.
    `agent/prompts/` is where text the model reads lives, and nothing in the
    engine loads it — an op reaching into the decide layer for a sentence would
    invert the layering `CLAUDE.md` draws. `tests/test_agent_prompts.py`'s
    200-character threshold is that boundary made checkable, so this stays
    under it and says only what is actionable: what you declared.
    """
    declared = ", ".join(sorted(inputs)) or "(none)"
    return (
        f"{_first_line(exc)} A sql step reads only what it declares in 'inputs', "
        f"spelled the same way in the query. Declared: {declared}. "
        "An earlier step is named by its bare id."
    )


def _first_line(exc: Exception) -> str:
    """DuckDB's complaint without its `Did you mean` tail.

    That tail suggests `__portia_raw_<name>`, the staging relation
    :func:`apply_sql` registers before casting it into a view. It is real, it is
    reachable, and it is not something any caller should be told to type.
    """
    return str(exc).split("Did you mean")[0].strip()


def apply_sql(inputs: dict[str, Table], sql: str, *, name: str = "sql") -> OpResult:
    """Run one SELECT over the named tables, returning the table + provenance.

    ``inputs`` is the step's *declared* inputs, and only those are registered:
    naming a table the step didn't declare is an error rather than a silent
    dependency. That declaration is what lets `checks.outcome` still report
    which input contributed nothing — the measurement Run 2 didn't have.

    **The declared inputs are materialized, and that is the sandbox's price.**
    `docs/DUCKDB_MIGRATION.md` §6.1 preferred attaching the project store
    read-only and exposing views for the declared inputs. Probing killed it, on
    two independent counts: DuckDB refuses ``ATTACH`` outright when
    ``enable_external_access=False``, so the attach and the filesystem lock
    cannot both be had; and with the store attached, ``store.anything`` remains
    reachable by a schema-qualified name, so the only thing standing between the
    agent and an undeclared table would be :func:`check_sql` — the half this
    module says out loud is bypassable and exists to give a good error.

    So the connection here holds **exactly** the declared inputs and nothing
    else, which is the one arrangement where the guarantee does not depend on
    reading the query correctly. This is the single place in the engine where a
    whole relation leaves the database, and it is deliberate rather than
    overlooked (§13 of the migration doc).
    """
    import duckdb

    check_sql(sql)

    # Where the data cannot be copied, it is not (`docs/CONNECTOR.md` §2.8).
    first = next(iter(inputs.values()), None)
    if first is not None and backend.is_remote(first.con):
        return _apply_remote(inputs, sql, name=name, con=first.con)

    # Read-only by construction: an in-memory database with no external access,
    # so `COPY … TO`, `read_csv()` and extension installs fail inside DuckDB even
    # if `check_sql` were fooled into passing them through.
    sandbox = duckdb.connect(":memory:", config={"enable_external_access": False})
    # Registered explicitly, because this is the one connection in the engine
    # that deliberately does not come from `core/io.connect` — and it is where
    # the agent's own SQL runs, which is the query most worth being able to stop.
    cancel.watch(sandbox)
    try:
        input_rows = {}
        for input_name, table in inputs.items():
            frame = table.con.execute(table.query).fetch_df()
            input_rows[input_name] = int(len(frame))
            staging = f"__portia_raw_{input_name}"
            sandbox.register(staging, frame)
            # The declared input, with the types it actually had. See `_cast`.
            sandbox.execute(
                f"CREATE VIEW {quote_ident(input_name)} AS "
                f"SELECT {_cast(table.dtypes)} FROM {quote_ident(staging)}"
            )
        try:
            result = sandbox.sql(sql)
        except duckdb.CatalogException as exc:
            # **A missing table is portia's error, not DuckDB's** (`PIPELINE.md`
            # §8.3). The sandbox holds exactly the declared inputs, so "that
            # table does not exist" always means "you named something you did
            # not declare" — and DuckDB's own message ends with a suggestion
            # drawn from `__portia_raw_`, an internal staging name that appears
            # nowhere in portia's vocabulary and cannot be acted on. Same rule
            # as `handlers._table` one layer up: say what the name *could* be.
            raise SqlTableUnknown(_unknown_table(exc, inputs)) from exc
        # The types the *query* produced, captured before the trip back out.
        types = dict(zip(result.columns, (str(t) for t in result.types), strict=True))
        out = result.df()
    finally:
        sandbox.close()

    con = next(iter(inputs.values())).con if inputs else duckdb.connect(":memory:")
    provenance = {
        "op": "sql",
        "sql": sql.strip(),
        "inputs": sorted(inputs),
        "input_rows": input_rows,
        "result_rows": int(len(out)),
        "columns": [str(c) for c in out.columns],
        "flags": [],
    }
    table = _restore_types(Table.from_frame(out, name, con), types)
    # The declared SELECT already reads its inputs by the names the step declared,
    # so it *is* the compiled form — no second rendering, nothing to drift. This is
    # the one op where execution and compilation genuinely diverge (the sandbox
    # materializes; a file does not), and it is also the one where the compiled
    # text is the agent's own words, captured verbatim. `docs/PIPELINE.md` §3.
    return OpResult(table=table, provenance=provenance, compiled=sql.strip())


def _cast(types: dict[str, str]) -> str:
    """A select list restoring every column to a named type.

    The data crosses the sandbox boundary through pandas, twice, and pandas has
    no date type — so a ``DATE`` column arrives inside the sandbox as a
    ``TIMESTAMP`` and leaves it the same way. The query would then be written
    against a type its input never had, and a step downstream would join
    ``2026-06-12`` against ``2026-06-12 00:00:00``, or report it to the copilot
    that way. The boundary has to be crossed; it should not also be a place
    where types quietly change. So both crossings are repaired, on the way in
    from the declared input's own schema and on the way out from the schema the
    query produced.
    """
    return ", ".join(
        f"CAST({quote_ident(col)} AS {sql_type}) AS {quote_ident(col)}"
        for col, sql_type in types.items()
    )


def _restore_types(table: Table, types: dict[str, str]) -> Table:
    """The result, back in the schema the query produced. See :func:`_cast`."""
    select = _cast(types)
    if not select:
        return table
    return Table(
        name=table.name, query=f"SELECT {select} FROM {subquery(table.query)}", con=table.con
    )


def render_text(provenance: dict) -> str:
    """Human-readable SQL step summary, for the CLI."""
    rows = ", ".join(f"{name} {n}" for name, n in provenance["input_rows"].items())
    lines = [
        f"sql over {rows} rows → {provenance['result_rows']} rows "
        f"× {len(provenance['columns'])} cols",
    ]
    lines += [f"    {line}" for line in provenance["sql"].splitlines()]
    return "\n".join(lines)
