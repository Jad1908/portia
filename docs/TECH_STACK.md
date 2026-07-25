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

## Data & compute — pandas-first, SQL only when scale forces it

- **pandas for the local MVP.** For local messy CSVs that fit in memory, pandas is the right
  tool and fully sufficient: every deterministic check is bread-and-butter pandas — profiling
  (`isna`, `nunique`, `describe`, dtypes), join row-conservation and fan-out (`merge` +
  `groupby`/`value_counts`), duplicate detection (`duplicated`). You build the whole local phase
  in what you already know.
- **DuckDB — for scale, and now also for the escape hatch.** *Decided 2026-07-25: agent-authored
  custom steps are DuckDB SQL, which makes DuckDB a core dependency earlier than "only for scale"
  below anticipated. Reasoning in `BACKLOG.md` → "The escape hatch"; the short version is that SQL
  is the only option that keeps the spec reviewable in a PR, keeps the filesystem/network away
  from the agent, stays stable across versions, and survives the pandas → DuckDB → Snowflake seam.*
- **DuckDB only for scale.** pandas has one hard limit — it loads everything into RAM — and the
  product's premise is data too big to eyeball / too big to be local. DuckDB is the local answer:
  `pip install duckdb`, **embedded (no server)**, reads larger-than-memory CSV/Parquet, and
  **interoperates with pandas** — it queries a DataFrame directly and hands one back:
  ```python
  import duckdb, pandas as pd
  df = pd.read_csv("messy.csv")
  out = duckdb.sql("SELECT currency, COUNT(*) FROM df GROUP BY currency").df()  # → DataFrame
  ```
  So it's not pandas *or* DuckDB — you write pandas and drop into one SQL line only where you
  need scale or a warehouse pushdown.
- **Snowflake tier via the Snowflake MCP server.** For the ~15–20-table tier, push computation to
  the warehouse and pull back only small results; never pull full tables. We are the MCP *client*
  (BYO creds, local).
- **Compute stays behind a checks layer.** Each check is a small function (e.g.
  `join_report(left, right, keys) -> {...}`) returning structured evidence. Whether it counts
  with pandas, DuckDB, or Snowflake is an implementation detail — so pandas → DuckDB/Snowflake is
  a **swap, not a rewrite**, and the copilot/spec logic never changes.
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

## Durable artifact

- The **spec/contract**: a plain-text, **git-diffable, re-runnable** file in the user's repo —
  reviewable in a PR, readable by any agent. Its schema **emerges from real runs** (format TBD;
  likely YAML/JSON). Re-running against a changed source produces a readable diff.
- A generated **report** (markdown/HTML) as a durable summary; live decisions surface in the UI.

## Deliberately out of scope

- Heavy infra, servers we operate, scheduling/orchestration/CDC (per the brief).
- Handing warehouse credentials to hosted services — **local-first** (`pip install` → localhost).
- Frontend engineering beyond what a Python-authored framework gives us.
