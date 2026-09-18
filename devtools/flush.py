"""Reset a portia project to "data on disk and nothing else":
    python -m devtools.flush <project> [--catalog] [--graph] [--specs] ...

**A tool for working on portia, not part of it.** Re-running the same task
against the same project is how a prompt change or a new tool gets judged at all,
and doing that by hand is four deletes, a YAML edit and a Cypher statement — one
of which has no CLI anywhere in portia because deleting measurements is not
something the product should offer.

**What it never touches is the data.** `docs/PIPELINE.md` §2.7 retired the
ingested store, so a project's files are read in place and the catalog only
points at them. Re-indexing 12 GB of parquet costs no copy, which is what makes
resetting cheap enough to do between runs. On a warehouse project the same
holds one level up: the `connection` and `agent_writes` stay, the `scope` goes
with the catalog entries it lists (:data:`AGENT_KEYS`), and re-scoping a table
is one metadata query.

**Default is everything except the histories**, because a chat log is the only
artifact here that nothing can regenerate at all: the catalog comes back from the files,
the specs from a session, the graph from the catalog, but the record of what the
copilot did on 2026-08-17 exists once. `--history` says it out loud.

**It refuses to delete anything git tracks.** The repo root is itself a portia
project and `specs/sales_join.yaml` is hand-authored and committed, so a
mistyped path is a real way to lose real work. Tracked files are reported and
skipped rather than making the whole run fail — the untracked 99% of a sandbox
project is still what you wanted flushed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from portia import catalog, figures, findings

#: Keys in `project.yaml` that *indexing* wrote — groups the agent proposed, the
#: source index, and the warehouse `scope`. Cleared with the catalog, because a
#: brief still describing last run's groupings is not a clean project, and
#: because `scope` is the remote half of the source index: `catalog.scope_table`
#: appends to it as it writes the entry and `remove_source` drops the two
#: together. Left behind, it names tables no entry describes, and the picker
#: draws each of them as *in scope* with its tick disabled — so nothing can be
#: indexed and the pinned section, which draws off the entries, shows nothing
#: (2026-09-06). The keys that are *yours* survive: `project`, `data_dir`,
#: `connection` and `agent_writes` are setup rather than output, and retyping
#: them on every reset would make the reset expensive, which is the one thing it
#: must not be.
AGENT_KEYS = ("groups", "sources", "scope")

#: What each category is, in the order a plan prints them. `history` and `brief`
#: are absent from :data:`DEFAULT` and reachable only by naming them.
CATEGORIES = (
    "catalog",
    "graph",
    "specs",
    "findings",
    "figures",
    "models",
    "outputs",
    "history",
    "brief",
)

#: `findings` is in here and `history` is not, and the two look similar enough to
#: be worth separating. A finding's prose is judgment nothing can regenerate,
#: which is the argument that keeps chat logs out of the default — but a finding
#: is *read back by the agent* (`describe_source`), so one left over from the
#: previous attempt at the same task means the next run starts holding its own
#: prior conclusions. That is not a reset. The queries underneath are in the chat
#: log either way, which is what makes losing the sentence recoverable at all.
#:
#: `figures` is in for `runs/`'s reason rather than a finding's: nothing reads a
#: figure back to the agent, but a saved chart is a row in the gallery and a tab
#: one click away, so a reset that kept them would open on the last attempt's
#: pictures. A figure carries its own rows and its query is in the chat log
#: (`docs/VISUALIZATION.md` §6.2), so nothing in it is lost that the log does
#: not hold.
DEFAULT = ("catalog", "graph", "specs", "findings", "figures", "models", "outputs")


@dataclass
class Plan:
    """What a flush would remove. Computed before anything is deleted.

    Same shape and the same reason as `portia.cli.import_data.plan`: the listing
    is the real thing rather than a description of it, and a refusal is found
    before the first file goes rather than halfway through.
    """

    root: Path
    paths: dict[str, list[Path]] = field(default_factory=dict)
    edits: list[Path] = field(default_factory=list)
    graph: bool = False
    tracked: list[Path] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(self.paths.values()) and not self.edits and not self.graph


def _tracked_files(root: Path) -> set[Path]:
    """Everything git knows about under ``root`` — empty if it is not a repo.

    Asked once for the whole tree rather than per file: `git ls-files` on a
    project with ten thousand outputs is one process, and `--error-unmatch` per
    path is ten thousand.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()  # not a repo, or no git — nothing is tracked
    return {(root / name).resolve() for name in out.stdout.split("\0") if name}


def _existing(*paths: Path) -> list[Path]:
    return [p for p in paths if p.exists()]


def _glob(base: Path, pattern: str) -> list[Path]:
    return sorted(base.glob(pattern)) if base.exists() else []


def plan(root: Path, categories: tuple[str, ...]) -> Plan:
    """What flushing ``categories`` out of ``root`` would remove.

    Raises ``ValueError``, never ``SystemExit`` — a refusal is "this project
    cannot be flushed", which a caller other than `main` has to be able to
    report its own way.
    """
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"no such project directory: {root}")
    portia_dir = root / ".portia"
    if not portia_dir.is_dir():
        raise ValueError(f"not a portia project — no .portia in {root}")

    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise ValueError(f"unknown category {unknown[0]!r} — have: {', '.join(CATEGORIES)}")

    found: dict[str, list[Path]] = {}
    edits: list[Path] = []
    project_yaml = portia_dir / "project.yaml"

    if "catalog" in categories:
        found["catalog"] = _existing(portia_dir / "sources")
        if project_yaml.exists():
            edits.append(project_yaml)
    if "specs" in categories:
        found["specs"] = _glob(root / "specs", "**/*.yaml")
    if "findings" in categories:
        found["findings"] = _glob(root / findings.FINDINGS_DIR, "*.yaml")
    if "figures" in categories:
        # Saved charts, in whatever folders the user dragged them into
        # (`figures.load_all` walks the same tree).
        found["figures"] = _glob(root / figures.FIGURES_DIR, f"**/*{figures.SUFFIX}")
        # And the charts a host drew that nobody kept (`figures.stash`): the
        # window lists them as unsaved, so they are the last attempt's pictures
        # as much as a saved one is.
        found["figures"] += _existing(portia_dir / figures.DRAWN_DIR)
    if "models" in categories:
        # The compiled SQL and what the last build measured about the table it
        # produced. Both are build outputs and both are stale together — a
        # profile of a table whose `.sql` has been thrown away describes
        # nothing (`catalog.index_model`).
        found["models"] = _glob(root / "models", "**/*.sql") + _glob(
            portia_dir / catalog.MODELS_DIR, "*.yaml"
        )
    if "outputs" in categories:
        # `runs/` is the pre-rename location for saved reports — read by the app,
        # never written (docs/CONVERSATION.md §3). It still accumulates in old
        # projects, so a reset that left it would leave the left pane populated.
        found["outputs"] = _existing(root / "out", root / "runs")
    if "history" in categories:
        found["history"] = _existing(
            portia_dir / "chats", portia_dir / "indexing", portia_dir / "runs"
        )
    if "brief" in categories and project_yaml.exists() and project_yaml not in edits:
        edits.append(project_yaml)

    tracked = _tracked_files(root)
    kept: list[Path] = []
    for name, paths in found.items():
        safe = []
        for p in paths:
            hits = sorted(t for t in tracked if t == p.resolve() or t.is_relative_to(p.resolve()))
            kept += hits
            if not hits:
                safe.append(p)
        found[name] = safe

    return Plan(
        root=root,
        paths=found,
        edits=edits,
        graph="graph" in categories,
        tracked=sorted(set(kept)),
    )


def _flush_brief(path: Path, *, brief: bool, catalog: bool) -> None:
    """Rewrite `project.yaml` in place, dropping only what was asked for.

    Rewritten rather than deleted even when both are flushed, because
    `data_dir` is setup rather than output: losing it makes the next index
    reach for the wrong directory, which is a confusing failure rather than a
    clean slate.
    """
    doc = yaml.safe_load(path.read_text()) or {}
    if catalog:
        for key in AGENT_KEYS:
            doc.pop(key, None)
    if brief:
        doc.pop("project", None)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def _flush_graph(root: Path) -> str:
    """Delete this project's nodes and edges. **Best effort**, like every other
    graph write in portia (`KNOWLEDGE_GRAPH.md` §6.6): a stopped container is not
    a reason to abandon a reset whose file half already succeeded.

    It is a `DETACH DELETE` and it takes `OVERLAPS` with it, which nothing inside
    portia will ever do for you — a rebuild prunes structural edges only, because
    an absent measured edge has to keep meaning *nobody measured* (§4.4). That is
    exactly why this lives in `devtools/` and not behind a product flag.
    """
    from portia.knowledge import schema, store

    project = schema.project_id(root)
    try:
        with store.session() as session:
            summary = session.run(
                f"MATCH (n {{{schema.PROJECT}: $project}}) DETACH DELETE n", project=project
            ).consume()
        return f"graph: deleted {summary.counters.nodes_deleted} nodes for {project}"
    except store.GraphUnavailable as exc:
        return f"graph: not flushed — {exc}"


def render(p: Plan, categories: tuple[str, ...]) -> str:
    lines = [f"{p.root}"]
    for name in CATEGORIES:
        for path in p.paths.get(name, []):
            n = sum(1 for _ in path.rglob("*") if _.is_file()) if path.is_dir() else 1
            lines.append(
                f"  {name:9} {path.relative_to(p.root)}  ({n} file{'s' if n != 1 else ''})"
            )
    for path in p.edits:
        keys = _keys_to_clear(categories)
        # Labelled by what is doing the clearing, not by the file: `groups` and
        # `sources` go with the catalog, and only `project` is the brief.
        label = "brief" if "project" in keys else "catalog"
        lines.append(f"  {label:9} {path.relative_to(p.root)}  (clearing {', '.join(keys)})")
    if p.graph:
        lines.append(f"  {'graph':9} every node and edge for this project, OVERLAPS included")
    for path in p.tracked:
        lines.append(f"  SKIPPED  {path.relative_to(p.root)} — git tracks it")
    return "\n".join(lines)


def _keys_to_clear(categories: tuple[str, ...]) -> tuple[str, ...]:
    keys = AGENT_KEYS if "catalog" in categories else ()
    return (*keys, "project") if "brief" in categories else keys


def flush(p: Plan, categories: tuple[str, ...]) -> list[str]:
    """Do it. Returns one line per thing that happened."""
    done: list[str] = []
    for name in CATEGORIES:
        for path in p.paths.get(name, []):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            done.append(f"removed {path.relative_to(p.root)}")
    for path in p.edits:
        _flush_brief(path, brief="brief" in categories, catalog="catalog" in categories)
        done.append(f"rewrote {path.relative_to(p.root)}")
    if p.graph:
        done.append(_flush_graph(p.root))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset a portia project, keeping its data.",
        epilog=(
            "With no category flags: "
            + ", ".join(DEFAULT)
            + ". History and brief are opt-in — a chat log is the one thing here "
            "nothing can regenerate."
        ),
    )
    parser.add_argument("project", help="the project directory (the one holding .portia)")
    for name in CATEGORIES:
        parser.add_argument(f"--{name}", action="store_true", help=f"flush the {name}")
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="print the plan, change nothing"
    )
    args = parser.parse_args()

    chosen = tuple(name for name in CATEGORIES if getattr(args, name))
    categories = chosen or DEFAULT

    try:
        p = plan(Path(args.project), categories)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    print(render(p, categories))
    if p.empty:
        raise SystemExit("nothing to flush")
    if args.dry_run:
        raise SystemExit(0)
    print()
    for line in flush(p, categories):
        print(f"  {line}")


if __name__ == "__main__":
    main()
