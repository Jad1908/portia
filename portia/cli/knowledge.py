"""Build the knowledge graph: ``python -m portia.cli.knowledge [--write]``.

§5 says a build command is needed regardless of anything else: someone cloning
the repo gets the YAML from git and no graph, so there has to be a way to
construct it from the project.

**Reading it costs nothing and needs no database.** With no flags this reads the
catalog and the specs, builds the graph in memory and prints what is in it —
which is how phase A is checked without an agent or a server anywhere near it
(§9.4). ``--cypher`` prints exactly what would be sent, for the same reason.
``--write`` is the only form that needs Neo4j running.

Unresolved models are reported, never treated as a failure. A `sql` step's
output columns cannot be named from a spec, and a pipeline that uses the hatch
is not a broken pipeline — the count is evidence for §7's open question about
buying a SQL parser, and nothing more.
"""

from __future__ import annotations

import argparse

from portia.knowledge import build
from portia.knowledge.build import render_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a portia project's knowledge graph.")
    parser.add_argument("--root", default=".", help="project root (default: .)")
    parser.add_argument("--write", action="store_true", help="write it to Neo4j")
    parser.add_argument(
        "--cypher",
        action="store_true",
        help="print the statements a --write would send, and send nothing",
    )
    args = parser.parse_args()

    result = build.build_graph(args.root)
    print(render_text(result))

    if args.cypher:
        _print_cypher(result.graph)
    if args.write:
        _write(result.graph)


def _print_cypher(graph) -> None:
    from portia.knowledge import store

    stamp = store.new_build_id()
    print("")
    for statement in store.constraint_statements():
        print(f"{statement};")
    for statement, params in [
        *store.node_writes(graph, stamp),
        *store.edge_writes(graph, stamp),
        *store.prune_writes(stamp),
    ]:
        rows = params.get("rows")
        print(f"{statement};" + (f"  -- {len(rows)} row(s)" if rows else ""))


def _write(graph) -> None:
    """Send it. The failure to say clearly is "the database isn't running"."""
    from portia.knowledge import store

    config = store.settings()
    try:
        driver = store.connect()
    except Exception as exc:
        raise SystemExit(f"cannot reach Neo4j at {config['uri']}: {exc}") from None

    with driver:
        with driver.session(database=config["database"]) as session:
            stamp = store.write(graph, session)
    print(f"\nwritten to {config['uri']} as build {stamp}")


if __name__ == "__main__":
    main()
