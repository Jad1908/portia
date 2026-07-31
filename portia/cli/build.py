"""Build the project's pipeline: ``python -m portia.cli.build [--check]``.

Runs every spec in dependency order and writes the whole ``models/`` tree — one
``.sql`` per spec, plus the ``_sources.sql`` that creates the source names they
read. No model spend; this is the deterministic half.

``--check`` writes nothing and reports which models have drifted from their spec,
which is the form CI wants.
"""

from __future__ import annotations

import argparse

from portia import pipeline
from portia.core.present import count


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile a portia project to SQL.")
    parser.add_argument("--root", default=".", help="project root (default: .)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; report models whose .sql no longer matches their spec",
    )
    args = parser.parse_args()

    if args.check:
        stale = pipeline.stale_models(args.root)
        for name in stale:
            print(f"stale  {name} — its spec changed since the .sql was generated")
        if stale:
            raise SystemExit(1)
        print("every generated model matches its spec")
        return

    built = pipeline.build_project(args.root)
    if not built:
        print(f"no specs found under {args.root}/specs — nothing to build")
        return

    for model in built:
        layer = f"[{model.layer}] " if model.layer else ""
        print(f"{layer}{model.name} → {model.sql_path}")
        # Blocking flags and drift are reported, never suppressed: whether to ship
        # a pipeline with a known zero in it is the human's call, and a builder
        # that hid the file would be making that call in code.
        for step_id, drift in model.drift.items():
            for field, d in drift.items():
                print(f"    ⚠ DRIFT {step_id}.{field}: expected {d['expected']}, got {d['actual']}")
        if model.blocking:
            print(f"    ⚑ blocking: {', '.join(model.blocking)}")

    print(f"\nbuilt {count(len(built), 'model')}")
    if any(m.blocking for m in built):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
