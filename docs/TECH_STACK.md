# Tech Stack — portia

*Referenced by `PLAN.md`. This records our stack choices and the reasoning behind them.*

**Guiding principle:** this is not a SWE project — rigor lives in the modelling. So we minimize
infrastructure and frontend surface, stay in Python wherever we can, and keep the layers
**decoupled** (engine ↔ event stream ↔ UI) so any single piece can be swapped without a rewrite.

---

## Language & orchestration

- **Python** throughout, with **`uv`** for environment and dependency management — project
  defined by a **`pyproject.toml`** (and a committed **`uv.lock`**).
- **Claude Agent SDK** (`claude-agent-sdk`) for the copilot loop: agent loop, context management,
  **MCP client**, custom tools. Docs: `code.claude.com/docs/en/agent-sdk`.
- **Model is a config knob**, not a fixed choice. We develop on a cheaper, smaller model at low
  effort — if it works there, the *engine* is good, not the model; the flagship is a ceiling
  check / upgrade, never a dependency. **Claude Pro only** (no API budget, no Max), so loops stay
  **token-lean** (the agent sees compact profiles/schemas, never raw data). *Verified 2026-07-25:*
  the SDK authenticates off the local Claude Code login with no `ANTHROPIC_API_KEY` set; how it
  meters is still open. portia itself writes **no auth code** — see `PLAN.md` → "Auth posture".

## Data & compute — DuckDB, with pandas at the edges

*Rewritten 2026-07-28. This section used to say "pandas-first, SQL only when scale forces it".
Scale forced it; `docs/DUCKDB_MIGRATION.md` is what happened.*

- **DuckDB under the whole engine.** `pip install duckdb`, **embedded (no server)**, reads
  larger-than-memory CSV and Parquet. A project reads its sources **in place, from inside the
  repo**, and everything downstream — profiling, join diagnosis, the ops, `run_spec` — is a lazy
  relation behind `core.table.Table`. Measured on real data: 4.82 GB across three tables indexes in 32 s,
  a 50M × 3M join is diagnosed in 3.8 s, and **peak memory is bounded by the largest table rather
  than the total**, which is what makes ~20 tables workable at all.
- **Why we *used to* ingest rather than query the files in place** — kept because the reversal
  below is the useful part. Two reasons, and the second was meant to be the real one:
  columnar storage is ~20× faster on column-scoped reads, and — decisively — if a source *is* a
  `read_csv` call then the agent's SQL needs file-reading rights, which is exactly what
  `ops/sql.py` exists to withhold. Data inside the database means the hatch needs no filesystem
  access at all. `DUCKDB_MIGRATION.md` §3.
  - **Reversed — the store was removed 2026-07-31** (`PIPELINE.md` §2.7). Both arguments
    turned out weaker than they read. The speed one never landed: `run_spec`, every agent check and
    every CLI tool re-read the original files anyway, so the fast copy was written and then ignored.
    And the sandbox one does not apply — `ops/sql.py` materializes its declared inputs into a fresh
    restricted connection whatever their query is, so the hatch never sees a `read_csv` regardless.
    What is left is a hidden second copy of the user's data, against a product that is tightening to
    **sourcing only from files already in the repo**. If reads get slow, the answer is **parquet in
    the repo** — columnar, typed, already supported, and still one copy you can see.
    *Removing it moved no evidence at all: all 35 golden cases came out byte-identical, because
    ingesting was `CREATE TABLE … AS <read_query(path)>` — the reader and its null tokens were
    always the same ones, and only the materialization differed.*
- **pandas is still here, at four edges, deliberately.** The fixtures (tiny, and the readable
  definition of the test data), `load_frame` for small reads, the renderers, and the SQL hatch's
  sandbox boundary. `tests/test_table.py` fails if anything *else* pulls a whole relation into
  memory — the rule survives exactly as long as no one adds a `.df()`, so it is a test rather than
  a convention.
- **Parquet as well as CSV**, registered in the same one place. Parquet carries its schema, so the
  CSV reader's sniffing stops being part of the answer; it is also ~4× smaller. Converting is a
  `COPY … TO` one-liner and deliberately **not** a portia feature — rewriting someone's data is not
  a data-harmonization concern.
- **Snowflake tier via the Snowflake MCP server.** For the ~15–20-table tier, push computation to
  the warehouse and pull back only small results; never pull full tables. We are the MCP *client*
  (BYO creds, local). `core.table.Table` is the seam it plugs into — a name, a query, and a
  connection is not a DuckDB-shaped idea.
- **Compute stays behind a checks layer.** Each check is a small function returning structured
  evidence. Whether it counts with DuckDB or Snowflake is an implementation detail — so the copilot
  and the spec logic never change.
  - **What the seam was actually worth (2026-07-28).** It did what it promised where it mattered:
    the *evidence dicts, tool signatures and prompts were untouched* by the migration, and all ten
    end-to-end golden cases came out byte-identical. But every *implementation* behind them had to
    be rewritten. The seam bounds the blast radius; it does not make the change free. The stronger
    lesson is that **the golden files did more work than the abstraction did** — freezing the
    evidence before moving anything is what made the swap checkable rather than hopeful.
- **Entity resolution:** `rapidfuzz` (+ optionally `recordlinkage` / `dedupe`) for blocking and
  fuzzy scoring.

## Interface (UI)

Goal: modern and **serious**, not gadgety — but the builder is not a frontend dev and this is
not a SWE project, so we minimize JS/frontend learning.

- **Ruled out — Streamlit / Gradio.** Gadgety, ML-demo aesthetic, constrained layout.
- **Not yet — a bespoke Vue/React SPA** (what n8n has). That is real frontend engineering (JS
  build stack, component architecture, state management, hand-wired websockets). Appropriate
  later; overkill and high-risk for a solo non-frontend builder now.
- **Recommendation — NiceGUI.** You write **Python**; it is built on **Vue + Quasar** under the
  hood, so you get real Vue components and a modern, professional look without writing JS.
  Real-time **websockets are built in**, which fits the streaming copilot Q&A + side-panels
  layout (chat/question flow, decision log, drop report, spec preview). It is the closest thing
  to "Vue like n8n, but I stay in Python."
- **Alternative — Reflex.** Pure Python that compiles to a React/Next.js app; a more app-like
  ceiling if we later grow this into a real product. Slightly steeper.
- **Safety net.** The engine emits a clean **event stream** (questions, insights, decisions) and
  the UI sits on top of it. The UI is **swappable** — choosing NiceGUI now does not lock us out
  of a bespoke Vue/React frontend later; we would replace the UI without touching the engine.
- **The look is specced independently of the framework** — `DESIGN.md` is CSS custom properties and
  component definitions, not NiceGUI APIs, precisely so the swap above stays cheap. Ships as an
  optional `ui` extra (like `agent`), so a core install stays pandas + pyyaml + duckdb.

## Durable artifacts

- The **spec/contract**: a plain-text, **git-diffable, re-runnable** file in the user's repo —
  reviewable in a PR, readable by any agent. Its schema **emerges from real runs** (format TBD;
  likely YAML/JSON). Re-running against a changed source produces a readable diff.
- A generated **report** (markdown/HTML) as a durable summary; live decisions surface in the UI.
- The **run log** — one JSONL per copilot turn, project-local (`portia/runlog.py`).
- **The compiled pipeline — shipped 2026-07-31** (`PIPELINE.md`, `portia/pipeline.py`). One `.sql` file per
  spec, in its own folder, **dbt-shaped**: one file builds one table, and it references other models
  by name. portia writes the model files and nothing else — no `dbt_project.yml`, no `profiles.yml`,
  no `schema.yml` — so the output drops into a dbt project without portia becoming a dbt wrapper or
  having to track dbt's conventions.
  - **This is a commitment worth naming as a stack choice**, because it decides what portia's output
    plugs into. dbt is the shape a data team already knows how to read, review and schedule, and
    matching it costs almost nothing: **the engine already generates this SQL on every run and
    discards it.** Composing steps as named blocks instead of nested sub-selects is the whole change.
  - **Compilation and execution are separate paths, so they must be pinned together.** Execution
    keeps the SQL sandbox; compilation only emits text. A test has to run both and assert the same
    table comes out — the migration's lesson that *the golden files did more work than the
    abstraction did* applies directly.

## Deliberately out of scope

- Heavy infra, servers we operate, scheduling/orchestration/CDC (per the brief).
- Handing warehouse credentials to hosted services — **local-first** (`pip install` → localhost).
- Frontend engineering beyond what a Python-authored framework gives us.
