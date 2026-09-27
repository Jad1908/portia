"""`python -m devtools.bench <command> …`: the eval and benchmark tooling at a terminal.

python -m devtools.bench invariants sandbox/            # every log under a root
python -m devtools.bench invariants some/.portia --strict --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devtools.bench import invariants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read what a run left behind. Counts, never scores."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("invariants", help="what broke a rule that holds on every route")
    inv.add_argument(
        "paths", nargs="+", type=Path, help="a log, a .portia folder, or a root to walk"
    )
    inv.add_argument(
        "--strict",
        action="store_true",
        help="accept rounding only, unless hedged; the default also accepts one unit of the last digit",
    )
    inv.add_argument("--json", action="store_true", help="one JSON object per log, then the totals")
    inv.add_argument("--no-flags", action="store_true", help="the counts only")

    args = parser.parse_args(argv)
    if args.command == "invariants":
        reports = invariants.check_all(args.paths, strict=args.strict)
        if not reports:
            print("no logs found", file=sys.stderr)
            return 1
        if args.json:
            for report in reports:
                print(json.dumps(report.as_dict(), ensure_ascii=False))
            print(json.dumps({"totals": invariants.totals(reports)}, ensure_ascii=False))
        else:
            print(invariants.render(reports, show_flags=not args.no_flags))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
