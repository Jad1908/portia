"""Bring outside data into the project: ``python -m portia.cli.import_data <file> --to <dir>``.

The deliberate half of `docs/PIPELINE.md` §2.7. ``index`` only accepts files that
are **already inside** the repo, because portia plugs into a project that holds
its own data and the user picks what is in scope. This command is how a file gets
in: you choose where it lands, portia says exactly what it is about to copy and
to where, and only then does it copy and index.

**It is a copy, never a move.** The original is not touched, not deleted, not
rewritten. A tool that relocates someone's data is not a data-harmonization
concern (`CLAUDE.md`), and the one time it matters is the time you find out
afterwards.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from portia.catalog import DEFAULT_DIR, index_source, project_root
from portia.core.io import find_data_files


def plan(sources: list[Path], destination: Path, root: Path) -> list[tuple[Path, Path]]:
    """What would be copied where. Computed before anything is written.

    Separate from doing it so the confirmation shows the real thing rather than a
    description of it, and so a name collision is found before the first byte
    moves rather than halfway through a batch. **Both edges call this** — the
    terminal and the window show the same plan because it is the same plan, not
    because two surfaces were written to agree.

    Raises ``ValueError``, not ``SystemExit``: a refusal here is "this import
    cannot go ahead", which a window has to be able to put on screen. Exiting is
    `main`'s way of reporting that, and it is the only caller allowed to decide it.
    """
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"the destination must be inside the project ({root}), got {destination}")

    pairs = [(src, destination / src.name) for src in sources]
    clashes = [dst for _, dst in pairs if dst.exists()]
    if clashes:
        names = ", ".join(str(c) for c in clashes)
        raise ValueError(f"already there, refusing to overwrite: {names}")
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy outside data into the project, then index it."
    )
    parser.add_argument("data", help="a data file, a directory of them, or a glob")
    parser.add_argument("--to", required=True, metavar="DIR", help="where in the project it lands")
    parser.add_argument("--dir", default=DEFAULT_DIR, help="catalog directory (default: .portia)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args()

    root = project_root(args.dir)
    try:
        sources = find_data_files(args.data)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    destination = Path(args.to)
    try:
        pairs = plan(sources, destination, root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    print(f"copying {len(pairs)} file(s) into {destination}/ — the originals are left alone:\n")
    for src, dst in pairs:
        print(f"  {src}  →  {dst}")

    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit("\nnot a terminal to confirm on — re-run with --yes")
        if input("\nproceed? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("nothing copied")

    destination.mkdir(parents=True, exist_ok=True)
    for src, dst in pairs:
        shutil.copy2(src, dst)
        print(f"copied  {dst}")
        print(f"indexed {index_source(dst, portia_dir=args.dir)}")


if __name__ == "__main__":
    main()
