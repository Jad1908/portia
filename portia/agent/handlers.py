"""What the agent is allowed to see and do — as plain functions.

Every tool the copilot can call bottoms out here: ``(args) -> jsonable dict``.
Deliberately **free of any SDK import**, so this file is unit-testable without
``claude-agent-sdk`` installed and the engine never learns which agent harness
is driving it (docs/TECH_STACK.md — engine and agent stay decoupled).

Two rules this module exists to enforce:

- **No raw data, ever.** These functions return compact evidence dicts from the
  checks/catalog layer. The agent has no filesystem tools (see ``session.py``),
  so this surface *is* its entire view of the data — which is what makes the
  loop token-lean at scale.
- **Facts only; no ranking.** Nothing here may sort, score, prioritize or
  suggest an answer. Shaping *how much* we return is fine (that's token budget);
  deciding *what matters* is the agent's job. Re-read ``CLAUDE.md`` →
  "facts vs judgment" before adding a helper that looks like a shortcut.

Errors are raised, not returned — ``tools.py`` catches them at the edge and
turns them into a tool result the agent can react to.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from portia import catalog, findings, knowledge, pipeline, spec
from portia.agent import chartspec, context, prompts
from portia.checks import profiling
from portia.checks.join import column_overlap
from portia.checks.join import join_findings as _join_findings
from portia.checks.outcome import BLOCKING_FLAGS, REPORT_KEYS
from portia.checks.profiling import profile_path
from portia.core import backend
from portia.core import dialect as dialects
from portia.core.io import connect, source_table
from portia.core.serialize import to_json, to_jsonable
from portia.core.table import Table
from portia.knowledge import measure, query, store
from portia.knowledge import schema as knowledge_schema
from portia.ops import join as join_op
from portia.ops import normalize as normalize_op
from portia.ops import sql as sql_op

#: Ops the spec knows how to execute, and what each step must carry. Validation
#: only — which op to *use* is the agent's call.
_REQUIRED_FIELDS = {
    "join": ("left", "right"),
    "normalize": ("input", "transforms"),
    "sql": ("inputs", "sql"),
}

#: What each op actually reports, and therefore the only things an ``expect``
#: block can assert against. An expectation on a field the op never emits drifts
#: forever — ``_drift`` compares it to ``None`` on every run — which trains
#: everyone to ignore drift. *Which* of these to assert is judgment; whether a
#: field exists at all is a fact, so it gets checked here rather than hoped for
#: in the prompt.
#:
#: Sourced from the ops themselves, so adding an op means declaring its keys
#: once, next to the code that emits them — and each op's tests assert the
#: declaration still matches a real run.
_EXPECTABLE = {
    "join": join_op.PROVENANCE_KEYS,
    "normalize": normalize_op.PROVENANCE_KEYS,
    "sql": sql_op.PROVENANCE_KEYS,
}

#: Separator for naming a table an earlier step produced, rather than an indexed
#: source: ``specs/training.yaml#reservations_hotels``. A multi-hop merge joins an
#: *intermediate* result, which is not a file and so cannot be in the catalog —
#: without this, "always measure before you decide" is impossible to obey from
#: hop 2 onward, and the agent is left recording blind. Observed doing exactly
#: that (docs/EVALUATION.md, Run 3).
STEP_REF = "#"
_STEP_REF_HINT = "'<spec path>#<step id>', e.g. 'specs/training.yaml#reservations_hotels'"

#: Where a step names a table it reads — defined by the spec format itself, so a
#: query declaring several inputs stays one fact rather than two lists. That
#: declaration is what lets `checks.outcome` say which input contributed nothing.
_REF_FIELDS = spec.REF_FIELDS
_REF_LIST_FIELD = spec.REF_LIST_FIELD


def step_vocabulary() -> dict[str, str]:
    """The words a step is allowed to use, generated from the ops that define them.

    Every one of these lists already exists in code — ``PROVENANCE_KEYS`` per op,
    ``HOWS``, ``TRANSFORM_OPS`` — and every one of them was, until now, visible to
    the model *only in a rejection*. `record_step`'s description said "base
    `expect` on what the check measured" and then refused the step for naming a
    field no op reports. A real run burned two round-trips guessing
    (``{"n_rows": 7}``, ``{"transforms": 1}``) before the validator taught it the
    vocabulary one error at a time.

    So the description states them — filled from here rather than retyped, or the
    prose rots the moment an op gains a field. `tests/test_agent_prompts.py`
    asserts the rendered description still matches these.
    """
    return {
        "expect_join": ", ".join(sorted(_EXPECTABLE["join"])),
        "expect_normalize": ", ".join(sorted(_EXPECTABLE["normalize"])),
        "expect_sql": ", ".join(sorted(_EXPECTABLE["sql"])),
        "hows": " | ".join(join_op.HOWS),
        "transform_ops": " | ".join(sorted(normalize_op.TRANSFORM_OPS)),
        "blocking_flags": ", ".join(sorted(BLOCKING_FLAGS)),
        "layers": " | ".join(spec.LAYERS),
    }


def get_context(portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Re-read the project's L1 context: prose, groups, and the source index.

    You already have this — it is in your system prompt. Call it only to pick up
    changes made *during* this session, e.g. after indexing or interpreting a
    source. Per-source detail is deliberately absent; climb to ``describe_source``
    for that.
    """
    cat = catalog.load_catalog(portia_dir)
    try:
        models: dict = context.model_index(_project_root(portia_dir))
    except ValueError as exc:
        models = {"error": str(exc)}
    return {
        "project": cat["project"],
        "groups": cat["groups"],
        "models": models,
        "sources": {
            name: {
                "summary": entry.get("summary", ""),
                "n_columns": len(entry.get("columns", [])),
                "interpreted": _is_interpreted(entry),
            }
            for name, entry in cat["sources"].items()
        },
    }


def describe_source(source: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """L2 — one source's *semantic* map: what it is and what its columns mean.

    Summary, column names, the role recorded for each, and its quality flags —
    but no statistics. This is usually enough to decide whether a source is
    relevant, which columns could be keys, and how two sources might relate.
    Reach for ``profile_source`` only when you need the numbers themselves.

    **It also carries what anyone has already learned about this table**
    (`portia/findings.py`), grouped by the table on the other side, capped, with
    the total stated. That is here rather than behind a tool of its own for one
    measured reason: `graph_lookup` was called **zero times across four runs**
    while this rung was called 23 times in one of them. A surface the agent has
    to *think to* consult does not get consulted, and there is no reason to test
    that a fifth time (`docs/FINDINGS.md` §6.1).

    **And the notes, for the same reason** *(2026-09-05, `docs/COPILOT.md` §2)*.
    A note is what a chat learned about this table that a summary cannot hold
    and a finding cannot either, because nothing was queried. They ride here,
    in the order they were written, so the read that precedes every build sees
    them without anyone remembering to ask.

    **A model's name works here too.** A built table has a catalog entry from
    the moment a step is recorded on its spec (`catalog.index_model`), and the
    audit that follows a build starts by describing what was just built.
    """
    entry = _entry(source, portia_dir)
    journal = findings.for_table(source, root=_project_root(portia_dir))
    profiled = catalog.is_profiled(entry)
    return {
        "source": entry.get("source") or entry["model"],
        "summary": entry.get("summary") or "",
        # A scoped warehouse table nobody has profiled says so as a **field**,
        # with the one number the information schema gave for free. A silence
        # here would read as *nothing to report* (`docs/CONNECTOR.md` §2.6).
        **(
            {}
            if profiled
            else {"profiled": False, "n_rows": (entry.get("indexed") or {}).get("rows")}
        ),
        "columns": [
            {
                "name": col["name"],
                "role": col.get("role"),
                **({"inferred": col.get("inferred")} if profiled else {"dtype": col.get("dtype")}),
                "flags": col.get("flags", []),
            }
            for col in entry.get("columns", [])
        ],
        # Absent when there are none, rather than an empty block: this rung is
        # 40% of a session's tool-result volume already (`tools.py`), and a line
        # saying *nobody has learned anything about this yet* would be paid for
        # on every source of every project that has never curated a chat.
        **({"findings": journal} if journal["n_findings"] else {}),
        **({catalog.NOTES: entry[catalog.NOTES]} if entry.get(catalog.NOTES) else {}),
    }


def graph_lookup(
    table: str, column: str | None = None, portia_dir: str = catalog.DEFAULT_DIR
) -> dict:
    """The router — *which* table should I look at, and where did this column come from.

    Not a rung on the disclosure ladder: the ladder is depth on one source, and
    this is breadth. It sits **before** ``describe_source`` (`KNOWLEDGE_GRAPH.md`
    §9.1) — ask it where to start, then climb.

    Named without a column it returns the table's neighbourhood: what it reads,
    what reads it, its groups, and every measured overlap grouped under the
    table on the other end, with what both columns look like beside each number.
    Add a column and it returns that column's lineage — one hop each way with
    the step that explains it, plus the files underneath.

    The graph lives in Neo4j, which may not be running. That is not an error in
    the question; it is reported as itself so the caller can carry on with the
    tools that need no database.

    Scoped to *this* project (`knowledge/schema.py`'s `PROJECT`): one server
    holds every project on the machine, so the question has to say which one it
    is about or it is a question about all of them.
    """
    project = knowledge_schema.project_id(_project_root(portia_dir))
    try:
        with store.session() as live:
            return query.lookup(live, table, column, project=project)
    except store.GraphUnavailable as exc:
        # Reframed rather than re-raised: what the model needs at this moment is
        # not the stack but which tools still work. The other refusals in
        # `prompts/errors/` exist for the same reason.
        raise ValueError(prompts.error("graph_unavailable", reason=str(exc))) from None


#: Rows a `query_data` answer carries back when the caller does not say.
#:
#: **It was a fixed cap and that was wrong** *(2026-09-02, found in a real
#: session)*. The reasoning was that the query may carry its own ``LIMIT`` and
#: two limits that disagree are a number nobody can account for. What actually
#: happened is that the agent wrote ``LIMIT 500``, silently received 20, and then
#: paged through an 89-row answer by **rewriting a 742-character query twice** —
#: the three calls are 98% and 99.9% identical, differing only in ``OFFSET``.
#: A cap with no way to ask past it is not a cap, it is a paging protocol nobody
#: documented.
#:
#: So it is a default and `limit`/`offset` are arguments. There is no ceiling
#: here because there is already one that is measured: `tools.RESULT_BUDGET`
#: refuses an oversized result and names a smaller question, which is the same
#: call `_requested` makes on an unknown column — a short answer that looks
#: complete is worse than a refusal.
QUERY_ROWS = 20


def _select(sql: str, inputs: list[str], portia_dir: str) -> Table:
    """Run one ``SELECT`` in `ops.sql`'s sandbox and hand back the handle.

    Shared by `query_data` and `plot_data`, which are the same door with two
    destinations — a paragraph the model reads, and a picture the user reads
    (`docs/VISUALIZATION.md` §2.1). One function, so the sandbox cannot end up
    configured two ways: `check_sql` refuses anything that is not a single
    ``SELECT``, and the query then runs on a connection holding **exactly** the
    declared inputs with ``enable_external_access=False``. Exploration must not
    be the seam that hands back the filesystem `session.py` withheld, and a
    second copy of this is how that stops being true of one of them.

    A handle, not data. Nothing is read until a caller asks.

    A ``<spec>#<step id>`` input is bound under the **bare step id**, and the
    query is rewritten to match — `_normalize_step_refs`' rule, applied to the
    read-only door *(2026-09-06)*. The description promised the `#` form and the
    binding didn't honour it: the input resolved (`_table` reaches a step's
    table), but it was registered under the full ref string, so ``FROM
    <step id>`` was refused as undeclared. The 2026-09-06 session hit that
    three times in a row, right after the same form had worked in
    ``record_step`` — two doors, one convention, honoured by one of them.
    """
    con = connect()
    tables: dict[str, Any] = {}
    renames: dict[str, str] = {}
    for name in inputs:
        key = name.partition(STEP_REF)[2] if STEP_REF in name else name
        if key != name:
            renames[name] = key
        if key in tables:
            raise ValueError(
                f"two inputs answer to the name {key!r} — refer to one of them "
                f"another way (a model name, or the other spec's step)"
            )
        tables[key] = _table(name, portia_dir, con)
    return sql_op.apply_sql(tables, sql_op.rename_tables(sql, renames), name="query").table


def query_data(
    sql: str,
    inputs: list[str],
    question: str,
    portia_dir: str = catalog.DEFAULT_DIR,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Ask the data one question. Runs a SELECT, returns the answer, writes nothing.

    **The tool for a question, where ``record_step`` is the tool for a
    decision.** Until this existed, `record_step` was the only path in the system
    that executed arbitrary SQL, so every question the prewritten checks could
    not answer had to be recorded permanently in a spec, gated on
    `checks.outcome`, and confirmed by a human. Seven of eleven recorded steps in
    the 2026-08-17 AQN run were unreachable and five of those were pure counting
    questions (`docs/EVALUATION.md`); the 2026-09-02 run then produced a spec
    whose two steps are both named ``diag_*`` by the agent itself. That is the
    structure, not sloppiness — there was no other door.

    Three things follow from it writing nothing, and each was a real cost:

    - **A zero is an answer.** Nothing here is gated, so *there are no rank-100
      Paris events in summer 2025* comes back as ``n_rows: 0``. Asked through
      `record_step` the same query was refused for `empty_output`, and the agent
      wrote two looser queries that returned rows instead.
    - **A follow-up costs one query.** `record_step` runs the *whole* spec to
      measure the step it is appending (`spec.run_spec`), so asking question
      three re-executed questions one and two. Here each question is one SELECT.
    - **Nothing durable changes**, so there is nothing for the human to approve
      and nothing to undo.

    The sandbox is `ops.sql`'s, unchanged and for its reasons: one ``SELECT``,
    checked readably first, then executed on a connection holding **exactly** the
    declared ``inputs`` with ``enable_external_access=False``. Exploration must
    not be the seam that hands back the filesystem `session.py` withheld.

    ``question`` is required **in code**, as `measure_overlaps`' ``reason`` is
    and for the same kind of reason: `review_queries` reads these back at the end
    of a chat so the agent can decide which ones were worth keeping, and thirty
    bare SQL strings are not reviewable. The sentence is the handle.
    """
    asked = str(question or "").strip()
    if not asked:
        raise ValueError(prompts.error("query_needs_a_question"))
    if not inputs:
        raise ValueError(prompts.error("query_needs_inputs"))

    produced = _select(sql, inputs, portia_dir)

    n_rows = produced.count()
    columns = list(produced.columns)
    # Paging happens **here**, on arguments, not by rewriting the query. See
    # :data:`QUERY_ROWS` for the session that made the difference visible.
    want = QUERY_ROWS if limit is None else max(0, int(limit))
    start = max(0, int(offset))
    window = Table(
        produced.name, f"SELECT * FROM ({produced.query}) LIMIT {want} OFFSET {start}", produced.con
    )
    # Both exits from a `Table` are capped (`core/table.py`); `rows` is the one
    # for evidence rather than for a screen, so DuckDB's own types narrow through
    # `core.serialize` instead of widening through pandas first. Records rather
    # than tuples, as `checks.join`'s example rows already are — a positional row
    # beside a column list is two things the reader has to align by hand.
    rows = [
        {col: to_jsonable(value) for col, value in zip(columns, row, strict=True)}
        for row in window.rows(want)
    ]
    answer = {
        "question": asked,
        "sql": sql,
        "inputs": list(inputs),
        "n_rows": n_rows,
        "columns": columns,
        "rows": rows,
    }
    if start:
        answer["offset"] = start
    if start + len(rows) < n_rows:
        # Says how to see the rest, because the alternative is what happened:
        # the same query rewritten with a bigger OFFSET.
        answer["more"] = prompts.error(
            "query_has_more_rows",
            shown=f"{start + 1}-{start + len(rows)}",
            n_rows=f"{n_rows:,}",
            next_offset=start + len(rows),
        )
    return answer


#: The channels a chart may encode. **Which column goes where, and nothing more**
#: (`docs/VISUALIZATION.md` §2.4).
#:
#: Every chart grammar is partly a transform language — Vega-Lite will take
#: ``aggregate: "mean"`` in a channel and Plotly will bin for you — and a number
#: a chart grammar produced is a number no ``SELECT`` returned, that nothing
#: logged and `findings.review` cannot read back. That is the line this project
#: exists to hold, restated for pixels. So the ``GROUP BY`` lives in the SQL,
#: where it is measured in the sandbox and copied into a finding verbatim, and a
#: channel is a column name.
#:
#: **The list is long on purpose** *(2026-09-03)*. It was `x`/`y`/`color` and the
#: marks were four, which meant the copilot told a user a pie chart was not
#: supported — and it was right, and there was no reason for it to be. A short
#: vocabulary is not the no-compute rule; it is an arbitrary limit that happened
#: to sit next to it. `theta` and `radius` are what a pie needs, `size` is what
#: makes a scatter a bubble chart, and `column`/`row` are small multiples.
def plot_data(
    sql: str,
    inputs: list[str],
    question: str,
    tab: str,
    vega: dict,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Draw the answer where the user can see it. Runs a SELECT, writes nothing.

    **The same door as `query_data` with the answer sent somewhere else**
    (`docs/VISUALIZATION.md` §2.1). Both run one ``SELECT`` in `ops.sql`'s
    sandbox through `_select`. `query_data` returns the rows to you as text;
    this one puts them on screen in a tab and returns you a receipt.

    That difference is why it exists. After indexing, portia has measured
    twenty-three sources and the user has read none of them, so a copilot that
    goes straight to *"should this join be inner or left?"* is asking a question
    the user cannot yet answer (`VISION.md` → *Flow*, phases 5 and 6). A chart is
    how they catch up.

    It is also why the rows are not in what you get back. A tool result is pasted
    into the conversation as text and `tools.RESULT_BUDGET` refuses it over
    30,000 characters, so a 5,000-point scatter is either refused outright or is
    you reading coordinates one at a time for a picture you will not look at.
    ``tools.plot_data`` splits this dict: the rows go to the browser, the rest
    comes back to you. **Nothing caps them**, because the model's context is no
    longer the constraint and a limit on what counts as too much to draw is code
    deciding what counts as bad (§2.7).

    ``tab`` names the tab and **is its identity**: drawing again under a name
    already on the strip replaces that chart in place, which is
    `record_step(supersedes=)`'s idiom. Portia does not guess which two charts
    are the same question; you say so by reusing the name.

    ``vega`` is **a Vega-Lite spec, written by the agent** (§2.9, 2026-09-03). It
    used to be a flat handful of channel names, and two sessions ran into the
    edge of that list — a pie chart, then a palette and a bar with a trend line
    over it. `chartspec.check` refuses the compute surface and passes everything
    else, so what is left to widen is Vega-Lite's problem rather than a release
    of portia's.

    **The argument is called ``vega`` and not ``spec``** for `CONVERSATION.md`
    §3's reason: a spec in this repo is the durable YAML record of what we did to
    the data, and this module imports that one. It is also the better teaching —
    the name says which grammar to write, which is the intervention §2.2 measured
    working.
    """
    asked = (question or "").strip()
    named = (tab or "").strip()
    if not asked:
        raise ValueError(prompts.error("query_needs_a_question"))
    if not named:
        raise ValueError(prompts.error("chart_needs_a_tab"))
    if not inputs:
        raise ValueError(prompts.error("query_needs_inputs"))
    chartspec.check(vega)

    produced = _select(sql, inputs, portia_dir)
    columns = list(produced.columns)
    # The encoding is respelled to the columns the SELECT returned when only
    # the case differs: `count(*) AS count` comes back from a warehouse as
    # `COUNT`, and six of twelve charts in one session were refused for it
    # (`CONNECTOR.md` §2.8.2). A real wrong-column encode is still refused below.
    vega = chartspec.respell(vega, dialects.resolve_columns(chartspec.fields(vega), columns))
    for field in chartspec.fields(vega):
        # Refused rather than drawn without it, as an unknown column is
        # everywhere else in this module: Vega-Lite draws an empty axis for a
        # field it cannot find, so the chart looks exactly as finished as the
        # one that was asked for and answers a different question in silence.
        if field not in columns:
            raise ValueError(
                prompts.error(
                    "chart_unknown_column",
                    field=field,
                    columns=", ".join(columns),
                )
            )

    n_rows = produced.count()
    # Uncapped on purpose (§2.7). These do not become tokens, so the one measured
    # limit in the system does not apply and there is no second one to invent.
    rows = [
        {col: to_jsonable(value) for col, value in zip(columns, row, strict=True)}
        for row in produced.rows(n_rows)
    ]
    return {
        "tab": named,
        "question": asked,
        "sql": sql,
        "inputs": list(inputs),
        "vega": vega,
        "columns": columns,
        "n_rows": n_rows,
        "rows": rows,
    }


def review_queries(chat: str | None = None, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Every question you asked this chat with `query_data`, numbered, with its answer.

    **The curation read, run before each reply rather than at the end of a
    chat.** The first version asked for it once, when the work was done, and the
    first real session never reached that moment: the user stopped after an
    answer and nine queries went unreviewed (`docs/FINDINGS.md` §5.2). A chat has
    no reliable end. A reply does, and it is the moment the agent has just
    finished a piece of work and still knows what it learned doing it.

    Each query says whether it has been ``kept`` already, so running this every
    turn is safe: only the new ones need judging, and nothing gets written twice.

    Most queries are not findings. One is when its answer *changed what happened
    next* — it decided a transformation, ruled an approach out, or is something
    the next person working on these tables would want before they start.

    There is nothing to clean up. A query you do not keep stays in the log and
    costs nothing; it is simply not indexed against the tables it touched.

    Defaults to this chat. Name an older one to curate it late — the numbers are
    still exact, because they come out of the log rather than out of anyone's
    memory.
    """
    reviewed = findings.review(portia_dir, chat=chat, root=_project_root(portia_dir))
    return {
        "chat": reviewed[0]["chat"] if reviewed else None,
        "n_queries": len(reviewed),
        "n_new": sum(1 for q in reviewed if not q["kept"]),
        # The SQL is deliberately not here. This list exists to be *read*, and
        # thirty queries' worth of SQL is the payload problem again — the
        # question is the handle, which is why `query_data` requires one.
        "queries": [
            {
                "n": q["n"],
                "question": q["question"],
                "result": q["result"],
                "failed": q["failed"],
                "kept": q["kept"],
            }
            for q in reviewed
        ],
    }


def record_finding(
    question: str,
    answer: str,
    so: str,
    about: list[str],
    from_queries: list[int],
    spec_name: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    chat: str | None = None,
) -> dict:
    """Keep one thing this chat learned, where the next session will find it.

    Everything here is authored by you **except the numbers**. ``from_queries``
    names `query_data` calls by the number `review_queries` gave them, and their
    SQL and results are copied out of the chat log verbatim. That is not
    bookkeeping: forty tool calls later you would be restating a measurement from
    memory, and a number you restate is a number you authored.

    ``about`` is what makes it findable — the tables and columns the finding
    concerns, as ``table.column`` or a bare table name. It is checked against the
    catalog, and an unknown name is refused rather than dropped.

    ``so`` says what the answer changed. If you cannot fill it in, this was a
    query rather than a finding, and it is already in the log.
    """
    root = _project_root(portia_dir)
    named = _finding_prose({"question": question, "answer": answer, "so": so})
    refs = _known_refs(about, portia_dir, root)
    reviewed = {q["n"]: q for q in findings.review(portia_dir, chat=chat, root=root)}
    queries = _cited(from_queries, reviewed)

    path = findings.record(
        question=named["question"],
        answer=named["answer"],
        so=named["so"],
        about=refs,
        queries=queries,
        spec_name=spec_name,
        root=root,
        portia_dir=portia_dir,
    )
    return {
        "finding": str(path.relative_to(root)),
        "about": refs,
        "spec": spec_name,
        "n_queries": len(queries),
    }


def _finding_prose(fields: dict[str, str]) -> dict[str, str]:
    """The three sentences, all required. Empty is a refusal, not a default."""
    named = {k: str(v or "").strip() for k, v in fields.items()}
    missing = [k for k, v in named.items() if not v]
    if missing:
        raise ValueError(prompts.error("finding_needs_prose", missing=", ".join(missing)))
    return named


def _known_refs(about: list[str], portia_dir: str, root: Path) -> list[str]:
    """``about``, checked against the catalog. A closed vocabulary, deliberately.

    This is `KNOWLEDGE_GRAPH.md` §4.8's argument for deferring Entity, applied to
    the journal: a free-text tag has no list to read first, so the names diverge
    — ``stations``, ``the station master``, ``Stations`` — and the index stops finding
    anything. There is a list to read first here, so it is read.

    Refused rather than dropped, as `_requested` refuses an unknown column: a
    finding silently filed under nothing is worse than one that was not written.
    """
    if not about:
        raise ValueError(prompts.error("finding_needs_about"))
    entries = catalog.load_catalog(portia_dir)["sources"]
    models = spec.discover_specs(root)
    checked = []
    for ref in about:
        table, column = findings.split_ref(str(ref))
        table = dialects.resolve_columns([table], [*entries, *models])[0]
        if table not in entries and table not in models:
            known = ", ".join(sorted({*entries, *models})) or "(none)"
            close = get_close_matches(table, [*entries, *models], n=3)
            hint = f" — closest: {', '.join(close)}" if close else ""
            raise ValueError(f"no table {table!r} in this project{hint}. Known: {known}")
        if column is not None and table in entries:
            names = [c["name"] for c in entries[table].get("columns", [])]
            if column not in names:
                close = get_close_matches(column, names, n=3)
                hint = f" — closest: {', '.join(close)}" if close else ""
                raise ValueError(
                    f"no column {column!r} in {table!r}{hint}. "
                    f"describe_source lists all {len(names)}."
                )
        checked.append(f"{table}.{column}" if column else table)
    return checked


def _cited(numbers: list[int], reviewed: dict[int, dict]) -> list[dict]:
    """The queries this finding came from, by their `review_queries` number."""
    if not numbers:
        raise ValueError(prompts.error("finding_needs_a_query"))
    missing = [n for n in numbers if n not in reviewed]
    if missing:
        available = ", ".join(f"#{n}" for n in sorted(reviewed)) or "(none)"
        raise ValueError(
            f"no quer{'y' if len(missing) == 1 else 'ies'} "
            f"{', '.join(f'#{n}' for n in missing)} in this chat. Available: {available}"
        )
    return [reviewed[n] for n in numbers]


def measure_overlaps(pairs: list[dict], portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Measure whether column pairs share values, and keep the answers.

    **The pairs are yours to choose** (`KNOWLEDGE_GRAPH.md` §5.1). Nothing in code
    picks them: which relationships are worth a query is a judgment from meaning,
    and a filter comparing min/max would drop `country_name` against
    `country_code` with total confidence and be wrong about the most important
    pair in the project.

    Each pair carries ``reason`` — why you think these two are worth comparing —
    and it is **required**. It is stored beside the numbers and it is what stops
    a zero from reading as a dead end a year later (§4.4).

    The structural half of the graph is refreshed first, because a measurement
    can only attach to columns the graph knows about, and refreshing is free.
    """
    con = connect()
    root = _project_root(portia_dir)
    graph = knowledge.build_graph(root, portia_dir=portia_dir).graph
    fingerprints = _table_fingerprints(graph)

    measured, edges = [], []
    for raw in pairs:
        pair = _pair(raw, graph, portia_dir)
        overlap = column_overlap(
            _table(pair.left_table, portia_dir, con),
            pair.left_column,
            _table(pair.right_table, portia_dir, con),
            pair.right_column,
        )
        measured.append({**overlap, "asked_because": pair.spec.asked_because})
        edges.append(
            measure.overlap_edge(
                pair.spec,
                overlap,
                left_fingerprint=fingerprints.get(pair.spec.left),
                right_fingerprint=fingerprints.get(pair.spec.right),
            )
        )

    return {"measured": measured, **_store_overlaps(graph, edges)}


def _store_overlaps(graph, edges: list) -> dict:
    """Write the measurements, and say plainly if they could not be kept.

    A stopped container must not lose the caller the numbers it just paid for,
    so this reports rather than raises: the measurements are in the result
    either way, and only their *durability* is in question (§6.6).

    **A reachable database is not the same as a stored measurement**, and that
    gap used to be silent (`docs/SQL_LINEAGE.md` §1.5). The write matches both
    ends, so a pair on a column the graph does not hold is dropped by Neo4j
    without an error — and this returned `stored: True` regardless. A number the
    agent paid a real query for, was told was kept, and then could not find is
    worse than the one §4.4 is about: there, at least, nobody had spent
    anything.
    """
    try:
        with store.session() as live:
            store.write(graph, live)
            written = store.write_measured(edges, live, graph.project)
    except store.GraphUnavailable as exc:
        return {"stored": False, "not_stored_because": str(exc)}
    if written < len(edges):
        return {
            "stored": False,
            "not_stored_because": prompts.error(
                "overlaps_not_attached", written=written, asked=len(edges)
            ),
        }
    return {"stored": True}


@dataclass(frozen=True)
class _ResolvedPair:
    """A requested pair, with both ends resolved to graph nodes and table refs."""

    spec: measure.Pair
    left_table: str
    right_table: str
    left_column: str
    right_column: str


def _pair(raw: dict, graph, portia_dir: str) -> _ResolvedPair:
    """One requested pair, validated. A missing reason is refused, not defaulted."""
    missing = [f for f in ("left", "left_column", "right", "right_column") if not raw.get(f)]
    if missing:
        raise ValueError(f"pair needs {', '.join(missing)}")
    reason = (raw.get("reason") or "").strip()
    if not reason:
        raise ValueError(prompts.error("overlap_needs_a_reason"))

    left, right = _graph_ref(raw["left"], graph), _graph_ref(raw["right"], graph)
    return _ResolvedPair(
        measure.Pair(left, raw["left_column"], right, raw["right_column"], reason),
        raw["left"],
        raw["right"],
        raw["left_column"],
        raw["right_column"],
    )


def _graph_ref(table: str, graph):
    """Which node in the graph a table name means — a Source's path, or a model."""
    for node in graph.nodes.values():
        if node.label == knowledge_schema.SOURCE and node.properties.get("name") == table:
            return node.ref
        if node.label == knowledge_schema.MODEL and node.key == table:
            return node.ref
    known = sorted(
        n.properties.get("name") or n.key
        for n in graph.nodes.values()
        if n.label in (knowledge_schema.SOURCE, knowledge_schema.MODEL)
    )
    raise ValueError(f"no table {table!r} in the graph — have: {', '.join(known) or '(none)'}")


def _table_fingerprints(graph) -> dict:
    """Each table's fingerprint now — what a measurement records itself against."""
    return {
        node.ref: node.properties.get(knowledge_schema.FINGERPRINT)
        for node in graph.nodes.values()
        if node.label in (knowledge_schema.SOURCE, knowledge_schema.MODEL)
    }


def profile_source(
    source: str,
    portia_dir: str = catalog.DEFAULT_DIR,
    *,
    columns: list[str] | None = None,
) -> dict:
    """L3 — everything the checks measured about one source, plus any read of it.

    Facts come **fresh from the profiling check**, not from the catalog's stored
    slice. The catalog trims what it keeps (median/std/top) because it's storage;
    the agent needs the full picture — min, max, quartiles, sample values — or it
    starts *deriving* the numbers it wasn't given. Surface evidence generously
    (CLAUDE.md); a derived figure is exactly what this project exists to prevent.

    ``summary`` and each column's ``role`` come from the catalog: whatever
    judgment has been recorded so far, empty until someone writes it.

    ``source`` may also name a table an earlier step produced
    (``<spec>#<step id>``). There is no catalog entry for one, so it comes back
    with no summary and no roles — only measurements, which is the whole point of
    asking: what do this table's columns look like *now*, after the step ran.

    ``columns`` narrows **the answer, not the measurement**. Everything is still
    profiled, so ``n_rows`` and ``n_cols`` remain statements about the whole
    table rather than about the subset. Measuring only the named columns would
    save 1.7 s on the AQN stations extract (191 columns, 1.79 s against 0.05 s for
    ten) and 6 s on its 11.4M-row event table. The payload saving is identical
    either way: 28,086 characters to 2,856 for ten columns.

    **Retiring ``candidate_keys`` took A2's stated reason with it**, and that is
    recorded here rather than acted on. The argument was that narrowing the
    measurement would make the key list unanswerable, because uniqueness is one
    ``count(DISTINCT)`` per column and those are exactly the queries a narrowed
    profile skips. There is no key list now, so the ban on narrowing rests on
    less than it did. Whether to narrow is its own decision with its own
    trade-off (`BACKLOG.md` → Checks), not a consequence of this one.
    """
    if STEP_REF in source:
        profile = profiling.profile(_step_table(source))
        return {
            "source": source,
            "summary": "",
            "n_rows": profile["n_rows"],
            "n_cols": profile["n_cols"],
            "columns": _requested(
                [{**col, "role": None} for col in profile["columns"]], columns, source
            ),
        }

    try:
        entry = _entry(source, portia_dir)
    except ValueError:
        # A spec nobody has recorded a step on or built has no entry yet, and
        # is still a table this project can produce.
        if source not in spec.discover_specs(_project_root(portia_dir)):
            raise
        entry = {}
    if "source" not in entry:
        # A model. Its table is reached by running its spec, which is what the
        # description has promised since 2026-08-31 and the handler refused
        # until 2026-09-05 (`BACKLOG.md` → Agent, closed with `docs/COPILOT.md`
        # §3): the audit after a build profiles what was just built. **On a
        # warehouse, the written table, and the facts are written back**
        # (2026-09-07): the scan used to run and leave the entry at
        # ``profiled: null``, so every audit paid for it again and the window
        # never showed a number for a table portia built.
        profile = _profile_model(source, portia_dir)
    elif entry.get(catalog.REMOTE):
        # The opt-in scan (`docs/CONNECTOR.md` §2.6): a scoped table was
        # indexed as metadata, and this is the moment somebody asked for the
        # numbers. The facts are written back, so the second ask is free.
        profile = catalog.profile_remote(source, connect(), portia_dir=portia_dir)
    else:
        profile = profile_path(_project_root(portia_dir) / entry["source"])
    roles = {c["name"]: c.get("role") for c in entry.get("columns", [])}
    return {
        "source": entry.get("source") or source,
        "summary": entry.get("summary") or "",
        "n_rows": profile["n_rows"],
        "n_cols": profile["n_cols"],
        "columns": _requested(
            [{**col, "role": roles.get(col["name"])} for col in profile["columns"]],
            columns,
            source,
        ),
        **({catalog.NOTES: entry[catalog.NOTES]} if entry.get(catalog.NOTES) else {}),
    }


def _requested(profiled: list[dict], columns: list[str] | None, source: str) -> list[dict]:
    """The columns the caller asked for, in the order they asked for them.

    Refuses on a name that is not there rather than returning what matched. A
    profile is what the agent reasons from, and a silently short one is the
    failure this whole stream exists to stop — the AQN build run shipped its
    deliverable without ever obtaining its main source's numbers, and nothing in
    the transcript reads as though the copilot knew it was missing them.

    The near miss is named instead of the column list. Listing all of them is
    what `catalog.py` does and it is right there on a narrow table; on the 191
    column source it makes the error 5,000 characters, which is the payload
    problem wearing a different hat.
    """
    if columns is None:
        return profiled

    by_name = {c["name"]: c for c in profiled}
    columns = dialects.resolve_columns(columns, by_name)
    missing = [c for c in columns if c not in by_name]
    if missing:
        close = get_close_matches(missing[0], by_name, n=3)
        suggestion = f" — closest: {', '.join(close)}" if close else ""
        raise ValueError(
            f"no column {missing[0]!r} in {source!r}{suggestion}. "
            f"{len(missing)} of {len(columns)} requested are not there; "
            f"describe_source lists all {len(profiled)}."
        )

    # Deduplicated, because asking twice is a typo rather than a request for two
    # copies, and the second one costs the same as the first.
    return list({c: by_name[c] for c in columns}.values())


def set_interpretation(
    source: str,
    summary: str | None = None,
    roles: dict[str, str] | None = None,
    note: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Record what this data *is* — the agent's read, written to the catalog.

    Writes judgment only; every fact is left untouched. Omit a field to leave
    the existing value alone. A ``note`` is appended to the entry rather than
    replacing anything (`catalog.set_interpretation`), so a chat can leave one
    sentence behind without restating the read.
    """
    if summary is None and not roles and note is None:
        raise ValueError("nothing to record — pass a summary, roles, a note, or several")

    path = catalog.set_interpretation(
        source, summary=summary, roles=roles, note=note, portia_dir=portia_dir
    )
    return {
        "source": source,
        "path": str(path),
        "summary_written": summary is not None,
        "roles_written": sorted(roles or {}),
        "note_written": note is not None,
    }


def set_group(
    name: str,
    context: str | None = None,
    sources: list[str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Record that some sources belong together, and the context they share.

    Use it when sources have something in common that no single source's entry
    can express — same vendor and the same quirks, one system's export, the
    tables that make up one workflow. That shared context then travels with all
    of them.
    """
    if context is None and sources is None:
        raise ValueError("nothing to record — pass a context, sources, or both")

    catalog.set_group(name, context=context, sources=sources, portia_dir=portia_dir)
    return {"group": name, "context_written": context is not None, "sources": sources or []}


# --- the merge loop ---------------------------------------------------------


def join_findings(
    left: str,
    right: str,
    keys: list[str] | None = None,
    left_on: list[str] | None = None,
    right_on: list[str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    *,
    left_columns: list[str] | None = None,
    right_columns: list[str] | None = None,
) -> dict:
    """What joining two sources on these keys would actually do — plus the rows.

    Returns key-level facts (overlap, coverage, relationship, fan-out, and the
    row counts each `how` would produce) *and* example rows: the unmatched ones,
    the null-key ones, the worst fan-out keys. Nothing here is ranked or scored.
    Whether a dropped row is a catastrophe or a non-event depends on the goal,
    which only you have.

    An example row carries its key columns and a few more; name
    ``left_columns``/``right_columns`` to choose the rest. The two sides are named
    separately because they are two schemas, exactly as ``left_on``/``right_on``
    already are.

    Call this **before** deciding anything about a merge.
    """
    con = connect()
    return _join_findings(
        _table(left, portia_dir, con),
        _table(right, portia_dir, con),
        on=keys,
        left_on=left_on,
        right_on=right_on,
        left_columns=left_columns,
        right_columns=right_columns,
    )


def record_step(
    spec_path: str,
    step: dict,
    layer: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    supersedes: str | None = None,
    target: str | None = None,
) -> dict:
    """Execute a decided step, measure what it produced, and record it if it holds.

    The step carries the decision (`op`, keys, `how`), an `expect` block stating
    what you predict the numbers will be, and a `rationale` saying *why*. The
    `expect` is what makes it falsifiable later: `run_spec` re-executes and
    reports drift against it. State what the check told you, not what you hope.

    **Recording runs it.** The candidate step is executed before anything is
    written and `checks.outcome` measures the resulting frame, so the returned
    dict says what the table actually looks like — not merely that YAML was
    saved. This is push, not pull: a verification the agent *may* call is one it
    will sometimes skip, and the run that shipped a table missing an entire
    source skipped exactly that (docs/EVALUATION.md).

    A step that hits a zero-condition is **not written**. Overriding is possible
    and deliberate: `acknowledge: [<flag>]` on the step, which lands in the YAML
    for the user to read in a diff.

    **Steps chain.** A step's output is registered under its ``id``, so a later
    step may name it as ``left``, ``right`` or ``input`` and receive that frame.
    Multi-hop work is built this way — join A to B, then join *that result* to C.

    ``supersedes`` names a step in this spec that this one **replaces** — a
    correction, in place, keeping its position so anything reading it keeps
    reading it. Steps were append-only until 2026-09-02 and the argument for it
    was that the decision record is a record; git is the record
    (`docs/FINDINGS.md` §7). The rule that survives is **nothing enters a spec
    unmeasured**, and a replacement is still a recording: the whole spec
    re-executes, `checks.outcome` measures what the replacement produced, and the
    gate applies to it. It is refused while another step reads the id being
    retired, which is `cli/remove_spec`'s condition at step granularity.

    Validation and serialization happen here, in code. The *content* is yours.
    """
    root = _project_root(portia_dir)
    # **The engine places the file; the argument only names it** *(2026-08-16)*.
    # A spec that declares a layer belongs in that layer's subdirectory, exactly
    # as its compiled `.sql` does (`spec.spec_path`), so the decision record and
    # the build output have one shape between them. An existing spec is never
    # moved — its path is whatever `discover_specs` already found — because
    # relocating a tracked file on the next recorded step is a diff nobody asked
    # for.
    name = Path(spec_path).stem
    found = spec.discover_specs(root).get(name)
    path = root / found if found else Path(spec_path)
    doc: dict[str, Any] = (
        spec.load_spec(path) if path.exists() else {"version": 1, "sources": {}, "steps": []}
    )
    sources: dict[str, str] = doc.setdefault("sources", {})
    steps: list[dict] = doc.setdefault("steps", [])

    # A spec-level claim, not a step-level one — the layer describes the table the
    # spec builds. Set once and left alone afterwards: silently re-layering a model
    # on a later step would move its file without anyone reading a decision.
    spec.validate_layer(layer)
    if layer and not doc.get("layer"):
        doc["layer"] = layer
    # Where the table is created on a warehouse — the same kind of claim as the
    # layer, and the agent's to make per table (`CONNECTOR.md` §2.7.1). Set once;
    # a different one later is refused rather than silently kept, because a
    # move the agent believes happened and didn't is a table in two places.
    spec.validate_target(target)
    if target and not doc.get("target"):
        doc["target"] = target
    elif target and target != doc.get("target"):
        raise ValueError(
            f"this spec already writes to {doc['target']!r}; a spec has one target. "
            f"To move the table, the user edits `target:` in the spec file."
        )
    if found is None:
        # New spec: now that the layer is settled, it can be placed.
        path = spec.spec_path(name, layer=doc.get("layer"), root=root)

    # Every other spec in the project is a table this step may read, by name. The
    # spec being written is excluded: a model cannot be its own input.
    models = {n: p for n, p in spec.discover_specs(root).items() if n != path.stem}

    step_ids = {s["id"] for s in steps}
    at = _superseded_index(steps, supersedes, replacement=step.get("id"))
    _normalize_step_refs(step, spec_path=str(path))
    _validate_step(step, existing=steps, replacing=supersedes)
    for ref in _source_refs(step, known_steps=step_ids | set(models)):
        try:
            sources[ref] = _source_ref(ref, portia_dir)
        except ValueError as exc:
            # It's none of the three things a name can be. Say all three, or the
            # caller assumes chaining is unsupported rather than mistyped.
            known = ", ".join(sorted(step_ids)) or "(none yet)"
            other = ", ".join(sorted(models)) or "(none)"
            raise ValueError(
                f"{exc}. Earlier steps in this spec: {known}. "
                f"Other models in this project, which you may name directly: {other}"
            ) from exc

    # Run before writing. An exception here — a missing column, a transform that
    # can't apply — is now surfaced instead of being written into a durable spec
    # that only fails when someone re-runs it, possibly months later.
    #
    # A replacement runs the **whole** spec with it in place, which is what makes
    # editing safe: a later step that the correction breaks fails here rather
    # than on somebody else's build. The result taken is the replacement's own,
    # at its position, and not the last step's.
    candidate = [*steps, step] if at is None else [*steps[:at], step, *steps[at + 1 :]]
    results = spec.run_spec({**doc, "steps": candidate}, base_dir=root, models=models)
    result = results[-1 if at is None else at]

    # Shape before post-conditions: a malformed prediction has to be fixed whether
    # or not the data is sound, and unlike a zero it is never legitimate — so
    # there is no acknowledgement for it.
    problems = _expect_shape_problems(step.get("expect") or {}, result.provenance)
    if problems:
        raise ValueError(prompts.error("expect_shape", problems="\n".join(problems)))

    if result.blocking:
        raise ValueError(
            prompts.error(
                "blocked_step",
                step_id=repr(step["id"]),
                flags=", ".join(result.blocking),
                facts=to_json(result.outcome),
            )
        )

    doc["steps"] = candidate
    steps = candidate
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.save_spec(doc, path)
    # Written before it is indexed, so the entry can say where the table is.
    written_to = _write_model(path.stem, results, target=doc.get("target"))
    return {
        "spec": str(path),
        "step_id": step["id"],
        "superseded": supersedes,
        "n_steps": len(steps),
        "layer": doc.get("layer"),
        "target": doc.get("target"),
        "outcome": result.outcome,
        "drift": result.drift,
        "acknowledged": result.acknowledged,
        "model": _index_model(
            path.stem,
            results,
            portia_dir,
            written_to=written_to,
            fingerprint=pipeline.fingerprint(doc),
        ),
        "written_to": written_to,
        "graph": _sync_graph(root, portia_dir),
    }


def _write_model(name: str, results: list[Any], *, target: str | None) -> str | None:
    """Create the model's table in the warehouse, on the hand-off only
    (`docs/CONNECTOR.md` §2.7.1). ``None`` everywhere else.

    **After the gate and after the spec is saved**, so a step a zero refused
    never reaches the warehouse and the table is the one the spec describes.
    Not best-effort: a refusal from the warehouse — the role cannot create the
    schema, say — is a sentence the agent has to read, because the next layer
    was going to read this table where it is.
    """
    active = backend.active()
    if not (active.remote and active.agent_writes):
        return None
    return pipeline.write_into_warehouse(name, results, target=target)


def _index_model(
    name: str,
    results: list[Any],
    portia_dir: str,
    *,
    written_to: str | None = None,
    fingerprint: str | None = None,
) -> str:
    """Record what the table this spec now builds looks like — best effort.

    **The fourth write moment, and the one that was missing.**
    `catalog.index_model` shipped on 2026-09-01 with `pipeline.build_project` as
    its only caller, on the argument that *building is the only moment this is
    free*: a model has no file, so reaching its table means running its spec, and
    the build that just finished is holding it.

    That argument is right and it named the wrong moment. Recording a step runs
    the **whole spec** through `run_spec` to measure the step being appended
    (`record_step`'s own docstring: *recording runs it*), so this function is
    holding exactly the same table, for exactly the same reason, on every call.
    Not indexing here meant a spec could be built up over fourteen recorded steps
    and still have no catalog entry, because the human had not happened to press
    Build — which is what the 2026-09-04 session hit when it tried to write a
    read of its own finished table and was told to *index it first*, naming a
    file no source of a model could ever have.

    Same failure rule as :func:`_sync_graph` beside it, and `pipeline._index`
    before that: the step is recorded and the spec is on disk, and a description
    of a table may not take down the thing it describes.

    **It costs 2%**, measured on the 2026-09-04 project's six-step spec over
    609k rows: 11.44 s for the `run_spec` this function's caller already paid,
    and **0.25 s** for the profile on top of it. That is not the general shape of
    a profile — `DUCKDB_MIGRATION.md` §14 measures one scan plus two queries per
    column, and one full parse per column on a CSV — and the reason it does not
    apply is that this table is the output of an op, already materialized, so
    there is no file to re-read 18 times.
    """
    table = results[-1].table if results else None
    if table is None:
        return "no table to describe"
    try:
        # Shape only on a warehouse: the 2% above is a scan there, per step.
        catalog.index_model(
            name,
            table,
            portia_dir=portia_dir,
            metadata_only=backend.is_remote(table.con),
            written_to=written_to,
            fingerprint=fingerprint,
        )
    except Exception as exc:  # noqa: BLE001 - a description must not fail a step
        return f"not updated — {exc}"
    return "updated"


def _sync_graph(root: Path, portia_dir: str) -> str:
    """Put the table this spec now builds into the graph — best effort, always.

    The third of §5's write moments, and the one that was missing: a spec written
    mid-conversation used to be invisible to `graph_lookup` until something else
    triggered a rebuild, so the graph could be confidently wrong about what
    exists. Recording a step is exactly when a **new table** comes into being,
    and the column lineage of that table is derived from the spec that was just
    saved — so this is a restatement of a file that changed one line ago.

    Cheap: building the graph reads YAML and runs nothing (`knowledge/build.py`),
    and the write is a handful of `UNWIND`s. It is also **never fatal** — the
    step is already recorded and the spec is already on disk, and failing the
    write because a container is stopped would undo none of that while losing
    the user their step (§6.6).
    """
    try:
        knowledge.sync(root, portia_dir=portia_dir)
    except store.GraphUnavailable as exc:
        return f"not updated — {exc}"
    return "updated"


def read_spec(
    spec_ref: str,
    *,
    journal: bool = False,
    measured: bool = False,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Open one spec as it is recorded, without running it.

    **The decision record was the one durable artifact the agent wrote and
    could not read** *(2026-09-04, `docs/PIPELINE.md` §9)*. `run_spec`
    re-executes every model behind a spec to measure it and hands back
    provenance and drift; the `rationale`, the `expect`, the SQL as written and
    which step reads which never came back, and nothing told the agent which
    specs existed. Asked about one, a Haiku session said it needed the spec's
    name and could only run it.

    The steps come back **verbatim**. The spec is a record, and a summary of a
    decision is a second author. Around them: what the spec reads and what
    reads it (`spec.dependencies`, the same DAG `build_project` walks), whether
    its compiled ``.sql`` is current, when its table was last built and how
    many findings sit under it. Those last two are counts rather than the
    things themselves so that an absence reads as a number and never as
    nothing (`KNOWLEDGE_GRAPH.md` §4.4); ``journal`` and ``measured`` add the
    things on request.

    ``spec_ref`` is a model name or a spec path. Both resolve through
    `discover_specs`, so the answer is about the file the project would build.
    """
    root = _project_root(portia_dir)
    models = spec.discover_specs(root)
    name, path = _resolve_spec(spec_ref, models, root)
    doc = spec.load_spec(path)
    steps = list(doc.get("steps") or [])
    layer = doc.get("layer")
    deps = spec.dependencies(models, base_dir=root) if name in models else {}
    compiled = pipeline.model_path(path, layer=layer, root=root)
    entry = catalog.load_models(portia_dir).get(name)
    briefs = findings.briefs_for_spec(name, root=root)
    out: dict[str, Any] = {
        "spec": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
        "model": name,
        "layer": layer,
        "sources": dict(doc.get("sources") or {}),
        "n_steps": len(steps),
        "steps": steps,
        "reads": sorted(deps.get(name, ())),
        "read_by": sorted(n for n, reads in deps.items() if name in reads),
        "compiled": str(compiled.relative_to(root)) if compiled.exists() else None,
        "compiled_stale": pipeline.is_stale(path, doc, layer=layer, root=root),
        "built": ((entry or {}).get("built") or {}).get("at"),
        "n_findings": len(briefs),
    }
    if journal:
        out["journal"] = briefs
    if measured:
        # ``None`` when nobody has built it — a fact, stated, not an empty list.
        out["measured"] = entry
    return out


def _resolve_spec(ref: str, models: dict[str, Path], root: Path) -> tuple[str, Path]:
    """A model name or a spec path, to the spec file the project would build.

    Refused with the project's spec names rather than a bare "not found": the
    agent that reaches for the wrong name is one turn from the right one, and
    the brief already lists them all.
    """
    name = Path(ref).stem
    if name in models:
        return name, root / models[name]
    given = root / ref
    if given.suffix == ".yaml" and given.is_file():
        return name, given
    known = ", ".join(sorted(models)) or "(none yet)"
    raise ValueError(
        f"no spec named {name!r}. Specs in this project: {known}. "
        "Each is listed under Pipeline in your brief."
    )


def run_spec(spec_path: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Re-execute a spec and report what each step actually did, plus any drift.

    Use it to check your own work: record a step, run it, and see whether the
    numbers match what you predicted. Drift is a disagreement between the spec's
    `expect` and today's result — not necessarily an error, but always worth
    surfacing rather than smoothing over.

    Each step also carries its `outcome`: the post-conditions measured on the
    table it produced. Drift says whether the prediction held; the outcome says
    what came out. A step can have no drift and still have produced a table with
    an entire source missing from it — that has happened.
    """
    root = _project_root(portia_dir)
    results = spec.run_spec(
        spec.load_spec(spec_path), base_dir=root, models=spec.discover_specs(root)
    )
    return {
        "spec": spec_path,
        "steps": [
            {
                "id": r.id,
                "op": r.op,
                "provenance": r.provenance,
                "drift": r.drift,
                "outcome": r.outcome,
                "acknowledged": r.acknowledged,
                "blocking": r.blocking,
            }
            for r in results
        ],
        "has_drift": any(r.has_drift for r in results),
        "blocking": sorted({flag for r in results for flag in r.blocking}),
    }


# --- internals --------------------------------------------------------------


def _project_root(portia_dir: str) -> Path:
    """The repo portia is plugged into — the parent of its own directory.

    Everything a project holds is relative to this: the specs, the compiled
    models, and (once `PIPELINE.md` §2.7 lands) every data file that may be
    indexed at all.
    """
    return Path(portia_dir).parent


def _table(ref: str, portia_dir: str, con=None):
    """Resolve a table reference: an indexed source, another model, or an earlier step.

    All file reading goes through ``core.io.load_table``. A ``con`` is passed
    when two references have to end up on the *same* connection — a join check
    reads both sides at once, and DuckDB cannot join across handles.
    """
    if STEP_REF in ref:
        return _step_table(ref, con)
    con = con or connect()
    try:
        return source_table(
            _source_ref(ref, portia_dir), con, base=_project_root(portia_dir), name=ref
        )
    except ValueError as exc:
        # Not an indexed source. Before giving up, try the project's other models:
        # a name is allowed to be any of three things, and a message that names
        # only one of them reads as "that table doesn't exist" when the truth is
        # "not by that name" (`docs/PIPELINE.md` §2.4).
        root = _project_root(portia_dir)
        models = spec.discover_specs(root)
        ref = dialects.resolve_columns([ref], models)[0]
        if ref in models:
            return _built_table(ref, models, root, con, portia_dir)
        known = ", ".join(sorted(models)) or "(none)"
        raise ValueError(
            f"{exc}. Models in this project: {known}. "
            f"For a table an earlier step in the spec you are writing produced: {_STEP_REF_HINT}"
        ) from exc


def _profile_model(name: str, portia_dir: str) -> dict:
    """A model's full profile: the written table through the catalog, else its spec run."""
    root = _project_root(portia_dir)
    con = connect()
    if written_table(name, spec.discover_specs(root), root, con, portia_dir):
        return catalog.profile_remote(name, con, portia_dir=portia_dir)
    return profiling.profile(_table(name, portia_dir))


def _built_table(ref: str, models: dict, root: Path, con, portia_dir: str):
    """A model's table: the one in the warehouse when this spec built it, else by running the spec.

    **Reading a built table back** (`CONNECTOR.md` §2.7.2, the half of
    `BACKLOG.md`'s entry that the hand-off made possible). A model entry that
    carries a ``table`` was written by a build of the spec whose ``fingerprint``
    it also carries; while the spec still has that fingerprint, the table in
    the warehouse *is* the model, and profiling it is one scan rather than a
    re-run of every step behind it on the meter. The moment the spec moves the
    fingerprint moves, and the read falls back to running it, as before. Only
    on a remote connection: locally there is nothing written anywhere.
    """
    written = written_table(ref, models, root, con, portia_dir)
    if written:
        return source_table({"table": written}, con, name=ref)
    return spec.model_table(ref, models, root, con, ())


def written_table(ref: str, models: dict, root: Path, con, portia_dir: str) -> str | None:
    """The warehouse table that *is* this model right now, or ``None``.

    The entry's ``table``, when the connection is remote and the entry's
    ``fingerprint`` is the spec's current one. Shared by `_built_table` and
    `profile_source`, so the read and the opt-in profile agree about which
    table the model is.
    """
    entry = catalog.load_models(portia_dir).get(ref) or {}
    written = entry.get("table")
    if not written or not backend.is_remote(con) or ref not in models:
        return None
    doc = spec.load_spec(root / models[ref])
    return str(written) if entry.get("fingerprint") == pipeline.fingerprint(doc) else None


def _step_table(ref: str, con=None):
    """Reach the table an earlier step produced, by re-running up to it.

    Only up to it: a later step may be the one being diagnosed and may not run
    at all yet. Executing the prefix is what ``record_step`` already does to
    measure a candidate, so this adds no new machinery — it just makes the same
    table reachable to a *read-only* check, before anything is written.
    """
    spec_path, _, step_id = ref.partition(STEP_REF)
    doc = spec.load_spec(spec_path)
    steps = doc.get("steps") or []
    ids = [s["id"] for s in steps]
    if step_id not in ids:
        known = ", ".join(ids) or "(no steps yet)"
        raise ValueError(f"no step {step_id!r} in {spec_path} — have: {known}")
    prefix = {**doc, "steps": steps[: ids.index(step_id) + 1]}
    root = Path(spec_path).parent.parent
    return spec.run_spec(
        prefix, base_dir=root, con=con or connect(), models=spec.discover_specs(root)
    )[-1].table


def _known_name(name: str, portia_dir: str) -> str:
    """``name`` as the project spells it, when only the case differs.

    The third session on Snowflake typed `INT_UNIFIED_INSPECTIONS` and
    `STG_NYC_INSPECTIONS` for models it had itself named in lower case — the
    warehouse had just shown them to it that way — and was refused twice
    (`CONNECTOR.md` §2.8.2's rule, applied to table names). Same resolver, same
    rule: exact first, then a unique case-insensitive match, else as it came.
    """
    root = _project_root(portia_dir)
    known = [*catalog.load_catalog(portia_dir)["sources"], *spec.discover_specs(root)]
    return dialects.resolve_columns([name], known)[0]


def _entry(source: str, portia_dir: str) -> dict:
    """A catalog entry by name: an indexed source first, then a built model.

    Sources first for `catalog._entry_file`'s reason: the two namespaces can
    collide and the source is the one with a file behind it. The model half is
    what lets `describe_source` open a table the project built, which the audit
    after every build starts with (`docs/COPILOT.md` §3). An unknown name is
    told about both halves, because *no indexed source* on a model that exists
    reads as *that table does not exist* when the truth is *not by that name*.
    """
    source = _known_name(source, portia_dir)
    cat = catalog.load_catalog(portia_dir)
    entry = cat["sources"].get(source)
    if entry is None:
        entry = catalog.load_models(portia_dir).get(source)
    if entry is None:
        known = ", ".join(cat["sources"]) or "(none indexed)"
        models = ", ".join(sorted(catalog.load_models(portia_dir))) or "(none built)"
        raise ValueError(
            f"no indexed source or built model {source!r} — sources: {known}; models: {models}"
        )
    return dict(entry)


def _source_ref(source: str, portia_dir: str):
    """What a spec's ``sources:`` block records for this source — a path, or a table.

    A model has an entry but no ref: it is reached by running its spec, which is
    `_table`'s fallback, so this refuses it the way it refuses an unknown name.
    """
    entry = _entry(source, portia_dir)
    if "source" not in entry:
        raise ValueError(f"{source!r} is a model, not a source")
    return catalog.source_ref_of(entry)


def _superseded_index(steps: list[dict], supersedes: str | None, *, replacement: Any) -> int | None:
    """Where the replaced step sits, or ``None`` when this is an ordinary append.

    Refused while **another** step reads the id being retired, which is
    `cli/remove_spec`'s condition at step granularity: the set being changed has
    to be closed under *is read by*. Reusing the same id is therefore the easy
    case and the common one — the name survives, so its readers do too.
    """
    if supersedes is None:
        return None
    at = next((i for i, s in enumerate(steps) if s.get("id") == supersedes), None)
    if at is None:
        known = ", ".join(s["id"] for s in steps) or "(none)"
        raise ValueError(f"no step {supersedes!r} in this spec. Steps: {known}")

    if replacement != supersedes:
        readers = [
            s["id"] for i, s in enumerate(steps) if i != at and supersedes in spec.step_inputs(s)
        ]
        if readers:
            raise ValueError(
                prompts.error(
                    "superseded_step_is_read",
                    step_id=repr(supersedes),
                    readers=", ".join(repr(r) for r in readers),
                    replacement=repr(replacement),
                )
            )
    return at


def _validate_step(step: dict, *, existing: list[dict], replacing: str | None = None) -> None:
    if not step.get("id"):
        raise ValueError("step needs an 'id'")
    if step["id"] in {s["id"] for s in existing} - {replacing}:
        # Recording a step twice by accident is still an error, and the message
        # names the deliberate way to do it on purpose. **What changed in
        # 2026-09-02 is that there now is one.** `STEPS ARE APPEND-ONLY` was
        # defended on the grounds that the decision record is a record; git is
        # the record, and a superseded step removed from a committed spec is
        # visible in the diff and recoverable from history — which is the
        # protection every other durable artifact here already leans on. The rule
        # that survives is not append-only, it is *nothing enters a spec
        # unmeasured*: a replacement is still a `record_step`, so it runs, the
        # whole spec runs with it, and the gate applies (`docs/FINDINGS.md` §7).
        raise ValueError(prompts.error("immutable_step", step_id=repr(step["id"])))

    _validate_grain(step.get("grain"))
    _validate_acknowledge(step.get("acknowledge"))

    op = step.get("op")
    if op not in _REQUIRED_FIELDS:
        raise ValueError(f"unknown op {op!r} — have: {', '.join(_REQUIRED_FIELDS)}")
    missing = [f for f in _REQUIRED_FIELDS[op] if not step.get(f)]
    if missing:
        raise ValueError(
            prompts.error(
                "missing_step_fields",
                op=op,
                missing=_and([repr(f) for f in missing]),
                stray=_stray_fields(step, op),
            )
        )
    if op == "join" and not (step.get("keys") or (step.get("left_on") and step.get("right_on"))):
        raise ValueError("join step needs 'keys', or both 'left_on' and 'right_on'")

    if op == "normalize":
        _validate_transforms(step["transforms"])

    if op == "sql":
        # Refused here rather than at execution, so a statement that isn't a
        # single read never reaches a spec — the same reason `_validate_grain`
        # runs before the step does.
        sql_op.check_sql(step["sql"])

    unknown = sorted(set(step.get("expect") or {}) - _EXPECTABLE[op])
    if unknown:
        raise ValueError(
            prompts.error(
                "expect_unknown",
                op=op,
                names=_and([repr(u) for u in unknown]),
                notes=_expect_notes(unknown, op),
                expectable=", ".join(sorted(_EXPECTABLE[op])),
            )
        )


def _and(names: list[str]) -> str:
    """`'a'`, `'a' and 'b'`, `'a', 'b' and 'c'` — a list a sentence can contain."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _stray_fields(step: dict, op: str) -> str:
    """Whether the step named its input the way a *different* op names it.

    A real run (2026-08-08) sent a `sql` step with `input` instead of `inputs`,
    got back "sql step needs inputs", changed nothing that mattered, and got the
    identical sentence again. The message was true and useless: it said what was
    absent and never that something very close to it was present, so there was
    nothing in it to act on. Three write confirmations bought one step.

    Derived from `_REQUIRED_FIELDS` rather than a hand-written list of confusions,
    so an op added later is covered by the op declaring its own fields.
    """
    elsewhere = {
        field: other
        for other, fields in _REQUIRED_FIELDS.items()
        if other != op
        for field in fields
        if field not in _REQUIRED_FIELDS[op]
    }
    found = [(f, elsewhere[f]) for f in step if f in elsewhere]
    if not found:
        return ""
    named = _and([f"{f!r} ({owner}'s field)" for f, owner in found])
    return f"The step does send {named}.\n\n"


def _expect_notes(unknown: list[str], op: str) -> str:
    """One line per field, for the ones where a bare rejection would mislead.

    `n_rows` is the case worth the code: it is not invented, it is sitting in the
    `outcome` block of the very result the model just read, so being told the op
    "never reports" it reads as a contradiction. Two reports, two questions —
    `CLAUDE.md` → "Drift and outcome are different questions" — and the fix is to
    say which one the word belongs to and name the op's own equivalent.
    """
    rows = _EXPECTABLE[op] & {"result_rows", "input_rows"}
    equivalent = "result_rows" if "result_rows" in rows else ("input_rows" if rows else "")
    lines = []
    for field in unknown:
        if field not in REPORT_KEYS:
            continue
        note = f"  {field!r} comes from the 'outcome' block, which is measured, not predicted"
        if field == "n_rows" and equivalent:
            note += f". The count {op} itself reports is {equivalent!r}"
        lines.append(note + ".")
    return "\n".join(lines) + "\n" if lines else ""


#: Longest actual value quoted back when a prediction's shape is wrong. Enough to
#: see the shape; not enough to paste a table into an error message.
EXAMPLE_CHARS = 90


def _expect_shape_problems(expect: dict, provenance: dict) -> list[str]:
    """Predictions that can never come true because they're the wrong type.

    ``_EXPECTABLE`` already rejects a field no op reports. This is the same
    disease one level down: the right field, the wrong kind of value. A run
    predicted ``{"transforms": 1}`` where ``transforms`` is a list of transform
    records — the key existed, so it validated, and that spec now drifts on every
    run forever (docs/EVALUATION.md, Run 3).

    Checked here rather than in ``_validate_step`` because it needs the *actual*
    reported value, which only exists once the step has run — and by this point
    it has.
    """
    problems = []
    for field, predicted in expect.items():
        actual = provenance.get(field)
        if _kind(predicted) != _kind(actual):
            example = str(actual)
            if len(example) > EXAMPLE_CHARS:
                example = f"{example[:EXAMPLE_CHARS]}…"
            problems.append(
                f"  {field}: you predicted {_kind(predicted)} ({predicted!r}), "
                f"but {provenance['op']} reports {_kind(actual)} — {example}"
            )
    return problems


def _kind(value: Any) -> str:
    """A coarse type name, in the words an error message should use.

    ``bool`` is checked before ``int`` because in Python it *is* one, and
    ``matches_prediction: 1`` should not pass as a boolean prediction. int and
    float share a kind — predicting ``10.0`` for a row count is not an error.
    """
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return "nothing" if value is None else type(value).__name__


def _validate_grain(grain: Any) -> None:
    """The grain *claim*'s shape. Whether it holds is measured after the run.

    Only the shape is checkable here — whether the columns exist depends on what
    the step produces, so a claim naming a column that never appears comes back
    as the `grain_columns_missing` post-condition rather than a validation error.
    """
    if grain is None:
        return
    if not isinstance(grain, list) or not grain or not all(isinstance(c, str) for c in grain):
        raise ValueError("'grain' must be a non-empty list of output column names")


def _validate_acknowledge(acknowledge: Any) -> None:
    """An override may only name a flag that can actually block.

    Acknowledging something that was never going to block reads, in a diff, like
    a decision the user should weigh — so it has to be a real one.
    """
    if acknowledge is None:
        return
    if not isinstance(acknowledge, list) or not all(isinstance(f, str) for f in acknowledge):
        raise ValueError("'acknowledge' must be a list of flag names")
    unknown = sorted(set(acknowledge) - BLOCKING_FLAGS)
    if unknown:
        raise ValueError(
            f"acknowledge names {', '.join(repr(u) for u in unknown)}, which never blocks. "
            f"Blocking flags: {', '.join(sorted(BLOCKING_FLAGS))}"
        )


def _validate_transforms(transforms: Any) -> None:
    """Check each transform's shape, not just that the list exists.

    Regression: a step was written with ``{"column": ..., "transform": "strip"}``
    instead of ``"op"``. It validated, was accepted, and only failed with a bare
    ``KeyError`` when the spec was re-run — which for a durable artifact could
    have been months later. Validating the container and not its contents is the
    same mistake as accepting an ``expect`` key no op reports.
    """
    if not isinstance(transforms, list):
        raise ValueError("normalize: 'transforms' must be a list")
    known = ", ".join(sorted(normalize_op.TRANSFORM_OPS))
    for i, t in enumerate(transforms):
        if not isinstance(t, dict):
            raise ValueError(f"normalize: transform {i} must be an object")
        if not t.get("column"):
            raise ValueError(f"normalize: transform {i} needs a 'column'")
        chosen = t.get("op")
        if not chosen:
            extra = " (did you mean 'op'?)" if "transform" in t else ""
            raise ValueError(f"normalize: transform {i} needs an 'op'{extra}. One of: {known}")
        if chosen not in normalize_op.TRANSFORM_OPS:
            raise ValueError(f"normalize: transform {i} has unknown op {chosen!r}. One of: {known}")


def _normalize_step_refs(step: dict, *, spec_path: str) -> None:
    """Let a step name its inputs the same way every other tool does.

    ``join_findings`` and ``profile_source`` need ``<spec>#<step id>`` — a step's
    output is not a file, so there is nothing else to call it. A step in a spec
    doesn't, because the spec it belongs to is the spec it is being written to.
    Two conventions for one idea, and Run 4 tripped over the seam three times,
    burning a round-trip and a write confirmation each: `#`-form into
    ``record_step``, bare id into ``join_findings``, `#`-form again.

    So the `#` form is accepted here too and reduced to the bare id, which is
    what the spec stores — a step referring to its own spec by path in its own
    spec is noise in a file whose whole point is being readable in a diff.

    **And it rewrites the query, not only the declaration** (`PIPELINE.md` §8,
    2026-09-04). A `sql` step names its inputs in two places — the ``inputs``
    list and the SQL text — and `ops.sql.apply_sql` registers each input under
    exactly the key the step declared. Normalizing one and not the other is what
    made the documented `#` form fail on `sql` steps and only `sql` steps:
    `join` and `normalize` read their inputs out of the fields this loop already
    fixed, so for them the declaration *is* the reference.
    """
    renames: dict[str, str] = {}

    def bare(ref: str, *, field: str) -> str:
        renamed = _bare_step_id(ref, field=field, spec_path=spec_path)
        if renamed != ref:
            renames[ref] = renamed
        return renamed

    for field in _REF_FIELDS:
        ref = step.get(field)
        if isinstance(ref, str):
            step[field] = bare(ref, field=field)
    if isinstance(step.get(_REF_LIST_FIELD), list):
        step[_REF_LIST_FIELD] = [
            bare(r, field=_REF_LIST_FIELD) if isinstance(r, str) else r
            for r in step[_REF_LIST_FIELD]
        ]

    # The rewritten SQL is what lands in the spec, which is `_bare_step_id`'s own
    # argument applied to the other half of the step: a query naming its own spec
    # by path, inside that spec, is noise in a file whose point is the diff.
    if step.get("op") == "sql" and isinstance(step.get("sql"), str) and renames:
        step["sql"] = sql_op.rename_tables(step["sql"], renames)


def _bare_step_id(ref: str, *, field: str, spec_path: str) -> str:
    """``specs/t.yaml#reservations_hotels`` → ``reservations_hotels``; anything else unchanged.

    A ``#``-reference to a *different* spec is reduced to that spec's **model
    name**, because one spec produces one table and the name of that table is the
    spec's own name (`docs/PIPELINE.md` §2.1, §2.4). Which step inside it produced
    the table is not the caller's business, and naming one would couple two specs
    to each other's internals.

    **Specs are compared by name, not by path** *(2026-08-16)*. It is the same
    rule one line down — a reference to another spec becomes its stem — and it
    has to be, now that the engine places a spec by its layer: the caller may
    say ``specs/orders.yaml`` for a file the engine wrote to
    ``specs/staging/orders.yaml``, and comparing paths made a step's reference to
    *its own spec* stop resolving the moment the file moved a directory down.
    """
    if STEP_REF not in ref:
        return ref
    named_spec, _, step_id = ref.partition(STEP_REF)
    if Path(named_spec).stem != Path(spec_path).stem:
        return Path(named_spec).stem
    return step_id


def _source_refs(step: dict, *, known_steps: set[str]) -> list[str]:
    """Source names the step reads, minus anything produced by an earlier step."""
    return [ref for ref in spec.step_inputs(step) if ref not in known_steps]


def _is_interpreted(entry: dict) -> bool:
    """Whether a source still carries the auto-drafted placeholder read."""
    return catalog.is_interpreted(entry)
