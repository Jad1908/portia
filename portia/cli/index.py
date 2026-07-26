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
from portia.core.io import supported_suffixes


def resolve(target: str) -> list[Path]:
    """Every supported data file at ``target`` — a file, a directory, or a glob."""
    path = Path(target)
    if path.is_file():
        return [path]

    suffixes = supported_suffixes()
    if path.is_dir():
        found = sorted(p for p in path.iterdir() if p.suffix.lower() in suffixes)
    else:  # treat it as a glob
        found = sorted(p for p in Path().glob(target) if p.suffix.lower() in suffixes)

    if not found:
        raise SystemExit(
            f"no supported data files at {target!r} (supported: {', '.join(suffixes)})"
        )
    return found


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
        written = index_source(path, portia_dir=args.dir)
        names.append(written.stem)
        print(f"indexed → {written}")

    if args.no_interpret:
        print("\nfacts only (--no-interpret). Catalog entries:\n")
        catalog = load_catalog(args.dir)["sources"]
        for name in names:
            print(render_source(catalog[name]), "\n")
        return

    from portia.agent.session import DEFAULT_MODEL
    from portia.cli.chat import run_turn

    print()
    run_turn(
        prompts.task("index_batch", names=", ".join(repr(n) for n in names)),
        model=args.model or DEFAULT_MODEL,
        effort=args.effort,
        cwd=".",
        portia_dir=args.dir,
    )


if __name__ == "__main__":
    main()
