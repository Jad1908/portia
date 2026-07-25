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

- **`impute` op** — fill nulls (mean/median/constant/…); pairs naturally with `rationale` (the
  mean-vs-median call is decided by one-off analysis, recorded as the "why"). Good next op.
- **`dedupe` op** — resolve duplicate rows/keys; gives the `fan_out` situation a real resolution.
- **`filter` / `derive` ops** — row selection and computed columns, common and safe.
- **The custom-step escape hatch** — let the agent author a transform we didn't prewrite, captured
  verbatim in the spec and measured by the same provenance harness. **Open decision: the language**
  (see the note in `spec` below). Not built — today `ops = {join, normalize}`.

## Spec — the durable artifact

- **Escape-hatch language (open decision).** How custom steps are expressed. Options: (a) **DuckDB
  SQL-first** + a narrow Python escape — reproducible, reviewable, matches the scale story
  *(leaning here)*; (b) **captured Python** pure-functions — maximally expressive, harder to make
  reproducible/reviewable; (c) **both, first-class**. Whatever wins, the provenance harness wraps it.
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

- **The copilot loop** (Claude Agent SDK) — reads the checks' evidence, does the judgement (what's
  material, what to ask, how to frame it), orchestrates the ops, writes decisions + rationale to the
  spec. This is where "decide" lives; deliberately not a deterministic module.
- **Sandbox data access** — hand the agent's code-execution a clean handle to the loaded frames so it
  can run read-only ad-hoc analysis (the imputation-shape question). Ephemeral; verdict → `rationale`.
- **Pro-auth verification** — confirm which models Claude Pro exposes to the Agent SDK and how they
  meter (open question in `PLAN.md`); the whole budget discipline rides on this.
- **Don't re-ask what's decided** — the agent asks only about what the spec hasn't answered; drift
  can re-open a specific decision. (Best shaped by real use, per the user.)

## Interface — the surface

- **Per-file "what this data is"** — the LLM's plain-language interpretation on top of the
  deterministic profile, with user-editable corrections (durable). `VISION.md` flow.
- **The three-panel app** (files · workflow · chat) — NiceGUI on the engine's event stream. Core to
  the product, deferred until the engine + agent are proven. `VISION.md`.

## Scale — data tiers

- **DuckDB tier** — larger-than-memory local CSV/Parquet behind the same checks/ops interface (swap,
  not rewrite). Also the natural home for the SQL escape hatch.
- **Snowflake tier** via the Snowflake MCP server — push compute to the warehouse, pull small results.

## Core / infra

- **More loaders** — Parquet (and beyond) in `core/io._LOADERS`; one line each.
- **CI** — run the hooks + tests on each PR. *Declined for now (2026-07); revisit if collaborators join.*

## Validation & product (from the brief §9)

- **The decisive experiment** — run the engine on real, non-synthetic, too-big-to-eyeball data from
  actual work. Does the frontier-agent baseline degrade at real scale? Nothing matters more.
- **The five conversations** — describe the tool to five DS/AI-engineers; ask what they do today
  instead. "I'd use this" from the founder is necessary, not sufficient.
