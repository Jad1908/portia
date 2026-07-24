# portia — read this first

**Before doing any work in this repo, read these docs.** They define the project's direction,
stack, and product vision. Read them every session, before proposing changes or writing code:

- `docs/PLAN.md` — direction: vision, non-negotiables, build order, open problems
- `docs/TECH_STACK.md` — the tech stack and the reasoning behind it
- `docs/VISION.md` — product vision & UI flows (the three-panel app)
- `docs/brief.md` — the original working brief (foundational context)

## How we work here

- **Plans stay directional**, not prescriptive step-by-step specs — they go obsolete. Give
  vision, stack, and watch-outs; let specifics emerge from real work.
- **Rigor lives in the modelling / deterministic code** — the LLM orchestrates, explains, and
  asks; it never eyeballs the data to produce numbers.
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
- **One way to emit evidence.** Checks return **compact, JSON-serializable dicts** built with
  **`portia.core.serialize`** (`to_jsonable`, `round_float`, `to_json`). Never hand-roll
  numpy→python coercion or float rounding — `int64` isn't JSON-serializable and `NaN` isn't valid JSON.
- **Checks are small, pure functions**: `check(inputs) -> structured evidence dict`. Deterministic
  in, structured out. **No printing, no human-formatting, no side effects inside a check** —
  rendering for humans/CLI/UI lives at the edge (e.g. `render_text`, the `python -m …` entrypoints).
- **Compute stays behind the checks layer** so pandas → DuckDB/Snowflake is a swap, not a rewrite
  (see `TECH_STACK.md`).
- **Reuse before you add.** Before writing a helper, look for an existing one. Shared helpers live
  in a shared module; never copy-paste a utility across tools.
- **Named constants, not magic numbers** — thresholds live as module constants in one obvious place.

**Package layout — one home per concern; don't let things pile up flat in `portia/`:**

- `portia/core/` — shared seams: `io.py` (loading) · `serialize.py` (compact JSON evidence)
- `portia/checks/` — the deterministic checks layer: `profiling.py`, `join.py`; entity-res next.
  A check + its `render_*` live together; add new checks here in that shape.
- `portia/fixtures/` — kept mock data (a builder per module, registered in `__init__`)
- `portia/cli/` — play surfaces: `python -m portia.cli.<tool>` (e.g. `portia.cli.profile`)

Rule of thumb: **`core` = reused everywhere, `checks` = the analysis, `cli` = human edge.** A new
file that's none of these probably belongs in one of them, not loose in `portia/`.

## Branching — never work on `main` directly

Unless the user explicitly says otherwise for a given change:

- **Do not edit, commit, or push on `main`.**
- At the start of a task, check the current branch (`git branch --show-current`). If a branch
  relevant to the active task is already checked out, work on that. Otherwise **create a new,
  descriptively-named branch off `main`** before making any changes.
- Only merge or push to `main` when the user asks.
