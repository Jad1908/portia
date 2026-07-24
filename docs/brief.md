# Working brief — agent-assisted data harmonization for data scientists

*Consolidated from a research + hands-on session, 23 July 2026. Status: pre-build. Nothing here is validated with users yet.*

---

## 1. The problem

Data scientists, FDEs and AI engineers routinely have to get from *N ugly sources* to *one table I trust enough to model on*. This work is:

- done ad hoc, in notebooks, and thrown away
- redone by the next person, and by the same person next quarter
- undefended — the assumptions behind a merge are invisible by the time anyone reads the numbers

Independently corroborated: a foundation-model lab CEO describing reconciling scraped training data as the tedious bottleneck. This is not a niche annoyance.

**The core difficulty is not generating a transformation. It is knowing whether the transformation was right.** There is no test suite for "did I harmonize these two sources correctly." Academic work on LLM-based harmonization keeps landing on human-in-the-loop designs for exactly this reason.

---

## 2. Positioning

**Analysis-time data preparation.** Everything between "here are N ugly sources" and "here is one table I trust."

| | |
|---|---|
| **User** | Data scientist / FDE / AI engineer. Rigorous, technical, comfortable with SQL and Python. Does **not** own the pipeline and does not want to. |
| **In scope** | Local CSVs → warehouse tables. Exploration, reconciliation, entity resolution, one-off and repeated analysis prep. |
| **Out of scope** | Orchestration, scheduling, CDC, incremental loads, SLAs, deployment. |
| **Not a** | no-code tool, BI tool, notebook, ETL platform. |

**Rejected alternatives, with reasons:**

- *No-code canvas* — user explicitly is an engineer; assembly is not the job
- *MCP server + separate UI* — clunky ping-pong loop, manual refresh, no shared state (see §6)
- *MCP Apps / inline UI* — evaluated and ruled out by the user as gadget-like
- *Skill or prompt pack* — accumulates nothing; that's the thing to beat, not to build

---

## 3. Landscape

**Layer 1 — Coding agent + MCP.** Claude Code / Codex against dbt Fusion MCP, warehouse MCPs. Strongest current option. Datafold's read: most vendor "agents" are thin wrappers, and the power-user move is bringing your own agent. **This is the real competitor.**

**Layer 2 — Warehouse-native.** Databricks Genie Code, Snowflake Cortex Code, BigQuery Data Engineering Agent (GA April 2026). Code-first and conversational, but locked to one warehouse.

**Layer 3 — Independent code-first platforms.** Bruin (MIT, CLI, Git-native), Ardent AI, Paradime DinoAI, Altimate, Ascend, Genesis. All aimed at the *pipeline* layer.

**Layer 4 — Legacy ETL + agent bolt-on.** Matillion Maia, Fivetran, SnapLogic, Prophecy, **Duckle**. No-code end. Not the target user.

**Evaluation harness worth knowing:** ADE-bench (dbt Labs, open source). Vendor-published scores are self-reported — use the harness, ignore the leaderboard.

**Adoption signal:** dbt's 2026 survey — 72% of teams prioritize AI-assisted agentic coding for data work; only 24% prioritize AI-assisted pipeline management. Demand is in the IDE, not the infrastructure.

**The gap:** every layer above assumes you already know your target schema and your sources are well-behaved. Almost nobody works on arbitrary-sources-to-coherent-model. Lume is the closest commercial attempt, small and vertical.

---

## 4. Competitive teardown — Duckle

Local-first visual ETL studio on DuckDB. MIT/Apache, desktop app, ~360 components, local Qwen 2.5 Coder 1.5B assistant ("Duckie"), built-in MCP server. Closest thing found to the same bet.

**Findings from hands-on evaluation:**

1. **Unsigned binary → macOS Gatekeeper wall.** Adoption tax before first use.
2. **Broken install.** Every unversioned `.dylib` alias shipped as a 0-byte file — symlinks lost in packaging. The local model could not start at all until the links were manually rebuilt. A `pip`/`brew`/`npm` distribution cannot fail this way.
3. **374 components on an empty canvas.** The target user's first reaction was "intimidating." The tool's premise is that the user's job is *assembly*.
4. **The AI layer is a bolt-on.** It's the landing-page differentiator and it crashed on launch.
5. **The generated pipeline was structurally incoherent.** Join predicates on an `id` column that exists in none of the three files; all three joins missing their second required input; three edges into one input port; no aggregation despite the prompt asking for totals.
6. **⭐ The validator checks node-local completeness, not graph-level coherence.** Status bar read *"1 problem: Columns to keep is required"* — the least important thing wrong with it.
7. **⭐ Join nodes expose a first-class `unmatched` output port — and nothing obliges you to connect it.** Every one was dangling. The pipeline would have run, produced a number, silently discarded rows, and reported 0 warnings.

Findings 6 and 7 are the most valuable output of the whole session.

**Read:** Duckle has durable artifacts and no reasoning. The verification *primitive* exists but is opt-in, buried among 374 others, and therefore used precisely when it is least needed — by people already being careful.

---

## 5. The baseline experiment, and why it is not decisive

Same three-CSV fixture (10 deliberate traps), same prompt, run through Claude Code + DuckDB.

**Result:** 8/10 traps caught unprompted in ~2 minutes. Refused to sum across currencies. Refused to sum a 1000× units mismatch. Merged three near-duplicate entity pairs. Volunteered a source-provenance matrix (effectively a drop report). Self-debugged its own fuzzy-match bug. Missed the DD/MM date ambiguity.

**Why this is an upper bound, not an estimate.** Raised by JA on reading the trace, verbatim:

> * This is a very small file, it read it completely and could spot easily the differences
> * very low cardinality on columns
> * the issues are very very very obvious, in real world the issues are much more complex and subtle
> * you created it and another instance of you found it lol

Expanded:

- **29 rows** — the model *read* the entire dataset rather than sampling it. At real scale it sees schemas and samples, never the data. Different task.
- **Low cardinality** — duplicates were visually obvious. Real columns have thousands of distinct values and the duplicates hide in the tail.
- **Legibility** — the traps were discrete, labeled and findable. Real mess is a thousand small inconsistencies where the hard part is deciding which three matter, not spotting them.
- **Circularity** — the fixture was written by Claude and solved by Claude. Same training distribution, same priors about what "messy data" looks like. A lock built and picked by the same hand. This objection alone is enough to disqualify the result as evidence about real performance.

**What it does establish:** the naive version of the pitch — "the agent won't notice, I'll build the thing that notices" — is dead at small scale. Whatever gets built must beat this, not the absence of this.

---

## 6. Differentiation

Two sentences, both earned this session:

> **Duckle makes verification available. We make it unavoidable.**

> **Claude Code has the reasoning and no durable artifact. Duckle has the durable artifact and no reasoning.**

Concretely, the product is **not the reasoning — it's the residue.** Three things the frontier-agent baseline does not do:

1. **Durability.** "Strip dots so `G.m.b.H.` → `gmbh`"; "billing is authoritative over web." Real institutional knowledge, currently living in a chat transcript and a scratchpad script. Gone next month.
2. **Auditability.** The agent *asserts* the entities resolved correctly. You cannot see which fuzzy matches were near-threshold, or which were judgement calls. To verify you must read the script.
3. **Reproducibility under drift.** New source next quarter → fresh, differently-reasoned answer, no diff against last time.

Design consequences:
- the drop report is **not a node you wire** — it is the default output, unsuppressable
- units and currency mismatches are refused, not summed, without a word
- every match carries a confidence and a reason, and near-threshold decisions are surfaced by default
- decisions persist as a reviewable spec and improve session 10 over session 1

---

## 7. Architecture

**Own the client. Embed the loop.**

MCP is unidirectional — an agent calls a tool and gets a result. There is no way for a separate UI to participate in the session, which is why "MCP server + your own app" produces the switch-window-and-refresh loop. Rejected.

Instead:

- **Embed an agent loop as a library** (Claude Agent SDK or equivalent) inside your own application. The harness is now `pip install`-able; it is not the differentiator.
- **Be an MCP *client*, not a server.** Connect out to Snowflake, BigQuery, dbt, DuckDB via their MCP servers — avoids building 50 connectors.
- **One process, one state.** Agent tool call mutates session state → websocket push → table view updates. No protocol boundary, no refresh. This is the Jupyter kernel/client architecture with a third participant on the bus.
- **Local-first: `pip install x && x serve` → browser on localhost.** Data people will not hand warehouse credentials to a seed-stage startup. Hosted later, for teams who want shared mapping history. Duckle's four failure modes above are all downstream of hand-rolled desktop distribution.
- **The durable artifact is a file in the user's repo** — a declarative mapping/contract spec, git-versioned, diffable, reviewable in a PR, readable by any agent. Degrades gracefully; no hostage-taking. (dbt's lesson: the format becomes the standard, the tooling becomes the business.)

**Precedent:** Cursor did not ship a VS Code plugin protocol. They forked the editor, because the surface *was* the product.

**Costs accepted:** you own model spend (BYO key), context management, and evals; you lose the "just add it to your existing setup" adoption path.

**Reference implementations to study:** `duckdb -ui` (localhost pattern, catalog panel), `rill start` (single binary, code-as-files, git-native), `marimo` (reactivity — downstream invalidation when an upstream mapping changes), OpenHands (open-source chat + panels layout).

---

## 8. Open questions

Ordered by how badly a wrong answer hurts.

1. **Does the frontier-agent baseline degrade at real scale?** 40 sources, 500 columns, 50M rows — the model can no longer read the data, only schemas and samples. If it still catches everything, this is a workflow convention, not a company. If it degrades sharply, that's exactly where the tool earns its keep. **This is the experiment that decides the project.**
2. **Would colleagues actually use it, or just agree that it sounds useful?** Currently untested. Cheap to test: describe it to five people and ask what they do today instead. "I'd use this" from the founder is necessary and not sufficient.
3. **Is verification a product or a feature?** Plausible that a harness vendor ships a good diff/provenance view and absorbs the generic case. Defense is accumulated project-specific decision history — which means the MVP must accumulate something or it *is* a skill.
4. **Local CSVs and 4,000-table warehouses are different jobs.** The first is "how do these five files join." The second is "which tables even matter, and I can't scan them all." Doing both from day one is a lot. Pick one; keep the artifact portable.
5. **What is the correctness oracle?** If neither the agent nor the user can verify a harmonization, "make verification unavoidable" risks meaning "surface more things the user also can't adjudicate." Surfacing must be ranked and cheap to act on, or it becomes noise.

---

## 9. Next steps

1. **Run open question #1** on real, non-synthetic, too-big-to-eyeball data from actual work. Nothing else should be built first.
2. **Have the five conversations** for open question #2.
3. **Design the spec file format** before any UI. If the artifact is right, the interface follows; the reverse is not true.
4. Only then: thin vertical slice — one agent loop, one review surface, one persisted decision, on the real dataset from step 1.

---

## Methodological note

This brief comes from one session with a strong bias: the fixture was Claude-authored and Claude-solved, the competitor teardown was a single 90-minute sitting on a v0.5.6 alpha, and every framing here was arrived at conversationally rather than tested. Four consecutive findings landed exactly where the thesis predicted, which is either good instinct or confirmation bias — worth staying honest about which. Treat this as a hypothesis with unusually well-specified next experiments, not as a conclusion.

If any single part of this brief should be read twice, it is the four objections in §5. They are the sharpest thinking in the document and they are the correct posture toward every other result recorded here.
