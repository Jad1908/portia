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
from pathlib import Path
from typing import Any

from portia import catalog, knowledge, spec
from portia.agent import prompts
from portia.checks import profiling
from portia.checks.join import column_overlap
from portia.checks.join import join_findings as _join_findings
from portia.checks.outcome import BLOCKING_FLAGS
from portia.checks.profiling import profile_path
from portia.core.io import connect, load_table
from portia.core.serialize import to_json
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
#: source: ``specs/training.yaml#otb_hotels``. A multi-hop merge joins an
#: *intermediate* result, which is not a file and so cannot be in the catalog —
#: without this, "always measure before you decide" is impossible to obey from
#: hop 2 onward, and the agent is left recording blind. Observed doing exactly
#: that (docs/EVALUATION.md, Run 3).
STEP_REF = "#"
_STEP_REF_HINT = "'<spec path>#<step id>', e.g. 'specs/training.yaml#otb_hotels'"

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
    return {
        "project": cat["project"],
        "groups": cat["groups"],
        "sources": {
            name: {
                "summary": entry.get("summary", ""),
                "n_columns": len(entry.get("columns", [])),
                "candidate_keys": entry.get("candidate_keys", []),
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
    """
    entry = _entry(source, portia_dir)
    return {
        "source": entry["source"],
        "summary": entry.get("summary", ""),
        "candidate_keys": entry.get("candidate_keys", []),
        "columns": [
            {
                "name": col["name"],
                "role": col.get("role"),
                "inferred": col.get("inferred"),
                "flags": col.get("flags", []),
            }
            for col in entry.get("columns", [])
        ],
    }


def graph_lookup(table: str, column: str | None = None) -> dict:
    """The router — *which* table should I look at, and where did this column come from.

    Not a rung on the disclosure ladder: the ladder is depth on one source, and
    this is breadth. It sits **before** ``describe_source`` (`KNOWLEDGE_GRAPH.md`
    §9.1) — ask it where to start, then climb.

    Named without a table it returns tables, never column pairs: what this one
    reads, what reads it, its groups, and which other tables share a measured
    overlap with it. Add a column and it returns that column's lineage — one hop
    each way with the step that explains it, plus the files underneath.

    The graph lives in Neo4j, which may not be running. That is not an error in
    the question; it is reported as itself so the caller can carry on with the
    tools that need no database.
    """
    try:
        with store.session() as live:
            return query.lookup(live, table, column)
    except store.GraphUnavailable as exc:
        # Reframed rather than re-raised: what the model needs at this moment is
        # not the stack but which tools still work. The other refusals in
        # `prompts/errors/` exist for the same reason.
        raise ValueError(prompts.error("graph_unavailable", reason=str(exc))) from None


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
    """
    try:
        with store.session() as live:
            store.write(graph, live)
            store.write_measured(edges, live)
    except store.GraphUnavailable as exc:
        return {"stored": False, "not_stored_because": str(exc)}
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


def profile_source(source: str, portia_dir: str = catalog.DEFAULT_DIR) -> dict:
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
    """
    if STEP_REF in source:
        profile = profiling.profile(_step_table(source))
        return {
            "source": source,
            "summary": "",
            "n_rows": profile["n_rows"],
            "n_cols": profile["n_cols"],
            "candidate_keys": profile["candidate_keys"],
            "columns": [{**col, "role": None} for col in profile["columns"]],
        }

    entry = _entry(source, portia_dir)
    profile = profile_path(_project_root(portia_dir) / entry["source"])
    roles = {c["name"]: c.get("role") for c in entry.get("columns", [])}
    return {
        "source": entry["source"],
        "summary": entry.get("summary", ""),
        "n_rows": profile["n_rows"],
        "n_cols": profile["n_cols"],
        "candidate_keys": profile["candidate_keys"],
        "columns": [{**col, "role": roles.get(col["name"])} for col in profile["columns"]],
    }


def set_interpretation(
    source: str,
    summary: str | None = None,
    roles: dict[str, str] | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
) -> dict:
    """Record what this data *is* — the agent's read, written to the catalog.

    Writes judgment only; every fact is left untouched. Omit a field to leave
    the existing value alone.
    """
    if summary is None and not roles:
        raise ValueError("nothing to record — pass a summary, roles, or both")

    path = catalog.set_interpretation(source, summary=summary, roles=roles, portia_dir=portia_dir)
    return {
        "source": source,
        "path": str(path),
        "summary_written": summary is not None,
        "roles_written": sorted(roles or {}),
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
) -> dict:
    """What joining two sources on these keys would actually do — plus the rows.

    Returns key-level facts (overlap, coverage, relationship, fan-out, and the
    row counts each `how` would produce) *and* example rows: the unmatched ones,
    the null-key ones, the worst fan-out keys. Nothing here is ranked or scored.
    Whether a dropped row is a catastrophe or a non-event depends on the goal,
    which only you have.

    Call this **before** deciding anything about a merge.
    """
    con = connect()
    return _join_findings(
        _table(left, portia_dir, con),
        _table(right, portia_dir, con),
        on=keys,
        left_on=left_on,
        right_on=right_on,
    )


def record_step(
    spec_path: str,
    step: dict,
    layer: str | None = None,
    portia_dir: str = catalog.DEFAULT_DIR,
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

    Validation and serialization happen here, in code. The *content* is yours.
    """
    path = Path(spec_path)
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

    root = _project_root(portia_dir)
    # Every other spec in the project is a table this step may read, by name. The
    # spec being written is excluded: a model cannot be its own input.
    models = {n: p for n, p in spec.discover_specs(root).items() if n != path.stem}

    step_ids = {s["id"] for s in steps}
    _normalize_step_refs(step, spec_path=str(path))
    _validate_step(step, existing=steps)
    for ref in _source_refs(step, known_steps=step_ids | set(models)):
        try:
            sources[ref] = _source_path(ref, portia_dir)
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
    result = spec.run_spec({**doc, "steps": [*steps, step]}, base_dir=root, models=models)[-1]

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

    steps.append(step)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.save_spec(doc, path)
    return {
        "spec": str(path),
        "step_id": step["id"],
        "n_steps": len(steps),
        "layer": doc.get("layer"),
        "outcome": result.outcome,
        "drift": result.drift,
        "acknowledged": result.acknowledged,
    }


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
        return load_table(_project_root(portia_dir) / _source_path(ref, portia_dir), con, name=ref)
    except ValueError as exc:
        # Not an indexed source. Before giving up, try the project's other models:
        # a name is allowed to be any of three things, and a message that names
        # only one of them reads as "that table doesn't exist" when the truth is
        # "not by that name" (`docs/PIPELINE.md` §2.4).
        root = _project_root(portia_dir)
        models = spec.discover_specs(root)
        if ref in models:
            return spec.model_table(ref, models, root, con, ())
        known = ", ".join(sorted(models)) or "(none)"
        raise ValueError(
            f"{exc}. Models in this project: {known}. "
            f"For a table an earlier step in the spec you are writing produced: {_STEP_REF_HINT}"
        ) from exc


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


def _entry(source: str, portia_dir: str) -> dict:
    cat = catalog.load_catalog(portia_dir)
    entry = cat["sources"].get(source)
    if entry is None:
        known = ", ".join(cat["sources"]) or "(none indexed)"
        raise ValueError(f"no indexed source {source!r} — have: {known}")
    return dict(entry)


def _source_path(source: str, portia_dir: str) -> str:
    return str(_entry(source, portia_dir)["source"])


def _validate_step(step: dict, *, existing: list[dict]) -> None:
    if not step.get("id"):
        raise ValueError("step needs an 'id'")
    if step["id"] in {s["id"] for s in existing}:
        # Steps are append-only. The old message here said "pick another", which
        # taught the workaround: a run that wanted to rewrite its `expect` to
        # match reality would simply have recorded `join_v2`. Say what the rule
        # is and why, rather than naming the loophole.
        raise ValueError(prompts.error("immutable_step", step_id=repr(step["id"])))

    _validate_grain(step.get("grain"))
    _validate_acknowledge(step.get("acknowledge"))

    op = step.get("op")
    if op not in _REQUIRED_FIELDS:
        raise ValueError(f"unknown op {op!r} — have: {', '.join(_REQUIRED_FIELDS)}")
    missing = [f for f in _REQUIRED_FIELDS[op] if not step.get(f)]
    if missing:
        raise ValueError(f"{op} step needs {', '.join(missing)}")
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
            f"expect refers to {', '.join(repr(u) for u in unknown)}, which {op} never "
            f"reports — so it would drift on every run. Assert only measured fields: "
            f"{', '.join(sorted(_EXPECTABLE[op]))}"
        )


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
    """
    for field in _REF_FIELDS:
        ref = step.get(field)
        if isinstance(ref, str):
            step[field] = _bare_step_id(ref, field=field, spec_path=spec_path)
    if isinstance(step.get(_REF_LIST_FIELD), list):
        step[_REF_LIST_FIELD] = [
            _bare_step_id(r, field=_REF_LIST_FIELD, spec_path=spec_path)
            if isinstance(r, str)
            else r
            for r in step[_REF_LIST_FIELD]
        ]


def _bare_step_id(ref: str, *, field: str, spec_path: str) -> str:
    """``specs/t.yaml#otb_hotels`` → ``otb_hotels``; anything else unchanged.

    A ``#``-reference to a *different* spec is reduced to that spec's **model
    name**, because one spec produces one table and the name of that table is the
    spec's own name (`docs/PIPELINE.md` §2.1, §2.4). Which step inside it produced
    the table is not the caller's business, and naming one would couple two specs
    to each other's internals.
    """
    if STEP_REF not in ref:
        return ref
    named_spec, _, step_id = ref.partition(STEP_REF)
    if Path(named_spec) != Path(spec_path):
        return Path(named_spec).stem
    return step_id


def _source_refs(step: dict, *, known_steps: set[str]) -> list[str]:
    """Source names the step reads, minus anything produced by an earlier step."""
    return [ref for ref in spec.step_inputs(step) if ref not in known_steps]


def _is_interpreted(entry: dict) -> bool:
    """Whether a source still carries the auto-drafted placeholder read."""
    return catalog.is_interpreted(entry)
