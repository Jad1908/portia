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
    parser.add_argument("--table", help="ask the stored graph about one table instead of building")
    parser.add_argument("--column", help="with --table: that column's lineage")
    args = parser.parse_args()

    if args.table:
        _lookup(args.table, args.column)
        return

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


def _lookup(table: str, column: str | None) -> None:
    """The read path, from a terminal — the same queries the copilot's tool runs.

    A play surface, in the sense `CLI` always means here: it must go through
    `knowledge.query` rather than writing Cypher of its own, or the window, the
    copilot and the terminal end up with three opinions about one graph.
    """
    from portia.knowledge import query, store

    try:
        with store.session() as session:
            print(query.render_text(query.lookup(session, table, column)))
    except store.GraphUnavailable as exc:
        raise SystemExit(str(exc)) from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


def _write(graph) -> None:
    """Send it. The failure to say clearly is "the database isn't running"."""
    from portia.knowledge import store

    config = store.settings()
    try:
        with store.session() as session:
            stamp = store.write(graph, session)
    except store.GraphUnavailable as exc:
        raise SystemExit(str(exc)) from None
    print(f"\nwritten to {config['uri']} as build {stamp}")


if __name__ == "__main__":
    main()
