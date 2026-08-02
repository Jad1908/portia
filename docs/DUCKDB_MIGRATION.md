# The DuckDB migration — the scale tier

*Specced 2026-07-27, **complete 2026-07-28**. A **record, not a plan**: the engine is DuckDB
throughout and the code is the better description of what it does. Compacted 2026-08-02 to what the
code cannot say about itself — the traps, and the places measurement contradicted the plan. Section
numbers are preserved because a dozen comments in `portia/` and `tests/` cite them.*

**If you read three things, read §6.1, §6.3 and §13.** The sandbox design in §6.1 was **impossible**,
on two independent counts. Type inference diverged **three ways** where §6.3 predicted one, and the
unpredicted one would have made a null rate depend on which reader ran. And §1's headline —
*"memory stops scaling with the file and starts scaling with the answer"* — **is not true of a portia
profile** (§13). Predicting in advance still paid: §6 named the right three places to look, and got
two of the three answers wrong.

**Read `TECH_STACK.md` → "Data & compute" and `CLAUDE.md` → "Code conventions" first.**

---

## 1. Why now, with numbers

One 396 MB CSV (4M rows), pandas vs DuckDB, every DuckDB figure under a hard `memory_limit=512MB`
and never approaching it:

| | pandas | DuckDB |
|---|---|---|
| Profile one 396 MB CSV | **16.5 s · 1883 MB peak** (4.8× the file) | **0.3 s · 122 MB** |
| Join two files → 80M rows | materialises 80M rows (many GB) | **0.4 s · 228 MB** |
| Preview 15 rows | full file load | 0.2 s |

**The 80M-row join is the one that mattered** — portia's fan-out case, the thing that silently
inflated revenue in the hotel fixture (`EVALUATION.md`, Run 5). DuckDB *counted* it without building
it; pandas cannot measure that join on real data at all. Profiling cost is dominated by **text
columns** (~80× an int column at 2M rows), which is the thread §13 picks up.

> Read §13 before quoting the memory column. It measures different aggregates than a real profile
> needs.

---

## 2–5. The guarantee, the store decision, `Table`, module-by-module

Folded away 2026-08-02; the code is the record. What survives of each:

- **The guarantee held.** Every evidence dict came out byte-identical bar three declared exceptions,
  all in `tests/test_golden.py` with their reasons; `PROVENANCE_KEYS`, `BLOCKING_FLAGS` and the
  sandbox were unchanged. Nothing was approximated — §13 explains why that turned out to be forced
  rather than chosen.
- **`Table` is `core/table.py`**, and it differs from the spec in two ways worth knowing: it holds
  **query text, not a bound relation** (a relation belongs to the connection that made it, and
  rebinding to a thread's `con.cursor()` has to be free), and it has **two exits, not one** —
  `head()` for rendering and `rows()` for evidence, because a DuckDB `DATE` routed through pandas
  reaches the copilot as `2026-06-12 00:00:00`.
- **DuckDB connections are not thread-safe.** The UI runs blocking work through `asyncio.to_thread`,
  so every threaded call takes its own `con.cursor()`. Getting this wrong produces intermittent,
  hard-to-reproduce corruption.

## 3. The central decision: ingest into a DuckDB store — ⚠︎ REVERSED

**There is no store any more** (2026-07-31, `docs/PIPELINE.md` §2.7). `core/store.py` is deleted;
sources are read in place, from inside the repo. This section is kept in name because **being wrong
here is the instructive part**, and because `TECH_STACK.md` and `PIPELINE.md` cite it.

The case for ingesting rested on two arguments and both were weaker than they read:

- **Speed never landed.** The store was written at index time and read by almost nothing —
  `run_spec`, every agent check and every CLI tool went to the original files anyway. A fast copy
  nobody reads is not a cache. That was true within days and went unnoticed for a month.
- **The sandbox argument does not apply.** `ops/sql.py` materializes its declared inputs into its
  own restricted connection whatever their query is, so the hatch never sees a `read_csv` regardless
  of where the input came from. §6.1 had already found the *specced* sandbox impossible; what it did
  not notice is that the replacement made this argument moot too.

What decided it was neither: portia now sources **only from files already inside the repo**, and
against that a hidden second copy is a worse trade than a re-parse. If reads get slow the answer is
**parquet in the repo** — columnar, typed, already supported, and still one copy you can see.

**Removing it moved no evidence at all.** All 35 golden cases came out byte-identical, because
ingesting was `CREATE TABLE … AS <read_query(path)>` — the reader and its null tokens were always the
same ones, and only the materialization differed. What survived the deletion: `store.memory()` →
`core.io.connect()`, and `store.is_stale` → `catalog.is_stale`, which was never about the copy — it
asks whether the *file* changed since we looked.

---

## 6. The three traps

### 6.1 The escape hatch's sandbox — the specced design was impossible

`ops/sql.py` opens a **fresh in-memory connection** with `enable_external_access=False` holding only
the step's declared inputs: the agent's SQL can reach nothing but its declared tables, and can touch
no file.

The preferred design was `ATTACH` the store read-only and create views for the declared inputs.
**Infeasible, probed 2026-07-28, and it fails twice over.** DuckDB refuses `ATTACH` outright when
`enable_external_access=False` (`Permission Error: Cannot access file`), so the attach and the
filesystem lock cannot both be had. And even with external access left on, `store.anything` stays
reachable by a **schema-qualified** name — `USE sandbox` hides undeclared tables from *unqualified*
names only. That leaves `check_sql` as the sole barrier, and this module's whole posture is that the
string check is bypassable and **the config is what actually holds**. (Two facts found on the way:
`SET enable_external_access=false` works at runtime and cannot be undone, and `DETACH` breaks any
view defined over the detached database.)

So the implementation materializes the declared inputs into the restricted connection — the one
arrangement where the guarantee does not depend on reading the query correctly.

**The cost, stated plainly: SQL steps stay memory-bound.** Every other op is a relation; this one
crosses a process boundary. Making it lazy needs a parse-tree check on table references to replace
what isolation currently provides for free. That is real work and it was never a migration task.

**The crossing also ate types**, which was not anticipated: pandas has no date type, so a `DATE`
input arrived in the sandbox as a `TIMESTAMP` and left as one, and the next step would have joined
`2026-06-12` against `2026-06-12 00:00:00`. Both crossings are now repaired from the schema either
side actually had.

### 6.2 Column-collision suffixes are load-bearing — *predicted correctly, and it held*

`checks/outcome.py` traces an output column back to the input that produced it using pandas'
`_x`/`_y` merge suffixes; that is how `source_did_not_contribute` (a blocking flag) is computed. SQL
has no such convention, so **the convention became explicit rather than inherited** — `ops/join.py`
aliases colliding right-hand columns to `<name>_y` and left-hand to `<name>_x`. Miss it and
`outcome` silently stops attributing columns and a blocking flag stops firing.

### 6.3 Type inference is not identical — *three divergences, not one*

| divergence | decision |
|---|---|
| all-null column — `float64` vs `VARCHAR` | As predicted. `_infer_semantic` normalises to `"empty"`, so the *reported* value agrees. `dtype` is a declared golden exception. |
| **ISO dates — `str` vs `DATE`** | **Not predicted.** DuckDB's sniffer types them; pandas kept them text. **Accepted**: a date is a date, `inferred` reads `datetime`. Consequence to watch: `join._dtype_kind` now returns `datetime`, so a DATE key joined to a VARCHAR key raises `key_dtype_mismatch` where pandas said nothing — correct, but a new flag on old data. |
| **`N/A` and 17 other tokens** | **Not predicted, and the worst of the three.** pandas nulls its default NA set; DuckDB nulls only the empty string, so one column would have read 40 present values on one tier and 39 on the other — a **null rate that depends on which reader ran**. Fixed at the source: `core.io.NA_TOKENS` is pandas' set, passed to DuckDB as `nullstr`, with a test asserting both tiers null precisely those tokens and no others. |

- **`mixed_types` was redefined** to "some values parse as numeric and some do not", on both tiers.
  The old definition was already dead on the path that matters — a CSV round-trip makes an `object`
  column uniformly text, so it only ever fired on an in-memory fixture and never on a file.
- **Two evidence fields were made deterministic on purpose.** `top` broke ties by row order and
  `samples` was "the first three rows"; neither is a fact about the data, and a `LIMIT` with no
  `ORDER BY` returns a different three each run once a scan goes parallel. `top` now breaks ties by
  value and `samples` are distinct and ordered. A change from an artifact to a measurement.

---

## 7. Parity testing — *built first, and it was the best decision here*

29 golden evidence files, written by the pandas engine **before anything moved**, and a test that
compares against them (`tests/golden.py`). Regenerating is guarded, and the reason is the whole of
their authority: they are evidence from an implementation that could not have been wrong in the same
way the new one is. All ten end-to-end `spec` cases came out **byte-identical** after every op,
`run_spec` and the outcome checks were rewritten.

**The lesson that outlived the migration: the golden files did more work than the abstraction did.**
The seam bounded the blast radius; the files are what made the swap checkable rather than hopeful.

## 8. Order of work — *done*

Ten steps across five branches (`duckdb-migration-spec` → `duckdb-engine-parity` →
`duckdb-checks-and-ops` → `ui-add-data` → `parquet-format`). The sequencing advice that held up:
**freeze the evidence first**, and do the sandbox last — which is how §6.1 was found to be impossible
before it was built on.

## 9. What stays pandas — deliberately

The fixtures (the readable definition of the test data, and tiny) · anything under
`head(PREVIEW_ROWS)` · `core/serialize.py` · the UI's rendering. `tests/test_table.py` fails if
anything else pulls a whole relation into memory.

---

## 11. A referentially-consistent subset — still worth building

**The copilot never sees the data**, only profiles. So its behaviour on a 5 GB table and on a
faithful subset is nearly identical, provided the subset preserves what the profile reports:
schemas, key overlap, spelling mismatches, null patterns and fan-out. A **referentially-consistent
subset** — pick a set of entity ids, then slice *every* table to rows reachable from them — gives a
fixture you can re-run in seconds and score against an answer key.

**Naive per-table sampling would not work**: independently sampled tables stop sharing keys, every
join looks empty, and the test measures nothing. The subset has to follow the foreign keys.

Scale is no longer the reason to want it. **Repeatability is** — it is what turns a copilot run from
an anecdote into a measurement.

## 12. Open questions

Answered by the work: ingestion was **eager** (and is now moot, §3); ingest was **typed** (§6.3);
`mixed_types` was **redefined** rather than dropped (§6.3); the sandbox is **isolation, not
attachment** (§6.1).

Still open: **exact vs approximate** — written here as a footnote about `approx_count_distinct`, and
measurement made it the central one. §13.

*(Two further questions here died with the store on 2026-07-31: what `memory_limit` the store's
connection should set, and whether the store should also hold step outputs.)*

## 13. What measurement changed: a profile is still O(n) in memory

*Added 2026-07-28. This supersedes the impression §1 gives.*

Profiling one CSV end to end, peak RSS of the whole process:

| CSV | pandas | DuckDB |
|---|---|---|
| 63 MB | 456 MB | 535 MB |
| 129 MB | 860 MB | 852 MB |
| 261 MB | 1569 MB | 1283 MB |

**Both grow linearly with the file.** DuckDB's slope is shallower (~4.7× the file against ~5.6×) and
it is **8× faster** — a real and sufficient reason to have done this. But the promise that memory
would stop tracking the input does not hold on this workload.

§1 is not wrong; it measured different aggregates. A profile needs three that are inherently
expensive, all exact. One free win was taken — asking for three quartiles as one list-valued
aggregate buffers the column once instead of three times, for identical values.

**The rest is not a drop-in, and this is the finding.** `approx_count_distinct` is HyperLogLog, and
its error is fatal *specifically where the number feeds a flag*:

- `n_distinct` for a 200-value column came back as **203**. `constant` fires on `n_distinct == 1`.
- `n_distinct` for a 6,000,000-value key came back as **5,185,212 — 13.6% low**. `possible_key`
  fires on `n_distinct == n_rows`, and it fills `candidate_keys`, which is what the copilot reads
  when deciding what to join on.

So *label the estimate and it is honest* **is not sufficient here.** A labelled estimate is fine for
a quartile, which is descriptive. An estimated `possible_key` is not a labelled fact, it is a
**wrong one**: a key that is not unique, presented as a candidate. That is the failure mode this
project exists to prevent, arriving through the back door of a performance optimisation.

The answer is probably a split rather than a switch: keep exact anything a flag depends on, and
accept that it is O(cardinality); approximate the purely descriptive numbers and label them in the
evidence dict.

**Until that is settled, profiling remains bounded by cardinality, not by file size.**
