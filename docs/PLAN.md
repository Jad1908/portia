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
