# portia — read this first

**Before doing any work in this repo, read these docs.** They define the project's direction,
stack, and product vision. Read them every session, before proposing changes or writing code:

- `docs/PLAN.md` — direction: vision, non-negotiables, build order, **where we are now**
- `docs/EVALUATION.md` — how we *would* measure the copilot, and what has actually been measured,
  which is much less than the file used to imply. **The eight runs in it are pipeline shakedown, not
  scores** — they held every prompt at its first draft, so they found real defects in portia's code
  and nothing about the copilot's judgment. Trimmed hard on 2026-08-04 for exactly that reason.
  Read it before trusting any claim that a part of the loop works, and **do not cite a run as
  evidence about a model**; it also records a result we retracted, and why.
- `docs/TECH_STACK.md` — the tech stack and the reasoning behind it
- `docs/VISION.md` — product vision & UI flows (the three-panel app), incl. the **V0** spec and its
  no-terminal audit. V0 *drives* the copilot rather than viewing it; the file records why the
  read-only draft was wrong.
- `DESIGN.md` — **the look**: mode-aware tokens, type, components. Required reading before writing
  any UI. It owns appearance; `VISION.md` owns layout and behavior. Note its one product-specific
  rule — *color and prominence communicate kind, never rank* — which is "facts vs judgment" applied
  to pixels.
- `docs/brief.md` — the original working brief (foundational context)
- `docs/DUCKDB_MIGRATION.md` — **the scale tier**, shipped 2026-07-28, compacted 2026-08-02 to what
  the code cannot say about itself. The part worth your time is **§6.1, §13 and §14, where
  measurement contradicted the plan**: the specced sandbox turned out to be impossible, a profile's
  memory still scales with cardinality because `possible_key` needs an exact `count(DISTINCT)`, and
  a profile turned out to be *one scan plus two queries per column* — which on a CSV meant one full
  parse per column, and 69× between the same data as CSV and as Parquet. Required reading before
  touching `checks/`, `ops/`, `core/io.py`, or anything that looks like a performance fix. **§3 was reversed on 2026-07-31** — the ingested store is gone — and it is kept, with the
  reasoning that failed, because it is the clearest example in this repo of an argument that read
  well and did not survive contact with how the code was actually used. *Section numbers are load-
  bearing: a dozen comments in `portia/` and `tests/` cite them.*
- `docs/PIPELINE.md` — **SQL as the artifact.** Designed 2026-07-30, shipped 2026-07-31.
  One `.sql` per spec, dbt-shaped and committed · cross-spec references **by name** with portia
  deriving the run order · an optional `layer` whose *absence* is the simple case · the agent
  deciding "new spec or new step" · and indexing restricted to files already in the repo, which
  **retired `.portia/store.duckdb`** (deleted — `core/io.connect` and `catalog.is_stale` are what
  survived it). Required reading before touching `spec.py`, `pipeline.py`, `ops/`, `catalog.py` or
  `cli/index.py`. **§6 is where the app half landed** — the compiled models are rendered, and the
  three design questions are answered there (a card is a table *or* a step depending on zoom level ·
  Run means this model and everything it reads · layers group and order, never rank).
- `docs/KNOWLEDGE_GRAPH.md` — **designed and revised 2026-08-04; phase A of §9.4 is built and
  B–D are next.** Read the Status note at the top for what shipped and the four things building it
  settled. Two halves that only pay off together (§2): **measured relationships** between sources —
  because nothing in portia holds one and `join_report`'s answer dies with the turn — and
  **column-level lineage**, which portia's specs already know and no surface can answer.
  **Neo4j, settled**; §3.2 records what each rejected option was rejected *for* (NetworkX for the
  query language, not for scale — the argument to not re-run). Source *and* model columns are nodes
  (§4.1). Six relationship kinds split by facts vs judgment (§4.2), where `DERIVES_FROM` is
  structural because the spec already says it, and carries `via` + a `step` pointer rather than a
  vocabulary of transform names. **§4.4 is the one to read twice**: a measured zero means *no shared
  values*, never *unrelated* — `France` vs `FRA` — so measuring raw values is least reliable exactly
  where portia is most needed, and the fix is that **the edge carries the reason the agent asked**
  (samples stay off it; the agent climbs the ladder for those). §4.5 makes a stale measurement
  detectable off fingerprints portia already computes. **§4.8 defers Entity** — the one node kind
  with nothing underneath it, and the one whose names diverge because there is no list to read
  first, unlike Group. **§5.1 is the central mechanism: the agent picks which pairs get measured,
  during indexing** — not a code prefilter (which §6.5 keeps, with the three reasons it failed) and
  not a sweep. §5.2 keeps a rule we wrote and dropped. §6.1: the graph surfaces edges and **never
  ranks** them. §7 lists what is open and what was closed on purpose. §8 keeps a gate we removed and
  why it did not hold. **§9 is the loop**, and it is the half that decides whether any of this is
  worth having: the graph sits *before* L2 as a router, not above L4 as a rung · indexing is
  rewritten and the conversation phase is not · two new tools, not one · and a four-phase order
  where the read path precedes the agent-writes-measurements path, because at source 23 the agent
  queries the graph while filling it.
- `docs/BACKLOG.md` — parking lot of deferred ideas, by stream, with a compact **Shipped** list at
  the bottom. Not required reading; scan it when picking the next thing to build, and **add to it
  whenever we postpone something mid-work.**

## How we work here

- **Plans stay directional**, not prescriptive step-by-step specs — they go obsolete. Give
  vision, stack, and watch-outs; let specifics emerge from real work.
- **Rigor lives in the modelling / deterministic code** — the LLM orchestrates, explains, and
  asks; it never eyeballs the data to produce numbers.
- **The agent may author a transform; it may never author a number.** The SQL escape hatch
  (`ops/sql.py`) lets the agent write a step we didn't prewrite, which is close to letting it
  author analysis — the thing this project forbids. What holds the line is that a custom step is
  captured **verbatim** in the spec, executed by the engine, and measured by the same harness as
  every other op. It is a *step*, reviewable in a diff, not a hidden reasoning act. Everything the
  agent asserts about the result still has to come from a check.
- **Facts vs judgment — the sharp line.** Deterministic code owns *facts and consequences*
  (measurements, and what each available action would do — computed, never guessed). The **agent**
  owns *judgment*: which facts are material (given the goal/domain/context the engine can't have),
  what to ask, how to frame it, what to recommend. **Checks surface evidence generously; they must
  never rank, prioritize, score "impact", or suggest an answer** — that bakes context-free judgment
  into code that then fails on hard, subtle problems at scale. Invest in *richer observations*, not
  a decision layer. (The tool calls stay deterministic; the agent never writes its own analysis.)
- **DuckDB throughout the engine** (migrated 2026-07-28, `docs/DUCKDB_MIGRATION.md`). pandas
  survives in exactly four places and each is deliberate: the fixtures, `load_frame` for small
  reads, the renderers, and the SQL hatch's sandbox boundary. `tests/test_table.py` fails if
  anything else pulls a whole relation into memory.
- **Budget: Claude Pro only** (no API, no Max) — develop on a cheaper, smaller model at low
  effort; keep loops token-lean (compact profiles/schemas, never raw data).
- **Don't start building without agreed direction.** Ask before large scaffolding.

## Code conventions — built to scale, DRY from the start

We will build *many* tools and checks. They must **compose**, not accumulate into spaghetti with
ten different ways to do the same thing. These seams are non-negotiable; respect them before
adding code, and extend them rather than working around them:

- **One way to load data, and one way to write it.** All file I/O goes through **`portia.core.io`**
  — `load_table` for the engine, `load_frame` only for small reads, `write_table` for output. Never
  call `pd.read_csv` or name a DuckDB reader in a tool, check, notebook, or CLI: a format registers
  its reader, its options **and how to write it back** in `core/io.py`, once, so support can't
  arrive on one tier and not the other, and a format you can load but not save is a trap you find
  at the end of a long run. `NA_TOKENS` lives there too, because a null rate that depends on which
  reader ran is the exact disagreement `core/present.py` exists to stop.
  - **CSV and Parquet.** Parquet carries its schema, so the CSV reader's sniffing stops being part
    of the answer — and it is ~3.3× smaller (measured, ZSTD, on a real extract). Converting is a
    one-off `COPY … TO … (FORMAT PARQUET, COMPRESSION ZSTD)`, not something portia ships: a tool
    that rewrites someone's data is not a data-harmonization concern.
  - **The currency is `core.table.Table`** — a name, a `SELECT`, and a connection. A handle, not
    data. `head()` and `rows()` are the only ways out and both are capped; nothing else in `portia/`
    may materialize a relation (there is a test).
- **One way to emit evidence.** Checks return **compact, JSON-serializable dicts** built with
  **`portia.core.serialize`** (`to_jsonable`, `round_float`, `to_json`). Never hand-roll
  numpy→python coercion or float rounding — `int64` isn't JSON-serializable and `NaN` isn't valid JSON.
- **Checks are small, pure functions**: `check(inputs) -> structured evidence dict`. Deterministic
  in, structured out. **No printing, no human-formatting, no side effects inside a check** —
  rendering for humans/CLI/UI lives at the edge (e.g. `render_text`, the `python -m …` entrypoints).
- **Compute stays behind the checks layer** so pandas → DuckDB/Snowflake is a swap, not a rewrite
  (see `TECH_STACK.md`).
- **One home for prompts — inline prompt text is forbidden.** Every string the model reads lives
  in **`portia/agent/prompts/`** as markdown and is loaded with `prompts.load` / `prompts.tool` /
  `prompts.task`. Never write instruction text into a Python string — not a module constant, not a
  `@tool` description, not an f-string, not "just this once". This is enforced:
  `tests/test_agent_prompts.py` fails on any non-docstring string literal over 200 characters
  anywhere in `portia/`, and separately on any `@tool` that doesn't take its description from a
  file. Docstrings are exempt — they're written for us, not the model.
  *Why it's a rule and not a preference:* `record_step`'s description once lost a sentence saying
  steps chain, and the copilot concluded portia couldn't express a two-hop join and told the user
  to go use dbt instead. Prompt text is the least stable, most performance-sensitive part of the
  system; it has to be diffable and reviewable as prose. (Schema *field* labels like
  `"Indexed source name"` stay inline — see `prompts/README.md` for that boundary.)
- **Reuse before you add.** Before writing a helper, look for an existing one. Shared helpers live
  in a shared module; never copy-paste a utility across tools.
- **Named constants, not magic numbers** — thresholds live as module constants in one obvious place.

**Package layout — one home per concern; don't let things pile up flat in `portia/`:**

- `portia/core/` — shared seams: `table.py` (**the currency** — a lazy relation) · `io.py` (loading
  — **the only way a file becomes a table**, plus `connect()` for the connection to read it on;
  there is no ingested store any more, `docs/PIPELINE.md` §2.7) · `serialize.py` (compact JSON
  evidence) ·
  `present.py` (**one way to show a measured value to a human** — rates, counts, a value on one
  line. Every surface renders the same numbers; the day the terminal and the app disagree about a
  null rate is the day someone has to work out which one to believe.)
- `portia/checks/` — the deterministic checks layer (read-only **diagnosis**, facts only):
  `profiling.py`, `join.py` (`join_report` = key-level facts; `join_findings` = facts + example
  rows), `outcome.py` (post-conditions on a frame an op **produced** — every other check reads
  inputs). Surface evidence generously; never rank or recommend. A check + its `render_*` live
  together; add new checks in this shape.
  - **`outcome.BLOCKING_FLAGS` is the one place a check can stop the loop, and it holds zeros
    only** — an empty table, a column that went in with data and came out all-null, a source that
    contributed nothing, a declared `grain` that isn't unique. A zero needs no threshold to be a
    fact. The moment a tunable number appears in that set, code is deciding what counts as bad,
    which is the deterministic-planner mistake this project already reversed. Rates and ratios are
    reported and never block.
- `portia/ops/` — the execution layer (**produces** data): `apply_join`, `apply_normalize`
  (coerce/clean columns), `apply_sql` (the escape hatch — one DuckDB `SELECT` over the tables the
  step declares in `inputs`, for work the prewritten ops can't express: aggregating, deduping,
  filtering, deriving). Every op returns an `OpResult` (a `Table` + unsuppressable provenance
  report), so producing a step that fans out to 80M rows costs one `count(*)`.
  - **The hatch is sandboxed in two independent halves, and both stay.** `check_sql` refuses
    anything that isn't a single `SELECT`, readably, before DuckDB is touched; the query then runs
    on a connection holding **exactly the declared inputs and nothing else**, with
    `enable_external_access=False`. The string check is bypassable on purpose — it exists to give a
    good error — and the isolation is what actually holds. `session.py` gives the agent no
    filesystem tools; the hatch must never quietly hand them back.
  - **Its inputs are materialized, so SQL steps are the one memory-bound op**, and that is a
    consequence of the sandbox rather than an oversight — attaching the store instead was tried and
    is infeasible (`DUCKDB_MIGRATION.md` §6.1). Don't "fix" it without reading that.
  - **Resist adding a prewritten op for something the hatch already does.** What the agent strains
    to express in SQL is the evidence for which op deserves promoting — build the op when a real
    run reaches for it, not before (`BACKLOG.md`).
- `portia/agent/` — the **decide layer, as an agent rather than a module** (Claude Agent SDK).
  `handlers.py` = the callable surface as pure `(args) -> jsonable dict` functions, no SDK import,
  testable without it · `tools.py` = `@tool` wrappers + the in-process MCP server, the only place
  the SDK meets the engine · `context.py` = the L1 project brief · `events.py` = SDK messages
  normalized to portia events (the seam the UI sits on) · `ask.py` = intercepts `AskUserQuestion`
  so decisions reach the human · `session.py` = the options block + client lifecycle ·
  `prompts/` = **every instruction the model reads** — L0 system prompt, the L1 brief
  template, one file per tool description, one per CLI task. Nothing the copilot reads is
  embedded in a Python string; see `prompts/README.md`. Requires the `agent` extra.
  - **No built-in filesystem or shell tools**, so it physically cannot read raw data — its whole
    view is the checks' evidence.
  - **Context arrives in layers, cheapest first.** L0 how-to-work + L1 *this project* (prose,
    groups, one-line source index) are **composed into the system prompt** — pushed, not fetched,
    because a tool the agent *may* call is one it will sometimes skip, and skipping the project
    context makes its judgment generic. Everything above is pull-based and the agent decides when
    to climb: L2 `describe_source` (meaning, no stats) → L3 `profile_source` (full facts) →
    L4 `join_findings`. **Adding a tool means placing it on that ladder and saying so in its
    description** — the description is what teaches the model when to climb.
  - Do not add a code layer that ranks decisions or suggests answers — see "facts vs judgment".
- **Durable artifacts** (git-diffable YAML, the residue that makes this a product, not a script):
  - `portia/spec.py` — the **spec** (*what we did to the data*). **A spec produces one table**, and
    it is about to compile to one `.sql` file — dbt-shaped, committed, referencing other specs by
    name (`docs/PIPELINE.md`). The SQL already exists: `run_spec` composes each step's `SELECT` into
    the next and discards the result at the end of the run. Keeping it, as named blocks rather than
    nested sub-selects, is the whole of that change.
    Contents: sources + decided steps + `expect` + `rationale` + an optional `grain` claim and an
    `acknowledge` list; `run_spec` re-executes,
    detects drift, and attaches each step's measured `outcome`. **Drift and outcome are different
    questions** — drift asks whether the prediction held, the outcome asks what came out. A correct
    prediction about a broken join is still a broken join. Recording a step **runs** it
    (`handlers.record_step`), so a step that hits a zero is never written; overriding means writing
    `acknowledge` into the YAML, where the human reads it in a diff.
  - `portia/pipeline.py` — the **compiled pipeline** (*what someone else can run*). One `.sql` per
    spec under `models/`, each step a named CTE, sources named and created by a generated
    `_sources.sql`. A **build output**: regenerated from the spec, not hand-edited (the spec holds
    the `rationale`, `expect` and `grain` that SQL cannot), but committed, because the pipeline is
    the deliverable. `build_project` runs the project in dependency order; `is_stale` compares a
    file's header fingerprint to its spec. **Compilation and execution are separate paths and
    `tests/test_pipeline.py` pins them together** by running both and comparing the tables.
  - `portia/catalog.py` — the **context catalog** (*what the data is*), in `.portia/`: project
    context + groups + per-source metadata (Layer 1 prose `summary`, Layer 2 per-column `role` +
    check facts). The agent's memory. **Update rule: facts refresh, prose/roles are preserved** —
    corrections are never clobbered (facts vs judgment, applied to updates).
  - `portia/knowledge/` — the **knowledge graph** (*what the tables and columns are to each other*),
    in Neo4j (`docs/KNOWLEDGE_GRAPH.md`). Three modules and the split is the seam: `schema.py` (the
    closed vocabulary + a `Graph` — imports no driver) · `build.py` (catalog + specs → a `Graph`;
    **runs nothing and opens no connection**, so column lineage comes off `ops.join.join_columns`
    rather than a second implementation of the `_x`/`_y` rule) · `store.py` (the only module that
    knows what Cypher is, behind the `graph` extra). **A rebuild owns the structural half and
    nothing else** — stale structural edges are deleted, `OVERLAPS` is never touched, because an
    absent edge has to keep meaning *nobody measured* (§4.4). Built by
    `python -m portia.cli.knowledge`; nothing writes it automatically yet.
  - `portia/runlog.py` — the **run log** (*what the copilot did*): one JSONL per turn in
    `.portia/runs/`, one `Event` per line under a header naming model, effort, prompt and portia
    sha. Read with `python -m portia.cli.runs`. **Project-local, with no central store and nothing
    that prunes** — a turn only means something beside the catalog it read, so deleting a project
    deletes its turns and nothing aggregates across them (`EVALUATION.md` → "Where the logs live").
    Two rules hold it in place. **It is teed at the
    edges** (`cli/chat.run_and_render`, `ui/turn`) and never inside the engine — the moment
    `events.py` writes files it stops being a seam and becomes a logging framework. And
    **`summary` counts; it never scores.** Rungs pulled, questions asked, writes refused, ops
    chosen, tokens — all cost-and-behaviour descriptors. "Asked three times" is neither good nor
    bad without a goal, and only `EVALUATION.md`'s answer keys supply one. A comparison view was
    specced and dropped for inviting exactly that reading.
- `portia/fixtures/` — kept mock data (a builder per module, registered in `__init__`)
- `sandbox/` — **gitignored scratch space for throwaway test projects** (`sandbox/run1`, …). Spin up
  as many as you like; none of it reaches the repo. Test runs used to land in the repo root or in
  `/tmp`, and the first cluttered the tree while the second was gone by morning.
- `portia/cli/` — play surfaces: `python -m portia.cli.<tool>` (e.g. `profile`, `join`, `run`,
  `index`, `runs`) · **`build`** compiles every spec to `models/*.sql` (`--check` is the CI form:
  writes nothing, fails if a `.sql` no longer matches its spec) · **`import_data`** is how outside
  data enters the repo, and it copies rather than moves. Its `plan()` — what would be copied where,
  computed before anything is written — is **called by both edges**, so the window and the terminal
  cannot disagree about where a file is going. It raises `ValueError`, never `SystemExit`: exiting
  is `main`'s way of reporting a refusal, not the function's.
- `portia/ui/` — the **app** (`python -m portia.ui`, `ui` extra): three panes on the same event
  stream, driving a turn through `ask.py`'s injected `answer`/`confirm`. Same status as `cli/` — an
  **edge**, and the two must never disagree about a number.
  - **`ui/engine.py` is the only module in here that calls the engine**, and **nothing in `ui/`
    computes**. A panel that wants a number the engine doesn't expose is a signal to add it to
    `checks`/`spec`, not to calculate it in a widget.
  - **The left pane is a real directory tree, filtered** (`ui/tree.py`, 2026-08-02) — a file is
    drawn if portia knows it (catalog · `discover_specs` · compiled models · outputs · saved runs)
    or if `core/io` registers a reader for its suffix; a folder is drawn if something under it
    survived. This **reversed** `DESIGN.md`'s "the left pane is curated, not a disk tree", and the
    argument that failed is kept there beside the reversal. The curation is a *filter* now; six flat
    sections were hiding where every file lived and fixing the folder layout the agent may produce.
    `tree.py` imports no NiceGUI and no engine — `engine.known_files` supplies the classification
    and the identity a click hands the panes.
  - **Runs and Turns are two artifacts, and one of them is not in the tree.** A *run* executed a
    spec (markdown, project-root `runs/`) and is a file like any other; a *turn* was the copilot
    deciding what the spec should say (JSONL, `.portia/runs/`, `runlog.py`), and `.portia/` is not
    walked — so Turns is pinned below the tree, as is the **project brief** above it. Selecting a
    turn replays it through `transcript`'s own renderers — one set of renderers, live and replayed,
    or the window ends up with a second opinion about a turn that is already written down.
  - **`ui/settings.py` is the one place a preference lives**, and it holds controls, not behaviour:
    every field binds the state the surface that spends it reads, so it is a second place to
    *change* a setting and never a second setting. The theme, the project switch, the brief, the
    model and effort, and the import destination were chrome across the top of every screen; a
    toolbar says where you are and acts on what is in front of you. It is **four tabs**, sharing
    `pane-tabs` with the transcript rather than growing a second tab style.
  - **A side pane is closed by dragging its edge past its floor**, which leaves a rail to reopen it.
    The floor was always "the width at which this pane stops being worth having" (`DESIGN.md`), so
    it was the honest place to close it; the toolbar toggles are gone.
  - **The four run actions are drawn on the middle pane, right-aligned to it** — they act on that
    pane and nothing else, and from the toolbar's corner they sat above the transcript. It is also
    the only place they *can* be aligned to it: a dragged pane's width never reaches the server, so
    chrome above the panes cannot know where the middle one ends. Their tooltips are the action's
    name and nothing more; a hover is not where a sentence gets read.
  - **The middle pane is one canvas at two zoom levels** (`docs/PIPELINE.md` §6). A card in the
    project graph is a *table* — one spec, one table — and a card inside an opened one is a *step*.
    Three node kinds, and the distinction is the point: a `SOURCE` is a file that arrived, a
    `MODEL` is a table portia built. Picking a spec on the left **navigates** the canvas rather
    than replacing it.
  - **Run is scoped, not partial.** Run executes the open spec *and everything it reads*, then
    writes their `.sql`; Build does the project. One mechanism (`pipeline.build_project(only=…)`),
    so the window and `cli.build` cannot produce different SQL.
  - `state.py`, `graph.py` and `tree.py` import no NiceGUI, so the app's logic is testable without
    a browser.
  - The look lives in `ui/assets/portia.css` as `DESIGN.md`'s tokens — not in Python strings, and
    not in NiceGUI APIs, so swapping the framework stays cheap (`TECH_STACK.md`). Behaviour that
    has to be client-side lives beside it as its own file for the same reason: `canvas.js` (pan and
    zoom), `scroll.js` (where each pane is scrolled to), `viewport.js` (the window width, which
    `DESIGN.md`'s width bands need and CSS cannot supply once panes are inside splitters),
    `choose_files.applescript` (the native file chooser).
  - **Where you are looking is the client's state, and only the client's.** The canvas's pan and
    zoom, and each pane's scroll offset (`c.scroll_area`): neither reaches Python, neither is
    measured, neither is persisted. A round trip per wheel tick would make the only
    directly-manipulated surface the laggiest, and NiceGUI *replaces* a refreshable's elements
    rather than patching them — so a rebuilt pane starts at the top unless the client puts it back.
    Anything the *server* wants either of them to do is stated declaratively in the DOM with a
    token or a key, never driven by a `run_javascript` during a render: that races the DOM patch.
  - **The middle pane draws in one pass** (`workflow.pane` is not a coroutine). A refresh deletes
    its elements and only then runs the function; an `await` in between sends the delete and the
    rebuild in two batches and the browser paints the gap. Work that genuinely blocks stays
    threaded in `engine.py`; reading a file a pane is about to draw does not belong there.
  - **`DESIGN.md`'s product rule is the UI's version of facts vs judgment: colour and prominence
    communicate *kind*, never *rank*.** No sorting by severity, no badge that grows with its number,
    no roll-up that implies a score. The engine refuses to rank; the screen must not do it on the
    engine's behalf.

Rule of thumb: **`core` = reused everywhere · `checks` = diagnosis (facts) · `ops` = execution ·
`spec` + `catalog` + `runlog` = the durable artifacts · `knowledge` = what they are to each other ·
`agent` = judgment · `cli` + `ui` = human edges.**
Deciding is the agent's job, not a layer. A new file that's none of these probably belongs in one of
them, not loose in `portia/`.

## Branching — never work on `main` directly

Unless the user explicitly says otherwise for a given change:

- **Do not edit, commit, or push on `main`.**
- At the start of a task, check the current branch (`git branch --show-current`). If a branch
  relevant to the active task is already checked out, work on that. Otherwise **create a new,
  descriptively-named branch off `main`** before making any changes.
- Only merge or push to `main` when the user asks.
