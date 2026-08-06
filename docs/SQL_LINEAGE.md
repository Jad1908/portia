# Column lineage through the SQL hatch — the problem, and how to fix it

*Specified and **built** 2026-08-06. This closes `KNOWLEDGE_GRAPH.md` §7's second bullet
("Column lineage through the `sql` hatch") and `BACKLOG.md`'s matching entry, and it changes
what those two said: they framed the choice as **coarse now, a parser later on evidence**, and
§3 is the finding that the coarse option is not the cheap one it was assumed to be.*

*§1–§8 are the specification, kept as written. §9 is what building it settled.*

> ## Status — built, 2026-08-06
>
> | | what landed | where |
> |---|---|---|
> | the parser | output columns and their origins, read out of the SQL text | `knowledge/sqllineage.py` |
> | the walk | the `sql` branch, and the rank read off the parse tree | `knowledge/build.py` |
> | the honest gap | `derivation: "unknown"` on a column with nothing underneath it | `knowledge/schema.py`, `query.py` |
> | the silent loss | a measurement that could not be attached is no longer reported as stored | `knowledge/store.py`, `agent/handlers.py` |
>
> Verified against a live Neo4j — 665 tests, including the Cypher. **One deliberate departure
> from §5.5 and one from §5.7; §9 says what they are and why.**

---

## 1. What actually breaks

`knowledge/build.py` walks a spec step by step, carrying each intermediate relation's column
names and where each one came from. `join` and `normalize` name their output columns, so the
walk can keep going. A `sql` step declares *table* names in `inputs` and says nothing about
columns, so the walk stops: `build.py:324` returns a relation whose `columns` is `None`, and
the model lands in `BuildResult.unresolved`.

That much is written down. The three consequences below are not, and the second is bigger
than the one everybody has been discussing.

### 1.1 The model loses its columns entirely, not just its lineage

`_add_model` (`build.py:273`) creates Column nodes **from the produced relation**. No produced
columns means no `HAS_COLUMN` edges and no Column nodes — so the model is present in the graph
as a bare node with `READS` edges and nothing underneath it.

This is the loss that matters, and `KNOWLEDGE_GRAPH.md` §4.1 says why in its own words:

> A model is where the data has been harmonized, so it is the one place where measuring shared
> values is trustworthy. […] Excluding model columns excludes the only columns clean enough for
> the measurement to mean what it says.

A hatch step therefore does not merely cost lineage. **It removes the model from the measured
half of the graph** — the half §2 calls the point of the whole thing — at exactly the columns
§4.1 argues are the only trustworthy ones to measure.

### 1.2 It cascades, and position does not help

`_model_relation` (`build.py:288`) propagates an unresolved relation forward with the reason
`_UNBUILT`, so a model reading a hatch-poisoned model is itself blank. And the walk is
sequential: a hatch step anywhere in a spec blanks everything after it in that spec, including
steps whose own columns are perfectly nameable.

The worked example below has the hatch step **first** and a plain `join` second. The join's
output columns are fully determined by `join_columns` and cannot be recovered, because its left
input is unknown.

### 1.3 Measured, on the only project in this repo that has a hatch step

`sandbox/gui-test/incoming` — two sources, one spec, two steps:

```yaml
- id: clean_bookings
  op: sql
  inputs: [bookings]
  sql: select booking_id, trim(hotel_id) as hotel_id, guest_id, country_name, nights,
    revenue from bookings
- id: bookings_with_hotel
  op: join
  left: clean_bookings
  right: hotels
  keys: [hotel_id]
```

`build_graph` on it:

```
nodes:  Source 3 · Model 1 · Column 14 · Group 1
edges:  HAS_COLUMN 14 · IN_GROUP 3 · READS 2 · DERIVES_FROM 0
columns not resolved:
  bookings_with_hotel: step 'clean_bookings' is a sql step — see §7
```

**Zero `DERIVES_FROM` edges in the entire project**, and 0 of the model's 9 output columns are
nodes. The 14 Column nodes are all sources. The SQL is a flat projection over one table: eight
bare column references and one `trim`. Nothing about it is hard.

That is the shape of the failure worth reacting to — **all-or-nothing, and triggered by the
easiest possible query.** It is not a "complex SQL is hard to parse" problem; the current
behaviour is identical for `select *` and for `select a from t`.

### 1.4 The read path cannot say "I could not tell"

`KNOWLEDGE_GRAPH.md` §4.4's central rule is that absence must mean exactly one thing. In the
lineage half it currently means three, and `graph_lookup` cannot separate them:

| What is true | What the agent sees |
|---|---|
| The model has no columns (impossible in practice) | `columns: []` |
| The columns exist but nobody could name them | `columns: []` |
| A named column is not on this table | `no column 'x' on 'y' — have: (none in the graph)` |

That last message (`query.py:185`) reads as *this column does not exist*, when the truth is
*portia could not work out this table's columns*. Different next move entirely.

There is a fourth, latent one, and it is a lie rather than a silence: `origins`
(`query.py:215-216`) defines a terminal column as one with no outgoing `DERIVES_FROM`. Any
partial fix that adds *some* edges makes an unresolvable column indistinguishable from a source
column — the graph would report a computed model column as the file the data came from. §5.5
is the fix, and it has to land in the same change as the edges.

### 1.5 A measurement that is paid for, reported stored, and silently dropped

Confirmed, and it is a defect in its own right that this gap reliably triggers.

`measure_overlaps` resolves a model by name (`handlers.py:274`) and reads its table through
`spec.model_table`, so **measuring a hatch-poisoned model's column succeeds**. The numbers come
back. Then `store.measured_writes` (`store.py:224-226`) writes with
`MATCH (a:Column …) MATCH (b:Column …) MERGE …` over an `UNWIND`, and a row whose Column node
does not exist is **skipped without error**. `write_measured` returns the number of edges it was
*handed*, and `_store_overlaps` returns `{"stored": True}` (`handlers.py:241`) as long as the
session opened.

So the agent asks for a measurement on the harmonized table, pays a real query for it, is told
it was stored, and next turn the graph says nobody ever looked. That is §4.4's ambiguity
reintroduced by a silent write, which is worse than the case §4.4 was written about — there,
at least, nobody had spent anything.

**Fix this regardless of what happens to lineage.** A write whose `MATCH` found nothing must be
reported, not counted. §5.7.

### 1.6 What the evidence does and does not support

`BACKLOG.md` says, correctly: *don't buy the parser before reading the number.* Here is the
number, and its limit.

- Across every spec in this repo and every project under `sandbox/`: **9 steps — 5 `join`, 3
  `normalize`, 1 `sql`.** One project of thirteen has a hatch step.
- That one hatch step zeroed 100% of its project's column lineage and 100% of its model's
  column nodes.

**N=1 is not "real projects run mostly through the hatch", and this document does not claim it
is** — `EVALUATION.md`'s standing rule about inferring from evidence that was not designed to
support the claim applies here as much as anywhere. The argument for acting is not frequency.
It is that the failure is **all-or-nothing, cascading, silent at the read path, and reached by
the simplest query anyone would write** — and that `ops/sql.py`'s own docstring calls the hatch
the thing that *unblocks* real work, so its usage rate is a floor, not a ceiling. The one
observed instance is also a case where `normalize` would have done the job, which says
something about how easily a spec ends up here.

---

## 2. What a fix may not break

Each of these is load-bearing somewhere else. A design that violates one is wrong even if it
produces better lineage.

1. **`build.py` runs nothing and opens no connection** (its docstring, and `_sync_graph`'s
   claim that a graph refresh is cheap). A graph must be buildable for a project whose data has
   moved, and it must say the same thing it said when the data was there.
2. **The graph holds a pointer, never a copy of the expression** (§4.2). The spec is the one
   place that says what a step does. Nothing may store a second statement of it.
3. **The vocabulary stays closed** (§4.2, `schema.py:96`). No new edge kind, no label per shape
   of derivation, no growth in `VIA_OPS` — which already contains `"sql"`, so the schema
   anticipated this and nothing needs to change there.
4. **Never guess an edge.** §1.1 of the graph design is that column *names* are what lie;
   an edge inferred from name equality would be that failure written into the store as
   structure.
5. **`expect` is the agent's prediction, not a fact.** `_drift` reports a mismatch and nothing
   blocks on it, so `expect.columns` can be wrong and still be in the YAML. It may never be a
   source of truth (facts vs judgment).
6. **The graph is optional** (§6.6). A stopped container costs nobody anything they had; a
   missing parser must cost no more than that.
7. **The graph must not rank** (§6.1). Nothing here scores a derivation's strength.

---

## 3. The finding: the coarse option is not the cheap one

§4.2 offers a fallback — *"a coarser true edge — this column came out of this step, which read
these tables"* — and §7 says start there. Working out where the output column **names** would
come from is what breaks it, because every coarse edge still needs them.

| Source of the names | Verdict |
|---|---|
| The SQL text | Needs a parser. `select *` and set operations defeat anything less, and a splitter that is right most of the time and silently wrong sometimes is what this repo refuses everywhere else. |
| `expect.columns` | Forbidden — constraint 5. It is a prediction, drift is non-blocking, and it is optional. |
| A run of the step | `record_step` already computes the true list in `provenance["columns"]` and throws it away — §1.2 of the graph design, happening again. But persisting it makes the spec hold a copy that goes stale, and building the graph would then depend on something having been run. Constraints 1 and 2. §4.4 below. |
| The compiled `models/*.sql` | Same parse problem, plus it does not exist at `record_step` time, which is one of the three write moments (§5). |

**So there is no coarse-but-honest answer that avoids a parser.** §7's "start coarse, decide
later on evidence" quietly assumed the coarse answer was cheap and the parser was the expensive
upgrade. It is one purchase, not two — and the parser that produces the names produces the
edges as a by-product.

That does not delete the coarse behaviour. It relocates it: coarse becomes what happens when
the parser is **not installed** (§5.6), which is a better place for it than a design stage.

---

## 4. The options, and what each is rejected for

### 4.1 Leave it (status quo)

Rejected. §1.1 and §1.5: the cost is not "lineage is incomplete", it is that harmonized tables
are absent from the measured half and measurements against them are silently discarded.

### 4.2 Edges by name equality

*Output column `hotel_id` derives from input column `hotel_id`, because the names match.*

Rejected by constraint 4, and it is the exact inversion of the graph's founding argument. It
would also be confidently wrong in the one direction that matters: a hatch step is often used
precisely to *rename* and *derive*, so name equality is least reliable where the hatch is most
used.

### 4.3 Fan-out: every output column derives from every input column

True in a uselessly weak sense — the step did read those tables. Rejected on two counts. It
produces a 9 × 30 hairball that makes `graph_lookup`'s column answer unreadable (§7 of the
design: a router that returns fifty things has not routed), and it makes `DERIVES_FROM`'s edge
count stop meaning what §4.2 says it means — *one is a rename, several is a composite* — by
making everything a composite.

### 4.4 Persist the produced column list from the run

`record_step` runs the step and has the real column names. Write them into the spec beside the
step; `build.py` reads them and keeps walking.

**Rejected, and it is the closest call in this document.** Three reasons, in increasing order
of weight:

- It is a copy of a re-derivable fact living next to `expect`, which is the *prediction* of the
  same fact. Two fields, one of them authoritative, differing in the YAML, is a diff nobody can
  read.
- It makes the graph depend on the step having been *run by portia*, which breaks the clone
  case (`git clone` + `python -m portia.cli.knowledge` and the graph is right) and constraint 1.
- **It still yields no lineage** — only names. It buys §1.1 and leaves §1.4 and the original
  §4.2 gap untouched, at the price of a durable artifact change. The parser buys all of it.

Worth keeping in view for one reason: it is the only option that survives a query using a
DuckDB construct the parser cannot read, which is what §5.6's fallback covers instead.

### 4.5 Parse the SQL — `sqlglot`

**Recommended.** MIT, pure Python, zero dependencies, a DuckDB dialect, and a lineage module.
It is what §4.2 and §7 already name as the eventual answer; §3 is the argument that "eventual"
was based on a false economy.

The thing that makes it cheap *here* specifically and expensive for a general tool: **portia
already holds the schema at the moment it needs it.** `build.py`'s walk is carrying every input
relation's column names when it reaches the hatch step, which is exactly what a parser needs to
qualify references and expand `*`. Nobody has to build or maintain a schema for this.

---

## 5. The design

### 5.1 Where the parse happens

In `build.py`, in the `sql` branch of `_step_relation`. **Parsing is not running** — no
connection, no file opened, no data touched — so constraint 1 holds unchanged and should be
said out loud in the code, because it looks like a violation at a glance.

### 5.2 What it is given

The step's `sql`, `read="duckdb"`, and a schema assembled from the relations the walk already
holds: `{input_name: [column, …]}` for each name in the step's `inputs`. This is what lets `*`
expand and what makes an unqualified reference resolvable to a side.

If an input's own columns are unknown (an unindexed source, or an upstream model that itself
failed), the step is unresolved for the existing reason, unchanged. The parser does not paper
over a missing schema.

### 5.3 What it produces

For each output column of the step: the set of input columns its value depends on.

- The output column becomes a Column node with `HAS_COLUMN`, as `join` and `normalize` produce
  today.
- One `DERIVES_FROM` edge per resolved origin, carrying `via: "sql"` and
  `step: "<spec>#<step id>"` — the same two properties, from the same closed set. No new
  vocabulary (constraint 3).
- `a + b AS c` therefore produces two edges, and the composite shape stays free from the edge
  count exactly as §4.2 specifies.

### 5.4 The rank, read off the parse tree

`build.py`'s existing rule ranks a step by how much it explains: `CHANGED` (a transform)
outranks `RENAMED` outranks `CARRIED`, ties to the later step. A hatch step has to answer the
same question, and the parse tree answers it structurally rather than by invention:

| The select expression | Rank |
|---|---|
| A bare column reference, output name equal to it | `CARRIED` |
| A bare column reference, output name different | `RENAMED` |
| Anything else — function, arithmetic, `CASE`, cast, aggregate | `CHANGED` |

So `select booking_id, trim(hotel_id) as hotel_id from bookings` gives `booking_id` a `CARRIED`
edge and `hotel_id` a `CHANGED` one — and a later `normalize` on `booking_id` correctly
outranks the hatch step for that column, which is the behaviour the rule exists to produce.

**Deliberately not distinguished:** aggregates, windows and joins inside the hatch get no
special rank. A vocabulary of transform kinds is what §4.2 refuses; three ranks with a
structural test is the whole of it.

### 5.5 The column nothing could resolve

Some output columns have no nameable origin — `count(*) AS n`, a literal, a `nextval`. The
column is real and must be a node; asserting a derivation for it would be a guess.

**An edgeless Column node is not enough**, because of §1.4: `origins` treats *no outgoing
`DERIVES_FROM`* as *this is where the data came from*, and would report `n` as a terminal
source column. That is the graph stating a falsehood, which is worse than the silence it
replaces.

**Proposal: a two-state property on the Column node** — `derivation: "unknown"`, absent
otherwise — and one `WHERE` clause in `origins` so an unknown-derivation column is never
returned as a terminal origin. This is the one place the schema grows, and the shape is chosen
against the failure §4.8 names: a flag with two states cannot fragment the way an open
vocabulary of node names can. It is not a new label and not a new edge kind.

The alternative — keep such columns out of the graph — reproduces §1.1 at column granularity
and is rejected for the same reason.

### 5.6 Degradation, and where the coarse answer went

`sqlglot` goes in the **`graph` extra**, imported lazily inside the `sql` branch. Without it,
`build.py` behaves exactly as it does today: the model is unresolved, with a reason that says
the parser is not installed. `pip install portia` is unchanged, and constraint 6 holds — a
missing parser costs what a stopped container costs, which is nothing anyone had.

The same path catches a query `sqlglot` cannot parse or cannot qualify. The reason string
distinguishes the three cases, because they have three different next moves: *no parser
installed* · *the SQL could not be parsed* · *an input's own columns are unknown*.

### 5.7 What `unresolved` becomes, and the write that must stop lying

`BuildResult.unresolved` stays the evidence artifact and gets finer: **one line per column**
that could not be resolved, rather than one per model. A model with eight clean columns and one
`count(*)` should report one line, not vanish.

Separately, and independent of everything above (§1.5): `store.write_measured` must report how
many edges its `MATCH` actually found, and `measure_overlaps` must say plainly when a
measurement it just paid for could not be attached. `{"stored": True}` when nothing was written
is the one failure mode in this document that costs the user something they already had.

---

## 6. What this does not fix

Say these out loud so the fix is not read as bigger than it is.

- **Model columns still carry no profile facts.** `_add_column` gives a model column a name and
  nothing else, honestly — nothing has profiled the table portia would build. Lineage arriving
  does not change that, and `graph_lookup` on a model column will still show `null_rate: null`.
- **A measured `OVERLAPS` on a model still requires the model's table to exist**, because
  `measure_overlaps` reads it through `spec.model_table`. This fix makes the edge *attachable*;
  it does not make it free.
- **`sqlglot` resolves syntax, not semantics.** `CASE WHEN a THEN b ELSE c END` yields three
  origins with no weighting, and that is correct and coarse — the graph does not rank (§6.1),
  and the `step` pointer is how you find out which branch mattered.
- **Composite keys** (§7) are untouched.
- **The hatch's other known gaps** are untouched: `BACKLOG.md`'s "a `sql` step reports no flags,
  ever" and its unstable output row order are separate items.

---

## 7. How it gets verified

The failure is exact and small, so the test can be too.

- **A fixture project with a hatch step**, of the shape `sandbox/gui-test/incoming` already has:
  a `sql` projection feeding a `join`. `build_graph` on it must produce **9 model Column nodes**
  and the `DERIVES_FROM` edges §5.3 and §5.4 specify — including `booking_id` `CARRIED` through
  the hatch and `hotel_id` `CHANGED` by it, with both edges pointing at `bookings.csv`'s
  columns.
- **A second case covering §5.5** — one output column with no nameable origin — asserting that
  the node exists, carries the flag, and does not appear in `origins`.
- **Cascade**: a spec reading the hatch-poisoned model resolves too.
- **Degradation**: with `sqlglot` absent, the result is today's, with a reason naming the parser.
- **`tests/test_graph_schema_doc.py` already fails** if `knowledge/schema.py` gains something
  `GRAPH_SCHEMA.md` does not mention, so §5.5's property forces the reference doc to be updated
  in the same change. That is the intended behaviour, not an obstacle.

---

## 8. Open

- ~~**Does `derivation: "unknown"` belong on the node?**~~ **Yes** — kept on the Column, as §5.5
  argued. The alternative (a property of the model saying *this table has unresolved columns*)
  cannot be read at the point the question is actually asked, which is a Cypher pattern walking
  column to column.
- **Does anything use the lineage to walk forward from a changed file?** §4.5 says the payoff of
  lineage plus staleness is *name exactly which model columns are affected*, and `BACKLOG.md`
  notes nothing asks that question yet. This fix is what makes the answer complete rather than
  partial, but it does not build it.
- **Does the hatch's usage rate change once the copilot is prompted better?** §1.6's N=1 is the
  honest state of the evidence. The number worth watching is in the run log — `BACKLOG.md`
  already asks for *how often `sql` is chosen over `join`/`normalize`* — and this document
  should be revisited against it rather than assumed correct.

---

## 9. What building it settled that the specification did not say

**Two departures from §5, both in the same direction: a silence the spec was willing to accept
turned out to be readable as something false.**

### 9.1 An unreadable column is returned by `origins`, marked — not filtered out

§5.5 said such a column must *never be returned as a terminal origin*, and stopped there. Taking
that literally means filtering it, and filtering it makes `origins` come back **empty** for a
column whose trail genuinely ends at a `count(*)`. An empty origins list reads as *this came from
nowhere*, which is a worse answer than the one the marker was invented to prevent.

So it is returned with `derivation: "unknown"` on the row, and the renderer says *nothing readable
underneath it*. That is the same rule §4.5 applies to a stale measurement — **mark, never delete**
— and the spec had already argued it one section earlier without noticing it applied here too.

The marker is also said on the **column itself**, not only on an origin row, because asking
`graph_lookup` about that column directly is the commoner way to arrive at it, and there the
origins list is empty for a legitimate reason.

One small consequence, decided for the agent's sake rather than the schema's: the property is
**omitted from an ordinary origin row rather than sent as `null`**. Every origin in a project
carrying a key that says nothing is tokens spent on nothing, and this answer is read by a model.

### 9.2 A partial write is a failure, not a partial success

§5.7 asked only that `write_measured` count what its `MATCH` found. It does — but the caller then
has to decide what a shortfall *is*, and the spec did not say.

It is reported as `stored: False` with the reason, not as a smaller number. The agent's next move
is identical whether one measurement was dropped or all of them were: use the numbers you have,
don't assume they will be there next turn, and don't re-run the tool. A count would invite reading
a shortfall as a success with a footnote.

### 9.3 The rank rule needed nothing new, and that was not obvious in advance

§5.4 proposed reading the rank off the *top-level select expression*. That is wrong for a CTE: a
value summed inside `WITH c AS (…)` and merely selected at the top would score as carried. What
the code does instead is carry the answer **down the whole path** — one function, operator, cast
or `CASE` anywhere between the input column and the output makes it changed.

This is the one place where the implementation is stricter than the spec, and it fell out of
testing rather than design: `WITH c AS (SELECT round(amount) AS amount FROM orders) SELECT amount
FROM c` is the case that caught it.

### 9.4 The evidence dropped to zero, which is the intended outcome

`BuildResult.unresolved` was the standing measure of what the hatch costs. On
`sandbox/gui-test/incoming` — the project §1.3 measured at **0 lineage edges and 0 model columns**
— it is now **empty**, with 9 model columns and 10 `DERIVES_FROM` edges, and `hotel_id` correctly
pointing at `bookings.csv` via the `sql` step that trimmed it *and* at `hotels.csv` via the join.

The dict does not stop being the evidence. It has simply become finer: one line per column that
could not be read, rather than one per model that vanished. What it now measures is the residue —
aggregates, literals, and queries no parser can resolve — which is a much smaller and more
interesting number than the one it used to report.
