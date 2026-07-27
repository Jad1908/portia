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
**pandas-first** for the local MVP; **DuckDB/SQL** introduced only for the too-big-for-memory
tier and a **Snowflake tier (~15–20 tables)** via the Snowflake **MCP server**, behind an
abstracted checks layer. UI: a Python-authored, serious (non-gadgety) framework —
**NiceGUI** (Vue under the hood) — sitting on the engine's event stream. Model is a config knob.

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
layer is **core, not deferred**; its shape is still to be designed (the user's vision).

**Where we are (2026-07-26).** The engine is built (`checks`, `ops`, `spec`, `catalog`), so is
the copilot loop (`portia/agent/` — in-process MCP server, layered context, `AskUserQuestion`
routed to a human, spec writing, chat CLI), and so is **V0 of the app** (`portia/ui/`) — the loop
now runs in one window, with no terminal. The **verification loop** now exists too: recording a
step executes it, `checks/outcome.py` measures the table it produced, and a step that hits a zero
is refused rather than written.

**What is not built is scale.** The engine is pandas throughout, so it needs several times a file's
size in RAM to look at it, and the next real test is ~20 PHQ tables, many multi-GB. Measured
2026-07-27 and specced in **`docs/DUCKDB_MIGRATION.md`**, now item 4 below.

**This is not yet a working copilot.** The loop has now faced a
model four times (Runs 3–6 in `EVALUATION.md`). Each fix closed one escape and revealed the next:
the spelling trap is dead, the tautology grain is dead, and Run 5 shipped a 3.85%-inflated table by
writing `acknowledge` without ever asking the user. **Run 6 changed the model rather than the
code** — `claude-opus-5` at low effort — and in the indexing phase alone raised the revenue
outliers nobody had ever asked about, predicted the fan-out before joining, and named portia's
missing aggregate itself. It never reached the gate, so the consent question is still open, but the
Runs 1–5 failures now read as **capability rather than architecture**. Read **`EVALUATION.md`**
before building on top of any of it — it separates what the engine can do from what the copilot has
been shown to do.

Next, in order:

1. ~~**The escape hatch**~~ — **shipped 2026-07-26** (`ops/sql.py`): the agent declares `inputs`
   and authors one DuckDB `SELECT`, captured verbatim and measured by the same harness as every
   other op. `ops = {join, normalize, sql}`. The fixture's fatal fan-out now has a handling the
   spec can express, and built by hand it produces the answer key's table exactly — 14 rows,
   revenue 136,240, zero inflation. **Next is to watch a model use it**: every previous run failed
   at a point where no correct move existed, so the whole sequence needs re-reading once one does.
   Resist promoting `aggregate`/`filter`/`dedupe` into prewritten ops until runs show which shape
   is actually reached for (`BACKLOG.md`).
2. **Make the consequence of a zero a computed fact, rendered where the human answers.** Run 5's
   override was taken alone, and the instruction it skipped ("tell the user what a total would be
   off by") asks for a number that is nowhere in the agent's evidence. Compute what a row
   multiplication does to each measure column and put it at the confirmation prompt, so consent is
   informed whether or not the agent cooperates. Demoted below the hatch by Run 6, which stated the
   consequence unprompted — but only qualitatively, and no capable model has yet been watched
   reaching a blocking flag.

3. ~~**The surface — V0 of the app.**~~ — **shipped 2026-07-26** (`portia/ui/`,
   `python -m portia.ui`, `ui` extra). Three panes on the engine's event stream, driving a turn
   through `agent/ask.py`'s injected `answer`/`confirm`. **The bar is met: a full test run with no
   terminal** — project creation, the brief, adding and profiling sources, the interpret turn with
   its write confirmations, the spec run, and every artifact, all in one window. The engine gained
   exactly one thing it was already missing (`events.TOOL_RESULT`); everything else is a renderer.
   *The claim this settles is that the loop is now **watchable**, not that it is good* — what the
   copilot does with a question is still scored by hand in `EVALUATION.md`.

4. **The scale tier — DuckDB (`docs/DUCKDB_MIGRATION.md`, specced 2026-07-27).** Promoted to the
   front because it is now a **blocker on the next real test**, not an eventual concern. The target
   dataset is ~20 PHQ tables, many multi-GB; pandas needs 4.8× a file's size to profile it and
   `run_spec` holds every source and every intermediate at once. Measured: the same profile costs
   122 MB and 0.3 s in DuckDB, and an 80M-row join — portia's fan-out case, the one that inflated
   revenue in Run 5 — can be counted in 0.4 s without being built. pandas cannot measure that join
   on real data at all. The guarantee is that **nothing the copilot reads changes**: same evidence
   dicts, same provenance keys, same sandbox, enforced by golden-file parity tests written before
   any implementation moves.

5. **The run log** (`EVALUATION.md`, specced 2026-07-26). The more painful half of the surface
   problem: seven runs scored by hand off transcripts, and the app's transcript still dies with the
   window. One JSONL per turn, teed at the edge in `cli/chat.run_turn` and the app's turn driver.
   `events.TOOL_RESULT` — its other prerequisite — landed with the app.

> **A cheaper answer to the question that motivated the migration.** The copilot never sees data,
> only profiles — so its judgment on a 5 GB table and on a *referentially-consistent subset* of it
> is nearly the same. Slicing every table to rows reachable from a chosen set of ids preserves
> schemas, key overlap, spelling mismatches and fan-out, and tests the reasoning question today at
> 1/100th the size. (Naive per-table sampling does not: independently sampled tables stop sharing
> keys and every join looks empty.) Scale and judgment are separable questions; only one of them
> needs the migration. `DUCKDB_MIGRATION.md` §11.

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
