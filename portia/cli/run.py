"""Run a spec: `python -m portia.cli.run <spec.yaml> [--write DIR] [--json]`.

Reloads the sources, executes every step, and prints each step's drop report
plus any drift from what the spec expected. No model spend. With --write, the
resulting tables are saved as CSVs named by step id.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from portia.spec import load_spec, render_text, run_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a portia spec.")
    parser.add_argument("spec", help="path to a spec .yaml file")
    parser.add_argument("--write", metavar="DIR", help="write each step's output table there")
    parser.add_argument("--json", action="store_true", help="emit provenance + drift as JSON")
    args = parser.parse_args()

    # Source paths in the spec are relative to the current directory (run from
    # the project root), the way a project config is normally resolved.
    results = run_spec(load_spec(Path(args.spec)))

    if args.json:
        print(
            json.dumps(
                [{"id": r.id, "provenance": r.provenance, "drift": r.drift} for r in results],
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_text(results))

    if args.write:
        out_dir = Path(args.write)
        out_dir.mkdir(parents=True, exist_ok=True)
        for r in results:
            if r.frame is not None:
                r.frame.to_csv(out_dir / f"{r.id}.csv", index=False)
                print(f"wrote {out_dir / f'{r.id}.csv'}")

    if any(r.has_drift for r in results):
        raise SystemExit(1)  # drift is a failure signal for scripts/CI


if __name__ == "__main__":
    main()
