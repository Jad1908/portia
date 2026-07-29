# portia — read this first

**Before doing any work in this repo, read these docs.** They define the project's direction,
stack, and product vision. Read them every session, before proposing changes or writing code:

- `docs/PLAN.md` — direction: vision, non-negotiables, build order, **where we are now**
- `docs/EVALUATION.md` — how we measure the copilot, and its **current honest score**. Read this
  before trusting any claim that a part of the loop works; it also records a result we retracted.
- `docs/TECH_STACK.md` — the tech stack and the reasoning behind it
- `docs/VISION.md` — product vision & UI flows (the three-panel app), incl. the **V0 viewer** spec
- `DESIGN.md` — **the look**: mode-aware tokens, type, components. Required reading before writing
  any UI. It owns appearance; `VISION.md` owns layout and behavior. Note its one product-specific
  rule — *color and prominence communicate kind, never rank* — which is "facts vs judgment" applied
  to pixels.
- `docs/brief.md` — the original working brief (foundational context)
- `docs/DUCKDB_MIGRATION.md` — **the scale tier**: why pandas caps us, the measurements, and the
  file-by-file plan to move `checks`/`ops`/`spec` onto DuckDB without changing anything the copilot
  reads. Required reading before touching `checks/`, `ops/`, or `core/io.py`.
- `docs/BACKLOG.md` — parking lot of deferred ideas, by stream. Not required reading; scan it when
  picking the next thing to build, and **add to it whenever we postpone something mid-work.**

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
- **pandas-first**; DuckDB/SQL only when scale forces it (behind an abstracted checks layer).
- **Budget: Claude Pro only** (no API, no Max) — develop on a cheaper, smaller model at low
  effort; keep loops token-lean (compact profiles/schemas, never raw data).
- **Don't start building without agreed direction.** Ask before large scaffolding.

## Code conventions — built to scale, DRY from the start

We will build *many* tools and checks. They must **compose**, not accumulate into spaghetti with
ten different ways to do the same thing. These seams are non-negotiable; respect them before
adding code, and extend them rather than working around them:

- **One way to load data.** All file reading goes through **`portia.core.io.load_frame`**
  (dispatches by format). Never call `pd.read_csv`/`read_parquet` in a tool, check, notebook, or
  CLI — register new formats in `core/io.py`, once. This is also the pandas → DuckDB/Snowflake seam.
  - **It has a hard ceiling, and it is measured.** pandas needs ~2.4× a CSV's size to hold it and
    ~4.8× to profile it, and `run_spec` holds every source *and* every intermediate at once. Anything
    past a few hundred MB per file does not work today. `docs/DUCKDB_MIGRATION.md` is the plan; until
    it lands, do not add code that assumes a whole table fits in memory.
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

- `portia/core/` — shared seams: `io.py` (loading) · `serialize.py` (compact JSON evidence) ·
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
  filtering, deriving). Every op returns an `OpResult` (frame + unsuppressable provenance report).
  Same swap seam as checks.
  - **The hatch is sandboxed in two independent halves, and both stay.** `check_sql` refuses
    anything that isn't a single `SELECT`, readably, before DuckDB is touched; the connection then
    runs with `enable_external_access=False`. The string check is bypassable on purpose — it exists
    to give a good error — and the config is what actually holds. `session.py` gives the agent no
    filesystem tools; the hatch must never quietly hand them back.
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
  - `portia/spec.py` — the **spec** (*what we did to the data*): sources + decided steps + `expect`
    + `rationale` + an optional `grain` claim and `acknowledge` list; `run_spec` re-executes,
    detects drift, and attaches each step's measured `outcome`. **Drift and outcome are different
    questions** — drift asks whether the prediction held, the outcome asks what came out. A correct
    prediction about a broken join is still a broken join. Recording a step **runs** it
    (`handlers.record_step`), so a step that hits a zero is never written; overriding means writing
    `acknowledge` into the YAML, where the human reads it in a diff.
  - `portia/catalog.py` — the **context catalog** (*what the data is*), in `.portia/`: project
    context + groups + per-source metadata (Layer 1 prose `summary`, Layer 2 per-column `role` +
    check facts). The agent's memory. **Update rule: facts refresh, prose/roles are preserved** —
    corrections are never clobbered (facts vs judgment, applied to updates).
- `portia/fixtures/` — kept mock data (a builder per module, registered in `__init__`)
- `sandbox/` — **gitignored scratch space for throwaway test projects** (`sandbox/run1`, …). Spin up
  as many as you like; none of it reaches the repo. Test runs used to land in the repo root or in
  `/tmp`, and the first cluttered the tree while the second was gone by morning.
- `portia/cli/` — play surfaces: `python -m portia.cli.<tool>` (e.g. `profile`, `join`, `run`, `index`)
- `portia/ui/` — the **app** (`python -m portia.ui`, `ui` extra): three panes on the same event
  stream, driving a turn through `ask.py`'s injected `answer`/`confirm`. Same status as `cli/` — an
  **edge**, and the two must never disagree about a number.
  - **`ui/engine.py` is the only module in here that calls the engine**, and **nothing in `ui/`
    computes**. A panel that wants a number the engine doesn't expose is a signal to add it to
    `checks`/`spec`, not to calculate it in a widget.
  - `state.py` and `graph.py` import no NiceGUI, so the app's logic is testable without a browser.
  - The look lives in `ui/assets/portia.css` as `DESIGN.md`'s tokens — not in Python strings, and
    not in NiceGUI APIs, so swapping the framework stays cheap (`TECH_STACK.md`).
  - **`DESIGN.md`'s product rule is the UI's version of facts vs judgment: colour and prominence
    communicate *kind*, never *rank*.** No sorting by severity, no badge that grows with its number,
    no roll-up that implies a score. The engine refuses to rank; the screen must not do it on the
    engine's behalf.

Rule of thumb: **`core` = reused everywhere · `checks` = diagnosis (facts) · `ops` = execution ·
`spec` + `catalog` = the durable artifacts · `agent` = judgment · `cli` + `ui` = human edges.**
Deciding is the agent's job, not a layer. A new file that's none of these probably belongs in one of
them, not loose in `portia/`.

## Branching — never work on `main` directly

Unless the user explicitly says otherwise for a given change:

- **Do not edit, commit, or push on `main`.**
- At the start of a task, check the current branch (`git branch --show-current`). If a branch
  relevant to the active task is already checked out, work on that. Otherwise **create a new,
  descriptively-named branch off `main`** before making any changes.
- Only merge or push to `main` when the user asks.
