# Direction — Agent-Assisted Data Harmonization Copilot (portia)

*A direction document: vision, stack, the non-negotiables, and the problems we'll have to
solve — deliberately not a step-by-step plan.*

## Vision

A **copilot that dives into your data and comes back with questions and insights on how to
proceed.** You point it at N ugly sources and ask for a merge; it explores, and instead of
silently producing a number or hard-stopping when something is off, it **surfaces the decision
and asks you** — the way Claude Code asks clarifying questions to keep building in the right
direction. For example:

- Units don't match on a merge → it flags it and offers options: suggested conversions, or a
  prompt for a custom conversion rule.
- NaNs in a column you're aggregating → it asks how to fill them, and suggests strategies.
- Near-duplicate entities → it shows the ambiguous matches and asks which are the same.

Every answer becomes a **durable, recorded decision** — the residue. The product isn't the
reasoning; it's the accumulated, auditable, reproducible set of choices that got you to *one
table you trust*. Built by/for a DS/AI engineer; rigor lives in the modelling, SWE rough edges
are fine.

**Success:** it accelerates discovery, produces a table the user trusts, and is totally
transparent about assumptions, decisions, and limitations. **The interactive surface and the
engine are equally important** — the questions-and-insights UX *is* the product.

## Stack

Python + **Claude Agent SDK** (agent loop, context management, **MCP-client**, custom tools).
**DuckDB** under the whole engine since 2026-07-28 — sources (CSV or Parquet) are read **in place,
from inside the repo**, and everything downstream is a lazy relation — with a
**Snowflake tier (~15–20 tables)** via the Snowflake **MCP server** to come, plugging into the same
`core.table` seam. pandas stays for fixtures, small reads and rendering. UI: a Python-authored,
serious (non-gadgety) framework — **NiceGUI** (Vue under the hood) — sitting on the engine's event
stream. Model is a config knob. **There is no second copy of the user's data** — the ingested store
was tried and removed (2026-07-31, `PIPELINE.md` §2.7).

**Full stack + reasoning: see `TECH_STACK.md`.**

## The non-negotiables

- **Deterministic code detects and measures; the LLM orchestrates, explains, and asks.** Every
  number (drop counts, row conservation, match scores, null rates) comes from a reproducible
  function the agent calls — the model never eyeballs the data. This is what survives real scale,
  where it only ever sees schemas and samples.
- **Never silently do the wrong thing.** When a check fires — mismatched units, NaNs, near-dup
  entities, dropped rows, fan-out — the copilot **surfaces it with suggested resolutions and
  asks**; it doesn't guess and it doesn't just stop. A provenance/drop report is always produced.
- **Every decision is durable.** Answers persist as a plain-text, git-diffable, re-runnable
  spec/contract in the user's repo; its schema emerges from real runs; re-running against a
  changed source produces a readable diff.

## Build order (order, not a rigid plan)

Deterministic detection/profiling engine first (testable on real data with no model spend) →
the interactive copilot loop over it (the questions-and-insights UX, emitting a spec + report +
a decision stream) → the surface where those questions are asked and answered. The interactive
layer is **core, not deferred** — all three now exist; `VISION.md` is where its shape is worked out.

**Where we are (2026-08-03).** The engine is built (`checks`, `ops`, `spec`, `catalog`), so is the
copilot loop (`portia/agent/` — in-process MCP server, layered context, `AskUserQuestion` routed to
a human, spec writing, chat CLI), and so is **V0 of the app** (`portia/ui/`) — the loop runs in one
window, with no terminal. The **verification loop** exists too: recording a step executes it,
`checks/outcome.py` measures the table it produced, and a step that hits a zero is refused rather
than written.

**The pipeline is the artifact, and the app shows it** (`docs/PIPELINE.md`). Every spec compiles to
one committed `.sql` under `models/`; the window lists them, flags any whose file has drifted from
its spec, and draws the project as a DAG of tables where a card opens in place onto the steps that
build it. **Run** executes a model and everything it reads and writes their SQL; **Build** does the
project. Outside data comes in through an import that states what it will copy and to where before
it copies anything.

**The window was overhauled on 2026-08-02, and the theme was putting every control where the thing
it acts on is.** The left pane is a **real directory tree, filtered** to what portia knows or can
read (`ui/tree.py`) — which reversed `DESIGN.md`'s "curated, not a disk tree", with the argument
that failed kept beside the reversal. Preferences left the toolbar for a tabbed **Settings** panel
(`ui/settings.py`); the four run actions left it for the **middle pane they act on**; a side pane is
closed by dragging its edge past its floor and reopened from the rail that leaves behind. What is
left of the toolbar is a mark, the project's name and a gear. None of it changed what the engine
does — it is all edge — but it is the first time the app was shaped by using it rather than by
speccing it, and `BACKLOG.md` records what that turned up and did not fix.

**The add-data screen was rewritten the same day, and it is where a project now says what its data
is.** Choosing the folder that holds the data is an in-page browse through the repo — sub-folders
that have readable data under them, with a count — and it writes **`data_dir` into `project.yaml`**,
the first durable project setting since the brief. That is what scopes the left pane's un-indexed
files, and what an import defaults to. Importing outside data is a fold-away second route beside it,
and **one button copies what was planned and profiles what was ticked**, so the screen has exactly
one control that writes anything. The browser drop zone is gone: it was a third route doing the same
job, and the only one whose refusals portia could not explain.

**The pass after it (2026-08-03) was the first-run path driven on a real extract, and everything it
found was a surface claiming more than it knew.** The project-brief box asked *"what does one row
mean, and which source is authoritative for what"* — a question about the data, which got answers
about the data: half of it portia measures for itself, and half of it the copilot may never take on
trust. It asks for the **project** now (domain and goal · how it is modelled — what you produce, at
what grain, over what horizon · roughly what data exists), which is what `VISION.md` always
specified and the screen had drifted off. The CLI prompt and the copilot's ask-for-context moved
with it, so no surface teaches that box differently. In the same shape: a source that nobody had
interpreted was showing `catalog`'s auto-drafted restatement of its own profile in the prose slot,
which reads as a read of the data — it says *not read yet* now, and leaves the facts to the columns
table where they are measured. The add-data list stopped offering to re-index what it had already
indexed, and its CTA caption stopped counting a copy as if it replaced a profile. Nothing here
changed what the engine does; all of it changed what the window claims.

**Scale is built.** The engine is DuckDB throughout and everything is a lazy relation. Measured end
to end on real PHQ data: 4.82 GB across three tables indexes in 32 s, a 50M × 3M join is diagnosed
in 3.8 s, and a full spec run over 50M rows takes 27 s. **Peak memory is bounded by the largest
table, not the total**, which is the property that makes ~20 tables workable — with the one
exception `DUCKDB_MIGRATION.md` §13 records, that a *profile* still scales with cardinality.

**This is not yet a working copilot, and that is the whole of what is left.** Eight runs, all
failing (`EVALUATION.md`). Each fix closed one escape and revealed the next: the spelling trap is
dead, the tautology grain is dead, and Run 5 shipped a 3.85%-inflated table by writing `acknowledge`
without ever asking the user. **Run 6 changed the model rather than the code** — `claude-opus-5` at
low effort — and in the indexing phase alone raised the revenue outliers nobody had ever asked
about, predicted the fan-out before joining, and named portia's missing aggregate itself. It never
reached the gate, so the consent question is open, but the Runs 1–5 failures now read as
**capability rather than architecture**.

**Run 8 (2026-07-29) is the first run on real data, and it moved the diagnosis.** 23 PHQ sources,
4.8 GB, a read-only goal. The model planned a two-path join, headed its own gaps *"Critical Unknowns
(Need to Measure)"*, **measured none of them**, then asked permission to measure. Both joins it
proposed match **0 keys**, which two 0.02 s queries established afterwards. **The engine is no
longer the constraint.** The nearest candidate cause is in our own prompt, which still calls
profiling *"Expensive… Not for browsing"* from when that described pandas.

Read **`EVALUATION.md`** before building on top of any of it — it separates what the engine can do
from what the copilot has been shown to do. Nothing since Run 8 has been agent work, so that
diagnosis is still the current one.

**Shipped, in the order it happened:**

1. **The escape hatch** (2026-07-26, `ops/sql.py`). The agent declares `inputs` and authors one
   DuckDB `SELECT`, captured verbatim and measured by the same harness as every other op.
   `ops = {join, normalize, sql}`. Built by hand it produces the hotel answer key exactly — 14 rows,
   revenue 136,240, zero inflation. **No model has yet been watched reaching for it** (Run 7 made
   zero `sql` calls), so resist promoting `aggregate`/`filter`/`dedupe` into prewritten ops until
   real runs show which shape is actually reached for (`BACKLOG.md`).

2. **The surface — V0 of the app** (2026-07-26, `portia/ui/`, `python -m portia.ui`). Three panes on
   the engine's event stream. **The bar is met: a full test run with no terminal.** The claim this
   settles is that the loop is now *watchable*, not that it is good. Revised 2026-07-28 after
   watching it fail with twenty real files, and the lesson generalises: **the app's test suite
   renders no tables and drives no flows**, so every one of those failures was found by hand.

3. **The scale tier — DuckDB** (2026-07-28, `docs/DUCKDB_MIGRATION.md`). The engine is DuckDB
   throughout; pandas survives only in the fixtures, the loader's small-read path, the renderers,
   and the SQL hatch's sandbox boundary, and a test fails if anything else pulls a whole relation
   into memory. **All ten `spec` golden cases come out byte-identical to evidence the pandas engine
   wrote**, and the 80M-row fan-out that inflated revenue in Run 5 is reported in 0.1 s without
   being built. **What measurement changed is the part worth reading** — §6.1, §6.3 and §13.

4. **The run log** (2026-07-29, `portia/runlog.py`, `python -m portia.cli.runs`). One JSONL per
   turn, teed at both edges, replayable in the terminal and under **Turns** in the app's left pane.
   A copilot turn no longer dies with the window. It caught one thing worth carrying into any cost
   claim: the SDK's `input_tokens` excludes cached input, so a 14,651-token turn reported **17** —
   and nearly all of a portia turn's input is the pushed L0/L1 context, i.e. exactly the cached
   part. **It has not yet proved itself:** no run has been scored *using* it.

5. **The pipeline overhaul — SQL as the artifact** (designed 2026-07-30, shipped 2026-07-31,
   rendered 2026-08-01, `docs/PIPELINE.md`). portia already generated every line of SQL it needed
   and threw it away when the run ended. Keeping it turns the durable artifact from "a recipe we can
   re-run" into **a pipeline you can hand to a data team** — one `.sql` per spec, dbt-shaped,
   committed. Seven decisions, all settled and all implemented.
   Two things worth carrying forward. **Removing the store moved no evidence at all** — all 35
   golden cases came out byte-identical, because ingesting was `CREATE TABLE … AS <read_query(path)>`
   and the reader was therefore always the same one; only the materialization differed. And the
   frontend pass found **two real bugs the engine work had introduced silently**: the app could not
   see specs in subdirectories, and its Run did not resolve cross-spec references — so a spec that
   ran from the CLI failed in the window, which is the one seam `VISION.md` says must never break.

**Next.** Two things, and the first gates the second.

**The PHQ test — begun 2026-07-29, and it already changed the next question.** 23 sources are
indexed and interpreted, and the engine held: cardinality is the ceiling as `DUCKDB_MIGRATION.md`
§13 predicted. The first goal turn is **Run 8**, and it says the constraint has moved from the
engine to the copilot — it planned a join over 4.8 GB without measuring anything. So the useful
order is: **fix the prompt's cost signal first (`BACKLOG.md` → Agent), then re-run the same goal**,
and score it *using* the run log, which is the test that log is still waiting for. A model that
won't call `profile_source` makes every other finding on this dataset unreadable. Still worth doing
before reading much into a run: `fan_out` fires on every fact-to-dimension join because it reads
either side's key multiplicity rather than the result's, which at this scale is how a real warning
gets learned as noise.

**The consequence of a zero, as a computed fact rendered where the human answers.** Run 5's override
was taken alone, and the instruction it skipped ("tell the user what a total would be off by") asks
for a number that is nowhere in the agent's evidence. Compute what a row multiplication does to each
measure column and put it at the confirmation prompt, so consent is informed whether or not the
agent cooperates. No capable model has yet been watched reaching a blocking flag.

> **A referentially-consistent subset would make both of those cheaper**, by turning a copilot run
> from an anecdote into something re-runnable in seconds and scoreable against an answer key.
> `DUCKDB_MIGRATION.md` §11 for why naive sampling cannot do it.

## Budget & model discipline

Claude **Pro only** — no API budget, no Max. Turned into a principle: **develop on a cheaper,
smaller model at low effort.** If it works there, the *engine* is good, not the model; the
flagship is a ceiling check / upgrade, never a dependency. Keep every loop **token-lean** (the
agent sees compact profiles and schemas, never raw data).

**Auth — verified 2026-07-25 (`claude-agent-sdk` 0.2.128). The budget principle holds.** The SDK
is not an API client: it ships a Claude Code binary (`_bundled/claude`) and drives it as a
subprocess, so it authenticates exactly as an interactive Claude Code session does — off the local
login, no `ANTHROPIC_API_KEY` involved. Confirmed against real usage: runs show up as **Haiku
consumption on the subscription**, and no API billing exists to draw on. The `total_cost_usd` the
SDK reports is Claude Code's `/cost` estimate (tokens × list price), not a charge.

**Auth posture — deliberately not a portia feature.** The SDK docs say *"unless previously
approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits
for their products"*, and SDK use is governed by the **Commercial** Terms while a Pro subscription
sits under the Consumer Terms. The prohibited act is *offering* claude.ai login as part of a
product — not a developer using their own account. So portia writes **zero auth code**: no auth env
vars, no proxying, no detection. Auth resolves inside the bundled binary, so there is no
"compatibility" to build or withhold; the code is identical either way. Keep the README neutral —
name the API key as the supported path, don't advertise subscription auth, and don't design around
subscription rate limits. Ask Anthropic (the docs' "unless previously approved" is the invitation)
before portia has users or is promoted as subscription-powered.

## What we'll have to solve

- **When to ask vs. decide.** A copilot that asks 200 questions is useless — ask only about what
  matters, prioritized by impact (rows affected × materiality), and suggest good defaults so
  answering is cheap. **Crucial: this prioritizing/defaulting is the _agent's_ judgment (it has the
  project context and goal), NOT a deterministic module.** We tried a deterministic "planner" that
  ranked decisions and suggested answers in code, and reversed it — it bakes context-free judgment
  into code that fails on hard problems at scale. The engine surfaces facts + example rows
  generously; the agent ranks, frames, and asks. See `CLAUDE.md` → "facts vs judgment".
- **The correctness oracle.** Where a check has ground truth, assert it; where it's a judgement
  call, the copilot asks rather than guessing — and records the answer so it isn't re-litigated.
- **Good suggested resolutions** — unit conversions, fill strategies, match thresholds — so the
  copilot proposes, not just interrogates.
- **The spec/contract format** — diffable, re-runnable, readable diff on drift; let it emerge.
- **Entity resolution at real cardinality** — duplicates hide in the tail, not in obvious pairs.
- **Scale** — schemas + samples, never full data; the Snowflake tier is a different job.
- **The interaction/UI design** — how questions are surfaced and answered; the three-panel
  product vision (files · workflow · chat) is captured in `VISION.md`; its own design problem.
- Validate on the **real, too-big-to-eyeball data**, not synthetic fixtures.

## Reuse

Claude Agent SDK · DuckDB `SUMMARIZE` / `approx_count_distinct` / quantiles · `rapidfuzz`
(+ optionally `recordlinkage` / `dedupe`) for entity resolution · Snowflake MCP server.
