# The knowledge graph — design

*Designed 2026-08-04, revised the same day after the design was talked through.
**Phase A is built** — see the status note below. This document is the whole of the decision,
written so a new session can pick it up cold.*

> ## Status — phase A shipped, 2026-08-04
>
> `portia/knowledge/` (`schema.py` · `build.py` · `store.py`), `python -m portia.cli.knowledge`,
> a `graph` extra and a `docker-compose.yml`. **The write path only** (§9.4 phase A): sources,
> models, columns, groups, `READS` and column-level `DERIVES_FROM`, all read off the catalog and
> the specs. No agent, no tool, no measurement — B, C and D are untouched.
>
> **Four things the build settled, which the design did not say:**
>
> - **The lineage rank.** §4.2 gives `DERIVES_FROM` a single `via` + `step`, which assumes one hop;
>   a spec has several. The rule is now: a step that *changes the values* (a `normalize` transform)
>   outranks one that only *renames* (a join's `_x`/`_y`), which outranks one that merely *carries*
>   the column, ties to the later step. A first draft scored a shared key's `coalesce(l.k, r.k)` as
>   a change on both sides, and that was wrong — it picks a value rather than changing one, and
>   scoring it that way buried the transform that had made the key match in the first place. The
>   composite shape stays free from the edge count, exactly as §4.2 says.
> - **The `sql` hatch's cost is now countable.** A model downstream of a `sql` step gets its Model
>   node and its true `READS` edges and **no** Column nodes, and is named in
>   `BuildResult.unresolved` with the reason. That dict is the evidence §7 asks for before buying
>   `sqlglot`.
> - **A rebuild owns the structural half and nothing else.** Everything written carries the build
>   that wrote it; stale *structural* edges are deleted, `OVERLAPS` is never touched, and a node
>   that still carries one survives even after the catalog drops the column. Phase C writes those
>   edges; phase A had to be unable to take them away, and that was cheaper to honour now than to
>   retrofit onto a writer that already deletes freely.
> - **Nothing is hooked into indexing or `record_step` yet**, though §5 names those as the write
>   moments. Doing it now would make an index depend on a container being up, which is the leak
>   §6.6 warns about, and nothing reads the graph until phase B.
>
> Lineage was checked end to end on a two-spec project: `mart_orders.amount` →
> `stg_orders.amount` → `data/orders.csv:amount`, with the pointer on each hop. **The Neo4j writer
> is not yet verified against a live server** — its test skips without one.

*What the revision changed, because it moved the centre of the design: column-level **lineage** is
now half of what the graph is for (§2, §4.2) and a model's output columns are nodes (§4.1) · **the
agent chooses which pairs get measured**, during indexing, which replaced both the blanket sweep and
pure opportunistic capture (§5.1, with §6.5 keeping the sweep design and why it failed) · a measured
zero is **not** evidence of no relationship, and the `France`/`FRA` case is why — so **the edge
carries the reason it was asked for** (§4.4) · edges record what they were measured against, so a
stale measurement is detectable (§4.5) · **Entity is deferred** and §4.8 says what separates it from
Group · and **§9 is new**, on what all of this does to the agent's loop, which §1–§8 did not say.*

*Start at the vocabulary table under §4 if you are new to this document. Then §9.*

*It is a design, not a plan. It says what we want, what we chose, and what to watch out for. It does
not say how to implement anything — per `PLAN.md`, plans stay directional.*

---

## 1. Why

### 1.1 Nothing tells the copilot which pairs of tables can plausibly join

The only thing in portia's context that suggests a relationship between two sources is a **column
name**. Names are all the agent has to go on before it measures, and names are exactly what lies:
a column called `LOCATION` in the PHQ data holds `PRIME LOCATION` / `SECONDARY LOCATION` — a
site-quality grade, four distinct values, 51% null. Nothing portia holds says that, and nothing
portia holds could have said which of 23 sources it does or does not connect to.

This is a gap in the infrastructure, and it is visible without running anything: there is no
artifact, anywhere in the project, that answers *"which columns in this project share values with
this one."*

> *An earlier draft argued this section from a specific failing run. That run held every prompt at
> its first draft and was not designed to separate a prompt problem from a missing-context problem,
> so it could not carry the argument — see `EVALUATION.md`'s status note. The gap above is a
> property of the code and needs no run to establish it.*

### 1.2 We measure relationships and throw them away

`checks/join.py` measures exactly the missing fact. `join_report` computes key overlap between two
tables as aggregates over one full outer join of the key multiplicities. It is fast and it is real.

Then the turn ends and the answer is gone. Nothing accumulates. The next turn asks the same question
from scratch, or worse, doesn't ask it and guesses again.

### 1.3 Nothing in portia holds relationships *between* sources

The catalog is one file per source. It records what a source *is*. It has no shape for what a source
*relates to*. Groups are the closest thing, and they are asserted membership, not measurement — and
`BACKLOG.md` notes nothing downstream consumes them.

`BACKLOG.md` already has this as **"broad how-sources-interact model — likely joins / relationships
across sources (the context-aware end goal). Deferred as too early."** This document is that item,
picked back up.

### 1.4 The context ladder doesn't scale

L1 is pushed into every system prompt and it is exhaustive — one line per source, ~30 tokens each.
`BACKLOG.md` flags it: fine at 3 sources, unproven at 50.

An exhaustive index is the wrong shape at scale. What you want is a neighbourhood: start where you
are, walk outward, stop when you have enough. That is a graph traversal, not a document read.

---

## 2. What we want it to do

Six things, roughly in order of value.

1. **Answer "how do I join A and B" from measurement rather than from column names.** Find every
   measured overlap between a column of A and a column of B, with its numbers.

2. **Answer multi-hop questions.** *A doesn't touch C directly, but A connects to B and B connects
   to C.* This is the single strongest argument for the graph existing. It is one line of Cypher and
   it is genuinely painful in SQL.

3. **Say where a column came from, at column granularity.** This model column is the same column as
   that source column, renamed · this one is that one after a transform · this one is computed from
   three others. Portia knows all of it — a spec records the op, the keys and the transform — and
   **nothing in portia can currently answer it**, at any surface. §4.2's `DERIVES_FROM`.

4. **Let the agent traverse instead of reading everything.** A rung on the context ladder between L1
   and L2: *what connects to this source, and how strongly.*

5. **Accumulate.** Every measurement the copilot makes lands somewhere and is still there next turn.
   The graph grows where the attention went.

6. **Collapse columns into entities.** 23 sources × ~30 columns is ~700 columns but maybe 15 real
   things — a customer, a hotel, a booking, a date. One asserted relationship (*this column
   represents that entity*) makes the whole picture legible.

**Where 1 and 3 meet is the point of the whole thing.** Overlap alone is weak on raw data, because
unharmonized columns are precisely the ones that don't share values yet (§4.4). Lineage alone
restates the pipeline. Together they show the *path*: this raw column becomes that model column via
this step, and the model column overlaps that other source — so here is how you get from a mess to a
join. That path is the product.

**What it is not for.** It is not a decision layer. See §6.1.

---

## 3. The stack

### 3.1 Neo4j

Property graph database. Cypher query language. Runs as a server — Docker locally, or their hosted
free tier with nothing running on your machine.

### 3.2 What we rejected, and why

**NetworkX** — a Python library, in memory, no server. Rejected because **the agent could not write
its own queries.** With NetworkX you expose a fixed set of Python functions, so the agent can only
ask questions we anticipated. Writing arbitrary Python is not an option: `agent/session.py`
deliberately gives the model no way to run code.

*Note for anyone re-litigating this:* the usual argument against NetworkX is that it won't scale.
That argument is wrong and shouldn't be used. **The graph's size is schema-shaped, not data-shaped.**
4.8 GB is 23 sources; 48 GB is still 23 sources. A large warehouse at 500 tables and 15,000 columns
is still small for an in-memory graph. NetworkX was rejected for the query language, nothing else.

**DuckDB with node and edge tables** — we already run DuckDB, so this was tempting. Rejected because
multi-hop questions need recursive SQL, which is hard for a person to write and worse for a model to
write correctly. The thing we most want a graph for is the thing SQL is worst at.

**Kuzu** — would have been ideal: embedded, pip-installable, no server, Cypher. **It is dead.** The
GitHub repository was archived on 10 October 2025 after Apple acquired Kùzu Inc.; 0.11.3 was the
final release. Community forks exist (`bighorn`) but are too young to depend on. Check this before
assuming the landscape is unchanged — an embedded Cypher database is exactly what this design wants,
and if a credible one appears it would remove the server cost entirely.

**RDF / SPARQL** (rdflib, Oxigraph) — meets the checkboxes: embedded, real query language,
persistent. Rejected because **edge properties are our core need and RDF is worst at them.** Our most
valuable relationship carries numbers (coverage, key count, when it was measured). In RDF that needs
reification or RDF-star. Property graphs exist precisely because edge properties matter.

### 3.3 Why a server is an acceptable cost

Portia today is `python -m portia.ui` and nothing else. Neo4j breaks that. That is a real loss and it
was weighed.

The trade is **a one-time setup cost against a permanent maintenance cost.** Running a container is
paid once and does not grow. Building a graph engine, a query layer and a graph UI ourselves is owned
forever — every bug, every missing feature. For a solo project on a tight budget, taking the setup
cost is the right side of that trade.

### 3.4 Why the mainstream option specifically

**The agent writes the Cypher.** Claude knows Neo4j's Cypher far better than any alternative graph
language, because there is far more of it in the world. When the model holds the pen, popularity is a
technical property, not a fashion.

### 3.5 Known costs, accepted

- Portia stops being one command. Users need the database running.
- The app must behave sensibly when the database is down. This is new behaviour it does not have.
- Neo4j Community is GPL licensed. Fine to use. It matters only if portia ever ships with a database
  bundled inside it. Not a problem to solve now.

---

## 4. The schema

### The vocabulary, defined

The rest of §4 argues about these. This is what each one *is* — and what **identifies** it, which
matters more than it looks: a graph written incrementally merges on that key, so an identifier that
two sessions disagree about produces two nodes for one thing (§4.8).

| Node | What it is | Identified by | Carries |
|---|---|---|---|
| **Source** | A data file that arrived from outside portia and has been indexed. One per catalog entry. | Path relative to the project root | Prose summary, file fingerprint (`size`/`mtime`), candidate keys |
| **Model** | A table portia built. One spec → one table → one node. | Spec name | Optional `layer`, spec fingerprint |
| **Column** | One named column belonging to exactly one Source or one Model. | (parent node, column name) | Type, null rate, distinct count, min/max, role |
| **Group** | A set of sources someone said belong together, with prose saying why. | Name | The group's `context` string |
| **Entity** | A real-world thing the data is about — a customer, a hotel, a booking. | Name, agent-invented | — |

**Column is the definition that matters.** A column belongs to exactly one table and is never shared.
Two tables that both have `customer_id` produce **two** Column nodes. Whether they are the same thing
is exactly what `OVERLAPS` and `REPRESENTS` exist to answer, so merging them at the node level would
assume the answer to the question the graph is being built to ask.

**Source vs Model is the arrived/built distinction** the app's canvas already draws. It matters here
because the two go stale for different reasons (§4.5) and are trustworthy for different things
(§4.1, §4.4).

| Edge | Direction | Means | Origin |
|---|---|---|---|
| `HAS_COLUMN` | Source/Model → Column | This table has this column | Structural |
| `IN_GROUP` | Source → Group | This source was placed in this group | Structural (restating an assertion) |
| `READS` | Model → Source/Model | This model's spec declares that table as an input | Structural |
| `DERIVES_FROM` | Column → Column | This column's values were produced from that one | Structural |
| `OVERLAPS` | Column — Column | These two were compared; here is how far their values coincide | Measured |
| `REPRESENTS` | Column → Entity | This column identifies or describes that entity | Asserted |

Three where the shape is not obvious:

- **`DERIVES_FROM` points backwards**, output column → input column(s). Count a column's outgoing
  edges and you have the shape for free: one is a rename or a transform, several is a composite.
- **`READS` and `DERIVES_FROM` are the same fact at two zoom levels.** `READS` is table-level and
  already exists as the pipeline DAG; `DERIVES_FROM` is column-level and is the new capability.
- **`OVERLAPS` is one undirected edge holding two directional numbers** (§4.3).

### 4.1 Columns are nodes

Node kinds: **Source · Column · Model · Group · Entity.**

Two alternatives were considered and rejected.

**Sources as nodes, columns as properties on them.** Smaller and simpler — about 23 nodes. Rejected
because it breaks the main use case immediately. The most valuable relationship is *this column
overlaps that column*, and if columns are not nodes that relationship has nowhere to attach. You
would bury the column names inside the edge, and then "what does `customer_id` connect to" requires
inspecting every edge. It also makes entities impossible: a column cannot point at "customer" if it
is not a thing.

**Everything above, plus a summary source-to-source edge** saying "these two can be joined," so you
need not walk through columns. Rejected because it is derived from the column edges. Two things would
then state the same fact and could disagree. Derive it in the query instead.

The cost of columns-as-nodes is size: ~700 nodes at PHQ scale. That is nothing for Neo4j.

**A model's output columns are nodes too** — decided 2026-08-04, reversing what §7 used to say. The
reason §7 gave for leaving them out was that they roughly double the graph, and **that reason does
not survive §6.4**, which says in as many words not to design for volume: a few thousand nodes is
nothing here. Two things then argue for including them.

- **A model is where the data has been harmonized**, so it is the one place where measuring shared
  values is trustworthy. Country names have become codes, dates have become one format. Excluding
  model columns excludes the only columns clean enough for the measurement in §4.2 to mean what it
  says (§4.4).
- **Column lineage has nowhere to attach without them.** *This model column is that source column,
  renamed* is an edge between two columns. If one end isn't a node, the fact has no home — the same
  argument that made source columns nodes in the first place.

### 4.2 Relationships, grouped by where they come from

Six kinds. Each has exactly one origin. The grouping is deliberately `CLAUDE.md`'s facts-vs-judgment
line, because it maps onto this perfectly.

**Structural — free, a restatement of files:**

- Source `HAS_COLUMN` Column · Model `HAS_COLUMN` Column
- Source `IN_GROUP` Group
- Model `READS` Source or Model
- Column `DERIVES_FROM` Column

**Measured — a fact, carries numbers:**

- Column `OVERLAPS` Column

**Asserted — judgment:**

- Column `REPRESENTS` Entity

Keep this set small. A vocabulary that grows per run is the `BACKLOG.md` "role vocabulary" problem
happening again, one layer up.

#### `DERIVES_FROM` — why it is structural and not a new kind of judgment

It is in the free group because **the spec already says all of it.** A `normalize` step names the
column and the transform applied to it; a `join` step names both inputs and the keys. Reading that
into edges is translation, not inference — the same status as `HAS_COLUMN`.

**Two properties, and no vocabulary of transform kinds.** The obvious move is a label per shape of
derivation — *renamed · transformed · merged · computed*. Don't: that is a set of names invented per
run, which is the failure mode the paragraph above warns about. Instead:

- `via` — which op produced it, from the **closed** set `{join, normalize, sql}`. It is a constant in
  code (`ops/`), so this vocabulary physically cannot grow without a code change.
- `step` — a pointer, `<spec>#<step id>`, the form `join_findings` already uses.

Between them you get more precision than a label would, not less. The number of incoming edges says
the shape for free — one source column is a rename or a transform, several is a composite — and
`via` says which machinery did it. For the exact expression you follow `step` into the spec, which is
**the one place that says what the step does**. The graph holds a pointer, never a copy, so there is
no second statement of the transform to drift out of sync with the first.

**Table-level lineage is already built and this is not it.** `pipeline.build_project` resolves
dependency order, `ui/graph.py` draws the DAG, and `PIPELINE.md`'s optional `layer` is already the
stg/int/mart grouping. `Model READS` restates that in Neo4j so it can be traversed with everything
else. **`DERIVES_FROM` at column level is the genuinely new capability** — nothing in portia can
answer it today.

**The `sql` hatch is where this stops being free.** A `sql` step declares `inputs` as *table* names
only. `SELECT a + b AS c` means `c` derives from `a` and `b`, and nothing has parsed that. The honest
fallback is a coarser true edge — *this column came out of this step, which read these tables* —
rather than a precise guessed one. Real column lineage through arbitrary SQL needs a parser
(`sqlglot` has one); that is a later decision, in §7, not a reason to delay the other two ops.

### 4.3 Overlap is one edge carrying two numbers

Overlap is not symmetric. 98% of orders' customer ids may exist in customers while only 40% of
customers appear in orders. Both numbers matter and they are different questions.

**One edge holding both**, not two edges facing opposite ways. Two edges means two records of a single
measurement, and they will drift apart.

### 4.4 Record the zero — and know what a zero does not mean

An absent edge is ambiguous. *"We measured these two and they share nothing"* and *"we never looked"*
both appear as nothing, and they are completely different answers.

**Write the zero as an edge.** A measured zero is a real fact — *these two columns were compared and
share nothing* — and it needs no threshold to be true, which is the same reasoning that governs
`outcome.BLOCKING_FLAGS`.

**A zero means no shared values. It does not mean unrelated, and the difference is the product.**
Take `country_name` holding `France`, `Germany` and `country_code` holding `FRA`, `DEU`. The overlap
is zero and the zero is *true*. It is also the most misleading fact in the project: those two columns
describe the same thing and are joinable after a mapping.

Now notice which pairs that hits. Columns that already share values are the ones that were **already
clean**. Columns needing a transform before they match are **the harmonization work — what portia is
for**. So measuring raw values is most reliable on the data that needs portia least, and produces
confident zeros on exactly the pairs that matter most.

Three consequences, all load-bearing:

1. **The edge may only assert the narrow literal thing** — these two columns share no values. Whether
   that means *unrelated* is a judgment and belongs to the agent (§6.1).
2. **It is an argument against measuring pairs nobody asked about.** A zero from a pair the agent
   chose is informative — it checked for a reason, and the answer settles it. A zero from a blanket
   sweep is a confident-looking record that nobody has any context for. See §6.5, which used to
   recommend the sweep.
3. **Model columns matter more, not less.** A model is where the mapping has already been applied,
   so its columns are the ones where a measured overlap means what it says (§4.1).

#### What keeps a zero from reading as "unrelated": the reason it was asked for

**Settled 2026-08-04.** The edge carries **why the agent asked for the measurement**, in its own
words, beside the numbers. Compare:

> `country_name` ↔ `country_code`: overlap 0

> `country_name` ↔ `country_code`: overlap 0. *Asked because: both appear to identify countries, one
> by name and one by code.*

Same measurement. The first reads as a dead end; the second reads as a work item. Nothing about the
fact changed — what changed is that the hypothesis it was testing is still attached to it.

**This only became available once the agent picks the pairs** (§5.1). Under a code sweep there is no
reason to record, because nothing had one. That is a cost of sweeping this design did not see at
first, and it is sharper than the cost/volume arguments in §6.5.

**It bends §4.2's "each edge kind has exactly one origin", deliberately.** An `OVERLAPS` edge then
carries engine numbers *and* an agent sentence. The precedent is `spec.py`: a step holds `rationale`
(the agent's words) beside `outcome` (what was measured) and nobody confuses them. The rule that
keeps it honest is the same one — **the sentence is labelled as the agent's and may never be
generated from the numbers**, and the numbers may never be adjusted to fit the sentence.

**Samples stay off the edge** (settled 2026-08-04). Seeing `France, Germany` beside `FRA, DEU` is
what makes a zero investigable, but that is what the disclosure ladder is for: the edge says which
pair and why, and the agent climbs to `profile_source` for values. Copying samples onto the edge
would put data in a metadata store and create a second copy to go stale (§4.5). The tool description
is what has to teach the climb.

**Store what cost a query; derive the rest.** A zero that falls out of comparing two `min`/`max`
values costs microseconds and is re-derivable faster than it can be read back. Writing those down
would bury the real measurements under free ones. So: measured overlaps are edges; anything
derivable from catalog facts alone is derived at query time. Absence of an edge then means exactly
one thing — nobody measured — which is what this section is protecting.

### 4.5 An edge records what it was measured against

**Not in the original design at all, and it is the gap that shows up fastest in real use.** §5 says
how edges get written. Nothing said how they stop being true.

Four ways a measurement goes stale: a source file is re-issued by the vendor · a column is renamed or
dropped · a model's spec changes and it is rebuilt · a model is rebuilt because something upstream of
it changed.

Portia already detects the first and third, separately and for other reasons — `catalog.is_stale`
compares a source file's recorded `size`/`mtime` against the file now (`catalog.STALENESS_FACTS`),
and `pipeline.is_stale` compares a `.sql` against what its spec would produce. **So the fingerprints
already exist.** A measured edge carries the ones for both of its ends at the moment it was measured,
and "is this number still backed by the data it was taken from" becomes the comparison portia already
makes in two places, not new machinery.

**Mark stale; never delete.** Deleting returns you to the ambiguity §4.4 exists to remove — a
deleted edge is indistinguishable from one that was never measured. Marking lets the agent see the
number *and* that it was taken against a file that has since moved. It is also what the app already
does with a drifted model: it flags it, it does not remove it.

**What this unlocks, which is bigger than the invalidation itself.** Put staleness next to
`DERIVES_FROM` and you can walk *forward* from a changed source file and name exactly which model
columns are affected and which measurements are now suspect. Today `catalog.is_stale` can say a file
changed and nothing at all about what depended on it.

### 4.6 The prose summary lives as a property

The Layer 1 summary the agent writes at indexing stays prose, held as a property on the Source node.

This is worth doing on its own: Neo4j full-text indexes properties, so *"which sources mention
revenue"* becomes a real query. Today that answer is spread across twenty YAML files and nothing can
search them.

### 4.7 Do not extract edges from prose

The tempting move is to have a model read the summaries and pull relationships out of them. **Don't.**
That is where knowledge graph projects go to die — extraction is unreliable and nobody can check it.

Instead: **the agent writes prose and structured edges in the same act.** It already interprets a
source and writes a summary and column roles. At that same moment it knows the grain, the entity, the
system the data came from. Have it say those as edges too. Same judgment, two shapes, no extraction
step in between.

### 4.8 Group and Entity — the two node kinds whose names are invented

Both are named by a person or the agent rather than by something on disk, so both look like the
`BACKLOG.md` "role vocabulary" problem. **They are not the same risk, and the thing that separates
them is where the identifier comes from.**

First, note the failure being guarded against here is *not* the one §4.2 warns about. Those are two
different problems:

- **Edge kinds multiplying** breaks queries outright — you cannot write one without knowing what
  relationship names exist.
- **Node names multiplying for the same real thing** lets every query run and return a *fragmented*
  answer. Subtler, and much harder to notice.

Neither Group nor Entity risks the first: each has exactly one edge kind (`Source IN_GROUP Group`,
`Column REPRESENTS Entity`), and there is deliberately no group-to-group or entity-to-entity
relationship. The edge vocabulary stays closed. The second is where they diverge:

- **Group is convergent**, because `catalog.set_group` keeps **one shared list that the agent reads
  before it writes** (via `get_context`). A later session sees `external_events` already exists and
  joins it rather than coining `event_sources`. The graph restates the catalog; it does not invent
  the name and cannot make this worse than it already is.
- **Entity is divergent by default**, because there is no such list. `customer`, `Customer`,
  `client` and `guest` become four nodes for one thing — and that defeats the entity layer's whole
  purpose. §2 wants it to collapse ~700 columns into ~15 things; a fragmented vocabulary collapses
  them into 40 and leaves the picture **worse than not having it at all.**

**So: Group ships, Entity defers.** Two things make that safe to do in this order.

- **Entity is the only node kind with nothing underneath it.** Every other one is read off a file or
  measured. Entity is pure assertion, and §2 already ranks it last by value.
- **Nothing else depends on it.** Delete Entity and every other node and edge still works — which is
  not true of Column, or of either table kind. That is the test for whether a piece of a schema can
  wait.

**When it does ship, the fix is to make it convergent the way Group already is:** the agent must be
shown the existing entity list before it may name one, so reusing is easier than coining. Constrain
the *mechanism*, not the vocabulary — the reason not to fix a vocabulary early (§7) is unchanged.

---

## 5. How the graph gets built

**The user never touches Neo4j.** Portia fills it. Neo4j starts empty and does not discover anything
on its own.

Three kinds of material, arriving at moments that already exist in the product.

**Free — already on disk.** The catalog holds sources, columns, column facts and groups. The specs
hold which tables read which, and — per §4.2 — which column came from which, via which op. All of
that is translation, not inference: no decisions, no cost, nothing to ask anyone. This is the
skeleton, and it appears the moment a source is indexed or a spec is saved.

**Measured — costs a query.** The overlap edges. Two moments, and the first is what makes the graph
useful before anyone has asked it anything.

**Asserted — judgment.** Entities, grain, and anything else the agent decides. Lands when a source is
interpreted, which already happens.

So the write hooks onto: **index a source · save a spec · measure a join.** Each is one addition to
something that exists.

**A build command is needed regardless.** Someone cloning the repo gets the YAML from git and no
graph. There has to be a way to construct it from the project.

### 5.1 The cold start, and who chooses what gets measured

**Decided 2026-08-04, and it is the answer to the one question §7 had left open.**

A graph that only records what the copilot happened to check is empty of measurements on the first
question of a new project — which is when help is worth most. A graph that measures every pair is
§6.5's problem and, worse, §4.4's: hundreds of thousands of confident zeros nobody asked for.

**The agent chooses the pairs, during indexing.** It already reads each source, writes a summary,
assigns column roles and proposes groups. Choosing *which relationships are worth measuring* is one
more decision taken at that same moment, from that same reading — and portia then measures them and
writes the numbers. Same act, same rung, no extraction step (§4.7).

Three reasons this is the right shape, not merely a compromise between the two:

1. **It puts the guessing where this project already says guessing belongs.** *Which facts are
   material, given the goal and the domain* is the agent's job by `CLAUDE.md`'s central rule. A
   prefilter in code choosing pairs is a deterministic layer making a context-free judgment — the
   planner mistake, again (§6.1).
2. **It is the only mechanism that can catch §4.4's misleading zero.** Code comparing `min`/`max`
   sees `France`/`FRA` share nothing and drops the pair forever. An agent that has just read both
   summaries can see one holds country names and the other codes, know they are related, and know a
   mapping is needed first. That is judgment from *meaning*, which the filter does not have and
   cannot get.
3. **A zero it gets back is informative**, because it asked for a reason (§4.4).

**The cost is bounded by what the agent asks for**, which is what makes this affordable where a
sweep was not. And "the graph is empty at first" was never quite true: the structural half — every
source, every column, groups, model reads, column lineage, and the prose summaries as searchable
text (§4.6) — is free and present from the first index. What arrives with this decision is that the
*measured* half is no longer empty either.

**Still open:** whether the agent also volunteers pairs outside indexing, and whether a user-invoked
sweep exists as an escape hatch for someone who wants the graph filled without a conversation. §7.

### 5.2 Drift — considered and deliberately dropped

An earlier draft of this design had a rule: *nothing may exist only in Neo4j; every node and edge must
be a restatement of something in a file, so the graph can always be rebuilt.* It was dropped, and the
argument is kept here because it is the kind of rule that sounds prudent and gets re-proposed.

Three reasons it failed.

1. **It caps the graph at the expressiveness of YAML.** If everything in the graph must be mirrored in
   a file, the graph can never hold anything richer than the files. That defeats the purpose of having
   one.
2. **It does not solve what it claims to.** The drift it guards against comes from someone
   hand-editing a file. Writing the measurements to a second file just moves the risk there.
3. **Users own their manual edits.** Every project has this property and no project builds machinery
   for it.

**The consequence, which is real and accepted:** Neo4j is a store, not a cache. Lose it and the
measurements are gone. That is affordable because measurements are re-derivable from the data — losing
them costs time, not truth. Decisions still live in the spec, in plain text, in git, exactly as
`PLAN.md` requires.

---

## 6. Attention points when building this

Not implementation steps. These are the things that will go wrong quietly.

### 6.1 The graph must not rank

This is the big one. The temptation will be enormous: *sort the candidate joins by overlap so the
agent sees the best one first.* That is a deterministic layer making a context-free judgment call,
which is the planner mistake this project already built and reversed (`PLAN.md`, `CLAUDE.md` →
facts vs judgment).

**The graph surfaces edges and their numbers. The agent decides which matter.** No score, no "best
candidate", no ordering by strength.

### 6.2 Edge existence is threshold-free; edge weight is reported, never ranked

Whether an edge exists at all is *does even one key value match* — a zero, needing no threshold.
That keeps the graph's structure on the facts side of the line. The numbers on the edge are reported
and never used to order anything.

### 6.3 Letting the agent write Cypher is the SQL escape hatch, again

You have made this decision once already, for `ops/sql.py`. It applies unchanged:

- Read-only connection.
- The graph's shape has to be in the agent's context, or it cannot write a valid query.
- The query is captured so a human reads it, rather than being a hidden reasoning act.

**The stakes are lower here than for the SQL hatch.** The graph holds metadata about the data, not
the data. A bad query leaks nothing the agent did not already have.

### 6.4 Do not design for data volume

Repeating §3.2 because it will come up again: the graph grows with the number of sources and columns,
not with rows or bytes. Do not build partitioning, sharding, or pruning for a graph that will have a
few thousand nodes.

### 6.5 Choosing which pairs to measure is judgment, not filtering

700 columns is roughly 245,000 possible pairs. You cannot measure them all — each is a query against
real data. Storage is trivial; **choosing is the whole problem.**

**§5.1 settles who chooses: the agent does, during indexing.** This section survives to record the
answer that was tried first and did not hold, because it is a persuasive one and it will be
re-proposed.

#### The prefilter design, and why it was dropped

The rejected proposal was a cheap deterministic filter over material the profiler already computes —
type compatibility, cardinality ratio, value-range disjointness, name similarity (`rapidfuzz` is in
`PLAN.md`'s reuse list) — narrowing 245,000 pairs to a measurable few, with a rule about which
filters were allowed to discard a pair: *an excluder must be a proof, an includer may be a
heuristic.* Range disjointness and type class prove a zero from numbers already in the profile, so
they could discard; name similarity only guesses, so it could reorder but never discard.

That rule is internally sound. Three things killed it anyway.

1. **The whole apparatus existed because code was choosing.** The elaborate care about which filter
   may discard is what you need *only* if the picking happens in a deterministic layer. Move the
   picking to the agent and the problem it solves stops existing. Building rules to make code guess
   safely, when the architecture already has something whose job is guessing, is the planner mistake
   wearing a different hat (§6.1).
2. **A proof can still be misleading, and range disjointness is the worst offender.** `France` and
   `FRA` share no values and no range. The filter discards the pair with certainty and is *correct*,
   and the pair was the most important one in the project (§4.4). The filter is not wrong about
   values; it is blind to meaning, and meaning is the only thing that gets you there.
3. **Sweeping is a liability even when affordable.** Every swept pair is a stored confident zero
   nobody asked about, plus a measurement someone must keep valid over time (§4.5). Volume was never
   the objection — §6.4 says not to design for volume — but 245,000 unrequested facts is a context
   and staleness problem regardless of what it costs to compute.

One sub-argument was also dropped and is worth naming, because it sounded like a saving and was not.
*Scope the sweep to the source: when indexing source N, compare its columns against everything
indexed so far — ~20,000 pairs rather than 245,000, at a moment the user is already waiting.* Index
all 23 sources and you have done all 245,000 pairs either way. **It changes when you pay, not how
much**, and presenting it as an answer to cost was wrong.

#### What survives from it

- **`profiling.py` computes `min` and `max` per column and `catalog._column_facts` does not persist
  them.** Still worth fixing on its own — it is a fact the profiler already paid for and drops on
  the floor, and it is exactly what the agent needs to see to judge a pair.
- **Approximate sketching** (HyperLogLog via `approx_count_distinct`) is still **not** recommended:
  two ~2% errors compound, and near zero the error swamps the signal — and near zero is precisely
  where the interesting answers are.
- **Cheap facts belong in front of the agent, not in front of a filter.** Type, cardinality, range
  and name are all useful for deciding whether a pair is worth a query. The change is who reads
  them.

### 6.6 It must be an optional dependency

Portia already has `agent` and `ui` extras. The graph should be another. If a stopped container breaks
the whole product, a design decision has leaked into a hard requirement.

### 6.7 Naming and placement

`portia/graph.py` collides with `portia/ui/graph.py`, which is the pipeline's layout module and has
nothing to do with this. Pick a different name.

This introduces a new seam. `CLAUDE.md`'s package layout and its "core / checks / ops / spec+catalog+
runlog / agent / cli+ui" rule of thumb will need updating when it lands.

### 6.8 Nothing in `ui/` computes

Standing rule, and it applies here. If a panel wants a number from the graph, it goes through
`ui/engine.py`. A widget must not query Neo4j.

### 6.9 The visual layer is a separate purchase from the database

People assume the store and the picture come together. They don't.

Neo4j's own browser is good and free. Alternatively there are mature graph visualization libraries
(Cytoscape.js, sigma.js, vis.js) that do force layout, expand-on-click and hairball management, and
can live inside portia's own canvas.

And a lot already exists: `ui/graph.py` computes positions, `workflow.py` draws cards and curved
edges with arrowheads, `canvas.js` gives pan and zoom. A static first view could reuse all of it.

**But there is a design collision to settle first.** `DESIGN.md` says colour and prominence
communicate kind, never rank, and `ui/graph.py` follows it strictly — position comes only from
dependency order, nothing is re-sorted by a number. **A force-directed layout breaks that.**
Well-connected nodes drift to the centre and the eye reads the centre as important. That is a layout
algorithm ranking by connectivity. It needs an explicit answer in `DESIGN.md`, not a quiet breach.

---

## 7. Open questions

Not decided. Listed so a new session knows they are open rather than overlooked.

- **What a graph query is allowed to return at once.** §4.4's storage rule keeps the *store* from
  filling with noise; it does nothing about the *context window*. A column with genuine overlaps
  against fifty others hands back fifty edges. Neither the design nor the discussion that produced
  it has an answer, and §9 makes it sharper rather than softer: a router that returns fifty things
  has not routed.
- **Column lineage through the `sql` hatch** (§4.2). `join` and `normalize` give it for free; a
  `sql` step declares only table-level `inputs`. Options: a coarse step-level edge that is true, or
  a parser (`sqlglot`) for real column-level lineage. Start coarse; decide later on evidence.
- **Composite keys.** A join sometimes needs two columns together. The schema in §4 only knows
  single-column overlaps. This will show up in real data and the modelling is not obvious.
- **Entity, whenever it ships.** §4.8 defers it and says the fix is a mechanism — show the agent the
  existing entity list before it may coin a name — rather than a fixed vocabulary. *When* that
  happens, and what the read-before-write surface looks like, is not decided.
- **Does the agent write Cypher in the first version,** or does it start with a small number of fixed
  queries? The whole stack was chosen so it *can*. That does not mean it should on day one.
- **Does anything measure outside indexing?** §5.1 puts the agent's choosing at index time. Whether
  it also volunteers pairs mid-conversation, and whether a user-invoked sweep exists as an escape
  hatch for filling the graph without a conversation, is not settled.
- **The force-layout collision** in §6.9.

**Closed during the 2026-08-04 design conversation**, recorded here so they are not re-opened by
accident:

- *Do a model's output columns get nodes?* **Yes** — §4.1. The reason for excluding them (graph
  size) contradicts §6.4, and they are the columns where measuring overlap is most trustworthy.
- *Proactive sweep vs opportunistic capture.* **Neither** — §5.1. The agent chooses the pairs during
  indexing. §6.5 keeps the sweep-with-prefilters design and the three reasons it failed.
- *How a stored zero is kept from reading as "unrelated".* **The edge carries the reason it was
  asked for** — §4.4. Available only because the agent picks the pairs; a sweep has no reason to
  record.
- *Do samples go on the edge?* **No** — §4.4. The edge says which pair and why; the agent climbs to
  `profile_source` for values. That is what the disclosure ladder is for.
- *Does Entity ship in the first version?* **No** — §4.8. It is the one node kind with nothing
  underneath it and the one whose names diverge by default.

---

## 8. Where this sits against everything else

**This is next** (`PLAN.md` → Next, 2026-08-04). The current work is infrastructure the agent does
not have, and §1.1–§1.4 are four statements about portia's code rather than about any model's
behaviour: no artifact holds relationships between sources · `join_report`'s answer is discarded when
the turn ends · the catalog is one file per source · L1 is exhaustive and pushed into every prompt.
None of them needs a run to establish and none is fixed by prompt work.

> **An earlier version of this section gated the graph on a prompt fix and a re-run**, on the
> reading that a failing run had shown the copilot's judgment to be the constraint. That reading did
> not survive: the runs in question held every prompt at its first draft and varied nothing, so they
> could not distinguish a prompt problem from a missing-context one, and `EVALUATION.md` was trimmed
> on 2026-08-04 to say so. The argument is kept here because it is a clean example of the failure
> this repo keeps writing down — **an inference from evidence that was never designed to support
> it**, which reads as a finding for as long as nobody asks what the experiment varied.

The prompt work in `BACKLOG.md` is still worth doing and is **independent** of this, not upstream of
it. Its premise is a measurement — profiling is a DuckDB aggregate now and the prompt still calls it
expensive — so it stands on its own.

---

## 9. What this does to the loop

*Added 2026-08-04. §1–§8 describe a store and say almost nothing about how it reaches the agent,
which is the half that decides whether any of it is worth having. A graph nobody queries is inert.*

### 9.1 The graph is not a rung on the ladder — it is what tells you which ladder to climb

The mistake to avoid is filing this under L5. The disclosure ladder is **depth on one source**:
L2 `describe_source` (meaning) → L3 `profile_source` (facts) → L4 `join_findings` (facts + rows).
Every rung answers *"tell me more about this table."*

The graph answers a different question: ***which* table should I be looking at** — and where does
this column come from. That is breadth, not depth. **It sits before L2, not above L4.** It is the
router that decides where to start climbing, which makes it cheap and early rather than expensive
and last.

That framing is what makes §1.4's payoff reachable. L1 is pushed into every system prompt today,
exhaustive at one line per source, and `BACKLOG.md` flags it as unproven at 50. **An agent that can
traverse to the relevant neighbourhood does not need L1 to list everything.** That is the point of
the whole exercise and it is the last thing to build (§9.4), not the first.

### 9.2 Indexing changes character; the conversation does not

**Indexing is the overhaul.** Today it is *read each source, describe it, group it*
(`prompts/tasks/index_batch.md`, `interpret.md`, and the `set_interpretation` / `set_group` writes).
It becomes *read each source, describe it, group it, **and say how it relates to what you have
already read***. That is a different task, and those prompts are rewritten rather than amended.

**The conversation phase gains a tool and keeps its shape.** Ask the graph where to look, then climb
the existing ladder. `join_findings` stays the heavy single-pair rung it already is.

### 9.3 The tool surface

Two new tools, and both need placing on the ladder in their descriptions — per `CLAUDE.md`, the
description is what teaches the model when to climb.

- **A read tool.** Fixed queries or Cypher is still open (§7). Its description must say it is a
  *router*: this tells you which source to look at and where a column came from, **not** more about
  one source. Getting that sentence wrong is how it ends up used as a worse `describe_source`.
- **A measure tool**, for indexing: many pairs, compact numbers, carrying the agent's reason per
  pair (§4.4). Deliberately **not** `join_findings`, which is heavy by design and returns example
  rows for a single pair. Two tools because they answer different questions at different costs.

Existing prompts that change: `index_batch.md` and `interpret.md` (the new task), `describe_source.md`
(a source can now say what it connects to), and eventually `agent/context.py` for the L1 shrink.

### 9.4 Build order, and why the loop cannot come afterwards

**Hand in hand — but ordered so each step is verifiable on its own.**

| Phase | What lands | What it settles | Loop change |
|---|---|---|---|
| **A** ✅ | Write path: structural edges + `DERIVES_FROM` from catalog and specs. The build command. | Whether the schema is right. Inspectable directly; **no agent involved**. | None |
| **B** | Read path: one tool, fixed queries. | Whether traversal helps at all. Lineage alone already earns it — *"where did this column come from"* is answerable with zero measurements. | One new tool |
| **C** | The agent picks pairs during indexing; measurements written with their reasons. | §5.1 and §4.4. | **Indexing is rewritten** |
| **D** | Shrink L1 from an exhaustive index to traversal. | §1.4. | Yes, and the riskiest |

**B before C, for a reason that is easy to miss.** At source 23 the agent must know something about
sources 1–22 to judge which pairs are worth measuring — which is the context problem the graph
exists to solve, appearing at the moment the graph is being filled. It resolves cleanly: **the agent
queries the graph while building it**, using the structural half that is already there. But that
only works if the read path exists first.

**D last, and on its own.** Changing what is pushed into every system prompt at the same time as
changing what indexing does moves two variables at once, and then nothing that follows can be
attributed to either. That is precisely the failure `EVALUATION.md` was trimmed for.
