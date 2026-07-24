"""Play with the join check: `python -m portia.cli.join <left> <right> --on KEY`.

Loads two data files and reports what joining them would do — no join is run,
no model spend. Use --left-on/--right-on for differently named keys, and repeat
--on for a composite key.
"""

from __future__ import annotations

import argparse

from portia.checks.join import join_report, render_text
from portia.core.io import load_frame
from portia.core.serialize import to_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Report the consequences of a join.")
    parser.add_argument("left", help="path to the left data file")
    parser.add_argument("right", help="path to the right data file")
    parser.add_argument("--on", action="append", help="shared key column (repeat for composite)")
    parser.add_argument("--left-on", action="append", help="left key column (with --right-on)")
    parser.add_argument("--right-on", action="append", help="right key column (with --left-on)")
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    args = parser.parse_args()

    report = join_report(
        load_frame(args.left),
        load_frame(args.right),
        on=args.on,
        left_on=args.left_on,
        right_on=args.right_on,
    )
    print(to_json(report) if args.json else render_text(report))


if __name__ == "__main__":
    main()
