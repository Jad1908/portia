"""Bring data into a portia project:
    python -m portia.cli.index <file|directory|glob> [--no-interpret] [--dir .portia]

Two things happen, in order:

1. **Deterministic indexing** — every file is profiled and its facts written to
   the catalog. Free, and it always happens.
2. **Interpretation** — the copilot reads those facts through the project context
   and records what each source *is*. Costs a model turn, so it is on by default
   and `--no-interpret` opts out. The whole batch is interpreted in **one**
   session, so it can see how the sources relate and group them.

The first time this runs in a directory it asks what the project is. That
description is what makes a column's meaning decidable, so nothing else can
usefully happen without it (docs/VISION.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portia.agent import prompts
from portia.catalog import index_source, init_project, load_catalog, render_source
from portia.core.io import find_data_files


def resolve(target: str) -> list[Path]:
    """Every supported data file at ``target``, or a clean exit if there are none."""
    try:
        return find_data_files(target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def ensure_project_context(portia_dir: str) -> None:
    """Prompt for the project description the first time, on stdin."""
    proj = Path(portia_dir) / "project.yaml"
    if proj.exists() and (load_catalog(portia_dir).get("project") or "").strip():
        return

    if not sys.stdin.isatty():
        raise SystemExit(
            "no project context set, and stdin isn't a terminal to ask on. "
            'Run once interactively, or pass --init "…".'
        )

    print(prompts.load("tasks/ask_for_context"))
    lines: list[str] = []
    print("  (finish with an empty line)")
    while (line := input("  > ").rstrip()) or not lines:
        if not line:
            continue
        lines.append(line)
    init_project(" ".join(lines), portia_dir=portia_dir)
    print(f"\ncontext saved → {portia_dir}/project.yaml\n")


def sync_graph(portia_dir: str) -> None:
    """Refresh the knowledge graph's structural half — **best effort, always**.

    §5 names indexing as a write moment, and §9.4 needs it: at source 23 the
    copilot queries the graph to judge which pairs are worth measuring, so the
    sources it has already read have to be in there by the time it looks.

    It never fails the index. An index that stops because a container is stopped
    is precisely the leak §6.6 warns about — a design decision escaping into a
    hard requirement — so a missing database is one printed line and nothing
    else. The facts are in the catalog either way; the graph is a restatement of
    them and can be rebuilt with `python -m portia.cli.knowledge --write`.
    """
    from portia import knowledge
    from portia.knowledge import store

    try:
        result = knowledge.sync(Path(portia_dir).parent, portia_dir=portia_dir)
    except store.GraphUnavailable as exc:
        print(f"\nknowledge graph not updated — {exc}")
        return
    counts = result.graph.counts()["nodes"]
    print(f"\nknowledge graph updated — {counts['Source']} sources, {counts['Column']} columns")


def main() -> None:
    parser = argparse.ArgumentParser(description="Index data into a portia project.")
    parser.add_argument("data", help="a data file, a directory of them, or a glob")
    parser.add_argument(
        "--init", metavar="CONTEXT", help="set the project context non-interactively"
    )
    parser.add_argument(
        "--no-interpret",
        action="store_true",
        help="index the facts only; skip the copilot's read (no model call)",
    )
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    parser.add_argument("--model", default=None, help="model to run the copilot on")
    parser.add_argument("--effort", default=None, help="how hard it thinks (low … max)")
    args = parser.parse_args()

    if args.init is not None:
        init_project(args.init, portia_dir=args.dir)
        print(f"project context set → {args.dir}/project.yaml\n")
    else:
        ensure_project_context(args.dir)

    paths = resolve(args.data)
    names = []
    for path in paths:
        try:
            written = index_source(path, portia_dir=args.dir)
        except ValueError as exc:
            # An out-of-repo path is refused, not warned about (PIPELINE.md §2.7).
            # `catalog.source_ref` composes the message, including the import
            # command to run instead, so the rule is stated in one place.
            raise SystemExit(str(exc)) from None
        names.append(written.stem)
        print(f"indexed → {written}")

    sync_graph(args.dir)

    if args.no_interpret:
        print("\nfacts only (--no-interpret). Catalog entries:\n")
        catalog = load_catalog(args.dir)["sources"]
        for name in names:
            print(render_source(catalog[name]), "\n")
        return

    from portia import runlog
    from portia.agent.session import DEFAULT_MODEL
    from portia.cli.chat import run_turn

    print()
    run_turn(
        prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
        model=args.model or DEFAULT_MODEL,
        effort=args.effort,
        cwd=".",
        portia_dir=args.dir,
        kind=runlog.INDEXING,
    )


if __name__ == "__main__":
    main()
