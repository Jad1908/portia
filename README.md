<div align="center">
  <img src="assets/the-portia-spider.jpg" alt="portia" width="280">
  <h1>portia</h1>
  <p><strong>Start catching the bugs in your data.</strong></p>
</div>

---

An agent-assisted data-harmonization copilot — from *N ugly sources* to *one table you trust*.
It dives into your data and comes back with questions and insights, surfacing mismatched units,
missing values, and near-duplicate entities as decisions to make rather than silent guesses —
and records every choice as a durable, reproducible artifact.

Every number it tells you comes from deterministic code, never from the model reading your data.
The engine runs on DuckDB, so it works on tables too big to open: sources are ingested once into
the project, and a join that would explode to 80 million rows is *counted* rather than built.
CSV and Parquet.

## The app

Three panes — your files, the workflow, and the copilot — in one window:

```bash
uv sync --extra ui --extra agent
python -m portia.ui
```

Open a project directory (it gets created if it isn't there), write a few lines about what the
project *is*, drop your data in — CSV or Parquet — and go. Every question the copilot asks and every write it wants to
make stops on screen, with the evidence still next to it.

Pressing **Run** executes the spec in memory; **Write outputs** saves the tables to `out/` and
**Save report** saves the run as markdown to `runs/`. Nothing is written until you ask.

There are CLIs for the same engine — `python -m portia.cli.index`, `.chat`, `.run` — if you'd
rather stay in a terminal; `run --write out --report runs` produces the same two artifacts.

## Docs

- [Direction](docs/PLAN.md) — where this is going, and where it actually is
- [Evaluation](docs/EVALUATION.md) — how the copilot is scored, and its honest current score
- [Tech stack](docs/TECH_STACK.md)
- [Product vision](docs/VISION.md)
- [Design](DESIGN.md)
- [The scale tier](docs/DUCKDB_MIGRATION.md) — what the DuckDB engine cost, and what it found
- [Backlog](docs/BACKLOG.md)
- [Brief](docs/brief.md)
