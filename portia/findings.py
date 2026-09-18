"""The journal — *what we asked the data, and what it said*.

The fourth durable artifact, and the newest. Its siblings answer different
questions and none of them could hold this one (`docs/FINDINGS.md` §3):

- the **catalog** is keyed by one source
- the graph's **`OVERLAPS`** is keyed by a column pair and deliberately carries
  no transform (`KNOWLEDGE_GRAPH.md` §4.4 keeps even samples off the edge)
- the **spec** is what we decided to *do*, and is re-runnable by definition

*"After rounding both sides to 0.135°, stations match localities at 26% with 574
in one Paris cell"* fits none of them. Two tables, under a transform, at a chosen
parameter, with a judgment about the fan-out. The discriminator that places it is
**whether it has to be re-runnable**: a spec step must be, because the pipeline
re-executes it; a finding must not be, because its value is the sentence and the
moment rather than the query.

**Written after the work, not during it.** The agent cannot know at minute 3
whether a measurement will matter at minute 40, so `review` reads back what a
chat actually asked and :func:`record` keeps the few that earned it.

**The numbers are lifted from the log and never retyped**, and that is the one
rule here that is not negotiable. Forty tool calls later, possibly across a
compaction, an agent writing ``26% coverage`` from memory is *authoring a
number*, which is the line this project exists to hold (CLAUDE.md). So a finding
names the `query_data` calls it came from and this module copies their SQL and
their results out of the chat JSONL. The agent authors prose and nothing else.

**They live in ``findings/`` at the project root, committed**, beside `specs/`
and `models/` rather than inside `.portia/`. The journal is a *human* surface
(`docs/FINDINGS.md` §6.3): it answers "why does this pipeline look like this",
which a compiled `.sql` cannot carry and which someone reads in a diff having
never talked to the copilot. The argument the other way is real and was weighed —
the catalog is the agent's memory and lives in `.portia/`, and the agent does
read findings — but a chat log is disposable and a finding is not, and only one
of the two directories survives being cloned.

**Nothing here ranks.** `for_table` groups and caps; it never sorts by
importance, and a stale finding is *marked*, never deleted — the same rule
`knowledge/measure.py` follows, and for the same reason: a measurement that no
longer holds is still evidence that somebody tried.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from portia import catalog, pipeline, spec

#: Where a project keeps its journal. Root-level and committed — see the module
#: docstring for the argument, and for the one that was weighed against it.
FINDINGS_DIR = "findings"

#: How a `record` call names the queries it came from: their position in the
#: chat, one-based, as `review` numbered them. Positions rather than the SDK's
#: `toolu_…` ids because the model has to copy them by hand and a log is
#: append-only, so query 7 stays query 7 for as long as the file exists.
QUERY_REF = "from"

#: Findings shown per neighbouring table before the group says how many more
#: there are. `AQN_READINGS` is the root most other AQN tables hang off, so it
#: accumulates everything; this is the cap `knowledge/query.py` already applies
#: per neighbour, for the same reason and in the same shape.
PER_NEIGHBOUR = 3

#: Words of the question that become the filename. Long enough to tell two
#: findings apart in a directory listing, short enough to stay a filename.
SLUG_WORDS = 8

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


# --- writing ----------------------------------------------------------------


def record(
    *,
    question: str,
    answer: str,
    so: str,
    about: list[str],
    queries: list[dict],
    spec_name: str | None = None,
    root: str | Path = ".",
    portia_dir: str | Path = catalog.DEFAULT_DIR,
) -> Path:
    """Write one finding. Every prose field is required, and `so` is the reason why.

    ``so`` says what the answer changed. It is required for `measure_overlaps`'
    reason rather than to limit volume: a finding that cannot say what it changed
    is not a finding, it is a query somebody ran.

    ``queries`` are already-resolved `query_data` calls — see :func:`review`.
    Their SQL and results are copied in verbatim; nothing here accepts a number
    typed by a caller.
    """
    root = Path(root)
    doc: dict[str, Any] = {
        "question": question.strip(),
        "answer": answer.strip(),
        "so": so.strip(),
        "about": list(about),
        "spec": spec_name,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        # What each named table looked like when this was measured. Read-time
        # comparison only: nothing invalidates a finding when a file changes, and
        # `stale_against` marks it (`KNOWLEDGE_GRAPH.md` §4.5).
        "fingerprints": fingerprints(_tables(about), root=root, portia_dir=portia_dir),
        # **Which queries this rests on, by chat and position.** Not decoration:
        # `review` reads it back so a query already kept is shown as kept. The
        # agent reviews before each reply rather than at the end of a chat
        # (§5.2), so without this it would meet the same nine queries again on
        # the next turn with no way to tell which it had already used.
        "queries": [
            {
                "chat": q.get("chat"),
                "n": q.get("n"),
                "question": q["question"],
                "sql": q["sql"],
                "result": _answer_only(q),
            }
            for q in queries
        ],
    }
    path = _free_path(root / FINDINGS_DIR, slug(question))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return path


def _answer_only(query: dict) -> str:
    """The tool result with the fields this record already carries dropped.

    `query_data` echoes the ``question``, the ``sql`` and the ``inputs`` back in
    its result, which is right for the model — it is reading one payload — and
    wrong in a file where those three sit two lines above. Left whole, a finding
    states its question three times and its SQL twice, in a YAML somebody reads
    in a diff.

    **This is not a softening of the never-retype rule.** It removes keys, in
    code, that are stored verbatim elsewhere on the same record; it touches no
    number and the agent is not consulted. Anything that will not parse as JSON
    is kept exactly as it came back, because guessing at a shape we did not
    produce is how a result gets quietly truncated.
    """
    import json

    text = query.get("result", "")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(payload, dict):
        return text
    kept = {k: v for k, v in payload.items() if k not in ("question", "sql", "inputs")}
    return json.dumps(kept, separators=(",", ":"))


def slug(question: str) -> str:
    """A filename from the question, because a name is one more field to get wrong.

    Specs are named by the agent because a spec name is a *table* name that other
    specs reference. Nothing references a finding by name, so deriving it keeps
    the filename guaranteed to relate to the content — and makes a directory
    listing of `findings/` readable on its own.
    """
    words = _SLUG_STRIP.sub("-", question.lower()).strip("-").split("-")
    return "-".join(w for w in words if w)[:80] or "finding"


def _free_path(directory: Path, stem: str) -> Path:
    """``<stem>.yaml``, suffixed if taken. Two identical questions is not an error."""
    path = directory / f"{stem}.yaml"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}.yaml"
        n += 1
    return path


# --- reading ----------------------------------------------------------------


def load_all(root: str | Path = ".") -> list[dict]:
    """Every finding in the project, newest first, each carrying its ``path``."""
    directory = Path(root) / FINDINGS_DIR
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.yaml")):
        doc = _read(path)
        if doc:
            found.append({**doc, "path": str(path.relative_to(Path(root)))})
    return sorted(found, key=lambda d: str(d.get("at") or ""), reverse=True)


def for_spec(spec_name: str, root: str | Path = ".") -> list[dict]:
    """The journal for one model — what was asked on the way to building it.

    Oldest first, because a journal is read forwards. That is the one place in
    this module where order is chosen, and it is chronological rather than
    anything that could be mistaken for a ranking.
    """
    return sorted(
        (f for f in load_all(root) if f.get("spec") == spec_name),
        key=lambda d: str(d.get("at") or ""),
    )


def around_model(model: str, inputs: list[str], root: str | Path = ".") -> list[dict]:
    """Findings about the tables a model is built from, and about the model itself.

    **The second half of the journal, and the half that needed no agent** *(2026-09-04,
    `docs/FINDINGS.md` §5.3)*. `for_spec` shows what was recorded *for* a model, and
    that needs `record_finding` to have named it. This shows what was learned about
    the tables under it — the sources and the models it reads, which the spec
    already declares — so a finding filed against ``orders`` turns up under every
    model built from ``orders``, whether or not anyone thought to name the model.

    A fact, not a guess: the spec says what it reads. Anything `for_spec` would
    return is left out, so the two lists never repeat a record. Oldest first, as
    the journal is.
    """
    tables = {model, *inputs}
    return sorted(
        (
            f
            for f in load_all(root)
            if f.get("spec") != model and tables & set(_tables(f.get("about") or []))
        ),
        key=lambda d: str(d.get("at") or ""),
    )


def for_table(
    table: str,
    root: str | Path = ".",
    *,
    per_neighbour: int = PER_NEIGHBOUR,
    with_table: str | None = None,
) -> dict:
    """Findings that touch ``table``, grouped by the table on the other side.

    **Grouped rather than filtered**, and the default matters. `graph_lookup`
    went unused across four runs because it required knowing what you were
    looking for; a group per neighbour answers *what has anyone learned about
    this table* without the caller supplying a second name. ``with_table``
    narrows it when they have one.

    Each group is capped and states its own total, so a short list never reads as
    a short history — the rule `knowledge/query.py` already follows.
    """
    groups: dict[str, list[dict]] = {}
    for finding in load_all(root):
        tables = _tables(finding.get("about") or [])
        if table not in tables:
            continue
        others = sorted(t for t in tables if t != table)
        for neighbour in others or [""]:
            groups.setdefault(neighbour, []).append(finding)

    if with_table is not None:
        groups = {k: v for k, v in groups.items() if k == with_table}

    return {
        "table": table,
        "n_findings": len({f["path"] for group in groups.values() for f in group}),
        "with": {
            neighbour: {
                "n": len(group),
                "findings": [_brief(f) for f in group[:per_neighbour]],
            }
            for neighbour, group in sorted(groups.items())
        },
    }


def _brief(finding: dict) -> dict:
    """One finding as a handle: the question, what it changed, and where to read it.

    **Progressive disclosure is this function.** The full record carries every
    query and every result; a handle carries three sentences, so `describe_source`
    can name twenty findings without becoming the payload problem again.
    """
    return {
        "question": finding.get("question", ""),
        "answer": finding.get("answer", ""),
        "so": finding.get("so", ""),
        "path": finding.get("path", ""),
    }


def briefs_for_spec(spec_name: str, root: str | Path = ".") -> list[dict]:
    """The journal for one model as handles, oldest first, each with its date.

    What `read_spec` hands the agent on request: the same three sentences
    `describe_source` shows per finding, in the order a journal is read. The
    queries stay in the file, where `cli/journal show` prints them.
    """
    return [{**_brief(f), "at": f.get("at")} for f in for_spec(spec_name, root=root)]


# --- staleness --------------------------------------------------------------


def fingerprints(
    tables: list[str], *, root: str | Path = ".", portia_dir: str | Path = catalog.DEFAULT_DIR
) -> dict:
    """What each named table looks like now, as the graph already fingerprints it.

    A source is its file's size and mtime (`catalog.STALENESS_FACTS`); a model is
    its spec's fingerprint (`pipeline.fingerprint`). Computed here rather than
    read off the graph so a finding never needs Neo4j to be running.
    """
    entries = catalog.load_catalog(portia_dir)["sources"]
    specs = spec.discover_specs(Path(root))
    out: dict[str, str | None] = {}
    for table in tables:
        if table in entries:
            indexed = entries[table].get("indexed") or {}
            size, mtime = indexed.get("size"), indexed.get("mtime")
            out[table] = None if size is None or mtime is None else f"{size}:{mtime}"
        elif table in specs:
            out[table] = pipeline.fingerprint(spec.load_spec(Path(root) / specs[table]))
        else:
            out[table] = None
    return out


def stale_against(
    finding: dict, *, root: str | Path = ".", portia_dir: str | Path = catalog.DEFAULT_DIR
) -> list[str]:
    """Which of this finding's tables have moved since it was measured.

    **A read-time comparison, and it marks rather than deletes.** A finding whose
    data changed is still evidence somebody tried, and deleting one would make an
    absent finding mean two things — `KNOWLEDGE_GRAPH.md` §4.4's hazard, which
    this project has already paid for once.

    A table with no fingerprint on either side is not reported: *cannot tell* is
    not the same claim as *has changed*.
    """
    recorded = finding.get("fingerprints") or {}
    now = fingerprints(list(recorded), root=root, portia_dir=portia_dir)
    return _moved(recorded, now)


def stale_marks(
    records: list[dict], *, root: str | Path = ".", portia_dir: str | Path = catalog.DEFAULT_DIR
) -> dict[str, list[str]]:
    """`stale_against` for a whole list at once, keyed by each finding's ``path``.

    One catalog read for the list rather than one per finding: the journal
    draws every finding under a model on each render of the report, and
    `fingerprints` loads the catalog to answer. Same comparison, same rule —
    marked, never deleted.
    """
    tables = sorted({t for f in records for t in (f.get("fingerprints") or {})})
    now = fingerprints(tables, root=root, portia_dir=portia_dir)
    return {f.get("path", ""): _moved(f.get("fingerprints") or {}, now) for f in records}


def _moved(recorded: dict, now: dict) -> list[str]:
    return sorted(t for t, was in recorded.items() if was and now.get(t) and now[t] != was)


# --- the closed vocabulary --------------------------------------------------


def _tables(about: list[str]) -> list[str]:
    """The distinct table names in an ``about`` list, in the order first seen."""
    seen: dict[str, None] = {}
    for ref in about:
        seen.setdefault(str(ref).split(".", 1)[0], None)
    return list(seen)


def split_ref(ref: str) -> tuple[str, str | None]:
    """``"stations.LATITUDE"`` -> ``("stations", "LATITUDE")``; a bare name -> no column."""
    table, _, column = str(ref).partition(".")
    return table, column or None


def _read(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# --- rendering --------------------------------------------------------------


def render_finding(finding: dict, *, stale: list[str] | None = None) -> str:
    """One finding for a human. Shared, so the terminal and the app agree."""
    lines = [
        finding.get("question", ""),
        f"  {finding.get('answer', '')}",
        f"  so: {finding.get('so', '')}",
        f"  about: {', '.join(finding.get('about') or [])}",
    ]
    if finding.get("spec"):
        lines.append(f"  spec: {finding['spec']}")
    if stale:
        # Named, never a verdict: what changed is a fact, whether the finding
        # still holds is judgment and belongs to whoever is reading it.
        lines.append(f"  ⚑ measured before {', '.join(stale)} changed")
    return "\n".join(lines)


def render_table(grouped: dict) -> str:
    """`for_table`'s answer, for a human. Groups, caps, and never a ranking."""
    if not grouped["n_findings"]:
        return f"no findings about {grouped['table']}"
    lines = [f"findings about {grouped['table']} ({grouped['n_findings']})"]
    for neighbour, group in grouped["with"].items():
        where = f"with {neighbour}" if neighbour else f"about {grouped['table']} alone"
        lines.append(f"  {where} ({group['n']})")
        lines += [f"    {f['question']} → {f['so']}" for f in group["findings"]]
        if group["n"] > len(group["findings"]):
            lines.append(f"    ...{group['n'] - len(group['findings'])} more")
    return "\n".join(lines)


# --- reading a chat back ----------------------------------------------------

#: The tool whose calls a curation pass reads back. One name in one place: the
#: journal is about questions asked, and `query_data` is the only tool that asks
#: one without also writing something.
#: The tools whose calls a review numbers. **Both doors, not just the text one**
#: *(2026-09-02, `docs/VISUALIZATION.md` §6)*. `plot_data` runs the same
#: ``SELECT`` in the same sandbox and differs only in where its answer went, so a
#: chart that changed a decision and a counting query that changed a decision are
#: the same kind of thing — and inventing a second home for one of them is how
#: the two drift apart.
#:
#: What lands in a finding is the SQL and the tool's **result**, which for a
#: chart is its receipt: the tab, the row count, the encoding and the span of
#: each axis. The rows themselves went to the browser and are not evidence in a
#: file somebody reads in a diff; the query that produced them is.
QUERY_TOOLS = ("query_data", "plot_data")
#: The text door, kept as its own name because the plotting half asks about it.
QUERY_TOOL = QUERY_TOOLS[0]
PLOT_TOOL = QUERY_TOOLS[1]


def review(
    portia_dir: str | Path = catalog.DEFAULT_DIR,
    *,
    chat: str | None = None,
    root: str | Path = ".",
) -> list[dict]:
    """Every question asked of the data in one chat, numbered, with what it returned.

    Both doors: a `query_data` call, whose answer came back as text, and a
    `plot_data` call, whose rows went to the browser and whose result is the
    receipt. `QUERY_TOOLS` is why they are one list.

    **There is no staging area, because the log already is one.** `runlog` writes
    one event per line and tees at the edges regardless of what the agent intends
    to do later, so a query and its result are durable and verbatim before anyone
    decides whether they mattered. What was missing was never a state — a staged
    record nobody promoted is *judged irrelevant*, *abandoned* or *forgotten* and
    nothing can tell those apart, which is `KNOWLEDGE_GRAPH.md` §4.4's hazard —
    it was this read (`docs/FINDINGS.md` §5.1).

    Defaults to the newest chat, which during a live one is the live one. Naming
    an older chat is what makes curation **not time-bound**: a chat from three
    weeks ago can be curated tomorrow by a fresh session, with the numbers still
    exact because they were never in anybody's memory.

    Positions are one-based and stable. A log is append-only, so a later query
    becomes 8 and never renumbers 7.
    """
    from portia import runlog
    from portia.agent import events

    path = _chat_path(portia_dir, chat)
    if path is None:
        return []
    transcript = runlog.read(path)

    results = {e.data.get("id"): e.data for e in transcript.events if e.kind == events.TOOL_RESULT}
    already = kept_queries(root, chat=path.stem)
    reviewed: list[dict] = []
    for event in transcript.events:
        if event.kind != events.TOOL_CALL:
            continue
        tool = events.tool_label(str(event.data.get("name") or ""))
        if tool not in QUERY_TOOLS:
            continue
        sent = event.data.get("input") or {}
        got = results.get(event.data.get("id")) or {}
        reviewed.append(
            {
                "n": len(reviewed) + 1,
                "chat": path.stem,
                "tool": tool,
                # Which tab it drew, for a `plot_data` call and empty otherwise.
                # It is how a chart on screen finds its own call in the log — the
                # tab name is the chart's identity everywhere else too (§3.3).
                "tab": sent.get("tab", "") if tool == PLOT_TOOL else "",
                "question": sent.get("question", ""),
                "sql": sent.get("sql", ""),
                # Verbatim, as the tool returned it. This is the string a finding
                # copies; nothing re-encodes it and nothing summarises it.
                "result": got.get("text", ""),
                "failed": bool(got.get("is_error")),
                # Stated so reviewing before every reply is idempotent: the agent
                # sees which of these it has already written up, and judges only
                # the rest.
                "kept": (len(reviewed) + 1) in already,
            }
        )
    return reviewed


def kept_queries(root: str | Path = ".", *, chat: str | None = None) -> set[int]:
    """Positions in ``chat`` that some finding already rests on."""
    return {
        int(q["n"])
        for finding in load_all(root)
        for q in (finding.get("queries") or [])
        if q.get("n") is not None and (chat is None or q.get("chat") == chat)
    }


def _chat_path(portia_dir: str | Path, chat: str | None) -> Path | None:
    from portia import runlog

    if chat:
        return runlog.find(chat, portia_dir)
    logs = runlog.logs_in(portia_dir, kind=runlog.CHAT)
    return logs[0] if logs else None


def render_review(reviewed: list[dict]) -> str:
    """The curation list for a human — the questions, not the SQL."""
    if not reviewed:
        return "no queries in this chat"
    lines = [f"{len(reviewed)} quer{'y' if len(reviewed) == 1 else 'ies'}"]
    for q in reviewed:
        marks = " (failed)" if q["failed"] else ""
        marks += " · kept" if q.get("kept") else ""
        lines.append(f"  #{q['n']}  {q['question']}{marks}")
    return "\n".join(lines)
