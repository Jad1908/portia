# The knowledge graph — design

*Designed 2026-08-04. Nothing is built yet. This document is the whole of the decision, written so a
new session can pick it up cold.*

*It is a design, not a plan. It says what we want, what we chose, and what to watch out for. It does
not say how to implement anything — per `PLAN.md`, plans stay directional.*

---

## 1. Why

### 1.1 The copilot guesses at relationships

Run 8 is the case that motivates this (`EVALUATION.md`). 23 real sources, 4.8 GB. The model planned
a two-path join, headed its own gaps *"Critical Unknowns (Need to Measure)"*, measured none of them,
and proposed two joins. **Both matched zero keys.** Two queries established that afterwards, in
0.02 s each.

The model had no cheap way to know which pairs of tables could plausibly join. So it guessed from
column names. Portia has nothing that could have told it otherwise.

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

Five things, roughly in order of value.

1. **Answer "how do I join A and B" from measurement rather than from column names.** Find every
   measured overlap between a column of A and a column of B, with its numbers.

2. **Answer multi-hop questions.** *A doesn't touch C directly, but A connects to B and B connects
   to C.* This is the single strongest argument for the graph existing. It is one line of Cypher and
   it is genuinely painful in SQL.

3. **Let the agent traverse instead of reading everything.** A rung on the context ladder between L1
   and L2: *what connects to this source, and how strongly.*

4. **Accumulate.** Every measurement the copilot makes lands somewhere and is still there next turn.
   The graph grows where the attention went.

5. **Collapse columns into entities.** 23 sources × ~30 columns is ~700 columns but maybe 15 real
   things — a customer, a hotel, a booking, a date. One asserted relationship (*this column
   represents that entity*) makes the whole picture legible.

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

### 4.2 Relationships, grouped by where they come from

Five kinds. Each has exactly one origin. The grouping is deliberately `CLAUDE.md`'s facts-vs-judgment
line, because it maps onto this perfectly.

**Structural — free, a restatement of files:**

- Source `HAS_COLUMN` Column
- Source `IN_GROUP` Group
- Model `READS` Source or Model

**Measured — a fact, carries numbers:**

- Column `OVERLAPS` Column

**Asserted — judgment:**

- Column `REPRESENTS` Entity

Keep this set small. A vocabulary that grows per run is the `BACKLOG.md` "role vocabulary" problem
happening again, one layer up.

### 4.3 Overlap is one edge carrying two numbers

Overlap is not symmetric. 98% of orders' customer ids may exist in customers while only 40% of
customers appear in orders. Both numbers matter and they are different questions.

**One edge holding both**, not two edges facing opposite ways. Two edges means two records of a single
measurement, and they will drift apart.

### 4.4 Record the zero

An absent edge is ambiguous. *"We measured these two and they share nothing"* and *"we never looked"*
both appear as nothing, and they are completely different answers.

**Write the zero as an edge.** A measured zero is a real fact, it is exactly the fact Run 8 needed,
and it needs no threshold to be true — which is the same reasoning that governs
`outcome.BLOCKING_FLAGS`.

### 4.5 The prose summary lives as a property

The Layer 1 summary the agent writes at indexing stays prose, held as a property on the Source node.

This is worth doing on its own: Neo4j full-text indexes properties, so *"which sources mention
revenue"* becomes a real query. Today that answer is spread across twenty YAML files and nothing can
search them.

### 4.6 Do not extract edges from prose

The tempting move is to have a model read the summaries and pull relationships out of them. **Don't.**
That is where knowledge graph projects go to die — extraction is unreliable and nobody can check it.

Instead: **the agent writes prose and structured edges in the same act.** It already interprets a
source and writes a summary and column roles. At that same moment it knows the grain, the entity, the
system the data came from. Have it say those as edges too. Same judgment, two shapes, no extraction
step in between.

---

## 5. How the graph gets built

**The user never touches Neo4j.** Portia fills it. Neo4j starts empty and does not discover anything
on its own.

Three kinds of material, arriving at three moments that already exist in the product.

**Free — already on disk.** The catalog holds sources, columns, column facts and groups. The specs
hold which tables read which. Translating that into nodes and edges is mechanical: no decisions, no
cost, nothing to ask anyone. This is the skeleton and it should appear the moment a source is indexed.

**Measured — costs a query.** The overlap edges. The first version costs nothing extra, because the
copilot already computes these when it checks a join — the answer is written to the graph instead of
being discarded. Sweeping every possible pair proactively is a much bigger job (§6.5).

**Asserted — judgment.** Entities, grain, and anything else the agent decides. Lands when a source is
interpreted, which already happens.

So the write hooks onto: **index a source · save a spec · measure a join.** Each is one addition to
something that exists.

**A build command is needed regardless.** Someone cloning the repo gets the YAML from git and no
graph. There has to be a way to construct it from the project.

### 5.1 Drift — considered and deliberately dropped

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

### 6.5 The hard problem is candidate generation, not storage

700 columns is roughly 245,000 possible pairs. You cannot measure them all — each is a query against
real data.

Storage is trivial. **Deciding which pairs are worth measuring is the actual engineering.** The
prefilters are nearly free because the profiler already computes the material: type compatibility,
cardinality ratio, value-range disjointness, and name similarity (`rapidfuzz` is already in
`PLAN.md`'s reuse list). Filter hard on those, then measure exactly on what survives.

One concrete gap found while designing this: `profiling.py` computes `min` and `max` per column, but
`catalog._column_facts` does not persist them. Range-disjointness is the cheapest and most decisive
prefilter available and it is currently dropped on the floor.

Approximate sketching (HyperLogLog via `approx_count_distinct`) was considered and is **not**
recommended: two ~2% errors compound, and near zero the error swamps the signal — which is exactly
the question Run 8 needed answered.

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

- **Composite keys.** A join sometimes needs two columns together. The schema in §4 only knows
  single-column overlaps. This will show up in real data and the modelling is not obvious.
- **Do a model's output columns get nodes?** It would make lineage complete. It also roughly doubles
  the graph, for columns that are derived. Probably not in a first version.
- **Entity vocabulary.** The agent will invent entity names per run, the same way it invents role
  names today (`BACKLOG.md` → "role vocabulary"). Same problem, same reason not to constrain it early.
- **Does the agent write Cypher in the first version,** or does it start with a small number of fixed
  queries? The whole stack was chosen so it *can*. That does not mean it should on day one.
- **Proactive sweep vs opportunistic capture.** §5 assumes opportunistic. A sweep is a real product
  difference — the graph would be useful before the copilot has looked at anything.
- **The force-layout collision** in §6.9.

---

## 8. Where this sits against everything else

**It is not obviously next, and a new session should not assume it is.**

`PLAN.md` says the constraint has moved from the engine to the copilot: eight runs, all failing, and
the queued work is a prompt fix followed by a re-run of the Run 8 goal, scored using the run log.

A knowledge graph does not fix a prompt that still prices profiling as expensive.

**The counter-argument, which is genuine:** Run 8's failure is what a missing graph looks like. The
model guessed two join paths because it had no cheap way to know which pairs could plausibly join, and
both guesses matched nothing. So this is adjacent to the live failure rather than orthogonal to it.

Both readings are defensible. The cheap way to settle it is to do the prompt fix and Run 9 first —
that tells you whether the copilot measures anything at all when prompted properly, which changes what
the graph is even for.
