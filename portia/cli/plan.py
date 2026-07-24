"""Plan a join into a spec step:
    python -m portia.cli.plan <left> <right> --on KEY [--id NAME] [-o SPEC.yaml]

Runs the checks, prints the decisions that matter (ranked, with suggested
answers), and — unless a blocker is found — records the proposed step into a
spec file. This is the deterministic stand-in for the copilot's decide→record.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from portia.core.io import load_frame
from portia.planner import propose_join_step, render_text
from portia.spec import add_step, load_spec, save_spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose + record a join step.")
    parser.add_argument("left", help="path to the left data file")
    parser.add_argument("right", help="path to the right data file")
    parser.add_argument("--on", action="append", help="shared key column (repeat for composite)")
    parser.add_argument("--left-on")
    parser.add_argument("--right-on")
    parser.add_argument("--id", help="step id (default: <left>_<right>)")
    parser.add_argument("-o", "--out", help="spec file to create/append the step to")
    args = parser.parse_args()

    left_name, right_name = Path(args.left).stem, Path(args.right).stem
    step_id = args.id or f"{left_name}_{right_name}"

    proposal = propose_join_step(
        load_frame(args.left),
        load_frame(args.right),
        step_id=step_id,
        left_name=left_name,
        right_name=right_name,
        on=args.on,
        left_on=args.left_on,
        right_on=args.right_on,
    )
    print(render_text(proposal))

    if not args.out:
        return

    if proposal.blocked:
        print(f"\nrefused to write {args.out}: resolve the blocker first.")
        raise SystemExit(1)

    out = Path(args.out)
    spec = load_spec(out) if out.exists() else None
    sources = {left_name: args.left, right_name: args.right}
    save_spec(add_step(spec, proposal.step, sources), out)
    print(f"\nrecorded step '{step_id}' → {out}")


if __name__ == "__main__":
    main()
