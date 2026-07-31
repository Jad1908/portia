"""Play with the join check: `python -m portia.cli.join <left> <right> --on KEY`.

Loads two data files and surfaces what joining them would do — the drop report
plus sample rows behind each anomaly. No join is run, no model spend. Use
--left-on/--right-on for differently named keys, and repeat --on for a composite.
"""

from __future__ import annotations

import argparse
import json

from portia.checks.join import join_findings, render_findings
from portia.core.io import connect, load_table


def main() -> None:
    parser = argparse.ArgumentParser(description="Surface the facts of a join.")
    parser.add_argument("left", help="path to the left data file")
    parser.add_argument("right", help="path to the right data file")
    parser.add_argument("--on", action="append", help="shared key column (repeat for composite)")
    parser.add_argument("--left-on", action="append", help="left key column (with --right-on)")
    parser.add_argument("--right-on", action="append", help="right key column (with --left-on)")
    parser.add_argument("--json", action="store_true", help="emit the raw findings as JSON")
    args = parser.parse_args()

    # One connection: a join reads both sides at once, and DuckDB cannot join
    # across handles.
    con = connect()
    findings = join_findings(
        load_table(args.left, con),
        load_table(args.right, con),
        on=args.on,
        left_on=args.left_on,
        right_on=args.right_on,
    )
    print(
        json.dumps(findings, indent=2, ensure_ascii=False)
        if args.json
        else render_findings(findings)
    )


if __name__ == "__main__":
    main()
