"""Play with the profiler: `python -m portia.cli.profile <file> [--json]`.

No agent, no model spend — point it at a data file and read what the
deterministic engine sees.
"""

from __future__ import annotations

import argparse

from portia.checks.profiling import profile_path, render_text
from portia.core.serialize import to_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile a single data file.")
    parser.add_argument("path", help="path to a data file (see portia.core.io for formats)")
    parser.add_argument("--json", action="store_true", help="emit the raw profile as JSON")
    args = parser.parse_args()

    profile = profile_path(args.path)
    print(to_json(profile) if args.json else render_text(profile))


if __name__ == "__main__":
    main()
