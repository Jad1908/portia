# Backlog — deferred ideas, by stream

*A parking lot, not a committed roadmap. Things we've decided are worth doing **later** so we can
stay focused **now**. Directional (per `PLAN.md`): each item is a one-line intent, not a spec.
**When we postpone something mid-work, add it here** so it isn't lost. Remove an item when it ships
(link the PR) or when we decide against it (say why).*

Streams mirror the architecture: `checks` · `ops` · `spec` · agent/decide · interface · scale ·
validation. See the module map artifact + `CLAUDE.md` for what already exists.

---

## Checks — diagnosis / evidence

- **Entity resolution** — near-duplicate detection with `rapidfuzz` (blocking + fuzzy scoring). The
  brief's "hard one"; judgement-heavy, so it leans hardest on surface-don't-decide.
- **More evidence, on demand** — the checks are convenient common analyses; keep adding as real work
  surfaces gaps (temporal gaps, distribution/skew, cross-column consistency). Don't prewrite a giant
  taxonomy — the agent computes ad-hoc; these are just handy starting points.
- **Scale-aware evidence** — cap columns (not just rows) in `join_findings` samples; profile from a
  sample/schema rather than the full frame once data is too big to scan.

## Ops — execution (trusted transforms)

- ~~**Nothing can aggregate**~~ — *fixed by the SQL escape hatch, shipped 2026-07-26
  (`ops/sql.py`). `ops = {join, normalize, sql}`. The hotel fixture's fatal fan-out now has a
  correct handling the spec can express — aggregate events to one row per city-date, then join —
  and it produces the answer key's table exactly: 14 rows, 14 bookings, revenue 136,240, zero
  inflation, with the event signal kept as `n_events`/`total_attendance` features. That is the
  first correct answer to this fixture in the project's history, though **by construction, not by
  a copilot** — no model has been watched reaching for it.*
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
- **A SQL step's provenance is thin, and might be earned back.** `join` reports what it dropped
  from each side because it knows what a key is; `sql` reports only shape (`result_rows`,
  `columns`). `checks.outcome` still measures the produced table, so the blocking gate is intact —
  but drift on a SQL step is weaker than on a join. If that bites, the fix is probably declared
  post-conditions on the step rather than trying to infer semantics from the query.

## Spec — the durable artifact

- ~~**The escape hatch — DuckDB SQL**~~ — *shipped 2026-07-26 (`ops/sql.py`, branch
  `sql-escape-hatch`). The agent declares `inputs` and authors one `SELECT` over them; the query is
  captured verbatim in the spec and wrapped by the same provenance and outcome harness as any other
  op. The rule tightened rather than bent — **the agent may author a transform; it may never author
  a number** — and that is now in `CLAUDE.md`. DuckDB became a core dependency, as anticipated;
  `TECH_STACK.md` records that it arrived for expressiveness, not scale.*
  - *Sandbox: `check_sql` refuses anything that isn't a single SELECT before DuckDB is touched, and
    the connection runs with `enable_external_access=False`. Two independent halves on purpose —
    the string check is bypassable and exists to give a readable error; the config is what holds.*
  - *Still open: the friction is the instrument, so **watch what it strains at** before promoting
    any of it into a prewritten op (see Ops above). And nothing has yet observed a **model** using
    the hatch — the correct hotel table was built by hand.*
- **Structured `evidence` field** — beyond free-text `rationale`: the key numbers that justified a
  decision (`{skew: 2.3, n_outliers: 40}`), so drift can later check whether the *reason* still holds.
- **Decision lifecycle** — a clean `accept`/re-baseline command (update `expect` intentionally,
  git-diffable) so drift isn't hand-edited; `revoke`/change is just editing the file.
- **Drift calibration** — split expectations into **invariants** (must never change → hard fail) vs
  **informational metrics** (row counts that naturally move → notice, not failure). Avoids alarm
  fatigue. Maybe drift-on-rationale (flag when a decision's justifying condition no longer holds).
- **Workflow chaining across specs** — a mature workflow consuming another's trusted output as a
  named, versioned artifact; spec versioning; drift across the chain (`VISION.md` open question).
- **Reproducibility of custom steps** — pin the execution environment (DuckDB version, or Python +
  deps + seeds) so a captured step truly re-runs identically.

## Agent — the "decide" layer (the copilot)

- ~~**The copilot loop**~~ — *shipped (`portia/agent/`, branch `agent-loop`): in-process MCP server
  over the checks/catalog/spec, `AskUserQuestion` routed to the human, event stream, chat CLI.
  Proven end-to-end on both flows — `interpret` writes the catalog read, `merge` measures a join,
  asks which trade-off to take, and writes a spec step whose `expect` block `run_spec` verifies
  clean.*
- ~~**`expect` vocabulary is hand-maintained**~~ — *fixed: each op declares `PROVENANCE_KEYS` next
  to the code that emits them, `handlers._EXPECTABLE` reads those, and each op's tests assert the
  declaration still matches a real run — so it can't rot silently.*
- ~~**Context flow**~~ — *shipped: L0+L1 composed into the system prompt (`agent/context.py`), the
  L2/L3 split (`describe_source` / `profile_source`), groups wired end to end, first-run stdin
  prompt, and bulk index+interpret in one session.* **Shipped but NOT validated** — the demo that
  appeared to prove it used a brief that stated the answer outright. See `EVALUATION.md` → "A
  retracted result". The plumbing is right; the evidence was not.
- ~~**The verification loop**~~ — *shipped (branch `verification-loop`): `checks/outcome.py`
  measures the frame a step produced, `record_step` executes before it writes so the measurement is
  pushed rather than offered, a step may declare a `grain` the engine checks, zero-conditions refuse
  to be written, and overriding means an `acknowledge` in the YAML. `no_matches` needed no special
  case — it surfaces as `empty_output` or `source_did_not_contribute`, derived from the output
  rather than from the op's flags. The immutability message no longer says "pick another id".*
  **Verified against the engine, not yet against the agent** — see `EVALUATION.md`.
- **A grain claim can be widened until it passes — the loop's open hole.** Run 3 was refused on
  `grain: [booking_id]`, then recorded `grain: [booking_id, event_name]` and passed: `event_name`
  is the column the fan-out varies over, so the claim is a tautology. It didn't override the gate,
  it dissolved it — the same move as rewriting `expect`, on the one field the agent authors.
  Candidate fix, claim-free so it can't be dissolved: **row conservation.** A left join whose output
  exceeds its left input multiplied rows; an inner join can only shrink. Binary, no tunable number,
  independent of anything the agent says. **Open design call:** a strict inequality is not literally
  a zero, so admitting it stretches the "only zeros block" rule — but it needs no threshold, which
  is what that rule is actually protecting. Decide before building.
- ~~**`expect` values aren't shape-checked, only their keys**~~ — *fixed (branch
  `expect-value-shapes`): `record_step` compares each prediction's kind against the value the step
  actually reported, which it has because it just ran it. No acknowledgement for this one — unlike
  a zero, a wrong-typed prediction is never legitimate.*
- **Grain examples should carry the row, not just the key.** Given only
  `{"booking_id": "B0009", "n_rows": 2}`, Run 3 invented the city and both event names in its
  summary — "Paris… Tech Summit and Marathon" for what was Amsterdam/Canal Festival/Design Week.
  Anything it can't measure it will estimate (`handlers.profile_source`'s docstring, same lesson).
  More evidence, not a sterner prompt.
- ~~**A partial join failure is invisible to a zero-only blocking rule**~~ — *largely answered, and
  the answer was evidence rather than a flag.* Run 3 stripped `city_name` but never lowercased it,
  so `" paris"` → `"paris"` never matched `"Paris"` and one event vanished;
  `source_did_not_contribute` correctly stayed quiet because four other events matched. Catching
  that with a **rule** needs a threshold, i.e. judgment, i.e. it isn't code's. But it needs no rule:
  now that `join_findings` reaches a step's output, the same call on hop 2 returns
  `{'city_name': 'paris', 'event_name': 'Marathon'}` in `unmatched_right_rows` and
  `('2026-06-12','Amsterdam') n_left 1 × n_right 2` in `fan_out_examples` — both failures, in plain
  rows, before anything is written. "Invest in richer observations, not a decision layer"
  (`CLAUDE.md`), demonstrated. Whether the agent *looks* is the open part, and that is a prompt
  question now rather than a structural one.
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
- **One tidy home for every injected instruction.** Prompt text currently lives in five places:
  `agent/prompts/copilot.md` (L0), the brief template in `agent/context.py`, **every tool
  description inline in `agent/tools.py`**, and the task prompts in `cli/chat.py` and
  `cli/index.py`. Tool descriptions are the highest-leverage, most performance-sensitive text in
  the system — the hotel run failed because one of them omitted a sentence — and they're buried in
  decorators. Move them all under `agent/prompts/` so wording can be diffed, reviewed and A/B'd
  without touching code, with a test that every tool resolves a description (no silent fallback).
- **Run log + the metrics that need no labels.** Write each run's events (`agent/events.py`) to
  JSONL, one line per event, and compute with pandas: which disclosure rungs were pulled and in
  what order, tokens and turns per decision, how often it asked, drift rate on recorded specs.
  ~30 lines, no infrastructure. Be honest about what these are: **cost and behaviour descriptors,
  not correctness** — only the answer keys make a number mean anything.
- **Langfuse, once the JSONL hurts.** Its job is browsing a run's timeline when debugging why one
  went sideways, not computing the metrics above. Free either way (self-host via Docker, or the
  cloud Hobby tier: 50k units/month, 30-day retention, 2 seats). The SDK drives Claude Code as a
  *subprocess*, so client auto-instrumentation sees nothing — `events.py` is the only sane
  emission point, which is also why the JSONL is not throwaway work.
- **Generated data has nowhere to live.** `run_spec --write` dumps `<step-id>.csv` and nothing
  knows it exists. Needs: a **code-owned** layout (`outputs/` at the project root for the data —
  users open these in Excel — index entry in `.portia/`), auto-profiling (free) but **not**
  auto-interpretation (a model turn per run), and `derived_from: <spec>#<step>` so a generated
  table can't be mistaken for source data. Path convention is not judgment: if every project
  invents its own tree nothing can find anything and the GUI's left panel has no stable view.
  This is what makes `VISION.md`'s workflow chaining safe.
- **Multi-turn chat** — `session.run` is one turn per invocation today; hold the `ClaudeSDKClient`
  open for follow-ups and wire `interrupt()`.
- **Don't reconstruct rows from samples** — asked for raw data the agent politely assembles a
  plausible table from `samples` and hedges. Honest, but consider whether the prompt should refuse
  outright.
- **Sandbox data access** — hand the agent's code-execution a clean handle to the loaded frames so it
  can run read-only ad-hoc analysis (the imputation-shape question). Ephemeral; verdict → `rationale`.
- ~~**Pro-auth verification**~~ — *answered 2026-07-25: the SDK drives a bundled Claude Code binary,
  so it authenticates off the local login and meters against the **subscription** (confirmed against
  real Haiku usage). The budget principle holds.* See `PLAN.md` → "Auth posture" for what portia
  does and doesn't claim about this — the posture is unchanged by the good news.
- **Don't re-ask what's decided** — the agent asks only about what the spec hasn't answered; drift
  can re-open a specific decision. (Best shaped by real use, per the user.)

## Context catalog — `.portia/` (the agent's memory)

*Substrate built (`catalog.py`): project context + groups + per-source Layer 1 prose / Layer 2
column roles + facts; facts refresh, judgment preserved. Remaining:*

- ~~**Semantic interpretation**~~ — *shipped: `catalog.set_interpretation` + the agent's
  `interpret` flow. The agent now writes `summary` and column `role`s from the project context.*
- **Role vocabulary** — the agent invents role names per run (`attribute` vs `category`,
  `unused` vs `dropped`). Fine while we learn what roles are useful; revisit once real use shows
  which ones carry weight, and only then consider constraining them.
- **Broad "how sources interact" model** — likely joins / relationships across sources (the
  context-aware end goal). Deferred as too early — forge convictions via the UI first.
- **Groups in use** — `groups` are stored but nothing consumes them yet; wire group context into
  downstream reasoning once the agent lands.
- **Catalog storage shape** — one-file-per-source now; revisit one-file vs per-group vs harmonized
  once we've used it (kept deliberately un-locked / hand-editable).
- **Context bundle** — a token-lean projection of the catalog for the agent to consume; and a
  drift-like "facts changed since summary written" signal.

## Interface — the surface

- **The three-panel app** (files · workflow · chat) — NiceGUI on the engine's event stream. Core to
  the product, deferred until the engine + agent are proven. `VISION.md`.

## Scale — data tiers

- **DuckDB tier** — larger-than-memory local CSV/Parquet behind the same checks/ops interface (swap,
  not rewrite). Also the natural home for the SQL escape hatch.
- **Snowflake tier** via the Snowflake MCP server — push compute to the warehouse, pull small results.

## Core / infra

- **README says nothing about auth — a decision awaiting the user, not an oversight.** `PLAN.md`
  → "Auth posture" settles what portia *does* (no auth code, ever) but not what the README
  *claims*. Naming the API key as the supported path is the safe posture; saying nothing is also
  neutral and is where it stands today. It is product positioning on the exact point the user
  cares about, so it is theirs to call — **don't quietly write it either way**. One line, whichever
  it is.
- **More loaders** — Parquet (and beyond) in `core/io._LOADERS`; one line each.
- **CI** — run the hooks + tests on each PR. *Declined for now (2026-07); revisit if collaborators join.*

## Validation & product (from the brief §9)

- **The decisive experiment** — run the engine on real, non-synthetic, too-big-to-eyeball data from
  actual work. Does the frontier-agent baseline degrade at real scale? Nothing matters more.
- **The five conversations** — describe the tool to five DS/AI-engineers; ask what they do today
  instead. "I'd use this" from the founder is necessary, not sufficient.
