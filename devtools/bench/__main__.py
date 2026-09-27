"""`python -m devtools.bench <command> …`: the eval and benchmark tooling at a terminal.

python -m devtools.bench invariants sandbox/            # every log under a root
python -m devtools.bench invariants some/.portia --strict --json
python -m devtools.bench check devtools/bench/cases/hotel.yaml
python -m devtools.bench run devtools/bench/cases/hotel.yaml --variant B --model claude-haiku-4-5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from devtools.bench import baseline, invariants, run
from devtools.bench import case as cases
from portia.agent import session


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

    check = sub.add_parser("check", help="whether a case file can run, before any model does")
    check.add_argument("case", type=Path)

    runner = sub.add_parser("run", help="one case through portia, nobody at the keyboard")
    runner.add_argument("case", type=Path)
    runner.add_argument("--variant", default=None, help="which script (the case's `variants`)")
    runner.add_argument("--model", default=session.DEFAULT_MODEL)
    runner.add_argument("--effort", default=None, choices=session.EFFORTS)
    runner.add_argument("--provider", default=session.DEFAULT_PROVIDER)
    runner.add_argument("--out", type=Path, default=run.DEFAULT_OUT)
    runner.add_argument(
        "--arm", default=run.ARM_PORTIA, choices=run.ARMS, help="which side of the comparison"
    )

    args = parser.parse_args(argv)
    if args.command == "check":
        try:
            loaded = cases.load(args.case)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(
            f"{loaded.name}: {len(loaded.threads)} threads, {len(loaded.facts)} facts, "
            f"variants {', '.join(sorted(loaded.variants)) or 'none'}, "
            f"caps {loaded.max_turns} turns / ${loaded.max_budget_usd:.2f}"
        )
        return 0
    if args.command == "run":
        loaded = cases.load(args.case)
        if args.arm == run.ARM_PORTIA:
            result = asyncio.run(
                run.run_case(
                    loaded,
                    variant=args.variant,
                    model=args.model,
                    effort=args.effort,
                    provider=args.provider,
                    out=args.out,
                )
            )
        else:
            result = asyncio.run(
                baseline.run_baseline(
                    loaded,
                    variant=args.variant,
                    diligent=args.arm == run.ARM_BASELINE_DILIGENT,
                    model=args.model,
                    effort=args.effort,
                    provider=args.provider,
                    out=args.out,
                )
            )
        print(f"{result.project}")
        for thread in result.threads:
            print(
                f"  {thread.name}: {thread.log}  routed {len(thread.routing)}, fallbacks {thread.fallbacks}"
            )
        return 0
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
