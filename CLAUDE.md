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
