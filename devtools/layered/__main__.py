"""Write the layered project:  python -m devtools.layered [--root DIR] [--no-index]"""

from __future__ import annotations

import argparse

from devtools.layered import DEFAULT_ROOT, build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="skip profiling — leaves a project whose arrows have no column detail",
    )
    args = parser.parse_args()

    root = build(args.root, index=not args.no_index)
    specs = sorted(p.stem for p in (root / "specs").rglob("*.yaml"))
    print(f"wrote {root}")
    print(f"  {len(specs)} specs: {', '.join(specs)}")
    print(f"\nopen it with:\n  python -m portia.ui --project {root}")


if __name__ == "__main__":
    main()
