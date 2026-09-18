"""Read the journal — what was asked of the data, and what it said:

    python -m portia.cli.journal list [--table T] [--spec S] [--root .]
    python -m portia.cli.journal show <name> [--root .]

The human half of `portia/findings.py`. The agent reads findings through
`describe_source`, one table at a time; this is the view that answers *why does
this pipeline look like this*, which is a question a person asks and a compiled
`.sql` cannot carry.

`--spec` is the journal proper: everything asked on the way to one model, oldest
first, because a journal is read forwards. `--table` is the other index over the
same records — what has anyone learned about this table — grouped by the table on
the other side, exactly as the agent receives it, because the terminal and the
window may never disagree about a measurement (`portia/core/present.py`).

Findings live in `findings/` at the project **root**, not in `.portia/`, so
`--root` is a project directory rather than a portia one. `--dir` is still needed
for the catalog, which is what a fingerprint is compared against.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from portia import catalog, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m portia.cli.journal")
    parser.add_argument("--root", default=".", help="project root (holds findings/)")
    parser.add_argument("--dir", default=catalog.DEFAULT_DIR, help="portia dir, for staleness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    listing = sub.add_parser("list", help="findings, by table or by spec")
    listing.add_argument("--table", help="group what is known about one table")
    listing.add_argument("--spec", help="the journal for one model, oldest first")

    show = sub.add_parser("show", help="one finding in full, with its queries")
    show.add_argument("name", help="the finding's filename, or a prefix of it")

    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.cmd == "list":
        return _list(args, root)
    return _show(args.name, root, args.dir)


def _list(args, root: Path) -> int:
    if args.table:
        print(findings.render_table(findings.for_table(args.table, root=root)))
        return 0

    found = findings.for_spec(args.spec, root=root) if args.spec else findings.load_all(root)
    if not found:
        print("no findings yet")
        return 0
    for finding in found:
        print(findings.render_finding(finding))
        print(f"  {finding['path']}")
        print()
    return 0


def _show(name: str, root: Path, portia_dir: str) -> int:
    match = next(
        (f for f in findings.load_all(root) if Path(f["path"]).stem.startswith(name)), None
    )
    if match is None:
        print(f"no finding matching {name!r}")
        return 1

    # Staleness is computed at read time and never stored — nothing invalidates a
    # finding when a file changes (`KNOWLEDGE_GRAPH.md` §4.5).
    stale = findings.stale_against(match, root=root, portia_dir=portia_dir)
    print(findings.render_finding(match, stale=stale))
    for query in match.get("queries") or []:
        print(f"\n  ? {query['question']}")
        print(f"    {query['sql']}")
        print(f"    → {query['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
