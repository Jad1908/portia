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

from collections.abc import Callable
from dataclasses import dataclass
from difflib import get_close_matches
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from portia import catalog, findings, knowledge, pipeline, results, spec
from portia.agent import chartspec, context, drawn, freshness, graphcards, prompts, reserved
from portia.checks import profiling, sql_joins
from portia.checks.join import column_overlap
from portia.checks.join import join_findings as _join_findings
from portia.checks.outcome import BLOCKING_FLAGS, REPORT_KEYS
from portia.core import backend, cancel, listing, present
from portia.core import dialect as dialects
from portia.core.io import connect, relative, source_table
from portia.core.serialize import to_json, to_json_compact, to_jsonable_nested
from portia.core.table import Table, subquery
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


# --- the source cards (2026-10-13, the tool revamp) --------------------------------
#
# `get_context`, `describe_source` and `profile_source` answer with a card whose
# size does not grow with the data (`core/listing.py`): every list is cut at a
# fixed length and the cut says how to ask a narrower question, every flagged
# column is named by the flag rule, and anything else about the columns is one
# `query_data` on `portia_columns` (`agent/reserved.py`). The grouped forms they
# replaced (`tools._by_type`) counted a flag without naming its columns, and an
# auto-drafted summary naming every mostly-null column rode along on all three,
# so a sparse 800-column table was over the budget however it was grouped.

#: Up to this many columns a card lists every one, with its type, role and
#: flags; past it the card counts them by type and names every flagged one
#: (:func:`_overview`). **A placeholder**, tuned by the scale test: two hundred
#: rows of ordinary names and short roles are 11,000 to 17,000 characters, about
#: half of `tools.RESULT_BUDGET`, and a card that does not fit anyway is sent as
#: its overview (:func:`overview_of`).
CARD_LISTS_ALL = 200

#: Names one type gives on a wide card: every one up to this many, else the
#: first this many in table order, as examples. Table order is the one order
#: that makes no choice about which columns are worth a look.
TYPE_EXAMPLES = 20

#: Columns one filtered `describe_source` lists before it says how many more match.
MATCH_CAP = 200

#: Columns one `profile_source` call gives facts for. A table no wider than this
#: comes back whole; a wider one, asked without a filter, comes back as its card.
PROFILE_CAP = 50

#: Notes a card carries: the latest this many, with how many there are in all.
CARD_NOTES = 10

#: Tables whose findings with this one a card shows, in name order, each with
#: its latest `findings.PER_NEIGHBOUR`. The rest are named and counted
#: (`FINDING_TABLES_NAMED`), and ``findings_with`` reads any one of them. A
#: card of a table forty others had findings with was 19,317 characters,
#: three-quarters of it findings (the scale test, 2026-10-13).
CARD_FINDING_TABLES = 10

#: Tables past `CARD_FINDING_TABLES` a card names, with how many findings each.
FINDING_TABLES_NAMED = 100

#: Findings ``describe_source(findings_with=…)`` shows about two tables, latest first.
FINDINGS_WITH = 20

#: Sources, and models, one `get_context` lists before it says how many more there are.
CONTEXT_ROWS = 100

#: What a card says about each column. Meaning only: L2 measures nothing.
DESCRIBE_FIELDS = ("name", "type", "role", "flags")

#: What `profile_source` says about each column: the facts indexing stores
#: (`catalog._SHAPE`, `catalog.LATER_FACTS`), so a stored answer and a measured
#: one read the same.
PROFILE_FIELDS = (
    "name",
    "type",
    "null_rate",
    "n_distinct",
    "min",
    "q25",
    "median",
    "q75",
    "max",
    "mean",
    "std",
    "top",
    "top_freq",
    "samples",
    "flags",
)

#: What `get_context` says about each table.
INDEX_FIELDS = ("name", "n_rows", "n_columns", "read", "about", "stale")
GROUP_FIELDS = ("name", "n_sources", "context")
NOTE_FIELDS = ("at", "text")

#: Where `profile_source`'s numbers come from, as its ``facts`` field says it:
#: what indexing stored, or a profile run for this call because nothing was stored.
STORED = "stored"
MEASURED_NOW = "measured now"

#: A column's type on a card when its entry records none (a hand edit).
UNTYPED = "untyped"


def card_vocabulary() -> dict[str, str]:
    """The caps and the flag names the card tools' descriptions state, from the code.

    For `step_vocabulary`'s reason: the caps are placeholders the scale test
    tunes, and a description that retyped them would go on saying 200 after
    the constant moved. One mapping for all three descriptions; each file uses
    the fields it needs.
    """
    return {
        "card": str(CARD_LISTS_ALL),
        "examples": str(TYPE_EXAMPLES),
        "notes": str(CARD_NOTES),
        "finding_tables": str(CARD_FINDING_TABLES),
        "match_cap": str(MATCH_CAP),
        "profile_cap": str(PROFILE_CAP),
        "rows": str(CONTEXT_ROWS),
        "flags": ", ".join(profiling.FLAGS),
    }


def get_context(
    portia_dir: str = catalog.DEFAULT_DIR, *, match: str | None = None, group: str | None = None
) -> dict:
    """L1 again, as the project card: what the project is, its groups, and its tables by name.

    You already have this: it is in your system prompt. Call it only to pick up
    changes made *during* this session, e.g. after indexing or interpreting a
    source. Per-source detail is deliberately absent; climb to ``describe_source``
    for that.

    **One line per table, and only what a person wrote** *(2026-10-13)*. This
    sent every source's ``summary`` whole, and an auto-drafted summary names
    every mostly-null column: one sparse table put the index of a forty-table
    project over `tools.RESULT_BUDGET`, and sixty ordinary ones did it alone.
    Now a source is its name, its row and column counts, whether anyone has
    read it, the first sentence of what they wrote (``about``) and whether its
    file changed since (``stale``, `freshness.statuses`, which asks no
    warehouse). Models are listed the same way beside the sources, because the
    card is every table the project has, a spec nobody has built included,
    with no counts. Each list is cut at `CONTEXT_ROWS`, in name order, with
    ``more`` pointing at the filters and at ``portia_tables``, which holds the
    same facts for every table.

    ``match`` narrows both lists to names matching a shell-style pattern,
    without case; ``group`` to that group's members. Either one answers the
    lists alone.
    """
    match, group = match or None, group or None
    cat = catalog.load_catalog(portia_dir)
    sources = cat["sources"]
    built = catalog.load_models(portia_dir)
    groups = list(cat.get("groups") or [])
    members = _members(groups, group) if group is not None else None
    try:
        specs = spec.discover_specs(_project_root(portia_dir))
        stale = freshness.statuses(portia_dir, sources, built)
    except ValueError:
        # Two specs sharing a name: `cli/build`'s to report. The built models
        # are still listed; their freshness cannot be traced (``null``).
        specs = {}
        stale = freshness.statuses(portia_dir, sources, {})
    # Every model the project has: built, or a spec nobody has recorded a step
    # through yet, which has no entry and so no counts.
    models = {name: built.get(name, {}) for name in sorted({*built, *specs})}

    def index(entries: dict[str, dict], more: Callable[[int], str]) -> dict:
        names = [
            name
            for name in sorted(entries)
            if _matches(name, match) and (members is None or name in members)
        ]
        shown = names[:CONTEXT_ROWS]
        rows = [_index_row(name, entries[name], stale.get(name)) for name in shown]
        return listing.listing(INDEX_FIELDS, rows, n=len(names), more=more(len(names) - len(shown)))

    tables = {
        "sources": index(sources, lambda left: prompts.more("context_sources", left=left)),
        "models": index(models, lambda left: prompts.more("context_models", left=left)),
    }
    if match is not None or group is not None:
        return tables
    shown_groups = groups[:CONTEXT_ROWS]
    return {
        "project": cat["project"],
        "n_sources": len(sources),
        "n_models": len(models),
        "n_groups": len(groups),
        "groups": listing.listing(
            GROUP_FIELDS,
            [
                [g.get("name"), len(g.get("sources") or []), g.get("context") or None]
                for g in shown_groups
            ],
            n=len(groups),
            more=prompts.more("context_groups", left=len(groups) - len(shown_groups)),
        ),
        **tables,
    }


def _index_row(name: str, entry: dict, stale: bool | None) -> list:
    """One table as `get_context` lists it: `INDEX_FIELDS`, from its entry alone."""
    written = catalog.written_summary(entry)
    return [
        name,
        catalog.row_count(entry),
        len(entry["columns"] or []) if "columns" in entry else None,
        written is not None,
        context.first_sentence(written) if written else None,
        stale,
    ]


def _members(groups: list[dict], group: str) -> set[str]:
    """The sources in ``group``, matched as a table name is; an unknown group is refused."""
    names = [str(g.get("name")) for g in groups]
    (wanted,) = dialects.resolve_columns([group], names)
    found = next((g for g in groups if str(g.get("name")) == wanted), None)
    if found is None:
        raise ValueError(
            prompts.error("unknown_group", group=group, groups=", ".join(names) or "(none)")
        )
    return {str(s) for s in found.get("sources") or []}


def _matches(name: str, pattern: str | None) -> bool:
    """Whether ``name`` matches a shell-style ``pattern``, without case.

    Without case because every engine portia runs on folds an unquoted name
    (`core/dialect.resolve_columns`): ``meter_*`` is the pattern a copilot
    types for ``METER_01`` on a warehouse. ``fnmatchcase`` on both lowered,
    because ``fnmatch`` itself folds or not by operating system.
    """
    return not pattern or fnmatchcase(name.lower(), pattern.strip().lower())


def describe_source(
    source: str,
    portia_dir: str = catalog.DEFAULT_DIR,
    *,
    columns: list[str] | None = None,
    match: str | None = None,
    type_: str | None = None,
    flag: str | None = None,
    findings_with: str | None = None,
) -> dict:
    """L2 — one table's card: what it is, what its columns mean, what anyone wrote about it.

    Its written summary, its row and column counts, each column's type, role
    and quality flags, and no statistics. This is usually enough to decide
    whether a source is relevant, which columns could be keys, and how two
    sources might relate. Reach for ``profile_source`` only when you need the
    numbers themselves.

    **The card never grows with the data** *(2026-10-13)*. Up to
    `CARD_LISTS_ALL` columns it lists every one (``fields`` once, a row each).
    Past it, it counts them by type with a few names each, names every flagged
    column by the flag rule (`core/listing.flagged`: the rarer side of each
    flag, a count only past a cap), counts the rest, and says in ``more`` how
    to list any of them. Its filters are that narrower question: ``columns``
    (exact names), ``match`` (a shell-style pattern, without case), ``type_``
    and ``flag``, together an *and*; they answer the matching columns alone,
    `MATCH_CAP` at a time. The grouped form this replaced counted flags without
    naming a column, and it carried the auto-drafted summary, which on a
    sparse table named every mostly-null column and made the description of
    an 800-column table too big to send in any form. ``summary`` is now only
    what somebody wrote (`catalog.written_summary`), else ``null``: the draft's
    facts are the flags and the row count, said where they belong. Worth
    remembering why size here matters: in the AQN build run this rung was 31
    calls and 101,601 characters, 40% of every tool result, and no single call
    was ever refused, so nothing flagged it.

    **It also carries what anyone has already learned about this table**
    (`portia/findings.py`), grouped by the table on the other side, capped, with
    the total stated: the first `CARD_FINDING_TABLES` tables in full, the rest
    named and counted, and ``findings_with`` reads the findings about this
    table and one other. That is here rather than behind a tool of its own for one
    measured reason: `graph_lookup` was called **zero times across four runs**
    while this rung was called 23 times in one of them. A surface the agent has
    to *think to* consult does not get consulted, and there is no reason to test
    that a fifth time (`docs/FINDINGS.md` §6.1).

    **And the notes, for the same reason** *(2026-09-05, `docs/COPILOT.md` §2)*.
    A note is what a chat learned about this table that a summary cannot hold
    and a finding cannot either, because nothing was queried. The latest
    `CARD_NOTES` ride here in the order they were written, counted against all
    of them (2026-10-13: a table with 140 notes sent all 140), and
    ``portia_tables`` holds every one.

    **A model's name works here too.** A built table has a catalog entry from
    the moment a step is recorded on its spec (`catalog.index_model`), and the
    audit that follows a build starts by describing what was just built.
    """
    name = _known_name(source, portia_dir)
    entry = _entry(name, portia_dir)
    profiled = catalog.is_profiled(entry)
    # A scoped warehouse table nobody has profiled says so as a **field**: its
    # types are the warehouse's and it has no flags yet. A silence here would
    # read as *nothing to report* (`docs/CONNECTOR.md` §2.6).
    unprofiled = {} if profiled else {"profiled": False}
    cols = list(entry.get("columns") or [])
    filtering = _filtering(columns, match, type_, flag)
    if filtering or findings_with:
        narrowed: dict[str, Any] = {"source": name}
        if filtering:
            picked = _matching(cols, name, columns=columns, match=match, type_=type_, flag=flag)
            shown = picked[:MATCH_CAP]
            narrowed.update(unprofiled)
            narrowed["n_columns"] = len(cols)
            narrowed["columns"] = listing.listing(
                DESCRIBE_FIELDS,
                [_row(col, DESCRIBE_FIELDS) for col in shown],
                n=len(picked),
                more=prompts.more("described_columns", left=len(picked) - len(shown)),
            )
        if findings_with:
            narrowed["findings"] = _findings_with(name, findings_with, portia_dir)
        return narrowed
    card: dict[str, Any] = {
        "source": name,
        "path": _path_of(name, entry, portia_dir),
        "summary": catalog.written_summary(entry),
        **unprofiled,
        **row_facts(entry),
        "n_columns": len(cols),
    }
    if len(cols) <= CARD_LISTS_ALL:
        card["columns"] = listing.listing(
            DESCRIBE_FIELDS, [_row(col, DESCRIBE_FIELDS) for col in cols], n=len(cols)
        )
    else:
        card.update(_overview(cols, name, measured=profiled, more=_card_more(name)))
    # Absent when there are none, rather than an empty block: this rung is
    # called for every table a session reads, and a line saying *nobody has
    # learned anything about this yet* would be paid for on every source of
    # every project that has never curated a chat.
    journal = findings.for_table(name, root=_project_root(portia_dir))
    if journal["n_findings"]:
        card["findings"] = _findings_card(journal, name)
    notes = list(entry.get(catalog.NOTES) or [])
    if notes:
        latest = notes[-CARD_NOTES:]
        card[catalog.NOTES] = listing.listing(
            NOTE_FIELDS,
            [[note.get("at"), note.get("text")] for note in latest],
            n=len(notes),
            start=len(notes) - len(latest),
            more=prompts.more("notes_earlier", left=len(notes) - len(latest), source=name),
        )
    return card


def _findings_card(journal: dict, source: str) -> dict:
    """`findings.for_table`'s answer with a cap on the tables, the rest named and counted.

    Each table already showed its latest `findings.PER_NEIGHBOUR` findings with
    its total; the number of tables was the part that grew. The first
    `CARD_FINDING_TABLES`, in `for_table`'s name order (findings about this
    table alone first), stay as they were. The rest are a list of name and
    count, and ``more`` says that ``findings_with`` reads any of them. The
    ``table`` key is dropped: it is the name the call gave.
    """
    groups = list(journal["with"].items())
    out: dict[str, Any] = {
        "n_findings": journal["n_findings"],
        "with": dict(groups[:CARD_FINDING_TABLES]),
    }
    rest = groups[CARD_FINDING_TABLES:]
    if rest:
        named = rest[:FINDING_TABLES_NAMED]
        out["also_with"] = listing.listing(
            ("table", "n"),
            [[table, group["n"]] for table, group in named],
            n=len(rest),
            more=prompts.more("findings_tables", left=len(rest) - len(named)),
        )
    if rest or any(len(group["findings"]) < group["n"] for _, group in groups):
        out["more"] = prompts.more("findings_with", source=source, cap=FINDINGS_WITH)
    return out


def _findings_with(source: str, other: str, portia_dir: str) -> dict:
    """The findings about ``source`` and ``other`` together, latest first, up to `FINDINGS_WITH`.

    ``other`` is matched against the tables the findings name, the way a column
    name is matched (`core/dialect.resolve_columns`): exactly, else one match
    without case. A table nothing has findings with answers none, which is a
    fact rather than a refusal.
    """
    journal = findings.for_table(
        source, root=_project_root(portia_dir), per_neighbour=FINDINGS_WITH
    )
    (other,) = dialects.resolve_columns([other], list(journal["with"]))
    group = journal["with"].get(other) or {"n": 0, "findings": []}
    briefs = group["findings"]
    return {
        "with_table": other,
        "findings": listing.listing(
            ("question", "answer", "so", "path"),
            [[b["question"], b["answer"], b["so"], b["path"]] for b in briefs],
            n=group["n"],
            more=prompts.more("findings_older", left=group["n"] - len(briefs)),
        ),
    }


def overview_of(card: dict) -> dict:
    """A card whose every-column list is over the budget, sent as the wide card instead.

    `tools.describe_source`'s ``coarser``: tried only when the card as built
    does not fit `tools.RESULT_BUDGET`, which two hundred columns with long
    names and long roles can manage. The overview is built from the listed
    rows, so nothing is read again, and it is the shape every card past
    `CARD_LISTS_ALL` already has: bounded whatever the names are. A filtered
    answer is the narrower question already and is returned as it came.
    """
    listed = card.get("columns")
    if "path" not in card or not isinstance(listed, dict) or "rows" not in listed:
        return card
    measured = card.get("profiled") is not False
    cols = [_unrow(dict(zip(listed["fields"], row, strict=True))) for row in listed["rows"]]
    out: dict[str, Any] = {}
    for key, value in card.items():
        if key == "columns":
            out.update(
                _overview(
                    cols, str(card["source"]), measured=measured, more=_card_more(card["source"])
                )
            )
        else:
            out[key] = value
    return out


def _card_more(source: str) -> str:
    """What a wide card says last: every column is counted, and how to list any of them."""
    return prompts.more("card_columns", source=source)


def _overview(columns: list[dict], source: str, *, measured: bool, more: str) -> dict:
    """A table too wide to list, as counts by type and every flagged column named.

    ``types`` is each type in the order it first appears in the table, with its
    count and its names, or its first `TYPE_EXAMPLES` names when it has more.
    ``flagged`` is `core/listing.flagged` over the columns, in the profiler's
    flag order; a flag given as a count says how to list it (``more``), and
    ``unflagged`` counts the columns that carry none. Neither is given for a
    table nobody measured, whose columns have no flags to read. Nothing is
    ordered by a measurement.
    """
    types: dict[str, list[str]] = {}
    for col in columns:
        types.setdefault(_type_of(col) or UNTYPED, []).append(str(col["name"]))
    out: dict[str, Any] = {
        "types": {
            kind: {"n": len(names), "columns": names}
            if len(names) <= TYPE_EXAMPLES
            else {"n": len(names), "examples": names[:TYPE_EXAMPLES]}
            for kind, names in types.items()
        }
    }
    if measured:
        flagged = listing.flagged(columns, order=profiling.FLAGS)
        for flag, named in flagged.items():
            if named.get(listing.LISTED) is False:
                named["more"] = prompts.more("flag_unlisted", flag=flag, source=source)
        out["flagged"] = flagged
        out["unflagged"] = sum(1 for col in columns if not col.get("flags"))
    out["more"] = more
    return out


def _type_of(col: dict) -> str | None:
    """A column's type as a card names it: the profiler's, else the engine's."""
    found = col.get("inferred") or col.get("dtype")
    return None if found is None else str(found)


def _flags_of(col: dict) -> list[str] | None:
    """A column's flags, or ``None`` when nobody measured it: not the same as none raised."""
    return [str(f) for f in col.get("flags") or []] if "inferred" in col else None


def _row(col: dict, fields: tuple[str, ...]) -> list:
    """One column as a list row: ``type`` and ``flags`` read as a card reads them."""
    derived = {"type": _type_of(col), "flags": _flags_of(col)}
    return [derived[f] if f in derived else col.get(f) for f in fields]


def _unrow(row: dict) -> dict:
    """A listed row back into a column as the catalog holds one, for `_overview`."""
    kind = "inferred" if row.get("flags") is not None else "dtype"
    return {"name": row["name"], kind: row.get("type"), "flags": row.get("flags")}


def _path_of(name: str, entry: dict, portia_dir: str) -> str | None:
    """Where a table is: a source's file or warehouse table, a model's spec."""
    if "source" in entry:
        return str(entry["source"])
    found = spec.discover_specs(_project_root(portia_dir)).get(name)
    return found.as_posix() if found is not None else None


def _filtering(*filters: Any) -> bool:
    """Whether a call narrowed its columns at all: an empty filter is no filter."""
    return any(f is not None and f != [] and f != "" for f in filters)


def _matching(
    cols: list[dict],
    source: str,
    *,
    columns: list[str] | None,
    match: str | None,
    type_: str | None,
    flag: str | None,
) -> list[dict]:
    """The columns that pass every filter given: named ones in the order named, else table order.

    An unknown name is refused (:func:`_requested`); an unknown flag too,
    because the flags are a closed vocabulary (`profiling.FLAGS`) and a typo
    would otherwise read as *no column carries it*. A type nothing has, or a
    pattern nothing matches, is an empty list: zero is an answer there, and
    the card already says which types the table has.
    """
    picked = _requested(cols, columns, source) if columns else list(cols)
    if match:
        picked = [col for col in picked if _matches(str(col["name"]), match)]
    if type_:
        wanted = type_.strip().lower()
        picked = [col for col in picked if (_type_of(col) or UNTYPED).lower() == wanted]
    if flag:
        wanted = flag.strip().lower()
        if wanted not in profiling.FLAGS:
            raise ValueError(
                prompts.error("unknown_flag", flag=flag, flags=", ".join(profiling.FLAGS))
            )
        picked = [col for col in picked if wanted in (_flags_of(col) or [])]
    return picked


def graph_lookup(
    table: str,
    column: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    *,
    neighbour: str | None = None,
    to: str | None = None,
) -> dict:
    """The map between tables: how to get from one to another, and what links them.

    Not a rung on the disclosure ladder: the ladder is depth on one source, and
    this is breadth. It sits **before** ``describe_source`` (`KNOWLEDGE_GRAPH.md`
    §9.1) — ask it where to start, then climb. And it is where a relationship
    is looked up before it is measured, since every measurement lands there.

    Four questions, one at a time (*2026-10-13*, `agent/graphcards.py` cuts
    each to a card): the table alone is its neighbourhood, one entry per linked
    table; ``neighbour`` is every measured pair between two tables in full;
    ``column`` is that column's lineage a few hops each way and what it was
    compared with; ``to`` is the routes from one table to another.

    The graph lives in Neo4j, which may not be running. That is not an error in
    the question; it is reported as itself so the caller can carry on with the
    tools that need no database.

    Scoped to *this* project (`knowledge/schema.py`'s `PROJECT`): one server
    holds every project on the machine, so the question has to say which one it
    is about or it is a question about all of them.
    """
    asked = [
        name for name, value in (("column", column), ("neighbour", neighbour), ("to", to)) if value
    ]
    if len(asked) > 1:
        raise ValueError(prompts.error("graph_one_question", given=_and(asked)))
    project = knowledge_schema.project_id(_project_root(portia_dir))
    try:
        with store.session() as live:
            answer = query.lookup(live, table, column, neighbour=neighbour, to=to, project=project)
    except store.GraphUnavailable as exc:
        # Reframed rather than re-raised: what the model needs at this moment is
        # not the stack but which tools still work. The other refusals in
        # `prompts/errors/` exist for the same reason.
        raise ValueError(prompts.error("graph_unavailable", reason=str(exc))) from None
    return graphcards.card(answer)


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
#: So it is a default and `limit`/`offset` are arguments. **The ceiling is
#: :data:`QUERY_CHARS` now, not a refusal** *(2026-10-13, the tool revamp)*: a
#: big ``limit`` used to be refused by `tools.RESULT_BUDGET` with nothing sent,
#: and the answer is now as many whole rows as fit, saying where to go on from.
QUERY_ROWS = 20

#: Columns a `query_data` answer carries before it says how many more there are
#: and how to ask for them (``column_offset``). **A placeholder** *(2026-10-13)*.
#: A row of a 1,579-column table was refused whole, one row or twenty, because
#: the columns alone were over `tools.RESULT_BUDGET`; the copilot had no way to
#: see any of it short of naming columns it had never been shown.
QUERY_COLUMNS = 50

#: How many characters an answer's rows may take, encoded. Rows are added whole
#: until the next would pass it, and at least one always is: fifty columns of
#: values cut at :data:`QUERY_CELL_CHARS` is under it. Leaves room in
#: `tools.RESULT_BUDGET` for the field names, the notices and the ``more`` lines.
QUERY_CHARS = 20_000

#: The longest value an answer shows whole. Past it the value is cut, ends in
#: :data:`CUT_MARK`, and the answer names the columns where that happened: the
#: kept copy holds it whole, and ``substr`` reads it in pieces. A free-text
#: column of long descriptions would otherwise let one row fill the answer.
QUERY_CELL_CHARS = 300

#: What a value cut at :data:`QUERY_CELL_CHARS` ends with.
CUT_MARK = "…"


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

    **A name portia answers for itself is an input too** *(2026-10-13,
    `agent/reserved.py`)*: ``portia_columns``, ``portia_tables``, and whatever
    else registers there. Each is built from portia's records on a local
    DuckDB connection of its own and copied into the sandbox like a source.

    **On a warehouse, a query reads portia's records or the warehouse's
    tables, never both** — refused here, before anything runs, with a sentence
    saying how to split it. The records are on this machine and the tables
    are where they are: the warehouse path runs the query where the data is
    (`ops/sql._apply_remote`), and putting a few thousand catalog rows into
    that statement as literals would ship portia's notes about every table to
    the warehouse on every such query, in a statement that grows with the
    catalog. A query naming only reserved inputs needs nothing from the
    warehouse, so it runs locally, in DuckDB's SQL, on any project, which is
    what makes the catalog queryable there at all. `apply_sql` chooses its
    path off the first input's connection, so the order of the inputs is kept.
    """
    own = [name for name in inputs if reserved.find(name) is not None]
    theirs = [name for name in inputs if reserved.find(name) is None]
    con = connect() if theirs else None
    if own and con is not None and backend.is_remote(con):
        raise ValueError(
            prompts.error(
                "reserved_with_warehouse",
                reserved=", ".join(own),
                tables=", ".join(theirs),
            )
        )
    built = reserved.tables(own, portia_dir, reserved.connection()) if own else {}
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
        tables[key] = built[name] if name in built else _table(name, portia_dir, con)
    return sql_op.apply_sql(tables, sql_op.rename_tables(sql, renames), name="query").table


def interrupted_facts(tool: str, args: dict, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """What portia measures about a data call the user interrupted, **after** the interrupt.

    `docs/CONVERSATION.md` §16. The copilot reads this in place of the result
    it will never have: how big each input is, and for each equality join the
    SQL makes between two named sides, how often the key repeats on each side
    and exactly how many rows the join produces (`checks/sql_joins.py`). The
    query that asked for it unpivoted two 1,579-column tables and joined them
    on the meter name alone; ran to completion, it was some 430 billion rows,
    and nothing on screen said so.

    A size is the catalog's on a warehouse and counted locally. **Locally the
    catalog's count is not used** *(2026-10-13, when every entry began to
    record one)*: a count is exact there and on nobody's meter, and a file may
    have changed since it was indexed. **On a warehouse
    nothing is counted**: what the catalog holds is all there is, and a join is
    not measured, because measuring it would be the scan on someone's meter
    that the interrupt was pressed to stop. A join this cannot read is listed
    with why, and the copilot is told those are the general facts only.

    The joins are counted in `ops/sql.sandbox`, the walls the query had: the
    statement's own CTEs run again there, and they are the agent's SQL.
    """
    names, sql = _what_it_read(tool, args)
    con = connect()
    if backend.is_remote(con):
        return {"scanned": False, "inputs": {name: _known_rows(name, portia_dir) for name in names}}
    # Each input on its own, so one that no longer resolves costs its own size
    # and not the whole answer. Under the name the query reads it by.
    tables: dict[str, Any] = {}
    keys: dict[str, str] = {}
    own = [name for name in names if reserved.find(name) is not None]
    built = reserved.tables(own, portia_dir, reserved.connection()) if own else {}
    for name in names:
        key = name.partition(STEP_REF)[2] if STEP_REF in name else name
        try:
            tables[key] = built[name] if name in built else _table(name, portia_dir, con)
        except ValueError:
            continue
        keys[name] = key
    bound = sql_op.rename_tables(sql, {n: k for n, k in keys.items() if n != k}) if sql else ""
    joins, unread = sql_joins.read(bound, tables) if bound else ([], [])
    facts: dict[str, Any] = {"inputs": {}}
    counted: dict[str, int] = {}
    if joins:
        with sql_op.sandbox(tables) as (box, counted):
            facts["joins"] = sql_joins.measure(box, bound, joins)
    for name in names:
        if name not in keys:
            facts["inputs"][name] = {"rows": None, "from": UNRESOLVED}
        elif keys[name] in counted:
            facts["inputs"][name] = {"rows": counted[keys[name]], "from": COUNTED}
        else:
            facts["inputs"][name] = {"rows": tables[keys[name]].count(), "from": COUNTED}
    if unread:
        facts["not_read"] = unread
    return facts


#: Where an input's size in `interrupted_facts` came from. Codes the copilot
#: reads, each said in words by `prompts/errors/interrupted_measured.md`.
FROM_CATALOG = "catalog"
COUNTED = "counted"
UNKNOWN = "unknown"
UNRESOLVED = "unresolved"


def _what_it_read(tool: str, args: dict) -> tuple[list[str], str | None]:
    """The tables a data call declared, and its SQL if it had one, off its arguments."""
    if tool in ("query_data", "plot_data"):
        return [str(n) for n in args.get("inputs") or []], str(args.get("sql") or "") or None
    if tool == "record_step":
        raw = args.get("step")
        step: dict = raw if isinstance(raw, dict) else {}
        names = [str(ref) for ref in spec.step_inputs(step) if ref]
        sql = str(step.get("sql") or "") if step.get("op") == "sql" else ""
        return list(dict.fromkeys(names)), sql or None
    if tool == "profile_source":
        return [str(args.get("source") or "")], None
    if tool == "join_findings":
        return [str(args.get("left") or ""), str(args.get("right") or "")], None
    if tool == "measure_overlaps":
        named = [
            str(pair.get(side) or "")
            for pair in args.get("pairs") or []
            if isinstance(pair, dict)
            for side in ("left", "right")
        ]
        return list(dict.fromkeys(n for n in named if n)), None
    return [], None


def _known_rows(name: str, portia_dir: str) -> dict:
    """An input's row count as the catalog already holds it, without asking the data.

    A warehouse table's entry carries the count its metadata gave for nothing
    (`catalog.scope_table`), PostgreSQL's as the planner's estimate
    (`catalog.rows_estimated`), or the count its last profile made; an entry
    indexed before counts were kept, and an earlier step, have none, and say
    ``None`` here (`catalog.row_count`).
    """
    if STEP_REF in name:
        return {"rows": None, "from": UNKNOWN}
    try:
        entry = _entry(name, portia_dir)
    except ValueError:
        return {"rows": None, "from": UNKNOWN}
    rows = catalog.row_count(entry)
    if rows is None:
        return {"rows": None, "from": UNKNOWN}
    known: dict[str, Any] = {"rows": rows, "from": FROM_CATALOG}
    if catalog.rows_estimated(entry.get("indexed")):
        known["approximate"] = True
    return known


def row_facts(entry: dict) -> dict:
    """A table's row count as an answer states it: the number, or why there is none.

    ``{"n_rows": n}``, with ``approximate: ["n_rows"]`` when it is the
    warehouse's estimate. When the entry holds no count the answer says so as
    a field rather than leaving a ``null`` to be read as zero or as unknown
    (`SQL_LINEAGE.md` §9): ``n_rows_note`` is why, in prompt text, and the
    two causes are told apart because their remedies differ. The drafted
    summary is never parsed for a count (`catalog.row_count`).
    """
    rows = catalog.row_count(entry)
    if rows is not None:
        estimated = catalog.rows_estimated(entry.get("indexed"))
        return {"n_rows": rows, **({"approximate": ["n_rows"]} if estimated else {})}
    if catalog.rows_recorded(entry):
        return {"n_rows": None, "n_rows_note": prompts.notice("rows_not_counted")}
    return {"n_rows": None, "n_rows_note": prompts.notice("rows_not_recorded")}


def query_data(
    sql: str,
    inputs: list[str],
    question: str,
    portia_dir: str = catalog.DEFAULT_DIR,
    limit: int | None = None,
    offset: int = 0,
    column_offset: int = 0,
) -> dict:
    """Ask the data one question. Runs a SELECT, returns the answer, writes nothing durable.

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
      and nothing to undo. The whole result is kept beside the chat's log
      (`portia/results.py`), which is the chat's own scratch and goes with it.

    The sandbox is `ops.sql`'s, unchanged and for its reasons: one ``SELECT``,
    checked readably first, then executed on a connection holding **exactly** the
    declared ``inputs`` with ``enable_external_access=False``. Exploration must
    not be the seam that hands back the filesystem `session.py` withheld.

    ``question`` is required **in code**, as `measure_overlaps`' ``reason`` is
    and for the same kind of reason: `review_queries` reads these back at the end
    of a chat so the agent can decide which ones were worth keeping, and thirty
    bare SQL strings are not reviewable. The sentence is the handle.

    **The answer is bounded and every cut says how to go on** *(2026-10-13, the
    tool revamp)*. Rows are capped by ``limit`` and by :data:`QUERY_CHARS`,
    columns by :data:`QUERY_COLUMNS` (``column_offset`` moves along them), a
    long value by :data:`QUERY_CELL_CHARS`; nothing is refused for size. The
    rows are a list under ``fields``, and the question, the SQL and the inputs
    are not sent back: the agent wrote them, and the log has them.

    **Asking for another page reads the kept copy** rather than running the
    query again: the same SQL over the same inputs, with ``limit``, ``offset``
    or ``column_offset`` given, is answered from the result this chat kept for
    it, and the answer says so (``paged``). On a warehouse that is the
    difference between one bill for a result and one per page. A call with
    none of the three is a question asked again, and runs.
    """
    asked = str(question or "").strip()
    if not asked:
        raise ValueError(prompts.error("query_needs_a_question"))
    if not inputs:
        raise ValueError(prompts.error("query_needs_inputs"))
    # Paging happens **here**, on arguments, not by rewriting the query. See
    # :data:`QUERY_ROWS` for the session that made the difference visible.
    want = QUERY_ROWS if limit is None else max(0, int(limit))
    start = max(0, int(offset))
    first_column = max(0, int(column_offset))

    log = results.log_for(portia_dir)
    paging = limit is not None or start > 0 or first_column > 0
    kept = results.same(log, sql, inputs) if paging else None
    notices: dict[str, str] = {}
    if kept is not None:
        n_rows, columns, source = kept.n_rows, list(kept.columns), results.table(kept)
        notices["paged"] = prompts.notice("result_paged", result=kept.handle, at=kept.at)
    else:
        produced = _select(sql, inputs, portia_dir)
        n_rows, columns = produced.count(), list(produced.columns)
        outcome = results.keep(
            produced,
            log=log,
            tool=findings.QUERY_TOOL,
            question=asked,
            sql=sql,
            inputs=list(inputs),
            n_rows=n_rows,
            columns=columns,
        )
        if isinstance(outcome, results.Kept):
            kept, source = outcome, results.table(outcome)
        else:
            source = produced
            notices["not_kept"] = _not_kept(outcome)
    handle = kept.handle if kept is not None else None

    def more_rows(shown: str, next_offset: int) -> str:
        if handle is None:
            return prompts.more(
                "query_rows_rerun", shown=shown, n_rows=f"{n_rows:,}", next_offset=next_offset
            )
        return prompts.more(
            "query_rows", shown=shown, n_rows=f"{n_rows:,}", next_offset=next_offset, result=handle
        )

    def more_columns(shown: str, next_column: int) -> str:
        if handle is None:
            return prompts.more(
                "query_columns_rerun",
                shown=shown,
                n_columns=f"{len(columns):,}",
                next_column=next_column,
            )
        return prompts.more(
            "query_columns",
            shown=shown,
            n_columns=f"{len(columns):,}",
            next_column=next_column,
            result=handle,
        )

    page = _page(
        source,
        columns,
        n_rows=n_rows,
        start=start,
        want=want,
        first_column=first_column,
        budget=QUERY_CHARS,
        more_rows=more_rows,
        more_columns=more_columns,
    )
    if "cut" in page:
        notices["cut"] = page.pop("cut")
    return {
        **notices,
        "result": handle,
        "n_rows": n_rows,
        "n_columns": len(columns),
        **page,
    }


def _not_kept(outcome: results.NotKept) -> str:
    """Why a result was not kept, in words, with the number that decided it."""
    if outcome.reason == results.TOO_MANY_ROWS:
        return prompts.notice(
            "result_not_kept_rows",
            n_rows=f"{outcome.detail:,}",
            cap=f"{results.KEEP_ROWS:,}",
        )
    if outcome.reason == results.TOO_MANY_VALUES:
        return prompts.notice(
            "result_not_kept_values",
            n_values=f"{outcome.detail:,}",
            cap=f"{results.KEEP_CELLS:,}",
        )
    if outcome.reason == results.TOO_LARGE:
        return prompts.notice(
            "result_not_kept_size",
            size=present.size(outcome.detail),
            cap=present.size(results.KEEP_BYTES),
        )
    if outcome.reason == results.NOT_WRITTEN:
        return prompts.notice("result_not_kept_error", error=outcome.detail or "")
    return prompts.notice("result_not_kept_no_chat")


def _page(
    table: Table,
    columns: list[str],
    *,
    n_rows: int,
    start: int,
    want: int,
    first_column: int,
    budget: int,
    more_rows: Callable[[str, int], str],
    more_columns: Callable[[str, int], str],
) -> dict:
    """One window of a result as an answer shows it: ``fields`` and whole rows, every cut said.

    Shared by `query_data` and `review_queries`' answers in full, so a result
    reads the same through either door. Columns are the
    :data:`QUERY_COLUMNS` from ``first_column`` (``columns_shown`` and
    ``more_columns`` when that is not all of them); rows are taken whole, from
    ``start``, while they fit in ``budget`` characters, and never fewer than
    one. A value longer than :data:`QUERY_CELL_CHARS` is cut, and ``cut`` names
    the columns it happened in, for the caller to put first.

    The rows are read once, with a ``LIMIT`` no larger than what could fit,
    so a big ``limit`` on a wide result reads what it can send and no more.
    """
    if columns and first_column >= len(columns):
        raise ValueError(
            prompts.error(
                "column_offset_past_end",
                column_offset=first_column,
                n_columns=f"{len(columns):,}",
            )
        )
    shown = columns[first_column : first_column + QUERY_COLUMNS]
    out: dict[str, Any] = {}
    if len(shown) < len(columns):
        out["columns_shown"] = f"{first_column + 1}-{first_column + len(shown)}"
        if first_column + len(shown) < len(columns):
            out["more_columns"] = more_columns(out["columns_shown"], first_column + len(shown))
    start = min(start, n_rows)
    # The most rows that could fit: every value at least one character and a
    # separator, and each row its brackets.
    fits = max(1, budget // (2 * max(1, len(shown)) + 2))
    fetched = _window(table, columns, first_column, len(shown), start, min(want, fits))
    rows: list[list[Any]] = []
    cut: dict[str, int] = {}
    used = 0
    for raw in fetched:
        row = []
        for column, value in zip(shown, raw, strict=True):
            cell = to_jsonable_nested(value)
            # A number, a boolean or a null is short; text and a nested value
            # (a list column) are what can run long.
            if isinstance(cell, str):
                text = cell
            elif isinstance(cell, list | dict):
                text = to_json_compact(cell)
            else:
                text = ""
            if len(text) > QUERY_CELL_CHARS:
                cell = text[:QUERY_CELL_CHARS] + CUT_MARK
                cut[column] = cut.get(column, 0) + 1
            row.append(cell)
        size = len(to_json_compact(row)) + 1
        if rows and used + size > budget:
            break
        rows.append(row)
        used += size
    if cut:
        out = {
            "cut": prompts.notice(
                "values_cut",
                n=present.count(sum(cut.values()), "value"),
                chars=QUERY_CELL_CHARS,
                next=QUERY_CELL_CHARS + 1,
                columns=", ".join(cut),
            ),
            **out,
        }
    if rows or start < n_rows:
        more = more_rows(
            f"{start + 1}-{start + len(rows)}" if rows else listing.NONE_SHOWN, start + len(rows)
        )
    else:
        more = prompts.more("rows_past_end", n_rows=f"{n_rows:,}")
    listed = listing.listing(shown, rows, n=n_rows, start=start, more=more)
    out.update({key: value for key, value in listed.items() if key != "n"})
    return out


def _window(
    table: Table, columns: list[str], first: int, width: int, start: int, n: int
) -> list[tuple]:
    """``n`` rows of ``table`` from ``start``: ``width`` columns of each, from column ``first``.

    Named in the ``SELECT`` when every name is distinct, so a wide result reads
    only the columns it sends; when two share a name (``SELECT a.id, b.id``)
    the whole row is read and the columns taken by position, because a name
    would pick the first of the two twice.
    """
    if n <= 0 or width <= 0:
        return []
    shown = columns[first : first + width]
    distinct = len(set(columns)) == len(columns)
    select = ", ".join(table.dialect.quote(c) for c in shown) if distinct else "*"
    window = Table(
        table.name,
        f"SELECT {select} FROM {subquery(table.query)} LIMIT {int(n)} OFFSET {int(start)}",
        table.con,
    )
    # Both exits from a `Table` are capped (`core/table.py`); `rows` is the one
    # for evidence rather than for a screen, so DuckDB's own types narrow through
    # `core.serialize` instead of widening through pandas first.
    got = window.rows(n)
    return got if distinct else [tuple(row[first : first + width]) for row in got]


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
    # **Kept, as a `query_data` result is** *(2026-10-13)*: the same door, so the
    # same rule, and the numbers behind a chart are then one `query_data` on its
    # handle away instead of a second run of its query, which on a warehouse is
    # a second bill. A chart over the cap is drawn all the same (§2.7); only
    # the receipt says it was not kept.
    outcome = results.keep(
        produced,
        log=results.log_for(portia_dir),
        tool=findings.PLOT_TOOL,
        question=asked,
        sql=sql,
        inputs=list(inputs),
        n_rows=n_rows,
        columns=columns,
    )
    kept = outcome if isinstance(outcome, results.Kept) else None
    # Read from the kept copy when there is one, so a warehouse is asked once.
    source = results.table(kept) if kept is not None else produced
    # Uncapped on purpose (§2.7). These do not become tokens, so the one measured
    # limit in the system does not apply and there is no second one to invent.
    rows = [
        {col: to_jsonable_nested(value) for col, value in zip(columns, row, strict=True)}
        for row in source.rows(n_rows)
    ]
    chart: dict[str, Any] = {
        "tab": named,
        "question": asked,
        "sql": sql,
        "inputs": list(inputs),
        "vega": vega,
        "columns": columns,
        "n_rows": n_rows,
        "rows": rows,
        "result": kept.handle if kept is not None else None,
    }
    if isinstance(outcome, results.NotKept):
        chart["not_kept"] = _not_kept(outcome)
    return chart


def view_chart(tab: str) -> dict:
    """The chart on ``tab`` as the window painted it. Runs nothing, writes nothing.

    **The one tool whose answer is a surface's and not the engine's**
    (`docs/VISUALIZATION.md` §12). `plot_data` hands the model a receipt and the
    rows to the browser, so the copilot has always narrated charts it could not
    see: it claimed a palette it had not applied (§2.9's session), and a chart
    with every axis label overprinted reads as ``drawn`` exactly like a clean
    one. The browser already made this picture, for its own refresh; this hands
    it over.

    **Pulled, never attached to a receipt.** A picture costs about its width
    times its height over 750 tokens, a reply may draw nine charts, and most of
    them need no second look.

    **The picture is for the drawing.** Whether it reads, whether the palette
    took, what to say to the user about its shape. It is never where a number
    comes from, and nothing seen in it may enter a step, a finding, a note or an
    interpretation: those rest on measurements, and a bar's height read off
    pixels is one the agent authored. That rule is prose
    (`prompts/tools/view_chart.md`) because no code can hold it, which is why the
    measured rows ride back beside the image: the right numbers are the nearest.

    Three refusals, each a different fact. The renderer refused the spec; no
    window is open to paint anything; or a window is open and has not painted
    this tab, which a closed tab and a name nobody drew look alike for.
    """
    named = (tab or "").strip()
    if not named:
        raise ValueError(prompts.error("view_needs_a_tab"))
    found = drawn.picture(named)
    if found is None:
        if (message := drawn.broken(named)) is not None:
            raise ValueError(prompts.error("chart_did_not_render", tab=named, message=message))
        if not drawn.listening():
            raise ValueError(prompts.error("chart_has_no_window", tab=named))
        raise ValueError(prompts.error("chart_not_on_screen", tab=named))
    return {
        "viewed": named,
        "width": found.width,
        "height": found.height,
        "image": found.image,
        "chart": found.chart,
    }


#: How many characters `review_queries`' list may take, encoded. Past it the
#: list holds the latest questions that fit and says how many earlier ones it
#: left out. **A placeholder** *(2026-10-13)*: an entry is a few hundred
#: characters, so this is some fifty questions, where fourteen used to be
#: refused because every answer was resent whole.
REVIEW_CHARS = 20_000

#: The longest one-row answer the list shows inline, as ``{column: value}``. A
#: count or a rate is a few dozen characters; a row of eighty columns is not an
#: answer to glance at, and ``queries=[n]`` returns it.
REVIEW_ANSWER_CHARS = 500

#: The longest first line of an error the list shows; ``queries=[n]`` has the rest.
FAILED_CHARS = 200

#: What one question is in the list, in order (`core/listing.listing`).
REVIEW_FIELDS = (
    "n",
    "question",
    "chart",
    "result",
    "n_rows",
    "n_columns",
    "answer",
    "failed",
    "kept",
)

#: Keys a logged `query_data` answer carried before 2026-10-13 that its
#: caller already holds: the call's own arguments, sent back.
_ECHOED = ("question", "sql", "inputs")


def review_queries(
    chat: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
    queries: list[int] | None = None,
) -> dict:
    """Every question you asked this chat with `query_data`, numbered, with what it found.

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

    **One line per question, not every answer again** *(2026-10-13)*. A chat
    of fourteen questions was refused at 30,741 characters because this sent
    each logged answer back whole. Now each question is its number, its
    sentence, the handle of the result the chat kept (`portia/results.py`),
    its size, the answer itself only when it is one short row, and the first
    line of its error if it failed. ``queries`` names the ones to read in full:
    from the kept copy when there is one, else from the log.
    """
    root = _project_root(portia_dir)
    reviewed = findings.review(portia_dir, chat=chat, root=root)
    name = reviewed[0]["chat"] if reviewed else None
    here = {k.handle: k for k in results.kept_in(reviewed[0]["log"])} if reviewed else {}
    if queries:
        return {"chat": name, **_in_full(reviewed, [int(n) for n in queries], here)}
    entries = [_review_row(q, here.get(q.get("stored") or "")) for q in reviewed]
    # The latest that fit, because the newest questions are the ones not yet
    # judged; the earlier ones are counted and reachable by number.
    shown: list[list[Any]] = []
    used = 0
    for entry in reversed(entries):
        size = len(to_json_compact(entry)) + 1
        if shown and used + size > REVIEW_CHARS:
            break
        shown.append(entry)
        used += size
    shown.reverse()
    start = len(entries) - len(shown)
    return {
        "chat": name,
        "n_new": sum(1 for q in reviewed if not q["kept"]),
        "queries": listing.listing(
            REVIEW_FIELDS,
            shown,
            n=len(entries),
            start=start,
            more=prompts.more("review_queries", left=start),
        ),
    }


def _review_row(query: dict, kept: results.Kept | None) -> list[Any]:
    """One question as the list shows it, in `REVIEW_FIELDS` order."""
    payload = _parsed(query.get("result", ""))
    n_rows: int | None = None
    n_columns: int | None = None
    if kept is not None:
        n_rows, n_columns = kept.n_rows, kept.n_columns
    else:
        # Off the logged answer: `n_columns` since 2026-10-13, the column list
        # before it, and a chart's receipt has the list too.
        if isinstance(payload.get("n_rows"), int):
            n_rows = payload["n_rows"]
        if isinstance(payload.get("n_columns"), int):
            n_columns = payload["n_columns"]
        elif isinstance(payload.get("columns"), list):
            n_columns = len(payload["columns"])
    failed = None
    if query.get("failed"):
        first = str(query.get("result") or "").strip().splitlines()
        line = first[0] if first else ""
        failed = line if len(line) <= FAILED_CHARS else line[:FAILED_CHARS] + CUT_MARK
    return [
        query["n"],
        query.get("question", ""),
        query.get("tab") or None,
        kept.handle if kept is not None else None,
        n_rows,
        n_columns,
        None if failed else _one_row(query, payload, kept),
        failed,
        bool(query.get("kept")),
    ]


def _parsed(text: str) -> dict:
    """A logged answer as the dict it was, or empty when it was not one (an error, prose)."""
    import json

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _one_row(query: dict, payload: dict, kept: results.Kept | None) -> dict | None:
    """The answer as ``{column: value}`` when it is one row short enough to show, else ``None``.

    Read off the logged answer when that holds the whole row, which costs
    nothing; from the kept copy when the answer showed only part of it (its
    columns cut, or a value cut short) or was a chart's receipt. The two say
    the same: both are the query's row through `core.serialize`.
    """
    if payload.get("n_rows") != 1 and (kept is None or kept.n_rows != 1):
        return None
    row: dict | None = None
    fields, rows = payload.get("fields"), payload.get("rows")
    whole = "columns_shown" not in payload and "cut" not in payload
    if query.get("tool") == findings.QUERY_TOOL and isinstance(rows, list) and len(rows) == 1:
        if isinstance(fields, list) and isinstance(rows[0], list) and whole:
            row = dict(zip(fields, rows[0], strict=False))
        elif isinstance(rows[0], dict):
            row = rows[0]  # a log written before answers were lists
    if row is None and kept is not None and kept.n_rows == 1:
        values = results.table(kept).rows(1)[0]
        row = {c: to_jsonable_nested(v) for c, v in zip(kept.columns, values, strict=True)}
    if row is None or len(to_json_compact(row)) > REVIEW_ANSWER_CHARS:
        return None
    return row


def _in_full(reviewed: list[dict], numbers: list[int], here: dict[str, results.Kept]) -> dict:
    """The answers ``numbers`` name, each as far as it fits beside the others.

    From the kept copy when there is one, paged as `query_data` pages it;
    else the logged answer without the arguments it echoed. They share
    :data:`REVIEW_CHARS` equally, and an answer that does not fit beside
    those before it is left out and named, to ask for on its own.
    """
    by_n = {q["n"]: q for q in reviewed}
    asked = list(dict.fromkeys(numbers))
    _unknown_queries(asked, by_n)
    share = REVIEW_CHARS // len(asked)
    answers: list[dict] = []
    left: list[int] = []
    used = 0
    for n in asked:
        query = by_n[n]
        answer = _answer(query, here.get(query.get("stored") or ""), share)
        size = len(to_json_compact(answer))
        if answers and used + size > REVIEW_CHARS:
            left.append(n)
            continue
        answers.append(answer)
        used += size
    out: dict[str, Any] = {"answers": answers}
    if left:
        out["more"] = prompts.more(
            "review_answers", left=", ".join(f"#{n}" for n in left), first=left[0]
        )
    return out


def _answer(query: dict, kept: results.Kept | None, budget: int) -> dict:
    """One question's answer in full, within ``budget`` characters of rows."""
    head: dict[str, Any] = {"n": query["n"], "question": query.get("question", "")}
    if query.get("tab"):
        head["chart"] = query["tab"]
    text = str(query.get("result") or "")
    if query.get("failed"):
        return {**head, "failed": _within(text, budget)}
    if kept is None:
        return {**head, "result": None, "logged": _logged(text, budget)}
    handle = kept.handle

    def more_rows(shown: str, next_offset: int) -> str:
        return prompts.more(
            "answer_rows",
            shown=shown,
            n_rows=f"{kept.n_rows:,}",
            next_offset=next_offset,
            result=handle,
        )

    def more_columns(shown: str, next_column: int) -> str:
        return prompts.more(
            "answer_columns",
            shown=shown,
            n_columns=f"{kept.n_columns:,}",
            next_column=next_column,
            result=handle,
        )

    page = _page(
        results.table(kept),
        kept.columns,
        n_rows=kept.n_rows,
        start=0,
        want=kept.n_rows,
        first_column=0,
        budget=budget,
        more_rows=more_rows,
        more_columns=more_columns,
    )
    cut = {"cut": page.pop("cut")} if "cut" in page else {}
    return {
        **head,
        **cut,
        "result": handle,
        "n_rows": kept.n_rows,
        "n_columns": kept.n_columns,
        **page,
    }


def _logged(text: str, budget: int) -> Any:
    """A logged answer as it came back, minus what it echoed, cut to ``budget`` and saying so."""
    payload = _parsed(text)
    if not payload:
        return _within(text, budget)
    payload = {k: v for k, v in payload.items() if k not in _ECHOED}
    if len(to_json_compact(payload)) <= budget:
        return payload
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return _within(to_json_compact(payload), budget)
    base = len(to_json_compact({**payload, "rows": []})) + CUT_NOTICE_CHARS
    taken: list[Any] = []
    for row in rows:
        base += len(to_json_compact(row)) + 1
        if taken and base > budget:
            break
        taken.append(row)
    notice = prompts.notice("logged_rows_cut", shown=len(taken), logged=len(rows))
    return {"cut": notice, **payload, "rows": taken}


#: Room left for the notice a cut logged answer carries, so it still fits.
CUT_NOTICE_CHARS = 300


def _within(text: str, budget: int) -> str:
    """``text`` whole when it fits ``budget``, else its start and a sentence saying how much was left."""
    if len(text) <= budget:
        return text
    notice = prompts.notice("logged_text_cut", shown=f"{budget:,}", size=f"{len(text):,}")
    return f"{notice}\n\n{text[: max(0, budget - len(notice) - 2)]}{CUT_MARK}"


def _unknown_queries(numbers: list[int], reviewed: dict[int, dict]) -> None:
    """Refuse numbers that are not questions in this chat, naming the ones that are."""
    missing = [n for n in numbers if n not in reviewed]
    if missing:
        available = f"#1-#{max(reviewed)}" if reviewed else "(none)"
        raise ValueError(
            prompts.error(
                "no_such_query",
                missing=", ".join(f"#{n}" for n in missing),
                available=available,
            )
        )


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
    queries = [_with_kept_rows(q) for q in _cited(from_queries, reviewed)]

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
        "finding": relative(path, root),
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
                    f"describe_source lists its {len(names)} columns, "
                    "and finds one by part of its name with match."
                )
        checked.append(f"{table}.{column}" if column else table)
    return checked


def _cited(numbers: list[int], reviewed: dict[int, dict]) -> list[dict]:
    """The queries this finding came from, by their `review_queries` number."""
    if not numbers:
        raise ValueError(prompts.error("finding_needs_a_query"))
    _unknown_queries(numbers, reviewed)
    return [reviewed[n] for n in numbers]


#: The most rows of a kept result a finding copies, and the most values: a
#: finding is read in a diff, so a wide result keeps fewer rows, never fewer
#: columns, and every row it keeps is whole. **Placeholders** *(2026-10-13)*.
FINDING_ROWS = 50
FINDING_CELLS = 5_000


def _with_kept_rows(query: dict) -> dict:
    """``query`` with its result copied from the kept copy, when the chat kept one.

    **The rows a finding rests on, exactly**, rather than the answer the
    agent happened to be shown: the first :data:`FINDING_ROWS` of the kept
    result, every column, each value as `core.serialize` gives it, and a
    sentence saying how many more the kept copy holds and under which handle.
    Copied, not referenced: the finding is committed and the chat's folder is
    not, and the finding must keep its numbers when the chat is deleted.

    A chart keeps its receipt, as it always has (`docs/VISUALIZATION.md` §6):
    its rows went to the browser, and the query that produced them is the
    evidence. The handle rides with it either way, so a later chat can read
    the whole result while the chat that kept it exists. A query whose result
    was not kept copies its logged answer, as before.
    """
    kept = results.find(query.get("log"), query.get("stored") or "")
    if kept is None or query.get("tool") != findings.QUERY_TOOL:
        return query
    take = min(kept.n_rows, FINDING_ROWS, max(1, FINDING_CELLS // max(1, kept.n_columns)))
    rows = results.table(kept).rows(take) if take else []
    listed = listing.listing(
        kept.columns,
        rows,
        n=kept.n_rows,
        more=prompts.more(
            "finding_rows",
            kept=len(rows),
            n_rows=f"{kept.n_rows:,}",
            result=kept.handle,
            chat=query.get("chat") or "",
        ),
    )
    body = {"n_rows": kept.n_rows, "n_columns": kept.n_columns}
    body.update({key: value for key, value in listed.items() if key != "n"})
    return {**query, "result": to_json_compact(body)}


def _kept_result(name: str, request: reserved.Request) -> Table:
    """A kept result, read by SQL under its handle (`portia/results.py`).

    **This chat's first; another chat's only through a finding that cites
    it** (the user's rule, 2026-10-13). There is no pool of every query
    across chats: a handle this chat did not keep is looked for among the
    findings' cited results, and refused when no finding cites it, when two
    chats' findings cite the same letters, or when the chat that kept it has
    been deleted, which takes its results with it.
    """
    handle = name.lower()
    kept = results.find(results.log_for(request.portia_dir), handle)
    if kept is not None:
        return results.table(kept)
    chats = sorted(
        {
            str(q["chat"])
            for finding in findings.load_all(_project_root(request.portia_dir))
            for q in finding.get("queries") or []
            if q.get("stored") == handle and q.get("chat")
        }
    )
    if not chats:
        raise ValueError(prompts.error("no_such_result", result=name))
    if len(chats) > 1:
        raise ValueError(prompts.error("result_in_two_chats", result=name, chats=", ".join(chats)))
    kept = results.find(results.log_for(request.portia_dir, chats[0]), handle)
    if kept is None:
        raise ValueError(prompts.error("result_chat_gone", result=name, chat=chats[0]))
    return results.table(kept)


reserved.register(results.HANDLE, _kept_result)


def measure_overlaps(pairs: list[dict], portia_dir: str = catalog.DEFAULT_DIR) -> dict:
    """Whether column pairs share values: from the graph where it knows, else measured and kept.

    **The pairs are yours to choose** (`KNOWLEDGE_GRAPH.md` §5.1). Nothing in code
    picks them: which relationships are worth a query is a judgment from meaning,
    and a filter comparing min/max would drop `country_name` against
    `country_code` with total confidence and be wrong about the most important
    pair in the project.

    Each pair carries ``reason`` — why you think these two are worth comparing —
    and it is **required**. It is stored beside the numbers and it is what stops
    a zero from reading as a dead end a year later (§4.4). Every pair is checked
    before any is measured, so a refusal costs no query.

    **The graph first** *(2026-10-13)*. A pair the graph already holds, measured
    against both tables as they are now (`measure.still_current`), is answered
    from it and marked so, with when it was measured; only the rest are
    measured, and only those are written. "The next session starts with them"
    was the promise, and until this nothing read them back before measuring
    again. A pair held but taken against a table that has changed since, or
    whose fingerprints cannot be compared, is measured afresh: the old number is
    not current, and re-measuring replaces it.

    **The answer is a receipt** (`graphcards.overlaps_receipt`): a line a pair,
    cut and said so past a fixed count, because the call has already written
    and a refusal for size would read as a failure about something that
    happened.

    The structural half of the graph is refreshed before writing, because a
    measurement can only attach to columns the graph knows about, and
    refreshing is free.
    """
    con = connect()
    root = _project_root(portia_dir)
    graph = knowledge.build_graph(root, portia_dir=portia_dir).graph
    fingerprints = _table_fingerprints(graph)
    columns = _graph_columns(graph)
    resolved = [_pair(raw, graph, columns) for raw in pairs]
    held, unreachable = _held_overlaps(resolved, graph.project)

    rows, edges = [], []
    for i, pair in enumerate(resolved):
        left_fp, right_fp = fingerprints.get(pair.spec.left), fingerprints.get(pair.spec.right)
        seen = held.get(i)
        if seen is not None and measure.still_current(seen, left_fp, right_fp):
            rows.append(_receipt_row(pair, seen, graphcards.FROM_GRAPH))
            continue
        overlap = column_overlap(
            _table(pair.left_table, portia_dir, con),
            pair.left_column,
            _table(pair.right_table, portia_dir, con),
            pair.right_column,
        )
        edge = measure.overlap_edge(
            pair.spec, overlap, left_fingerprint=left_fp, right_fingerprint=right_fp
        )
        edges.append(edge)
        measured = {
            **overlap,
            "left_distinct": overlap["left"]["n_distinct"],
            "right_distinct": overlap["right"]["n_distinct"],
            "measured_at": edge.properties["measured_at"],
        }
        rows.append(_receipt_row(pair, measured, graphcards.FROM_QUERY))

    kept: dict = {}
    if edges and unreachable is not None:
        # The read already found the graph down: a second connection would only
        # say so again, slower.
        kept = {"stored": False, "not_stored_because": unreachable}
    elif edges:
        kept = _store_overlaps(graph, edges)
    return graphcards.overlaps_receipt(
        rows, n_from_graph=len(rows) - len(edges), n_measured=len(edges), kept=kept
    )


def _held_overlaps(pairs: list[_ResolvedPair], project: str) -> tuple[dict[int, dict], str | None]:
    """What the graph already holds for these pairs, by position, or why it could not say.

    Only an unreachable graph is answered with nothing and its reason; the
    measuring then goes ahead and its results say they were not kept. Any
    other failure of the read is a bug in the read and is not swallowed.
    """
    keys = [
        (
            pair.spec.left.column(pair.spec.left_column).key,
            pair.spec.right.column(pair.spec.right_column).key,
        )
        for pair in pairs
    ]
    try:
        with store.session() as live:
            return query.stored_overlaps(live, keys, project=project), None
    except store.GraphUnavailable as exc:
        return {}, str(exc)


def _receipt_row(pair: _ResolvedPair, numbers: dict, answered_from: str) -> dict:
    """One line of `measure_overlaps`' receipt, its fields `graphcards.RECEIPT_FIELDS`."""
    return {
        "left": f"{pair.left_table}.{pair.left_column}",
        "right": f"{pair.right_table}.{pair.right_column}",
        **{
            key: numbers.get(key)
            for key in (*measure.MEASURED, "left_distinct", "right_distinct", "measured_at")
        },
        "from": answered_from,
    }


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
    except cancel.Cancelled:
        raise
    except store.GraphUnavailable as exc:
        return {"stored": False, "not_stored_because": str(exc)}
    except Exception as exc:  # noqa: BLE001 - measured numbers must not be lost to a failed write
        # A database that dropped mid-write, rather than one never reached
        # (2026-10-13): the numbers are measured either way, and the answer is
        # a receipt for them, so a failure to keep them is said, never raised.
        return {"stored": False, "not_stored_because": f"{type(exc).__name__}: {exc}"}
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


def _pair(raw: dict, graph, columns: dict) -> _ResolvedPair:
    """One requested pair, validated. A missing reason is refused, not defaulted.

    Each column is named as the graph spells it, when only the case differs
    (`dialect.resolve_columns`), so the measurement attaches to the column it
    was taken on. A name the graph does not hold stays as typed, and the
    measuring says whether the table has it.
    """
    missing = [f for f in ("left", "left_column", "right", "right_column") if not raw.get(f)]
    if missing:
        raise ValueError(f"pair needs {', '.join(missing)}")
    reason = (raw.get("reason") or "").strip()
    if not reason:
        raise ValueError(prompts.error("overlap_needs_a_reason"))

    left, right = _graph_ref(raw["left"], graph), _graph_ref(raw["right"], graph)
    (left_column,) = dialects.resolve_columns([raw["left_column"]], columns.get(left, []))
    (right_column,) = dialects.resolve_columns([raw["right_column"]], columns.get(right, []))
    return _ResolvedPair(
        measure.Pair(left, left_column, right, right_column, reason),
        raw["left"],
        raw["right"],
        left_column,
        right_column,
    )


def _graph_columns(graph) -> dict:
    """Each table's column names in the graph, by the table's ref."""
    tables = {
        f"{node.label.lower()}:{node.key}": node.ref
        for node in graph.nodes.values()
        if node.label in (knowledge_schema.SOURCE, knowledge_schema.MODEL)
    }
    out: dict = {}
    for node in graph.nodes.values():
        if node.label != knowledge_schema.COLUMN:
            continue
        # A column's key is its table's, then ``::`` and its name
        # (`schema.column_key`); cut by the name's length, which a name holding
        # ``::`` cannot fool.
        name = str(node.properties.get("name"))
        table = tables.get(node.key[: len(node.key) - len(name) - len("::")])
        if table is not None:
            out.setdefault(table, []).append(name)
    return out


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
    match: str | None = None,
    type_: str | None = None,
    flag: str | None = None,
) -> dict:
    """L3 — the measured facts about a table's columns: what indexing stored, filtered and capped.

    Per column, its type, null rate, distinct count, min, quartiles, max, mean
    and standard deviation, its commonest value and how often it occurs, three
    sample values and its flags (`PROFILE_FIELDS`), `PROFILE_CAP` columns at a time. The filters are
    `describe_source`'s: ``columns`` (exact names, in the order named),
    ``match``, ``type_`` and ``flag``. A table no wider than `PROFILE_CAP`
    comes back whole without one; a wider one, asked without a filter, comes
    back as its card's counts and flagged names, with ``more`` saying how to
    ask for the columns. Never a dump, and never a refusal for size.

    **Stored facts, not a fresh profile** *(2026-10-13, reversing 2026-07)*. This
    profiled the table again on every call, on the argument that the catalog
    trims what it keeps and the agent needs the whole picture, quartiles and
    sample values included, or it derives the numbers it was not given. The
    derivation that argument rested on was a min-max range made up from a
    median and a standard deviation; min and max have been stored since, and
    a scan per call is what the copilot paid for a fact indexing had already
    measured: on a warehouse every `profile_source` was a full scan on
    someone's meter. So the answer is the catalog's slice (`catalog._SHAPE`),
    which keeps the mean, the quartiles and the sample values too
    (`catalog.LATER_FACTS`): they were the reason for the old argument, and
    storing them answers it. An entry indexed before they were stored says so,
    as ``not_stored`` and a note, rather than measuring again or leaving a null
    to be read as *no mean*. ``facts`` says which kind of answer this is: ``stored``, or ``measured now`` where nothing was
    stored to read: a ``<spec>#<step id>``, a spec nobody has recorded a step
    through, a warehouse table or model nobody has profiled. The warehouse's
    are written back (`catalog.profile_remote`), so the next call reads them.

    ``source`` may also name a table an earlier step produced
    (``<spec>#<step id>``). There is no catalog entry for one, so it comes back
    with measurements only, which is the whole point of asking: what do this
    table's columns look like *now*, after the step ran.

    ``approximate`` names the fields that are an engine's estimate, as
    `row_facts` does for a row count: BigQuery's median is a sketch.

    The summary and the notes are the card's (`describe_source`), not this
    rung's. This rung is for numbers.
    """
    name, cols, rows, facts = _measured(source, portia_dir)
    head = {"source": name, "facts": facts, **rows, "n_columns": len(cols)}
    if _filtering(columns, match, type_, flag):
        picked = _matching(cols, name, columns=columns, match=match, type_=type_, flag=flag)
    elif len(cols) > PROFILE_CAP:
        more = prompts.more("profile_wide", n=len(cols), cap=PROFILE_CAP, source=name)
        return {**head, **_overview(cols, name, measured=True, more=more)}
    else:
        picked = cols
    shown = picked[:PROFILE_CAP]
    answer = {
        **head,
        "columns": listing.listing(
            PROFILE_FIELDS,
            [_row(col, PROFILE_FIELDS) for col in shown],
            n=len(picked),
            more=prompts.more("profiled_columns", left=len(picked) - len(shown), cap=PROFILE_CAP),
        ),
    }
    estimated = [f for f in PROFILE_FIELDS if any(f in (c.get("approximate") or []) for c in shown)]
    if estimated:
        # Beside a row count the warehouse estimated, when `row_facts` said so.
        answer["approximate"] = [*answer.get("approximate", []), *estimated]
    if any("inferred" in col and "samples" not in col for col in shown):
        # An entry indexed before these were stored: their nulls are not the
        # null of a text column's mean, and the copilot must not fill them in.
        answer["not_stored"] = list(catalog.LATER_FACTS)
        answer["not_stored_note"] = prompts.notice("facts_not_stored")
    return answer


def _measured(source: str, portia_dir: str) -> tuple[str, list[dict], dict, str]:
    """``(name, columns, row facts, facts)``: a table's stored facts, or a profile run now.

    Measured now only where nothing is stored (see `profile_source`). A
    warehouse table or model profiled here has its facts written back by
    `catalog.profile_remote`, and the entry read again for the roles kept on
    it; a spec run locally writes nothing, as it never did.
    """
    if STEP_REF in source:
        profile = profiling.profile(_step_table(source, portia_dir))
        return source, profile["columns"], {"n_rows": profile["n_rows"]}, MEASURED_NOW
    name = _known_name(source, portia_dir)
    try:
        entry = _entry(name, portia_dir)
    except ValueError:
        # A spec nobody has recorded a step on or built has no entry yet, and
        # is still a table this project can produce.
        if name not in spec.discover_specs(_project_root(portia_dir)):
            raise
        entry = {}
    if entry and catalog.is_profiled(entry):
        return name, list(entry.get("columns") or []), row_facts(entry), STORED
    if "source" in entry:
        # The opt-in scan (`docs/CONNECTOR.md` §2.6): a scoped table was
        # indexed as metadata, and this is the moment somebody asked for the
        # numbers. The facts are written back, so the second ask is free.
        profile = catalog.profile_remote(name, connect(), portia_dir=portia_dir)
    else:
        # A model. **On a warehouse, the written table, and the facts are
        # written back** (2026-09-07): the scan used to run and leave the
        # entry at ``profiled: null``, so every audit paid for it again and
        # the window never showed a number for a table portia built. Locally,
        # a spec with no entry is run, which is what a model costs.
        profile = _profile_model(name, portia_dir)
    return name, profile["columns"], {"n_rows": profile["n_rows"]}, MEASURED_NOW


def _requested(profiled: list[dict], columns: list[str] | None, source: str) -> list[dict]:
    """The columns the caller asked for, in the order they asked for them.

    Refuses on a name that is not there rather than returning what matched. A
    profile is what the agent reasons from, and a silently short one is the
    failure this whole stream exists to stop — the AQN build run shipped its
    deliverable without ever obtaining its main source's numbers, and nothing in
    the transcript reads as though the copilot knew it was missing them.

    The near misses are named instead of the column list. Listing all of them is
    what `catalog.py` does and it is right there on a narrow table; on the 191
    column source it makes the error 5,000 characters, which is the payload
    problem wearing a different hat. ``match`` is the way to a name known in
    part, and the refusal says so.
    """
    if columns is None:
        return profiled

    by_name = {c["name"]: c for c in profiled}
    columns = dialects.resolve_columns(columns, by_name)
    missing = [c for c in columns if c not in by_name]
    if missing:
        close = get_close_matches(missing[0], by_name, n=3)
        raise ValueError(
            prompts.error(
                "unknown_column",
                column=missing[0],
                source=source,
                missing=len(missing),
                asked=len(columns),
                closest=", ".join(close) or "(none close)",
            )
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
    reason: str,
    left_columns: list[str] | None = None,
    right_columns: list[str] | None = None,
) -> dict:
    """What joining two sources on these keys would actually do — plus the rows.

    Returns key-level facts (overlap, coverage, relationship, fan-out, and the
    row counts each `how` would produce), the nulls each `how` would leave in the
    carried columns, *and* example rows: the unmatched ones, the null-key ones,
    the keys repeated most. Nothing here is ranked or scored.
    Whether a dropped row is a catastrophe or a non-event depends on the goal,
    which only you have.

    An example row carries its key columns and a few more; name
    ``left_columns``/``right_columns`` to choose the rest. The two sides are named
    separately because they are two schemas, exactly as ``left_on``/``right_on``
    already are.

    **Its key measurement is kept in the graph** *(2026-10-13)*, as an
    `OVERLAPS` edge with ``origin: join_check``, beside ``reason``, which is
    required for the reason `measure_overlaps`' is: a zero without the sentence
    that asked for it reads as a dead end. The logs held 25 join checks and 5
    measurements, and every join check's numbers died with the turn. What was
    and was not kept is the answer's ``graph`` (:func:`_keep_join`), and
    nothing about the graph can fail the join's own answer.

    Call this **before** deciding anything about a merge.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError(prompts.error("join_needs_a_reason"))
    con = connect()
    found = _join_findings(
        _table(left, portia_dir, con),
        _table(right, portia_dir, con),
        on=keys,
        left_on=left_on,
        right_on=right_on,
        left_columns=left_columns,
        right_columns=right_columns,
    )
    return {**found, "graph": _keep_join(left, right, found["report"], reason, portia_dir)}


def _keep_join(left: str, right: str, report: dict, reason: str, portia_dir: str) -> dict:
    """Put a join check's key measurement in the graph, best-effort, and say what happened.

    ``{"kept": True}``, or ``{"kept": False, "why": <sentence>}``. Kept only
    when the join is on **one column a side between two tables the graph
    holds**, sources or models, because that is what an `OVERLAPS` edge is:

    - a composite key is several columns matched together, and no pair of
      single columns measures it;
    - a ``<spec>#<step>`` side is a table no build has made, so it has no node,
      and its key may be one the step transformed (`join_findings` names
      columns, so a transformed key only ever arrives this way);
    - a table against itself is not a link between tables.

    **Connected before the graph is built.** Building reads the whole catalog
    and every spec, and on a machine with no database, which is the ordinary
    case, that read would be paid on every join check for nothing. Any failure
    past that point is reported in the answer, never raised: the join was
    measured, and a database that is down or a column the graph lacks costs
    only the keeping (§6.6).
    """
    lkeys, rkeys = report["keys"]["left"], report["keys"]["right"]
    if len(lkeys) != 1 or len(rkeys) != 1:
        return _graph_not_kept(prompts.notice("join_not_kept_composite"))
    if STEP_REF in left or STEP_REF in right:
        return _graph_not_kept(prompts.notice("join_not_kept_step"))
    left, right = _known_name(left, portia_dir), _known_name(right, portia_dir)
    if left == right:
        return _graph_not_kept(prompts.notice("join_not_kept_same_table"))
    try:
        with store.session() as live:
            graph = knowledge.build_graph(_project_root(portia_dir), portia_dir=portia_dir).graph
            try:
                left_ref, right_ref = _graph_ref(left, graph), _graph_ref(right, graph)
            except ValueError:
                # A table with no node, which reads as its column missing.
                return _graph_not_kept(prompts.notice("join_not_kept_unattached"))
            fingerprints = _table_fingerprints(graph)
            edge = measure.overlap_edge(
                measure.Pair(left_ref, lkeys[0], right_ref, rkeys[0], reason),
                measure.from_join_report(report),
                origin=measure.ORIGIN_JOIN_CHECK,
                left_fingerprint=fingerprints.get(left_ref),
                right_fingerprint=fingerprints.get(right_ref),
            )
            store.write(graph, live)
            written = store.write_measured([edge], live, graph.project)
    except cancel.Cancelled:
        raise
    except store.GraphUnavailable as exc:
        return _graph_not_kept(prompts.notice("join_not_kept_unavailable", reason=str(exc)))
    except Exception as exc:  # noqa: BLE001 - the join's answer must not fail because of the graph
        error = f"{type(exc).__name__}: {exc}"
        return _graph_not_kept(prompts.notice("join_not_kept_unavailable", reason=error))
    if not written:
        return _graph_not_kept(prompts.notice("join_not_kept_unattached"))
    return {"kept": True}


def _graph_not_kept(why: str) -> dict:
    return {"kept": False, "why": why}


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

    # **Past here a press on this call's card no longer stops it**
    # (`core/cancel.Scope.commit`, `docs/CONVERSATION.md` §16). Up to here an
    # interrupt leaves nothing written, which is what the copilot is told; from
    # here the spec is being written, and a step stopped halfway through its
    # own recording would be a spec nobody decided. A press that already landed
    # stops it here instead. The exchange's Stop still reaches it, as it did.
    cancel.commit()
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
            steps=spec.step_shapes(results),
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


# --- the spec cards (2026-10-13, the tool revamp) ----------------------------------
#
# `read_spec` and `record_step` answer with a card whose size does not grow
# with the spec or the table, as the source cards do (`describe_source`): every
# list is cut at a fixed length and says how to ask a narrower question, every
# flagged column is named by the flag rule (`core/listing.flagged`), and a
# table's columns are the business of the rungs that read tables. The forms
# they replaced sent every step verbatim and, on request, the built table's
# whole catalog entry, or the outcome report naming every column with a null
# rate; past `tools.RESULT_BUDGET` each was shortened to counts and ends.

#: Steps one spec card lists before it says how many more there are. **A
#: placeholder**: fifty rows of short ids and one or two inputs each are about
#: 4,000 characters, and a spec is the working-out of one table, so a longer
#: one is rare enough that ``match`` is the way to the rest.
SPEC_STEPS = 50

#: Names one list on a spec card gives: the sources it declares, the models it
#: reads, the models that read it. **A placeholder.**
SPEC_NAMES = 50

#: Findings a spec card lists: the latest this many, counted against all of
#: them, as `describe_source` does with notes. **A placeholder.**
SPEC_FINDINGS = 30

#: Queries one finding read lists, and the characters of each one's result it
#: shows. A finding copies its results verbatim from the log, and one written
#: before the revamp holds a whole query answer, up to `tools.RESULT_BUDGET`.
#: **Placeholders.**
FINDING_QUERIES = 10
FINDING_RESULT_CHARS = 2_000

#: What a spec card says about each step: what it is, and its shape as the last
#: run measured it (`spec.step_shapes`), so no field needs a run.
STEP_FIELDS = ("id", "op", "inputs", "n_rows", "n_cols", "flags", "drift")
SOURCE_FIELDS = ("name", "ref")
NAME_FIELDS = ("name",)
FINDING_FIELDS = ("path", "at", "question")
QUERY_FIELDS = ("n", "chat", "question", "sql", "result", "result_chars")

#: The fields of a step's kept shape a card gives beside the step, in this order.
_SHAPE = ("n_rows", "n_cols", "flags", "drift")


def spec_vocabulary() -> dict[str, str]:
    """The caps the spec tools' descriptions state, from the code (`card_vocabulary`'s reason)."""
    return {
        "spec_steps": str(SPEC_STEPS),
        "spec_findings": str(SPEC_FINDINGS),
        "finding_queries": str(FINDING_QUERIES),
        "finding_chars": f"{FINDING_RESULT_CHARS:,}",
    }


def read_spec(
    spec_ref: str,
    *,
    step: str | None = None,
    finding: str | None = None,
    match: str | None = None,
    measured: bool = False,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Open one spec as it is recorded, without running it: the spec card.

    **The decision record was the one durable artifact the agent wrote and
    could not read** *(2026-09-04, `docs/PIPELINE.md` §9)*. `run_spec`
    re-executes every model behind a spec to measure it and hands back
    provenance and drift; the `rationale`, the `expect`, the SQL as written and
    which step reads which never came back, and nothing told the agent which
    specs existed. Asked about one, a Haiku session said it needed the spec's
    name and could only run it.

    **A card, and one step at a time in full** *(2026-10-13, the tool revamp)*.
    This sent every step verbatim, which is the right way to read one step and
    made twenty-odd of them a payload. The card is what the spec is: its name,
    layer and target, the sources it declares, the models it reads and that
    read it, whether its compiled ``.sql`` is current and when its table was
    built, and a row per step (`STEP_FIELDS`): id, op and inputs, and the
    rows, columns, flags and drift the last run measured, kept on the model's
    entry (`spec.step_shapes`) and given only while the spec is the one that
    run executed. ``step`` reads one step **verbatim**, because a summary of a
    decision is a second author; ``finding`` reads one finding of the journal
    whole. The findings recorded for this spec are listed by question and every
    one is counted, so an absence reads as a number and never as nothing
    (`KNOWLEDGE_GRAPH.md` §4.4). ``match`` narrows the steps to ids matching a
    shell-style pattern, which is how a spec longer than `SPEC_STEPS` is read.

    ``measured`` adds the built table's shape and its flagged columns, named by
    the flag rule, and points at `describe_source` and `profile_source` on the
    model for the rest. It used to add the model's whole catalog entry, a
    record per column, which a 1,579-column model could send in no form but
    grouped; the model is an indexed table, and the rungs that read tables
    read it.

    ``spec_ref`` is a model name or a spec path. Both resolve through
    `discover_specs`, so the answer is about the file the project would build.
    """
    root = _project_root(portia_dir)
    models = spec.discover_specs(root)
    name, path = _resolve_spec(spec_ref, models, root)
    if finding:
        return _finding_card(finding, root=root, portia_dir=portia_dir)
    doc = spec.load_spec(path)
    steps = list(doc.get("steps") or [])
    shown_path = relative(path, root) if path.is_relative_to(root) else str(path)
    entry = catalog.load_models(portia_dir).get(name)
    shapes, note = _last_shapes(entry, doc)
    head = {"spec": shown_path, "model": name}
    if step:
        return {**head, **_step_card(steps, step, shapes, note, spec_ref=shown_path, model=name)}
    picked = [s for s in steps if _matches(str(s.get("id")), match)] if match else steps
    listed = picked[:SPEC_STEPS]
    step_list = {
        "n_steps": len(steps),
        "steps": listing.listing(
            STEP_FIELDS,
            [_step_row(s, shapes) for s in listed],
            n=len(picked),
            more=prompts.more("spec_steps", left=len(picked) - len(listed)),
        ),
        **note,
    }
    if match:
        return {**head, **step_list}
    layer = doc.get("layer")
    deps = spec.dependencies(models, base_dir=root) if name in models else {}
    compiled = pipeline.model_path(path, layer=layer, root=root)
    sources = dict(doc.get("sources") or {})
    read_by = [n for n, reads in sorted(deps.items()) if name in reads]
    card: dict[str, Any] = {
        **head,
        "layer": layer,
        "target": doc.get("target"),
        "sources": _tables(SOURCE_FIELDS, [[k, v] for k, v in sources.items()], name),
        "reads": _tables(NAME_FIELDS, [[n] for n in sorted(deps.get(name, ()))], name),
        "read_by": _tables(NAME_FIELDS, [[n] for n in read_by], name),
        "compiled": relative(compiled, root) if compiled.exists() else None,
        "compiled_stale": pipeline.is_stale(path, doc, layer=layer, root=root),
        "built": ((entry or {}).get("built") or {}).get("at"),
        **step_list,
        "findings": _findings_list(name, root),
    }
    if measured:
        # ``None`` when nobody has built it: a fact, stated, not an empty block.
        card["measured"] = None if entry is None else _built_shape(name, entry)
    card["more"] = prompts.more("spec_card", model=name)
    return card


def _last_shapes(entry: dict | None, doc: dict) -> tuple[dict[str, dict], dict]:
    """Each step's kept shape by id, and the ``steps_measured`` field saying from when.

    The shapes are given only when the run that kept them ran this spec: a
    hand edit since moves the fingerprint, and a shape measured before it is
    the shape of a step that may no longer be what it was. Then
    ``steps_measured`` is ``null`` with a sentence beside it saying why
    (`notices/`), as `row_facts` does for a row count nobody recorded.
    """
    built = (entry or {}).get("built") or {}
    kept = built.get("steps")
    if kept is not None and built.get("fingerprint") == pipeline.fingerprint(doc):
        return {str(s.get("id")): s for s in kept}, {"steps_measured": built.get("at")}
    if kept is not None:
        why = prompts.notice("steps_changed", at=built.get("at"))
    else:
        why = prompts.notice("steps_not_measured")
    return {}, {"steps_measured": None, "steps_note": why}


def _step_row(step: dict, shapes: dict[str, dict]) -> list:
    """One step as the card lists it: `STEP_FIELDS`, its shape ``null`` where none was kept."""
    shape = shapes.get(str(step.get("id"))) or {}
    return [
        step.get("id"),
        step.get("op"),
        spec.step_inputs(step),
        *(shape.get(key) for key in _SHAPE),
    ]


def _tables(fields: tuple[str, ...], rows: list[list], model: str) -> dict:
    """A list of tables on a spec card, cut at `SPEC_NAMES` and saying where the rest are."""
    shown = rows[:SPEC_NAMES]
    left = len(rows) - len(shown)
    return listing.listing(
        fields, shown, n=len(rows), more=prompts.more("spec_tables", left=left, model=model)
    )


def _findings_list(name: str, root: Path) -> dict:
    """The findings recorded for this spec, oldest first: the latest `SPEC_FINDINGS`, all counted."""
    briefs = findings.briefs_for_spec(name, root=root)
    latest = briefs[-SPEC_FINDINGS:]
    return listing.listing(
        FINDING_FIELDS,
        [[b.get("path"), b.get("at"), b.get("question")] for b in latest],
        n=len(briefs),
        start=len(briefs) - len(latest),
        more=prompts.more("spec_findings", left=len(briefs) - len(latest)),
    )


def _built_shape(name: str, entry: dict) -> dict:
    """The built table's shape and flagged columns, and where to read the rest of it.

    `describe_source`'s overview of a wide table (:func:`_overview`) at any
    width: counts by type, every flagged column named by the flag rule, and a
    ``more`` naming the two rungs that read the model like any table.
    """
    cols = list(entry.get("columns") or [])
    return {
        "built": (entry.get("built") or {}).get("at"),
        **row_facts(entry),
        "n_columns": len(cols),
        **_overview(
            cols,
            name,
            measured=catalog.is_profiled(entry),
            more=prompts.more("spec_measured", model=name),
        ),
    }


def _step_card(
    steps: list[dict],
    wanted: str,
    shapes: dict[str, dict],
    note: dict,
    *,
    spec_ref: str,
    model: str,
) -> dict:
    """One step verbatim, where it sits, which steps read it, and its kept shape.

    An id is matched as a name is (`dialect.resolve_columns`): exactly, else
    the one id that differs only in case. One that is not there is refused with
    the closest ids rather than all of them, `_requested`'s reason.
    """
    ids = [str(s.get("id")) for s in steps]
    (found,) = dialects.resolve_columns([wanted], ids)
    if found not in ids:
        raise ValueError(
            prompts.error(
                "unknown_step",
                step=wanted,
                model=model,
                n=len(ids),
                closest=", ".join(get_close_matches(wanted, ids, n=3)) or "(none close)",
            )
        )
    at = ids.index(found)
    shape = shapes.get(found) or {}
    readers = [ids[i] for i in range(at + 1, len(steps)) if found in spec.step_inputs(steps[i])]
    return {
        "position": at + 1,
        "n_steps": len(steps),
        "step": steps[at],
        "read_by_steps": readers,
        **{key: shape.get(key) for key in _SHAPE},
        **note,
        "more": prompts.more("spec_step", ref=f"{spec_ref}{STEP_REF}{found}", model=model),
    }


def _finding_card(ref: str, *, root: Path, portia_dir: str) -> dict:
    """One finding whole: its prose, the tables it is about, which moved since, and its queries.

    Found by its file name alone, in the project's ``findings/``: the path a
    card lists, or just the name. Only the name is read off what the agent
    typed, so no path it sends reaches anywhere else on the disk. Each query's
    result is shown up to `FINDING_RESULT_CHARS` with its whole length beside
    it, and a ``cut`` notice comes first when any was cut: a finding written
    before the revamp carries a query's whole answer.
    """
    stem = Path(ref).name.removesuffix(".yaml")
    path = root / findings.FINDINGS_DIR / f"{stem}.yaml"
    if not stem or not path.is_file():
        known = sorted(p.stem for p in (root / findings.FINDINGS_DIR).glob("*.yaml"))
        raise ValueError(
            prompts.error(
                "unknown_finding",
                finding=ref,
                n=len(known),
                closest=", ".join(get_close_matches(stem, known, n=3)) or "(none close)",
            )
        )
    where = relative(path, root)
    record = next((f for f in findings.load_all(root) if f.get("path") == where), {})
    queries = list(record.get("queries") or [])
    shown = queries[:FINDING_QUERIES]
    results = [str(q.get("result") or "") for q in shown]
    rows = [
        [q.get("n"), q.get("chat"), q.get("question"), q.get("sql"), text[:FINDING_RESULT_CHARS]]
        + [len(text)]
        for q, text in zip(shown, results, strict=True)
    ]
    out: dict[str, Any] = {}
    if any(len(text) > FINDING_RESULT_CHARS for text in results):
        out["cut"] = prompts.notice("finding_result_cut", chars=f"{FINDING_RESULT_CHARS:,}")
    return {
        **out,
        "finding": where,
        **{key: record.get(key) for key in ("question", "answer", "so", "about", "spec", "at")},
        "changed_since": findings.stale_against(record, root=root, portia_dir=portia_dir),
        "queries": listing.listing(
            QUERY_FIELDS,
            rows,
            n=len(queries),
            more=prompts.more("finding_queries", left=len(queries) - len(shown)),
        ),
    }


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
        return _step_table(ref, portia_dir, con)
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


def _step_table(ref: str, portia_dir: str, con=None):
    """Reach the table an earlier step produced, by re-running up to it.

    Only up to it: a later step may be the one being diagnosed and may not run
    at all yet. Executing the prefix is what ``record_step`` already does to
    measure a candidate, so this adds no new machinery — it just makes the same
    table reachable to a *read-only* check, before anything is written.

    **The spec is found by name and run from the project root** *(2026-10-08)*,
    as ``record_step`` finds and runs it. The root used to be the spec's
    grandparent, which is ``specs/`` for a spec in a layer folder, so every
    source path resolved one directory too deep.
    """
    spec_path, _, step_id = ref.partition(STEP_REF)
    root = _project_root(portia_dir)
    models = spec.discover_specs(root)
    _, path = _resolve_spec(spec_path, models, root)
    doc = spec.load_spec(path)
    steps = doc.get("steps") or []
    ids = [s["id"] for s in steps]
    if step_id not in ids:
        known = ", ".join(ids) or "(no steps yet)"
        raise ValueError(f"no step {step_id!r} in {spec_path} — have: {known}")
    prefix = {**doc, "steps": steps[: ids.index(step_id) + 1]}
    return spec.run_spec(prefix, base_dir=root, con=con or connect(), models=models)[-1].table


def _known_name(name: str, portia_dir: str) -> str:
    """``name`` as the project spells it, when only the case differs.

    The third session on Snowflake typed `INT_UNIFIED_INSPECTIONS` and
    `STG_NYC_INSPECTIONS` for models it had itself named in lower case — the
    warehouse had just shown them to it that way — and was refused twice
    (`CONNECTOR.md` §2.8.2's rule, applied to table names). Same resolver, same
    rule: exact first, then a unique case-insensitive match, else as it came.
    """
    root = _project_root(portia_dir)
    known = [*catalog.source_names(portia_dir), *spec.discover_specs(root)]
    return dialects.resolve_columns([name], known)[0]


def _entry(source: str, portia_dir: str) -> dict:
    """A catalog entry by name: an indexed source first, then a built model.

    Sources first for `catalog._entry_file`'s reason: the two namespaces can
    collide and the source is the one with a file behind it. The model half is
    what lets `describe_source` open a table the project built, which the audit
    after every build starts with (`docs/COPILOT.md` §3). An unknown name is
    told about both halves, because *no indexed source* on a model that exists
    reads as *that table does not exist* when the truth is *not by that name*.

    **One entry is read, not the catalog** *(2026-10-08)*. Every tool that names
    a table comes through here, `query_data` once per input, and it loaded every
    source's YAML twice: 0.9-1.5 s of a `describe_source` that asks DuckDB
    nothing, on a project with two 1,579-column entries of 265 kB each.
    """
    source = _known_name(source, portia_dir)
    entry = catalog.load_source(source, portia_dir)
    if entry is None:
        entry = catalog.load_models(portia_dir).get(source)
    if entry is None:
        known = ", ".join(catalog.source_names(portia_dir)) or "(none indexed)"
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
