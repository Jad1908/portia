"""The knowledge graph — what the project's tables and columns are *to each other*.

`docs/KNOWLEDGE_GRAPH.md` is the design. The gap it closes is stated as four
facts about portia's code rather than about any model's behaviour (§1): nothing
holds a relationship *between* two sources, `join_report`'s answer dies with the
turn, the catalog is one file per source, and L1 is an exhaustive index pushed
into every prompt.

**Three modules, and the split is the point:**

- `schema.py` — the vocabulary and a `Graph` of nodes and edges. Pure: no Neo4j
  import, no filesystem, nothing to run. The same shape as `ui/state.py` not
  importing NiceGUI — the thing worth testing shouldn't need the thing that is
  awkward to start.
- `build.py` — the project, read into a `Graph`. Catalog and specs only; it runs
  no data and needs no connection.
- `store.py` — the Neo4j half, behind the ``graph`` extra (§6.6).

**What is here is phase A of §9.4**: the structural half — sources, models,
columns, groups, `READS` and column-level `DERIVES_FROM` — all of which is a
restatement of files (§4.2) and costs nothing to compute. The measured half
(`OVERLAPS`) and the agent's read tool are phases B and C, and the writer is
built so a rebuild of this half cannot delete them.
"""

from pathlib import Path

from portia.knowledge.build import BuildResult, build_graph
from portia.knowledge.schema import Edge, Graph, Node

__all__ = ["BuildResult", "Edge", "Graph", "Node", "build_graph", "refresh_fingerprints", "sync"]


def sync(
    root: str | Path = ".",
    *,
    portia_dir: str | Path | None = None,
    result: BuildResult | None = None,
) -> BuildResult:
    """Read the project and put its structural half in the store, in one call.

    The write moment §5 asks for: after indexing, after a recorded step, on
    Refresh. A measurement no longer calls it first; it rebuilds only when a
    column it measured is not in the graph yet (`handlers._write_attached`),
    because on a wide project the rewrite is every column of every table.

    Raises `store.GraphUnavailable` if the database is down. **Callers on the
    indexing path must catch that and carry on** — an index that fails because a
    container is stopped is exactly the leak §6.6 warns about.

    ``result`` is a build the caller already made, when it needs the build
    whether or not the write happens (`handlers._sync_graph`).
    """
    from portia.knowledge import store

    result = result or build_graph(root, portia_dir=portia_dir)
    with store.session() as live:
        store.write(result.graph, live)
    return result


def refresh_fingerprints(
    root: str | Path, session: object, *, portia_dir: str | Path | None = None
) -> None:
    """Set every table's fingerprint in the store to what it is now, before a read.

    What makes a read's ``stale`` true of the files as they are, not as they
    were at the last rebuild (`store.fingerprint_writes`). Stat calls, the
    specs and one write; no column is touched.
    """
    from portia import catalog, pipeline
    from portia.knowledge import store
    from portia.knowledge.schema import MODEL, SOURCE, project_id

    root = Path(root)
    now = pipeline.table_fingerprints(root, portia_dir or root / catalog.DEFAULT_DIR)
    store.write_fingerprints({SOURCE: now.sources, MODEL: now.models}, session, project_id(root))
