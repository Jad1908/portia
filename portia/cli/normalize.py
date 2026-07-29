"""Clean/coerce columns:
    python -m portia.cli.normalize <file> [--strip COL] [--lower COL]
        [--to-numeric COL] [--to-string COL] [-o OUT.csv]

Repeat a flag for multiple columns. Prints what changed and what failed to
convert; with -o, writes the cleaned table.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from portia.core import store
from portia.core.io import load_table, write_table
from portia.ops.normalize import apply_normalize, render_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize/coerce columns of a data file.")
    parser.add_argument("file", help="path to the data file")
    parser.add_argument("--strip", action="append", default=[], metavar="COL")
    parser.add_argument("--lower", action="append", default=[], metavar="COL")
    parser.add_argument("--to-numeric", action="append", default=[], metavar="COL")
    parser.add_argument("--to-string", action="append", default=[], metavar="COL")
    parser.add_argument("-o", "--out", help="write the cleaned table to this CSV")
    args = parser.parse_args()

    # Applied in a sensible order: tidy strings before parsing them to numbers.
    transforms = (
        [{"column": c, "op": "strip"} for c in args.strip]
        + [{"column": c, "op": "lower"} for c in args.lower]
        + [{"column": c, "op": "to_numeric"} for c in args.to_numeric]
        + [{"column": c, "op": "to_string"} for c in args.to_string]
    )
    if not transforms:
        parser.error("give at least one of --strip/--lower/--to-numeric/--to-string")

    result = apply_normalize(load_table(args.file, store.memory()), transforms)
    print(render_text(result.provenance))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        write_table(result.table, args.out)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
