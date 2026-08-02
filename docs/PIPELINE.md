# The pipeline overhaul — SQL as the artifact

> **Status: designed 2026-07-30, built 2026-07-31, rendered 2026-08-01.** All seven decisions in
> §2 are implemented and tested. §3 describes how compilation actually works, and it is accurate.
> §6's three design questions are answered, the app renders the pipeline, and §2.7's import
> surface is built. Nothing from this design is outstanding.
>
> The design is kept in full rather than trimmed to a changelog, because the *reasoning* is what a
> future session needs: several decisions removed things (`core/store.py` is deleted), and a reader
> who finds only the outcome will re-litigate the trade-off from scratch.
>
> Companion reading: `PLAN.md` (where this sits in the order), `VISION.md` (the flows it changes),
> `TECH_STACK.md` (the stack commitments it makes), `CLAUDE.md` (the seams it must respect).
>
> **What landed, in commit order:** compilation to `.sql` · cross-spec references by name · layers
> and `portia.cli.build` · repo-only sourcing, the import command, and the store's removal · the
> prompts · the frontend pass.

---

## 1. Why

portia's durable artifact today is a spec: YAML describing what was done to the data, plus a run
report. That is enough to *re-run* the work and enough to *review the decisions*. It is not enough
to **hand someone the pipeline**.

A real data project is a set of tables built from other tables — cleaned copies of each source,
combinations along the way, and the tables people actually query. The standard way to express that,
and the one a data team already knows how to read, review and schedule, is a folder of SQL files
where each file builds one table. dbt is the common name for that shape.

portia already generates all the SQL it needs. It just throws it away at the end of every run.

**This overhaul keeps it.**

---

## 2. The decisions

Seven, all settled. Anything not listed here was not decided and should not be invented.

### 2.1 One spec = one table

A spec produces exactly one table, named for the spec. Its steps are the intermediate work that
builds that table, and they become named blocks *inside* one query rather than tables of their own.

### 2.2 The artifact is plain `.sql` files, in their own folder

One `.sql` per spec. dbt-shaped — one file, one table, references other models by name — but portia
does **not** write `dbt_project.yml`, `profiles.yml` or `schema.yml`, and does not track dbt's
conventions. The files drop into a dbt project; portia does not become a dbt wrapper.

They live in their own directory, separate from `specs/`. Separation of concerns: `specs/` is the
decision record, the SQL directory is the build output.

### 2.3 The SQL is a build output, committed by default

You change the spec and regenerate; you do not hand-edit the `.sql`. The spec carries `rationale`,
`expect`, `grain` and `acknowledge` — none of which plain SQL can hold — so an editable `.sql` would
mean the decision record describes something other than what runs.

But it is **not gitignored**. The pipeline is the deliverable; someone has to be able to read it in
a PR without running anything. That admits a staleness risk (regenerate the spec, forget to commit
the SQL), which two things mitigate:

- every generated file carries a header naming the spec that produced it, when, and a fingerprint
  of the spec
- running a spec warns when the `.sql` on disk no longer matches what the spec would now produce

Beyond that it is the user's call, as with any generated-and-committed file.

### 2.4 Cross-spec references by name

A spec names another spec's output by **its plain name** — no path, no `#`, no `depends_on` list.
portia scans the project's specs, sees which one produces that name, and derives the run order
itself.

This is what dbt, SQLMesh, Dataform and Terraform all converged on, and portia is already halfway
there: `spec.step_inputs` deliberately does not distinguish "a source" from "an earlier step" — it
resolves a name. This extends that resolution to "or another spec's output".

**The rule it costs:** output names must be unique across the project. That is also what makes the
`.sql` filenames unique, so it is wanted anyway.

This **replaces** the current refusal at `agent/handlers.py:618` (`_bare_step_id`), which raises
*"A step can only chain from an earlier step in its own spec"*.

### 2.5 Layers are an optional field

A spec may declare `layer: staging | intermediate | mart`. The generated `.sql` lands in the
matching subfolder.

- **staging** — one lightly-cleaned copy per raw source. Types, names, whitespace. Nothing joined.
- **intermediate** — combinations on the way to an answer.
- **mart** — the tables people actually query.

**A project with no layers is a flat project**: no `layer` field, all `.sql` in one folder, done.
There is no second mode, no setting, no branch in the code — the simple case is the *absence* of a
field. This is how "the layered pattern is overkill here" is handled, and it must stay that way:
the moment there are two code paths, the simple case rots.

### 2.6 The agent's decision is "new spec or new step", not "should this persist"

Because one spec = one table, every spec's output persists by construction. So the judgment call
moves one step earlier, to the moment the agent is deciding where to put the work:

> **A new spec** → a table that persists, gets its own `.sql`, and can be referenced by name.
> **A new step in the current spec** → a named block inside an existing query.

The agent decides this and says why. Portia gives it the *fact* to decide on — **how many other
specs read this table** — and never a rule like "always promote a join". Facts vs judgment, applied
to pipeline shape.

At the start of a project the agent proposes the overall shape (flat or layered), asks via
`AskUserQuestion`, and the answer is recorded so it is not re-litigated every session.

Both of these need prompt work: `prompts/tools/record_step.md` and `prompts/copilot.md`.

### 2.7 Only data already inside the repo can be indexed

Out-of-repo paths are **not an option** — not a warning, not a flag, refused. portia plugs into a
repo that already holds the data and the user picks what is in scope.

Bringing outside data in is a **separate, deliberate import step**: the user chooses where in the
repo it lands, portia states plainly what it is about to copy and to where, copies it, then indexes
it. Original files are never modified.

Consequence, and it is the real prize: **spec source paths become repo-relative, always.** Today an
outside file lands an absolute path in the spec, and that spec only works on one laptop.

**`.portia/store.duckdb` goes away.** The store was a second, hidden copy of the user's data,
justified on read speed (0.21 s → 0.01 s per column-scoped read). Two things undercut it: the hot
paths never used it — `run_spec`, every agent check and every CLI tool re-read the original files
anyway — and one visible copy of the data is worth more than an invisible fast one. If read speed
ever bites, the answer is **parquet in the repo**: columnar, typed, already fast, already readable
by `core/io`, and still one copy you can see.

Dropping it does **not** weaken the SQL sandbox. `ops/sql.py` materializes its declared inputs into
a fresh restricted connection either way, so the sandbox never touches a file regardless of where
the input came from. (`DUCKDB_MIGRATION.md` §3's second argument for ingesting — "if a source *is* a
`read_csv` call the hatch needs file rights" — does not apply, because the hatch never sees the
source's query.)

---

## 3. How compilation works

This is the part to get right, so here it is mechanically.

### What happens today

`run_spec` keeps a dictionary of **name → a piece of SQL text**. Sources start as
`SELECT * FROM read_csv('path')`. Each step looks up its inputs, **wraps** their SQL in a bigger
`SELECT`, and stores the result under the step's `id`. After the last step, one deeply nested query
exists as a string. Nothing has executed; DuckDB runs it only when something asks for a number.

Traced on a two-step spec (normalize then join):

| after | name | SQL held |
|---|---|---|
| load | `orders` | `SELECT * FROM read_csv('data/orders.csv')` |
| load | `customers` | `SELECT * FROM read_csv('data/customers.csv')` |
| step 1 | `clean_orders` | `SELECT order_id, trim(customer_id) AS customer_id, amount FROM (`*step 0's SQL*`)` |
| step 2 | `orders_with_customers` | `SELECT … FROM (`*step 1's SQL*`) AS l LEFT JOIN (`*customers' SQL*`) AS r ON …` |

That string is discarded when the run ends.

### What changes

Stop nesting; start naming. Each step emits a named block, the blocks stack into one `WITH`, and
the whole thing is prefixed with a `CREATE TABLE`:

```sql
-- generated by portia from specs/orders_with_customers.yaml
-- 2026-07-30T14:22:01 · spec fingerprint a91f3c2
CREATE TABLE orders_with_customers AS
WITH clean_orders AS (
    SELECT order_id, trim(customer_id) AS customer_id, amount FROM orders
)
SELECT l.*, r.name
FROM clean_orders AS l
LEFT JOIN customers AS r ON l.customer_id = r.customer_id;
```

Identical query, identical result — the step ids become the block names. **That is the entire
distance between what exists and the artifact.**

### The `sql` step is not a problem

`ops/sql.py` breaks the chain today: for sandbox reasons it materializes its inputs, runs the query
on a restricted connection, and hands back a fresh table. Its query does not wrap its inputs'.

That does not matter for compilation, because **execution and compilation are two different
paths**. Execution keeps the sandbox exactly as it is. Compilation emits the step's *declared* SQL
text (already stored verbatim in the spec and in `provenance["sql"]`) as the block body. A `sql`
step compiles to a CTE as cleanly as any other op.

**But two paths that can disagree is precisely what this project hates.** So this is a hard
requirement, not a nice-to-have: **a test must run the spec through the engine and run the compiled
`.sql` through DuckDB, and assert the two produce the same table.** Golden files did more work than
the abstraction did during the DuckDB migration (`TECH_STACK.md`); the same applies here.

### One thing still to settle

**How a compiled file names its sources.** Two shapes:

- bare table names (`FROM orders`) — dbt-shaped and clean, but the file does not run standalone
  until something has created those names
- the file read inlined (`FROM read_csv('data/orders.csv')`) — runs standalone in DuckDB, but is
  not what a dbt model looks like

Recommendation: **bare names in the model files**, plus a generated companion that creates them as
views over the repo's files, plus the header comment naming which file each source came from. That
keeps the models dbt-droppable and the pipeline runnable on its own. Confirm before building.

---

## 4. What changes, by file

Rough map for whoever picks this up — not a task list, and not exhaustive.

| File | Change |
|---|---|
| `portia/spec.py` | compile steps to named blocks; resolve cross-spec names; derive run order across specs; emit `.sql`; the staleness check |
| `portia/ops/join.py`, `normalize.py`, `sql.py` | each op exposes its SQL fragment for compilation, separately from how it executes |
| `portia/agent/handlers.py` | `_bare_step_id` (`:618`) stops refusing cross-spec refs; `record_step`'s source resolution learns "another spec's output" |
| `portia/core/store.py` | deleted; `catalog.index_source` profiles the file directly |
| `portia/catalog.py` | no ingest; source paths recorded repo-relative |
| `portia/cli/index.py` | refuse out-of-repo paths; new import command that copies into a user-chosen location first |
| `portia/agent/prompts/tools/record_step.md` | the new-spec-vs-new-step decision; the `layer` field |
| `portia/agent/prompts/copilot.md` | proposing the project shape; what the layers mean |
| `portia/ui/` | `.sql` files in the left panel; the import flow; layer shown on the graph |
| new | spec discovery — finding every spec in the project so a name can be resolved to the spec that produces it |

---

## 5. What this does not change

- **Facts vs judgment.** Portia measures how often a table is reused; the agent decides what to do
  about it. No code ranks specs, scores pipeline shapes, or recommends a layer.
- **The SQL sandbox.** `ops/sql.py` keeps both halves. Compilation does not run anything.
- **The spec as the decision record.** `expect`, `grain`, `rationale`, `acknowledge`, drift and the
  outcome post-conditions all stay exactly where they are. The `.sql` is downstream of all of it.
- **`checks` stays read-only and unranked.**
- **One way to load data** (`core/io`), one currency (`core.table.Table`), one way to emit evidence
  (`core.serialize`).

---

## 6. The frontend check — done 2026-07-31, and it found two real bugs

> **Result of the pass described below.** Two things in `ui/` were silently broken by the engine
> work and are now fixed, with tests that fail against the old code:
>
> - **`specs_in` globbed one directory level**, so a layered project's specs — which live in
>   `specs/staging/`, `specs/marts/` — were **invisible in the left panel**. It goes through
>   `spec.discover_specs` now, which also means the app inherits the duplicate-name rule instead of
>   listing two specs that cannot both exist.
> - **The app's Run did not pass the model registry**, so a spec reading another spec's table
>   *failed in the window and worked from the CLI*. That is precisely the seam `VISION.md` says must
>   never break: `cli/` and `ui/` are two renderers of one engine.
>
> `ui/engine.py` also grew `models_in`, `stale_models` and `build` — the compiled pipeline, whether
> a generated file still matches its spec, and the app's half of `python -m portia.cli.build`. The
> engine side is done; **rendering them is not, and neither are the three design questions below.**

### Rendering — done 2026-08-01

All four, plus one thing the pass turned up.

- **The compiled models are a left-panel section.** `models/*.sql`, grouped by
  layer, its own heading rather than a row in Outputs — a run's CSV is a result, the
  pipeline is the deliverable. Selecting one shows the SQL read **off disk**, not
  recompiled from the spec, for the same reason a saved run report is read off disk:
  what the window shows has to be what a reviewer sees in the diff.
- **Staleness has two places to land**, both cheap enough for any render: a badge in
  the graph header naming how many, and a banner on the model saying what changed and
  what to do. Drift-coloured, never blocking — nothing is broken, the file is simply
  describing an older version of the decision record.
- **Build is in the toolbar**, and Run writes SQL too (see below).
- **The import flow is built** (§2.7). One destination field governs both routes — a
  browser drop and an import from disk — because where a file lands should not depend on
  how it arrived. Choosing files (natively, or by path/glob) produces a **plan**: every
  `from → to` pair listed in full, not summarised, because "3 files into data/" describes
  a plan and the list *is* one. Nothing is written until you confirm. `cli.import_data.plan`
  is the same function the terminal calls, so the two cannot disagree about where a file
  is going; it raises `ValueError` now rather than `SystemExit`, since a refusal is
  something a window has to be able to put on screen.
- **One thing the pass found:** `discover_specs` returned root-prefixed paths that
  every caller then re-joined to the root. Two accidents hid it — an absolute root
  makes the join a no-op, and `root="."` makes the prefix one — so it broke only on a
  relative root that isn't `.`, i.e. `cli.build --root sandbox/gui`. Fixed, with a test.

### The three questions — answered 2026-08-01

- **Two graphs → one canvas, two zoom levels.** The middle pane draws the project's
  models; a card opens **in place** to reveal its steps, and its neighbours reflow
  around it. Both readings of a card are true and the level says which one applies.
  Picking a spec in the left panel *navigates* to its card rather than replacing the
  view, because the canvas is the only place both levels are visible at once.
  The layout is `graph.project_layout`; it imports no NiceGUI, so this is settled by
  tests rather than by screenshots.
- **"Run" means this model and everything it reads**, and it writes their `.sql`.
  Build is the same mechanism at project scope. The behaviour already existed and was
  unnamed — `run_spec` re-runs upstreams because a table cannot be built before its
  inputs are — so `spec.upstream_of` names the set and `build_project(only=...)`
  scopes to it. Writing the SQL on every run is what narrows the staleness warning to
  what it should always have meant: *a spec edited outside the app*.
- **Layers are a grouping and a build order, never a quality ladder.** They group the
  left panel and ride on a model card as their plain name — no colour, no size, no
  per-tier roll-up, and no effect on position. The argument, because it comes up: a
  mart is more *processed*, not more *correct*. In the sandbox this was built against,
  the mart is the only model carrying blocking flags, and the defect was authored in
  staging (a `strip` that cast a numeric key to text, so `'1000.0'` never matched
  `'1000'`) and only *detected* in the mart. A ladder would rate the tier that erred
  as "raw, expected to be rough" and the tier that caught it as the good one. A
  `layer` is also a string a human typed and `validate_layer` only checks it is one of
  three words — it is the one field on that card with no evidence behind it. What says
  whether a table is right is its outcome check, per step.

### The original pass — kept for the reasoning

*Added 2026-07-30: this was not discussed while the seven decisions were being made, so it was
scheduled explicitly rather than assumed. The list below is what the pass looked for; the results
are above.*

`ui/` is an edge and must not compute (`CLAUDE.md`), so every item here is either "render something
the engine now exposes" or "the engine needs to expose something".

- **`.sql` files are a new artifact kind in the left panel.** Today it shows sources, specs, outputs,
  runs and turns. Compiled models are a sixth, and they are the *deliverable* — arguably the most
  important row on that panel.
- **There are now two graphs, and the middle pane only knows about one.** Within a spec, steps form
  a DAG (built, rendered today). Across specs, models form a DAG (new). Does the workflow pane show
  one spec's steps, the project's models, or both at two zoom levels? This collides with
  `VISION.md`'s oldest open question — *are cards steps or tables?* — and the answer just changed
  shape, because now **both are true at different levels**: a card in the project graph is a table
  (one spec, one table), and a card inside a spec is a step.
- **Layers are a grouping, never an ordering of quality.** `DESIGN.md`'s product rule applies
  directly: staging/intermediate/mart is a **kind**, so it may colour or group a graph, and it must
  never be rendered as a progression from worse to better, or as a score.
- **"Run" becomes ambiguous.** Today it runs one spec. With cross-spec references it could mean run
  this spec, or run everything it depends on first. That is `VISION.md`'s "run semantics" open
  question arriving for real, and it is an **engine** decision the UI merely surfaces.
- **The staleness warning needs somewhere to land** (§2.3) — a `.sql` on disk that no longer matches
  its spec has to be visible in the app, not only in a terminal.
- **The import flow is a new surface** (§2.7): choose a file outside the repo, choose where in the
  repo it lands, see plainly what will be copied and to where, confirm, then index. The existing drop
  zone already copies files in, so this is a narrowing plus an explicit destination step.
- ~~**One concrete breakage:** `ui/engine.py:237` opens the store.~~ *Done with §2.7 — indexing no
  longer threads a connection through at all.*

---

## 7. Deferred out of this task

Recorded so they are not silently absorbed:

- **Snowflake / cloud mode.** A product vision, not a mechanism — see `VISION.md`. Selected
  warehouse tables browsable in the portia UI with the same indexing, schema and statistics views as
  local mode, and **no data ever pulled down**. How that works gets decided when it is picked up.
- **Spec versioning and drift across a chain** — `BACKLOG.md` → Spec.
- **Run caching / partial runs** — once a project is a DAG of specs, "run only what changed" becomes
  askable. It is not part of this.
- **A `sql` step's unstable row order** makes `write_outputs` non-byte-reproducible
  (`BACKLOG.md` → Spec). It will apply to compiled output too.
