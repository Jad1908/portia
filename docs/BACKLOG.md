# Backlog — deferred ideas, by stream

*A parking lot, not a committed roadmap. Things we've decided are worth doing **later** so we can
stay focused **now**. Directional (per `PLAN.md`): each item is a one-line intent, not a spec.
**When we postpone something mid-work, add it here** so it isn't lost.*

*When something ships, move it to **Shipped** at the bottom as one compact line — the record of what
exists belongs in the code and in `PLAN.md`, and a parking lot half-full of finished work is a
parking lot nobody scans. Something we decide against stays in its stream with the reason.*

Streams mirror the architecture: `checks` · `ops` · `spec` · agent/decide · interface · scale ·
validation. See `CLAUDE.md` for what already exists.

---

## Checks — diagnosis / evidence

- **Entity resolution** — near-duplicate detection with `rapidfuzz` (blocking + fuzzy scoring). The
  brief's "hard one"; judgement-heavy, so it leans hardest on surface-don't-decide.
- **More evidence, on demand** — the checks are convenient common analyses; keep adding as real work
  surfaces gaps (temporal gaps, distribution/skew, cross-column consistency). Don't prewrite a giant
  taxonomy — the agent computes ad-hoc; these are just handy starting points.
- **Scale-aware evidence** — cap columns (not just rows) in `join_findings` samples; profile from a
  sample/schema rather than the full frame once data is too big to scan.
- **`fan_out` fires on a many:1 join that cannot inflate anything.** Seen on the 50M x 3M scale
  test: `relationship: many:1`, `result_rows` equal to the left's row count, and `fan_out` in the
  flags anyway — because `max_right_to_left` counts how many left rows share a key, which is ~17
  for any real fact-to-dimension join. Pre-existing (the frozen `otb_hotels` evidence has it too),
  and harmless at fixture size. At scale it means **the copilot sees `fan_out` on nearly every
  dimension join**, which is how a real signal gets learned as noise. The fix is probably to flag on
  what actually multiplies the *result*, not on either side's multiplicity — but that changes a flag
  the copilot reads, so it needs its own review.
- **`join_findings` cannot see nulls it is about to create.** It measures nulls in the **key
  columns** only — `n_null_keys` per side, and example null-key rows. It never looks at a non-key
  column and never reports anything about the *output's* columns, so it cannot say "under a left
  join, `event_name` will be null in 340 of the 500 result rows" even though the unmatched counts
  contain enough to derive it. Only `checks/outcome.py` catches an all-null column, and that is
  **after** the step has run — which is exactly the Run 2 failure the verification layer was built
  for, caught one moment too late. Every other op in the system has a measure-*before*; this is the
  gap. `record_step.md` even says the quiet part out loud ("a join can match nothing, leave every
  column from one side null, and still report exactly the row count you predicted") while the tool
  that exists to measure a join beforehand is structurally incapable of seeing it.

## Ops — execution (trusted transforms)

- **`apply_join` crashes instead of reporting a key-type mismatch.** Found 2026-07-30 while
  building the cross-spec work. `_select_list` emits `coalesce(l."k", r."k")` for a shared key
  name, and DuckDB refuses `coalesce(VARCHAR, BIGINT)` with a raw `BinderException` — so a join
  between keys of different kinds dies at bind time rather than producing the
  `key_dtype_mismatch` report `checks/join.py` already knows how to make. **The reachable path is
  an obvious one:** the profiler flags whitespace on a key, the agent records a `strip` (which
  casts to VARCHAR), then joins to a numeric key on the other side — clean up, then break. Note
  `checks/join.py` handles this correctly and deliberately (`_key_exprs` compares as text when the
  kinds differ, precisely so the report cannot crash); the *op* has no such care. Fix is probably
  an explicit cast in the coalesce, but it changes output types, so it wants its own review.
- **A `sql` step reports no flags, ever.** `ops/sql.py:167` hardcodes `"flags": []`, so the escape
  hatch is the least observable op in the engine — while `record_step.md` tells the agent to reach
  for it whenever `join` and `normalize` cannot express the work (aggregating, deduping, filtering,
  deriving). `join` re-exports the join check's flags and `normalize` has `coercion_failures`;
  `sql` has nothing. What a SQL step *could* honestly flag is worth thinking about before adding
  anything — it must stay facts-only, and "the query did something surprising" is not a fact.
- **`impute` op** — fill nulls (mean/median/constant/…); pairs naturally with `rationale` (the
  mean-vs-median call is decided by one-off analysis, recorded as the "why"). Good next op.
- **`dedupe` op** — resolve duplicate rows/keys; gives the `fan_out` situation a real resolution.
- **`filter` / `derive` ops** — row selection and computed columns, common and safe.
- **Promoting an op out of the hatch — wait for evidence.** All four above are now expressible in
  SQL, which is the point: **what the agent strains to write is the argument for prewriting it.**
  Promote one when real runs reach for the same shape repeatedly, or when the SQL for it is
  routinely subtly wrong. Building them now means designing an API for a user we still haven't
  watched. Things to watch for in a run log: how often `sql` is chosen over `join`/`normalize`
  where those would have fit (a signal the hatch is too *easy*), and whether the SQL steps cluster
  around one operation.
  - **No model has reached for the hatch yet** in any run to date, so the promotion evidence above
    has nothing in it. The first thing to learn is not which op to promote but **whether the hatch
    is discoverable at all** — and no run so far was designed to answer that, since none of them
    varied the description it is discovered through. Watch it on the next run that does.
- **A SQL step's provenance is thin, and might be earned back.** `join` reports what it dropped
  from each side because it knows what a key is; `sql` reports only shape (`result_rows`,
  `columns`). `checks.outcome` still measures the produced table, so the blocking gate is intact —
  but drift on a SQL step is weaker than on a join. If that bites, the fix is probably declared
  post-conditions on the step rather than trying to infer semantics from the query.

## Spec — the durable artifact

- **Structured `evidence` field** — beyond free-text `rationale`: the key numbers that justified a
  decision (`{skew: 2.3, n_outliers: 40}`), so drift can later check whether the *reason* still holds.
- **Decision lifecycle** — a clean `accept`/re-baseline command (update `expect` intentionally,
  git-diffable) so drift isn't hand-edited; `revoke`/change is just editing the file.
- **Drift calibration** — split expectations into **invariants** (must never change → hard fail) vs
  **informational metrics** (row counts that naturally move → notice, not failure). Avoids alarm
  fatigue. Maybe drift-on-rationale (flag when a decision's justifying condition no longer holds).
- **Spec versioning, and drift across a chain.** Deliberately out of the pipeline overhaul: an
  upstream re-run invalidating a downstream `expect` has no answer today. Related, and now askable
  because a project is a DAG of specs: **run caching / partial runs** — "run only what changed".
- **Outputs are hard-coded to CSV**, on an engine that reads and writes Parquet. `write_table`
  already dispatches on the extension, so pointing it at `.parquet` is one argument. What is missing
  is the *decision* — a project-level output format, or per-spec — and it pulls against the
  human-opens-it-in-Excel argument below, so decide the two together.
- **`cli.build` has no `--write`**, so writing a whole project's tables is an app-only gesture.
  `pipeline.write_outputs` is the engine half and takes one flag to reach the terminal.
- **Reproducibility of custom steps** — pin the execution environment (DuckDB version, or Python +
  deps + seeds) so a captured step truly re-runs identically.
- **A `sql` step's output row order is not stable, so `write_outputs` isn't byte-reproducible.**
  Found 2026-07-27 while freezing the golden files: the same `GROUP BY` over the same 7 rows
  returned **three different orderings in six runs** (DuckDB parallelises hash aggregation, and
  nothing promises order without `ORDER BY`). The *rows* are identical every time, so no evidence
  dict moves and nothing the copilot reads is affected — but `run --write` produces a CSV that
  re-diffs on every run, which cuts against "re-running against a changed source produces a
  readable diff". The golden harness sorts previews by value to sidestep it. Real fix is probably a
  deterministic order at the write edge (`write_outputs` sorts, or the step declares one), **not**
  an `ORDER BY` the agent has to remember.

## Agent — the "decide" layer (the copilot)

*Findings from the 2026-07-30 audit of every tool and every prompt — that session's map is the thing
to re-read before prompt work.*

- **The prompt still prices profiling as expensive, and DuckDB made it nearly free.**
  `prompts/tools/profile_source.md` says *"Expensive — the detailed rung"* and *"Not for browsing"*;
  `copilot.md` says *"Climbing costs tokens, so don't browse."* Both were written against a pandas
  engine that read a whole file to profile it. Post-migration a profile is a DuckDB aggregate, and
  two candidate joins over 100M rows measure in **0.02 s each**. What is still costly is the
  **tokens of the returned evidence**, not the work, and the prompt conflates the two. Separate
  them: keep "don't dump twenty profiles into context", drop the language that reads as "this call
  is expensive". The premise is a measurement, so this is worth doing regardless of what any run
  showed.
- **Tool descriptions reach the model as one unbroken line.** `prompts.tool()` does
  `" ".join(text.split())`, which correctly unwraps the source file's hard wrapping but also
  destroys headings, blank lines and list structure. `record_step.md` is **6,038 characters with
  zero newlines** — the `## 'sql'` heading runs into its body, the JSON example is flattened. Fix:
  unwrap *within* a paragraph, preserve blank lines and line starts. Small, but it changes every
  prompt the model reads, so it wants its own branch.
- **A grain claim can be widened until it passes — the loop's open hole.** A spec refused on
  `grain: [booking_id]` can record `grain: [booking_id, event_name]` and pass, because `event_name`
  is the column the fan-out varies over, so the claim is a tautology. That doesn't override the
  gate, it dissolves it — the same move as rewriting `expect`, on the one field the agent authors.
  Candidate fix, claim-free so it can't be dissolved: **row conservation.** A left join whose output
  exceeds its left input multiplied rows; an inner join can only shrink. Binary, no tunable number,
  independent of anything the agent says. **Open design call:** a strict inequality is not literally
  a zero, so admitting it stretches the "only zeros block" rule — but it needs no threshold, which
  is what that rule is actually protecting. Decide before building.
- **Grain examples should carry the row, not just the key.** Given only
  `{"booking_id": "B0009", "n_rows": 2}`, there is nothing in the evidence naming the rows that
  collided — and what the agent can't measure it will estimate (`handlers.profile_source`'s
  docstring, same lesson). More evidence, not a sterner prompt.
- **`--dir` never reaches the tools.** `portia_dir` flows into `build_system_prompt` only
  (`agent/session.py:73`). The MCP server runs in-process, so every handler falls back to
  `catalog.DEFAULT_DIR` unless the model spontaneously fills the optional `portia_dir` schema field —
  and `describe_source`'s shorthand schema does not even expose it, while `run_spec`'s handler
  ignores it entirely. So `chat --dir other` gives the agent a brief from `other/` and tools that
  read `.portia/`. Latent today because everything uses the default.
- **One string the model reads is still inline**, against the rule the whole `prompts/` directory
  exists to enforce: the write-declined message at `agent/ask.py:59`. It is under the 200-character
  threshold, so `tests/test_agent_prompts.py` does not catch it. It belongs in `prompts/errors/`,
  which is precisely the directory for text read at the moment the model picks its next move.
- **The three "flags" the agent sees are three unrelated vocabularies with one name.** Column flags
  from `profiling` (`high_null`, `possible_key`, …), join-proposal flags from `checks/join`
  (`fan_out`, `low_overlap`, …) and post-condition flags from `checks/outcome` (the five blocking
  zeros) all arrive under the key `flags`, and only context tells the model which set it is reading.
  Worth deciding whether that is fine or worth renaming. Related: four of them are threshold-based
  (`high_null` and `low_overlap` at 0.5, `high_cardinality` and `numeric_stored_as_text` at 0.9) and
  carry judgment-flavoured *names* — nothing blocks on them, so the facts-vs-judgment rule holds
  where it is written down, but the naming does quiet judgment anyway.
- **`describe_source` / `profile_source` still can't see a step's output** — only `join_findings`
  takes `<spec>#<step id>`. Profiling an intermediate table (what are its columns actually like
  now?) is the obvious next want; the resolver is already shared, so it is a small change. Waiting
  for a run to actually reach for it.
- **Iteration cap on a blocked step** — deliberately not built yet. A hard cap ("three refusals,
  then escalate to the human rather than loop") was in the original sketch; nothing in the loop
  counts attempts today, so a determined agent can retry indefinitely. Wait for a real run to show
  whether it loops at all before adding machinery for it.
- **`record_step` re-runs the whole spec** to measure the candidate step — O(n²) execution across a
  session. Free at fixture scale, not free at the DuckDB/Snowflake tier; needs incremental
  execution (cache each step's frame by id, invalidate downstream) before it goes anywhere real.
- **Brief growth at scale** — L1 is ~30 tokens per source. Fine at 3, unproven at 50; the source
  index will need to become searchable or group-scoped rather than exhaustive.
- **Drift rate is not in the run log** — it lives in the spec's run results rather than the event
  stream, and joining the two is its own small job.
- **Langfuse, once the JSONL hurts.** Its job is browsing a run's timeline when debugging why one
  went sideways, not computing metrics. Free either way (self-host via Docker, or the cloud Hobby
  tier). The SDK drives Claude Code as a *subprocess*, so client auto-instrumentation sees nothing —
  `events.py` is the only sane emission point, which is also why the JSONL is not throwaway work.
- **Generated data has nowhere to live.** `run --write` dumps a CSV and nothing knows it exists.
  Needs: a **code-owned** layout (`outputs/` at the project root for the data — users open these in
  Excel — index entry in `.portia/`), auto-profiling (free) but **not** auto-interpretation (a model
  turn per run), and `derived_from: <spec>` so a generated table can't be mistaken for source data.
  Path convention is not judgment: if every project invents its own tree nothing can find anything
  and the left panel has no stable view.
- **Multi-turn chat** — `session.run` is one turn per invocation today; hold the `ClaudeSDKClient`
  open for follow-ups and wire `interrupt()`. **Not a prerequisite for the app** — a turn is a
  complete unit of work, and V0 offers a fresh turn rather than a fake conversation.
- **Don't reconstruct rows from samples** — asked for raw data the agent politely assembles a
  plausible table from `samples` and hedges. Honest, but consider whether the prompt should refuse
  outright.
- **Sandbox data access** — hand the agent's code-execution a clean handle to the loaded frames so it
  can run read-only ad-hoc analysis (the imputation-shape question). Ephemeral; verdict → `rationale`.
- **Don't re-ask what's decided** — the agent asks only about what the spec hasn't answered; drift
  can re-open a specific decision. (Best shaped by real use, per the user.)

## Context catalog — `.portia/` (the agent's memory)

*Substrate built (`catalog.py`): project context + groups + per-source Layer 1 prose / Layer 2
column roles + facts; facts refresh, judgment preserved. Remaining:*

- **Role vocabulary** — the agent invents role names per run (`attribute` vs `category`,
  `unused` vs `dropped`). Fine while we learn what roles are useful; revisit once real use shows
  which ones carry weight, and only then consider constraining them.
- **Broad "how sources interact" model** — *picked back up 2026-08-04 as `KNOWLEDGE_GRAPH.md`,
  phase A built.* The design is that document; what is left of the item is its phases B–D, tracked
  under **Knowledge graph** below.
- **Groups in use** — `groups` are stored and rendered into the L1 brief, and now restated as
  `Source IN_GROUP Group` in the knowledge graph. Still nothing *reads* them until phase B.
- **Catalog storage shape** — one-file-per-source now; revisit one-file vs per-group vs harmonized
  once we've used it (kept deliberately un-locked / hand-editable).
- **Context bundle** — a token-lean projection of the catalog for the agent to consume; and a
  drift-like "facts changed since summary written" signal.

## Interface — the app

- **The tree has never been read on a big repo**, which is the case it was chosen for. The filter —
  a file appears if portia knows it, or `core/io` can read it *and* it is under `data_dir` — is what
  is supposed to carry it, and two things want watching when a real one turns up: whether "top level
  open, everything else closed" is the right default under a deep `data/`, and whether two hundred
  rows marked `not indexed` are useful or are noise. Do not add sorting or a search box before
  looking; both are the kind of thing that starts ranking (`DESIGN.md`). *Narrowed 2026-08-02 by
  `data_dir`, which removes the fixtures-and-notebooks half of the problem and none of the rest.*
- **The add-data picker lists every readable file under the chosen folder, with no cap.** Thirty is
  fine and the list scrolls; a data folder with two thousand files would render two thousand rows
  and four thousand DOM nodes. The honest fix when it turns up is probably to collapse the list by
  sub-folder rather than to paginate it — the ticks are already stored as a set of *exclusions*, so
  a folder-level tick is a set operation and not a new model. Not built: no run has needed it.
- **`data_dir` is one folder, and some projects have two.** `raw/` and `reference/` side by side
  forces you up to their parent, which pulls in whatever else lives there. A list of folders is the
  obvious shape and the picker already returns them one at a time; what stopped it was that every
  surface reading the setting (`tree`, the import destination, Settings) then has to answer "which
  one" and the import destination has no good answer. Wait for a real project that wants it.
- **Which folders are open is not persisted**, and neither is a pane you closed. Per session, like
  the canvas's pan and zoom. Cheap to keep in `.portia/` if reopening a project at the top level
  turns out to be annoying — and worth resisting until it is, since it is state to keep true.
- **"Settings do not load" was reported once on 2026-08-02 and never reproduced.** Open, close,
  reopen, all four tabs, a theme pick, a page reload, two clients and the project-picker route all
  behave, with a clean server log. The one path in that panel that could fail *silently* was closed
  — `open_dialog`'s refresh can no longer stop the dialog opening — but that is a fix for a
  candidate, not a diagnosis. If it recurs, what distinguishes the causes is whether the gear does
  nothing at all, notifies, opens onto a blank tab, or whether *every* click in the window is dead
  (which would be an orphaned Quasar dialog backdrop, seen once and not pinned down).
- **Indexing from the tree is one file at a time.** A folder full of un-indexed CSVs offers no "index
  all of these", and un-indexing is still only in the source inspector. Both want a right-click
  menu, which the tree does not have.
- **Groups are invisible in the UI.** The engine has them fully — `catalog.set_group`, a write tool
  the copilot can call, and `agent/context.py` renders them into the L1 brief so a group genuinely
  changes what the copilot sees. The app shows none of it: you cannot see a group, make one, or
  assign to one, and if the copilot creates one during indexing the only way to know is to open
  `project.yaml`. The read side is nearly free (they are already in `APP.catalog["groups"]`); the
  real question is whether the left pane nests sources under group headings (cleaner, bigger
  restructure of `artifacts.py`) or lists groups as a separate section (quick, slightly redundant).
  Membership editing needs multi-select, the fiddliest widget in the app.
- **The graph does not fit-to-content.** Zoom is built, but on a project too wide for the pane you
  zoom out by feel. That wants the layout's own dimensions, which `graph.Layout` already carries, so
  it is cheap when a project is big enough to ask for it.
- **Zoom does not change what a card shows.** At 40% a step card is an unreadable rectangle. A
  graph that dropped detail as it zoomed out would read better, but it is also the shape of thing
  that quietly starts ranking what survives — so it needs the "colour and prominence communicate
  kind, never rank" rule thought through before it is built, not after.
- **A model card cannot be opened from the graph to its `.sql`.** Clicking a source node opens its
  catalog entry and clicking a model header opens its spec; the compiled file is only reachable from
  the left panel. Probably a second affordance on the card rather than a different click.
- **The run report's table preview cannot be opened.** Found 2026-07-28 by driving the app in a
  browser. Clicking the `preview · N rows × M columns` expansion bubbles up to the step block's
  `on("click")`, which calls `_select_step` and re-renders the report — so the preview collapses as
  fast as it opens. The fix is to stop the expansion's click propagating.
- **The left pane can only see specs under `specs/`, and nothing makes the agent write there.**
  The path is the agent's choice — `record_step` takes whatever it is given, and `specs/<name>.yaml`
  is a *hint* in prompt text, not a constraint. A spec written to `phq.yaml` therefore exists, runs,
  and is invisible in the app, which reads as "it didn't create one". Two candidate fixes and they
  are not equivalent: glob the project recursively (the pane shows what is there), or make
  `record_step` refuse a path outside `specs/` (the tree stays predictable, which is what "generated
  data has nowhere to live" also wants). **Prefer the second.**
- **A selection still rebuilds a whole pane to move one highlight.** Fixed the visible half on
  2026-08-02 — the scroll position survives a rebuild (`assets/scroll.js`) and the middle pane draws
  in one pass so a click no longer paints a blank frame — but the rebuild itself is still there:
  clicking a row in the left panel discards and re-creates all of it so one `--selected` class can
  move. The honest fix is to repaint the affected rows in place, as `transcript._pick` already does.
  What makes it awkward is that `ui.refreshable` gives no handle on the elements it built, and a
  module-level registry would be shared by two tabs on one project — which `state.py` says is the
  intended case. It costs a DOM patch per click today, not a frame.
- **A source's catalog entry replaces the workflow pane** while you read it, rather than sitting
  somewhere the graph stays visible. It works and is discoverable ("Back to workflow"), but the
  middle pane is now two things.
- **A denied write leaves no note in the spec.** The transcript records it; nothing durable does.
- **Drag-and-drop is unverified.** The picker and the "add by path" field are what got tested.
- **The add-data copy is derived from the loader; nothing else is.** `screens._formats()` reads
  `supported_suffixes()`, so registering a format updates the screen with no edit. Worth doing the
  same wherever else a format is named in prose.
- **`tests/test_ui.py` renders nothing.** It covers state, badges, decisions and the folder chooser
  — and not one component that draws a table. That is why the preview bug above survived, and why
  the DuckDB migration's UI changes had to be checked by driving a browser by hand. A handful of
  tests calling `components.table_preview` and `workflow._table` with a real `Table` would catch
  both.

## Scale — data tiers

- **A profile's memory is bounded by cardinality** — the one part of the scale promise that did not
  land (`DUCKDB_MIGRATION.md` §13). Exact `count(DISTINCT)`, exact quantiles and the modal-value
  group-by are all O(n) or O(distinct). Approximating the quantiles is safe and 4× cheaper;
  approximating `count(DISTINCT)` is **not**, because `possible_key` and `constant` are equality
  tests against it and HyperLogLog came back 13.6% low on a 6M-row key. The interesting sub-problem:
  a cheap *exact* answer to the only question `possible_key` asks — `count(DISTINCT c) = count(*)` —
  which does not need the count itself.
- **`core.io.connect()` sets no `memory_limit`, so DuckDB helps itself to 75% of RAM.** A 2 GB limit
  did the same work in the same wall time at 5.0 GB peak instead of 6.8, because DuckDB spilled
  rather than failed. A conservative default looks close to free — but 2 GB was ample for that
  workload and might not be for a large sort. Decide against PHQ data.
- **SQL steps are the one memory-bound op.** The escape hatch materializes its declared inputs,
  because that isolation is what makes the sandbox independent of reading the query correctly
  (`DUCKDB_MIGRATION.md` §6.1). Making it lazy needs a parse-tree check on table references to
  replace what isolation currently gives for free.
- **A referentially-consistent subset extractor.** Slicing every table to rows reachable from a
  chosen set of ids preserves schemas, key overlap, spelling mismatches and fan-out. Naive
  per-table sampling does **not**: independently sampled tables stop sharing keys and every join
  looks empty. Scale is no longer the reason to want it — **repeatability is**, and `EVALUATION.md`
  has eight anecdotes and no re-runnable fixture.
- **Snowflake tier** via the Snowflake MCP server — push compute to the warehouse, pull small
  results. `core.table.Table` is the seam: a name, a query and a connection is not a DuckDB-shaped
  idea, which was the point of building it that way. The product vision for it is in `VISION.md`.

## Knowledge graph — `portia/knowledge/`

*Phase A is built (`KNOWLEDGE_GRAPH.md` → Status). These are what it deferred, plus the design's
own open questions where the code now has something to say about them.*

- **Replacing the L1 index with traversal — the other half of phase D.** What shipped is the
  trim: each source's line lost its column count and candidate keys and kept its name and one
  sentence. §1.4 wants the list itself gone, so the agent walks outward from wherever it is
  instead of being handed everything. **Not taken, and the reason is the interesting part** — the
  premise is "fine at 3 sources, unproven at 50", and unproven is not measured. What would have to
  be true first: a project big enough for the cost to be visible, *and* a re-runnable fixture that
  can say whether the copilot got worse — because the failure mode is subtle (generic judgment
  from missing context), it is exactly what `context.py` says pushing L1 exists to prevent, and
  §9.4 calls D the riskiest and insists it be evaluated on its own.
- **Two of §5's three write moments are hooked up; saving a spec is not.** `cli.index` refreshes
  the graph after indexing and `measure_overlaps` refreshes before it writes, both best-effort.
  `record_step` does not, so a spec written mid-conversation is invisible to `graph_lookup` until
  something else triggers a refresh. Cheap to add; left out because a refresh on every recorded
  step is a Neo4j round-trip inside the tightest loop in the product, and nobody has felt the gap
  yet.
- **Does anything measure outside indexing?** (§7, still open.) `measure_overlaps` is available in
  any turn and nothing stops the agent reaching for it mid-conversation — but only the indexing
  prompts *ask* for it. Whether a conversation-phase prompt should, and whether a user-invoked
  sweep should exist as an escape hatch, is unsettled. §6.5's three reasons against sweeping are
  unchanged.
- **Column lineage through the `sql` hatch** (§4.2, §7). A model downstream of a `sql` step gets no
  Column nodes and lands in `BuildResult.unresolved`. That dict is the evidence to decide on:
  if real projects run mostly through the hatch, `sqlglot` earns its place; if they don't, the
  coarse answer is the right one. **Don't buy the parser before reading the number.**
- **A build never notices a spec it did not run.** `is_stale` compares a `.sql` to its spec and
  `catalog.is_stale` compares a file to its record; the graph has both fingerprints on its nodes
  and nothing yet asks the question. Cheap, and it is what §4.5's "walk forward from a changed file
  and name the affected model columns" needs.
- **Composite keys** (§7). The schema knows single-column overlaps only. Nothing in phase A hits
  this — a composite join key already produces one `DERIVES_FROM` edge per key column — but
  `OVERLAPS` will.
- **Orphan nodes are pruned only when they have no relationships at all.** A Column that lost its
  table but kept a measurement stays, on purpose (§4.5: mark stale, never delete). Nothing marks it
  yet, so it is currently indistinguishable from a live one.
- **Drawing it** (§6.9). The store and the picture are separate purchases, and the collision to
  settle first is that a force-directed layout ranks by connectivity, which `DESIGN.md` forbids.
  The Neo4j browser is the answer until that has an explicit answer in `DESIGN.md`.
- **`profiling.py` computes `min`/`max` per column and `catalog._column_facts` drops them**
  (§6.5). A fact the profiler already paid for, and exactly what the agent needs to judge whether a
  pair is worth measuring in phase C.

## Core / infra

- **README says nothing about auth — a decision awaiting the user, not an oversight.** `PLAN.md`
  → "Auth posture" settles what portia *does* (no auth code, ever) but not what the README
  *claims*. Naming the API key as the supported path is the safe posture; saying nothing is also
  neutral and is where it stands today. It is product positioning on the exact point the user
  cares about, so it is theirs to call — **don't quietly write it either way**. One line, whichever
  it is.
- **`uv`'s cache prunes under disk pressure and takes the venv's dev tools with it.** Hit three
  times on 2026-07-28 while real data filled the disk: `pytest`, `ruff`, `mypy` and `pre-commit`
  are hardlinks into `~/.cache/uv`, so they vanish and every command fails with
  `No module named pytest`. Fix is `uv pip install -e ".[dev]"`. Worth a line in the README if it
  bites anyone else.
- **CI** — run the hooks + tests on each PR. *Declined for now (2026-07); revisit if collaborators
  join.*

## Validation & product (from the brief §9)

- **The decisive experiment** — run the engine on real, non-synthetic, too-big-to-eyeball data from
  actual work. Does the frontier-agent baseline degrade at real scale? Nothing matters more.
- **The five conversations** — describe the tool to five DS/AI-engineers; ask what they do today
  instead. "I'd use this" from the founder is necessary, not sufficient.

---

## Shipped

*One line each. The code is the description; these exist so an item isn't re-proposed, and so the
odd finding worth carrying forward isn't lost with it.*

**Engine**

- **The escape hatch — DuckDB SQL** — 2026-07-26, `ops/sql.py`. `ops = {join, normalize, sql}`.
  Built by hand it produces the hotel answer key exactly (14 rows, 136,240, zero inflation) — *by
  construction, not by a copilot.*
- **Nothing can aggregate** — closed by the hatch, same day.
- **The verification loop** — 2026-07-26, `checks/outcome.py` + `record_step` executing before it
  writes. `no_matches` needed no special case: it surfaces as `empty_output` or
  `source_did_not_contribute`, derived from the output rather than the op's flags.
- **`expect` vocabulary is hand-maintained** — fixed: each op declares `PROVENANCE_KEYS` beside the
  code that emits them, and its tests assert the declaration still matches a real run.
- **`expect` values aren't shape-checked, only their keys** — fixed: `record_step` compares each
  prediction's kind against the value the step just reported. No acknowledgement for this one —
  unlike a zero, a wrong-typed prediction is never legitimate.
- **A partial join failure is invisible to a zero-only blocking rule** — *answered with evidence
  rather than a flag.* Catching it with a rule needs a threshold, i.e. judgment. It needs no rule:
  now that `join_findings` reaches a step's output, the same call returns the unmatched row and the
  fan-out example before anything is written. "Invest in richer observations, not a decision layer",
  demonstrated. Whether the agent *looks* is a prompt question now, not a structural one.
- **DuckDB tier** — 2026-07-28, `DUCKDB_MIGRATION.md`. Real PHQ data: 4.82 GB indexes in 32 s, a
  50M × 3M join is diagnosed in 3.8 s, peak memory bounded by the largest table. Two of the three
  traps the spec named turned out wrong; §6.1 and §13 are the record.
- **More loaders — Parquet** — 2026-07-28. `core/io._FORMATS` registers a reader, its options **and**
  how to write the format back, so a format you can load but not save can't happen.
- **Workflow chaining across specs** — designed 2026-07-30, shipped 2026-07-31 (`PIPELINE.md`). A
  spec references another's output by plain name; portia derives the run order.
- **`write_outputs` is all-or-nothing** — one file per model 2026-07-31; finished 2026-08-02, when
  the app turned out to write only the *open* model over the top of the last one.
- **`handlers.profile_source` re-reads the file rather than the store** — moot: the store was
  removed 2026-07-31. *Worth keeping the finding that led there: it was written at index time and
  read by almost nothing. A fast copy nobody reads is not a cache.*
- **Ingesting Parquet inflates it ~2.3× on disk** — moot with the store, but the measurement stands
  and is why parquet-in-the-repo is the answer if reads get slow: 6.2 GB of Parquet produced a
  **19.2 GB** `store.duckdb`. DuckDB's native format compresses, but not as hard as Parquet+ZSTD.

**Agent**

- **The copilot loop** — `portia/agent/`: in-process MCP server, `AskUserQuestion` routed to the
  human, event stream, chat CLI.
- **Context flow** — L0+L1 composed into the system prompt, the L2/L3 split, groups wired end to
  end. **Shipped but NOT validated** — the demo that appeared to prove it used a brief that stated
  the answer outright (`EVALUATION.md` → "A retracted result"). The plumbing is right; the evidence
  was not.
- **Pro-auth verification** — 2026-07-25: the SDK drives a bundled Claude Code binary, so it
  authenticates off the local login and meters against the **subscription**. `PLAN.md` → "Auth
  posture" for what portia claims about it; the posture is unchanged by the good news.
- **Run log + the metrics that need no labels** — 2026-07-29, `portia/runlog.py` +
  `python -m portia.cli.runs`. Two surprises in `EVALUATION.md` → "The run log".
- **`copilot.md` told the model something false about itself** — "You never see raw rows." It does:
  `join_findings` returns up to 12 complete rows. Fixed 2026-07-31 by naming the tool and saying
  why. *The kind of thing to look for again: the code was right and the prompt was wrong.*
- **One tidy home for every injected instruction** — every tool description and task prompt now
  lives under `agent/prompts/`, enforced by `tests/test_agent_prompts.py`. One straggler remains
  (see `ask.py:59` above).
- **Prompt work the pipeline overhaul required** — `record_step.md` teaches new-spec-vs-new-step and
  the `layer` field; `copilot.md` covers proposing the project's shape.

**Knowledge graph**

- **Phase D (half) — the L1 trim** — 2026-08-04, `agent/context.py`. Each source's line dropped its
  column count and candidate keys, which is what `describe_source`/`profile_source` are for. The
  index itself stays; replacing it with traversal is above, with what would have to be true first.
  *Found on the way: `_first_sentence` had been appending a full stop unconditionally, so a
  one-sentence summary reached every system prompt ending in `..`*
- **Phase C — the agent picks the pairs** — 2026-08-04, `checks.join.column_overlap`,
  `knowledge/measure.py`, `measure_overlaps`, rewritten indexing prompts. The reason on a pair is
  **required in code**, because §4.4's whole argument is that the sentence is what stops a zero
  reading as a dead end. *The finding worth carrying: staleness turned out to be a **read-time**
  question — fingerprints on both ends and a comparison in the query — so "mark, never delete" is
  free rather than disciplined, and nothing has to invalidate anything when a file changes.*
- **Phase B — the read path** — 2026-08-04, `knowledge/query.py` + the `graph_lookup` tool. Fixed
  queries, taught as a *router* that comes before L2 rather than a rung above L4. *The finding
  worth carrying: §7's "what may a query return at once" needed no cap — asking about a table
  returns tables, so the fifty-edge answer is never built.*
- **Phase A — the write path** — 2026-08-04, `portia/knowledge/`. The catalog and the specs read
  into nodes and edges, column-level `DERIVES_FROM` included, with nothing run and no connection
  opened. `python -m portia.cli.knowledge` builds and prints it; `--write` needs Neo4j and nothing
  else does. *The finding worth carrying: lineage came out of `ops.join.join_columns` rather than a
  second implementation of the `_x`/`_y` rule — the same argument `OpResult.compiled` makes about
  compilation not being a second rendering of execution.*

**Interface**

- **The three-panel app** — V0 2026-07-26, `portia/ui/`. Drives a turn, catches every question and
  write confirmation; the no-terminal audit in `VISION.md` passes end to end.
- **Tool results are missing from the event stream** — fixed 2026-07-26 (`events.TOOL_RESULT`).
  `cli/chat.py` still ignores the kind **deliberately**, so terminal transcripts stay comparable
  across runs already scored; the log stores them and `cli.runs show` renders them. Logging and
  rendering are different jobs; only the second had a reason to stay still.
- **Nothing is editable** — 2026-07-27: brief editable from the toolbar, a source's summary and
  roles editable in place or correctable by asking the copilot. Both write through
  `catalog.set_interpretation`, which touches judgment and never a measured fact. *The brief moved
  again 2026-08-02: a row at the top of the tree, opening an editor in the middle pane.*
- **The left pane was six flat lists** — 2026-08-02 it became the project directory, filtered
  (`ui/tree.py`). Reverses `DESIGN.md`'s "curated, not a disk tree"; the argument that failed is
  kept there. It also answers `VISION.md`'s last open layout question, left-panel curation: the
  curation is a **filter**, not a layout.
- **Adding data was three routes and two half-buttons** — rewritten 2026-08-02. The screen now asks
  *which folder in this repo is the data* first, in an in-page browser, and stores the answer as
  `data_dir` in `project.yaml` — the first durable project setting after the brief, and what scopes
  the left pane. Importing outside data is a folded second section defaulting to that folder, and
  **one** button copies the plan and profiles the ticks. The browser drop zone was removed rather
  than fixed (`DESIGN.md` → Removed): it duplicated both other routes and was the only one that
  could refuse a file for a reason portia could not explain.
- **The chrome was in the wrong places** — the rest of the same overhaul, 2026-08-02, and the theme
  is that a control belongs where the thing it acts on is. Preferences moved out of the toolbar into
  a tabbed `ui/settings.py`; the four run actions moved onto the **middle pane** and became icons
  (Run and Build keeping their word, ruled off by `button-split`); a side pane is closed by dragging
  its edge past its floor and reopened from its rail, so the two pane toggles are gone; the brief
  became a row at the top of the tree opening an editor in the middle pane, so its button is gone
  too. The toolbar is a mark, a name and a gear. **Driving it turned up two things reading it never
  would**: the workflow pane rendered 404px inside a 1019px panel with the transcript rail floating
  mid-window, and the pane floors doubled as the close threshold and closed panes under a drag that
  only meant "narrower".
- **The graph is a fixed grid** — pan and a dot grid 2026-07-27; zoom 2026-08-01 (pinch,
  two-finger, buttons, anchored at the pointer).
- **The add-data screen said nothing while it worked** — fixed 2026-07-28. It used to fire the
  interpret turn on a screen with no transcript: you paid for a turn and watched a blank page.
- **A copilot turn disappears when the window does** — closed in three parts: run reports as
  markdown (2026-07-27), the turn as JSONL (2026-07-29), and a **Turns** section replaying it.
- **The import flow has no surface** — built 2026-08-01. One destination field governing both
  routes, a plan listing every `from → to` pair, nothing written until confirmed.
- **The source preview loads the whole file to show 15 rows** — fixed by the DuckDB migration.
- **Semantic interpretation** — `catalog.set_interpretation` + the agent's `interpret` flow.
