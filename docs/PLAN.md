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

**The loop is a conversation** (2026-08-07, `docs/CONVERSATION.md`). `session.run` held the SDK
client for exactly one prompt, and what that cost was not "no follow-up" — the catalog and the spec
survive a turn deliberately. What died was the *evidence*: the profile it pulled, the findings it
read, the key it rejected and why. So a follow-up re-climbed the ladder to get back where the last
turn ended, and a profile is the most expensive thing the agent does. `session.Conversation` now
holds one client across exchanges, the log's unit is the **chat** rather than the exchange, and the
window has a composer. Two findings worth carrying: **measurement reversed the spec's own §8** —
`interrupt()` cancels the parked `can_use_tool` task itself, so the elaborate resolve-first protocol
it specified was unnecessary — and the vocabulary fix was overdue rather than new, since
`.portia/runs/` held *turns* beside a project-root `runs/` that held runs. Three artifacts, three
words: a **run** executed a spec, a **chat** is a conversation, an **indexing** is a job.

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

**The copilot loop runs end to end, and its prompts have never been worked on.** Eight shakedown
runs exist (`EVALUATION.md`) and they were exactly that — checks that a turn completes, a spec is
written, the gate fires when it should, the run log captures it. Every one held the prompts at their
first draft and varied nothing, so they found **real defects in portia's own code** and nothing at
all about the copilot's judgment. `EVALUATION.md` was trimmed hard on **2026-08-04** to stop them
being read as a score; the causal arguments that had accumulated around them were never testable and
are gone.

**So what is left is infrastructure the agent does not yet have**, not tuning what it does with what
it has. Read `EVALUATION.md` for the defects those runs found, and for the standing rule that shapes
all of it: ground truth is cheap to *check* and expensive to *write*, so the answer keys are the
asset.

**Shipped, in the order it happened:**

1. **The escape hatch** (2026-07-26, `ops/sql.py`). The agent declares `inputs` and authors one
   DuckDB `SELECT`, captured verbatim and measured by the same harness as every other op.
   `ops = {join, normalize, sql}`. Built by hand it produces the hotel answer key exactly — 14 rows,
   revenue 136,240, zero inflation. **No model has yet been watched reaching for it**, so resist
   promoting `aggregate`/`filter`/`dedupe` into prewritten ops until real runs show which shape is
   actually reached for (`BACKLOG.md`).

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

4. **The run log** (2026-07-29, `portia/runlog.py`, `python -m portia.cli.history`). One JSONL each,
   teed at both edges, replayable in the terminal and under **Chats** and **Indexing** in the app's
   left pane — two histories since 2026-08-07 (`CONVERSATION.md` §3), because a conversation you had
   and a job the app ran are not one list. A copilot chat no longer dies with the window. It caught one thing worth carrying into any cost
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

**Shipped — the knowledge graph** (`docs/KNOWLEDGE_GRAPH.md`, designed and built 2026-08-04,
all four phases). The gap it fills is
structural and visible without any run: **the catalog is one file per source and has no shape for
what a source *relates to***, and `checks/join.py` measures exactly that relationship and then
discards it when the turn ends. On top of that, **L1 is exhaustive and pushed into every system
prompt** — one line per source, fine at 3 and the wrong shape at 50. What you want there is a
neighbourhood you walk outward from, which is a graph traversal, not a document read.

**And the second half is lineage**, which is the same gap one layer down: a spec records that a
column was renamed, coerced or computed from three others, and **no surface in portia can answer
"where did this column come from"**. The two halves are what make each other worth having — measured
overlap is weakest on raw sources, because unharmonized columns are precisely the ones that don't
share values yet, and the tables portia *builds* are where the mapping has already happened.

**Neo4j, settled** (§3 records what each rejected option was rejected *for*). The build order runs
cheapest-and-most-certain first: persist `min`/`max` in `catalog._column_facts`, which the profiler
already computes and the catalog drops · the structural skeleton from catalog + specs, including
column lineage, which is translation rather than inference · the write hooks on index-a-source and
save-a-spec · then the measured overlap edges. **Which pairs get measured is settled** (§5.1): the
agent picks them while it is indexing, the same act in which it already writes a summary and
proposes groups — not a code prefilter and not a sweep of all ~245,000 pairs. Above all, §6.1: **the
graph surfaces edges and never ranks them**, and §4.4: a measured zero means *no shared values*, not
*unrelated* — which is why the edge carries the reason the agent asked for it.

**Where it actually is.** §9.4's phases A and B are built and verified against a live Neo4j.
**A** is the write path — sources, models, columns, groups, `READS` and column-level
`DERIVES_FROM`, read off the catalog and the specs with nothing run and no connection opened.
**B** is the read path: `graph_lookup`, fixed queries, one new tool. Between them they settled the
lineage rank (a transform outranks a rename outranks a carry), made the `sql` hatch's cost
countable rather than guessed, fixed the rule that a rebuild may delete structural edges and
**never** a measurement, and answered §7's open question about what a query may return — *a router
returns tables*, so the fifty-edge answer never gets constructed and nothing has to be truncated to
avoid it. **C** is the measured half: the agent picks which column pairs are worth comparing *while it is
indexing*, and portia measures them and stores the numbers with the reason it was asked. **D** was
taken **half way, deliberately** — each source's line in the always-on brief lost its column count
and candidate keys, and the index itself stays. §1.4 wants it replaced by traversal; its premise is
"unproven at 50", which is not a measurement, and there is no re-runnable fixture that could say
whether the copilot got worse. `BACKLOG.md` holds what would have to be true first.

**And it has been watched once on real data** (2026-08-06, `EVALUATION.md`). Haiku at low effort,
23 PHQ sources, one indexing turn, $0.19. It chose 12 column pairs out of the ~245,000 the schema
permits; eight are ordinary foreign keys and measure like it, one is a name join that half-works,
one is a plain miss the measurement caught — and **the two that connect the event data to the hotel
golden record both measure zero**, one of them with mismatched types. That second one is the thing
worth remembering: a *type check* would have discarded it with certainty and been correct to, and
it is one of the two pairs that project exists to resolve. §6.5's warning that a provable excluder
can still be blind to meaning is no longer hypothetical. It also never called `graph_lookup` — three
indexing runs now, zero uses of the router — which is a prompt problem and is where the next work
is.

**The loop changes with it, not after it** (§9). The graph sits *before* `describe_source` as a
router — *which* table should I look at, and where did this column come from — rather than as a
deeper rung above `join_findings`. **Indexing is the part that gets rewritten**: it becomes "read
each source, describe it, group it, and say how it relates to what you have already read." The
conversation phase gains a tool and keeps its shape. Shrinking the exhaustive L1 index to a
traversal is the payoff and is **last**, on its own, so it is not moving at the same time as
anything else.

Also still worth doing: `fan_out` fires on every fact-to-dimension join because it reads either
side's key multiplicity rather than the result's, which at this scale is how a real warning gets
learned as noise.

**The consequence of a zero, as a computed fact rendered where the human answers.** At the moment
the gate refuses, `copilot.md` asks the agent to tell the user what a total would be off by — and
that number is nowhere in its evidence, which holds `n_duplicated_keys` and example keys and no
measure at all. Compute what a row multiplication does to each measure column and put it at the
confirmation prompt, so consent is informed whether or not the agent cooperates. **This is the same
shape as the graph work**: a missing measurement, not a missing instruction.

> **A referentially-consistent subset would make copilot runs cheaper to judge**, by turning one
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
