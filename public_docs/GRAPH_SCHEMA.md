# The knowledge graph — schema reference

*What is actually in Neo4j: every node label, every property, every edge kind, and where each
value comes from. This is the **reference** — what you will find if you open the browser and look.*

*Kept honest by `tests/test_graph_schema_doc.py`, which fails if `knowledge/schema.py` gains a
label or an edge kind this file does not mention.*

Open it at <http://localhost:7474> (`neo4j` / whatever `NEO4J_PASSWORD` is).

---

## The shape, in one line each

```
(:Source)-[:HAS_COLUMN]->(:Column)          a file that arrived, and its columns
(:Model) -[:HAS_COLUMN]->(:Column)          a table portia built, and its columns
(:Source)-[:IN_GROUP]->(:Group)             someone said these belong together
(:Model) -[:READS]->(:Source|:Model)        this model's spec declares that table as an input
(:Column)-[:DERIVES_FROM]->(:Column)        these values came from those        ← structural
(:Column)-[:OVERLAPS]->(:Column)            these two were compared             ← measured
```

Six kinds and no more. The vocabulary is **closed**: you cannot write a Cypher pattern without
knowing which relationship names exist, so new ones arrive by editing `knowledge/schema.py` in a
diff, never at runtime.

---

## `project` — on every node and every edge

**One Neo4j server holds every portia project on the machine**, so everything in here carries a
`project`: the project root's absolute path, from `schema.project_id()`. Every `MERGE` matches on
it, every query filters on it, and the prune is scoped to it.

That is not a stylistic choice. Neo4j **Community allows exactly one user database per server**
(which is why `docker-compose.yml` runs a *second server* for the tests rather than a second
database), so "a database per project" would mean a JVM, a volume and a port pair per project — to
hold a few thousand nodes. A tenant property is the standard shape for many small tenants in one
graph, and this is it.

Two things went wrong before it existed, and both are worth knowing because neither looked like a
graph bug:

- **A node key is project-*relative*** — a `:Source` is keyed by `data/orders.csv`. Two projects
  that each had one silently became **one node** holding both projects' columns.
- **The prune deletes what a build does *not* restate**, and named no project — so indexing one
  project deleted **every structural edge of every other project on the server**. What was left was
  the other projects' columns with no table attached, still wired to each other by `OVERLAPS` edges
  the prune is forbidden to touch: floating columns, connected to nothing.

Graph data written before this property exists is unattributable — nothing on it says which project
it was. `python -m portia.cli.knowledge --forget-unscoped` deletes it, and it is a flag rather than
something a build does for exactly that reason.

---

## Nodes

Every node is identified by **one** property, **within its project**. That pair is what an
incremental write merges on, so two writers disagreeing about it produces two nodes for one thing —
`store.constraint_statements()` puts a composite uniqueness constraint on `(project, key)` for each
label.

### `:Source` — a data file that arrived from outside portia

Identified by **`path`**.

| property | type | where it comes from | absent when |
|---|---|---|---|
| `path` | string | `catalog.source_ref` — relative to the project root | never (it is the key) |
| `name` | string | the catalog's key for it, i.e. the filename stem | the spec names a file nobody indexed |
| `summary` | string | `set_interpretation` — **the agent's prose read** | nobody has read it yet (see below) |
| `size` | int | `catalog.STALENESS_FACTS` | never indexed |
| `mtime` | float | `catalog.STALENESS_FACTS` | never indexed |
| `fingerprint` | string | `pipeline.table_fingerprints` — `"{size}:{mtime}"` of the file **as it is now**, `"gone"` when it is gone; a warehouse table's catalog facts | never indexed |
| `_build` | string | portia's bookkeeping; see *Rebuilds* | never |

**`summary` is absent unless a person or the copilot actually wrote one.** `catalog._auto_summary`
drafts a restatement of the profile so the YAML is never empty; that draft is deliberately *not*
carried here, because in a prose slot it is indistinguishable from a read of the data. **Absent
means nobody has read this source**, which is a fact worth being able to query:

```cypher
MATCH (s:Source) WHERE s.summary IS NULL RETURN s.name    // not yet interpreted
```

### `:Model` — a table portia built

Identified by **`name`** — the spec's filename, which `spec.discover_specs` already requires to be
unique across a project.

| property | type | where it comes from |
|---|---|---|
| `name` | string | the spec's filename stem |
| `spec` | string | the spec's path, relative to the project root |
| `fingerprint` | string | `pipeline.table_fingerprints` — a digest of its spec (`pipeline.fingerprint`) and the fingerprints of every table it reads; absent when one of them cannot tell |
| `lineage_unread` | string | `knowledge.build` — why its column lineage could not be read, as a code from `build.UNREAD_CODES`; absent when it was read |
| `lineage_unread_step` | string | `knowledge.build` — the step the lineage stopped at; absent with `lineage_unread`, and when the spec has no steps |
| `_build` | string | see *Rebuilds* |

**`spec` and `fingerprint` are pointers into the pipeline, and none of the pipeline's vocabulary is
here.** There is no `layer`: staging/intermediate/mart groups and orders the project canvas, and
says nothing about what a table is *to* another table, which is the only question this graph
answers.

### `:Column` — one named column of exactly one table

Identified by **`key`**, built by `schema.column_key` as `"{source|model}:{table key}::{column}"` —
for example `source:data/orders.csv::customer_id` or `model:mart_orders::note_x`.

| property | type | where it comes from | absent when |
|---|---|---|---|
| `key` | string | `column_key(...)` | never (it is the key) |
| `name` | string | the column's own name | never |
| `table` | string | the parent's key, for reading; the parent is reachable by `HAS_COLUMN` | never |
| `position` | int | `knowledge.build` — its place in its table from 1: the catalog's column order for a file, the output order for a model | the graph was built before 2026-10-08 |
| `role` | string | `set_interpretation` — **the agent's judgment** | nobody assigned one |
| `inferred` | string | `checks.profiling` | the model has not been built |
| `null_rate` | float | `checks.profiling` | the model has not been built |
| `n_distinct` | int | `checks.profiling` | the model has not been built |
| `flags` | list of string | `checks.profiling` | the model has not been built |
| `min`, `max` | number | `checks.profiling` | the column is not numeric, or the model has not been built |
| `top`, `top_freq` | value, int | `checks.profiling` | the column is numeric or boolean, or the model has not been built |
| `derivation` | `"unknown"` or `"not_traced"` | `knowledge.build` — see below | almost always; only a model's column ever carries it |
| `_build` | string | see *Rebuilds* | never |

**The shape facts are split by kind and you never get both halves** (`schema.SHAPE_FACTS`). A
numeric column carries its range; everything else carries its commonest value and how often that
value occurs. `checks.profiling._table_column` already makes exactly this split when it measures, and the
graph restates it rather than re-deriving it. Absent means *not applicable to this kind*, not
*unmeasured*.

**`position` is the only order the picture uses for a wide table's columns.** It is structural,
restated from the same files as `HAS_COLUMN`, so the order is a fact about the table and never a
measurement. Absent on an older graph, where the picture falls back to the key.

A modal value on a Column node is not the samples kept off an `OVERLAPS` **edge**. That rule
stops a *measurement* carrying a copy of the data it measured. This is one value describing the
column it sits on, the same restatement of the catalog as `null_rate` beside it — and it is there
because pair-picking is the agent's job and it used to be done from names and roles alone.

**A column belongs to one table and is never shared.** Two tables that both have `customer_id`
produce **two** nodes. Whether they are the same thing is exactly what `OVERLAPS` exists to answer,
so merging them would assume the answer to the question the graph is being built to ask.

**A model's columns carry facts once it is built** (since 2026-09-01: `catalog.index_model`
profiles the table a build or a recorded step produced). Before that, nothing has profiled the
table portia *would* build, and inventing numbers for it would be the one thing this project
forbids.

**`derivation` says why a model column's trail stops, and it has two values.** `"unknown"`: the
step was read, and nothing is underneath the value — a `count(*)`, a literal, anything a `sql` step
produced with no input column underneath it. `"not_traced"`: the step could not be read at all, so
the column may be carried unchanged from an input and nobody can say which; its model says why in
`lineage_unread`. The marker exists because *no outgoing `DERIVES_FROM`* already means something
else: on a file's column it means *this is where the data came from*, and without the marker a
computed column has the identical shape. A closed pair of values, never a vocabulary of reasons:
the reason is a code on the model, and the parser's own words stay in the build report. To ask for
the honest terminal columns:

```cypher
MATCH (c:Column {name: 'amount'})-[:DERIVES_FROM*]->(o:Column)
WHERE NOT (o)-[:DERIVES_FROM]->() AND o.derivation IS NULL
RETURN o.key
```

### `:Group` — a set of sources someone said belong together

Identified by **`name`**.

| property | type | where it comes from |
|---|---|---|
| `name` | string | `catalog.set_group` |
| `context` | string | the prose saying why they belong together |
| `_build` | string | see *Rebuilds* |

### `:Entity` — **does not exist**

Deferred on purpose. It is the one node kind with nothing underneath
it — every other one is read off a file or measured — and the one whose names diverge by default,
because there is no list to read before coining one. `customer`, `Customer`, `client` and `guest`
becoming four nodes for one thing would leave the picture worse than not having it.

---

## Edges

### Structural — a restatement of files, and free

These are recomputed from the catalog and the specs every time the graph is written. **A rebuild
owns them**: one that is no longer true is deleted.

| kind | from → to | properties |
|---|---|---|
| `HAS_COLUMN` | `Source`/`Model` → `Column` | `_build` |
| `IN_GROUP` | `Source` → `Group` | `_build` |
| `READS` | `Model` → `Source`/`Model` | `_build` |
| `DERIVES_FROM` | `Column` → `Column` | `via`, `step`, `_build` |

**`DERIVES_FROM` points backwards** — output column → the input column(s) its values came from.
Count a column's outgoing edges and the shape is free: one is a rename or a transform, several is a
composite (a shared join key gets two, one per side).

- **`via`** — which op, from the closed set `join` · `normalize` · `sql`. A constant in `ops/`, so
  this vocabulary cannot grow without a code change.
- **`step`** — a pointer, `"<spec>#<step id>"`, e.g. `mart_orders#clean`. **A pointer, never a
  copy**: the spec is the one place that says what a step did, and following it is what tells you
  which columns that step transformed.

A spec has several steps and the edge has one pointer, so the chain is compressed by a rule: a step
that **changes the values** (a `normalize` transform) outranks one that only **renames** (a join's
`_x`/`_y` suffix), which outranks one that merely **carries** the column; ties go to the later step,
and a column no step ever claimed keeps the step that first read it in.

**A `sql` step is read by a parser, and the rank comes off the parse tree.** That op declares table
names and nothing about columns, so `knowledge/sqllineage.py` reads them out of the SQL text using
the input columns the build already holds — through CTEs, sub-selects, `*` and joins. A value that
travelled as a bare column reference the whole way was *carried* (or *renamed*, if it arrived under
another name); a function, an operator, a cast or a `CASE` anywhere in the path makes it *changed*.
Aggregates and windows get no rank of their own — `step` already points at the one place that says
what the step did.

It needs `sqlglot`, from the `graph` extra. **Without it the step is unresolved**, and so is any
model downstream: `Model` node, true `READS` edges, no `Column` nodes. Same when the SQL cannot be
parsed or cannot be resolved against its inputs — **unless the model has been built from the spec
as it stands** (2026-10-14): then its columns come from its catalog entry, with their facts and
`derivation: "not_traced"`, so a join or an overlap can attach to them (an `UNPIVOT` staging step
is the case that found it). Lineage is still reported missing, and the model carries
`lineage_unread`. A `PIVOT`, an `UNPIVOT` or a `COLUMNS(...)` anywhere in the statement is refused
before it is traced: sqlglot reads the standard `UNPIVOT` and reports its name column as copied
from the unpivoted columns' values, which is wrong rather than missing. `python -m portia.cli.knowledge` prints what could
not be read and why — per model when the whole spec stalled, per column when only one did.

### Measured — costs a query, and is nobody's to delete

| kind | from → to | properties |
|---|---|---|
| `OVERLAPS` | `Column` → `Column` | `n_shared_values`, `left_coverage`, `right_coverage`, `comparable_types`, `left_repeats`, `right_repeats`, `asked_because`, `measured_at`, `left_fingerprint`, `right_fingerprint`, `origin` |
| `JOINED` | `Source`/`Model` → `Source`/`Model` | `left_columns`, `right_columns`, `relationship`, `n_shared_keys`, `left_coverage`, `right_coverage`, `left_repeats`, `right_repeats`, `comparable_types`, `asked_because`, `origin`, `found_in`, `measured_at`, `left_fingerprint`, `right_fingerprint` |

**Two kinds of record, told apart by what made them.** A *comparison* (`OVERLAPS`) says two columns
share values; the copilot asked because it thought they might be the same thing. A *join*
(`JOINED`) is a join that was actually done, between two tables, on one or more columns a side.

An `OVERLAPS` edge is written by `measure_overlaps`, which the copilot calls with pairs **it**
chose, with its reason. Between 2026-10-13 and 2026-10-15 `join_findings` wrote its key as one too,
`origin: join_check`; those stay, as comparisons. A join check is now a `JOINED` record.

| property | means |
|---|---|
| `n_shared_values` | how many distinct values appear on both sides |
| `left_coverage` | share of the **start** node's rows whose value exists on the other side |
| `right_coverage` | share of the **end** node's rows whose value exists on the other side |
| `comparable_types` | `false` means the two columns are different types and can never match, whatever the values say — a different problem from a genuine zero |
| `left_repeats` / `right_repeats` | the most times one value appears on that side; 1 means every value appears once. What tells a key from a shared axis: two hourly tables cover each other's timestamps fully, and only the repeats say one holds each hour once per site. Absent on an edge measured before 2026-10-15 |
| `asked_because` | **the agent's own sentence**, saying why it thought the pair was worth comparing |
| `measured_at` | when |
| `left_fingerprint` / `right_fingerprint` | what each end looked like at the time — see *Staleness* |
| `origin` | which tool measured it: `measured` (`measure_overlaps`) or `join_check` (`join_findings`); an edge with none was written by `measure_overlaps` before 2026-10-13 |

**Direction is load-bearing** and is the only thing that says which coverage is which. Overlap is
not symmetric: 98% of orders' customer ids may exist in customers while only 40% of customers appear
in orders, and both are true.

**One edge per pair of columns per `origin`.** Measuring a pair again with the same tool replaces
that tool's edge between the two columns, whichever way round either was taken; a measurement by the
other tool stays beside it, so a join check never erases a comparison the copilot chose.

#### `JOINED` — a join that was done

Between two **tables**, because a key of two columns is no pair of single columns. Written when a
join check runs (`join_findings`), when a step that joins two tables is recorded (`record_step`),
and when a finding is kept whose queries join two tables (`record_finding`). Each is a join the
copilot chose to do; code never chooses one. Measured with `checks.join.join_report`.

| property | means |
|---|---|
| `left_columns` / `right_columns` | the key columns on each side, in the order they are matched |
| `relationship` | `1:1`, `1:many`, `many:1` or `many:many`, from whether each side is unique on its key |
| `n_shared_keys` | how many distinct keys appear on both sides |
| `left_coverage` / `right_coverage` | share of that side's rows whose key is found on the other |
| `left_repeats` / `right_repeats` | the most rows one key has on that side; 1 means the side is one row per key |
| `comparable_types` | `false` when the keys' types can never match |
| `asked_because` | **the copilot's own words** for why it joined: a join check's reason, a step's rationale, a finding's question |
| `origin` | `join_check`, `step` or `finding` |
| `found_in` | the `<spec>#<step>` that joined, or the finding file whose query did; absent for a join check |
| `measured_at`, `left_fingerprint`, `right_fingerprint` | as on `OVERLAPS` |

**One record per pair of tables, per key, per `origin`.** The same tables joined on another key are
another join; the same join made again the same way, from either end, replaces the record before.

**A measured zero is written, and it does not mean "unrelated".** `France` against `FRA` shares no
values and the zero is correct; those two columns are the same thing after a mapping. That is why
`asked_because` is required in code — without the hypothesis attached, the most important pair in a
messy project reads as a dead end. **An absent edge means nobody measured**, and nothing else.

**Samples are not on the edge.** Seeing `France, Germany` beside `FRA, DEU` is what makes a zero
investigable, and that is what the disclosure ladder is for: the edge says which pair and why, and
the agent climbs to `profile_source` for values. Copying them here would put data in a metadata
store and create a second copy to go stale.

---

## Staleness

Nothing invalidates anything when a file changes. Every node carries a `fingerprint`, every measured
edge carries the two it was taken against, and **the comparison happens when someone reads**:

```cypher
MATCH (a:Column)<-[:HAS_COLUMN]-(ta), (b:Column)<-[:HAS_COLUMN]-(tb)
MATCH (a)-[r:OVERLAPS]->(b)
RETURN a.name, b.name, r.n_shared_values,
       r.left_fingerprint <> ta.fingerprint OR r.right_fingerprint <> tb.fingerprint AS stale
```

**The tables' fingerprints are what they are now.** A source's is its file's size and mtime off a
stat, not the catalog's record of them, so a file edited and never re-indexed has moved. A model's
folds its spec with what it reads, so a model rebuilt over a changed file has moved though its spec
has not. `graph_lookup` sets every table's fingerprint to its current value before it reads
(`store.fingerprint_writes`), so `stale` is about the files as they are, not as they were at the last
rebuild.

**Marked, never deleted.** A deleted edge is indistinguishable from one nobody ever measured, which
is the ambiguity the whole design exists to remove. `graph_lookup` returns `stale` on every overlap
it reports.

---

## Rebuilds, and what a rebuild may destroy

Every node and every **structural** edge carries `_build`, the id of the write that produced it.
`store.write` merges the new graph in, then deletes structural edges carrying an older stamp — they
restate files, and the files no longer say them.

**A rebuild is one transaction, behind its project's lock.** It first sets a property on a
`PortiaLock` node keyed by the project (`lock`, never `project`, so no read of a project's nodes
meets it), which holds Neo4j's write lock until the rebuild commits. Two rebuilds of one project
take turns; before this, one rebuild's prune could delete the edges another had just written.

**`OVERLAPS` is never touched.** It cost a query, no file restates it, and Neo4j is a store rather
than a cache. A `Column` that still carries one survives even after the catalog stops listing it, so
a measurement never silently loses the thing it was about.

Nodes are removed only when they have **no relationships at all** and an old stamp.

**A build that found nothing prunes nothing.** An empty graph is what `build_graph` returns when the
catalog cannot be read — a deleted `.portia`, a mistyped `--root`, a project half-way through being
set up — and none of those are the assertion *this project has no tables*. Pruning against one
deleted the project's whole structural half and left its measurements hanging off orphan columns,
which is a lot of damage for a wrong path, and unrecoverable in the one way that matters: a
measurement is the only thing in here no file can restate.

Both of these are scoped to **one project** — see the top of this file.

---

## Who writes it, and when

| trigger | writes | fails how |
|---|---|---|
| `python -m portia.cli.knowledge --write` | the structural half | exits with the reason |
| **Refresh** on the knowledge pane | the structural half | a toast, pane stays usable |
| indexing a source (window or `cli.index`) | the structural half | one line; the catalog is written regardless |
| `record_step` | the structural half, so a table appears as it is built | reported in the result; the step is already on disk |
| `measure_overlaps` | structural first, then the measurements not already stored and fresh | numbers still returned, `stored: false` |
| `join_findings` | its key measurement, when the join is on one column each side between two graph tables | the join's answer stands; `graph.kept: false` says why |

Every one of them is **best-effort**. A stopped container is never allowed to cost you work you had
already done.

---

## Recipes

**Every one of these is unscoped, and the browser holds every project.** Add
`WHERE n.project = '/abs/path/to/project'` (or `{project: '…'}` in the pattern) to ask about one —
the shape `knowledge/query.py` uses for the same reason.

```cypher
// which projects are in here at all
MATCH (n) RETURN n.project AS project, count(*) ORDER BY project

// everything, for one project
MATCH (n {project: $project})-[r]->(m {project: $project}) RETURN n, r, m

// what is in here
MATCH (n) RETURN n.project AS project, labels(n)[0] AS kind, count(*) ORDER BY project, kind

// where did this built column come from, all the way down
MATCH p = (:Model {name: 'mart_orders'})-[:HAS_COLUMN]->(:Column {name: 'amount'})
          -[:DERIVES_FROM*]->(:Column)
RETURN p

// what would break if this file changed
MATCH (:Source {name: 'orders'})-[:HAS_COLUMN]->(:Column)<-[:DERIVES_FROM*]-(c:Column)
MATCH (t)-[:HAS_COLUMN]->(c) RETURN DISTINCT t.name, c.name

// every measured pair, with the reason it was asked for
MATCH (a:Column)-[r:OVERLAPS]->(b:Column)
RETURN a.key, b.key, r.n_shared_values, r.left_coverage, r.right_coverage, r.asked_because

// the zeros worth acting on — measured, comparable, and someone had a reason
MATCH (a:Column)-[r:OVERLAPS]->(b:Column)
WHERE r.n_shared_values = 0 AND r.comparable_types
RETURN a.key, b.key, r.asked_because

// sources nobody has interpreted
MATCH (s:Source) WHERE s.summary IS NULL RETURN s.name

// which sources share a group
MATCH (s:Source)-[:IN_GROUP]->(g:Group) RETURN g.name, g.context, collect(s.name)
```

**Nothing in the graph is ranked, and no query here sorts by a number.** The engine refuses to
decide which overlap matters; a `ORDER BY r.left_coverage DESC` in a tool would be that decision
made in code, which is the line portia draws between facts and judgment.
