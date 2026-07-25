"""Index a data source into the .portia catalog:
    python -m portia.cli.index <data> [--init "project context"] [--dir .portia]

Profiles the source and writes/refreshes its catalog entry (facts refreshed,
your prose + roles preserved). --init sets the global project context first.
"""

from __future__ import annotations

import argparse

from portia.catalog import index_source, init_project, load_catalog, render_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a data source into the .portia catalog.")
    parser.add_argument("data", help="path to a data file to index")
    parser.add_argument("--init", metavar="CONTEXT", help="set the project context first")
    parser.add_argument("--dir", default=".portia", help="catalog directory (default: .portia)")
    args = parser.parse_args()

    if args.init is not None:
        init_project(args.init, portia_dir=args.dir)
        print(f"project context set → {args.dir}/project.yaml\n")

    path = index_source(args.data, portia_dir=args.dir)
    name = path.stem
    entry = load_catalog(args.dir)["sources"][name]
    print(f"indexed → {path}\n")
    print(render_source(entry))


if __name__ == "__main__":
    main()
