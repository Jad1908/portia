# The DuckDB migration — the scale tier

*Specced 2026-07-27, **complete 2026-07-28**. This is now a **record, not a plan** — the engine is
DuckDB throughout and the code is the better description of what it does. What is kept here is what
the code cannot say about itself: the decisions and why they were taken, and — more usefully — the
places where measurement contradicted the plan.*

**If you read three things, read §6.1, §6.3 and §13.** Between them:

> - The sandbox design in §6.1 was **impossible**, on two independent counts.
> - Type inference diverged **three ways** where §6.3 predicted one, and the unpredicted one would
>   have made a null rate depend on which reader ran.
> - §1's headline — *"memory stops scaling with the file and starts scaling with the answer"* — **is
>   not true of a portia profile**. §13 has the numbers. Read it before quoting §1.
>
> Predicting in advance was still worth it: §6 named the right three places to look. It got two of
> the three answers wrong.

**Read `TECH_STACK.md` → "Data & compute" and `CLAUDE.md` → "Code conventions" first.** This document
assumes both.

---

## 1. Why now, with numbers

The engine loads every table into pandas. Measured on this machine, one 396 MB CSV
(4M rows: ints, floats, low- and high-cardinality text):

| | pandas (today) | DuckDB |
|---|---|---|
| Profile one 396 MB CSV | **16.5 s · 1883 MB peak** (4.8× the file) | **0.3 s · 122 MB** |
| Join two files → 80M rows | materialises 80M rows (many GB) | **0.4 s · 228 MB** |
| Exact `count(distinct)`, 4M rows | inside the 1883 MB | 0.3 s · 346 MB |
| Preview 15 rows | full file load | 0.2 s |
| CSV → columnar, one-off | — | 0.5 s, 396 MB → **70 MB** |
| Column-scoped query after that | — | 0.21 s → **0.01 s** |

Every DuckDB figure was taken under a hard `memory_limit=512MB` and never approached it.

Two facts decide the design:

- **Memory stops scaling with the file and starts scaling with the answer.** Profiles and join
  diagnostics are a handful of numbers, so they cost a handful of megabytes at any input size.
- **The 80M-row join is the one that matters.** That is portia's fan-out case — the thing that
  silently inflated revenue in the hotel fixture (`EVALUATION.md`, Run 5). DuckDB *counted* it in
  0.4 s without building it. pandas cannot measure that join on real data at all.

Secondary measurements that shaped the plan:

- Indexing does **not** leak. Profiling the same file four times: 620 → 728 → 832 → 777 MB. It
  plateaus; the frame is released and the memory reused. RSS never returns to the OS (allocator
  behaviour, not a leak), so the process looks like it permanently holds ~3.5× the largest file.
- Profiling cost is dominated by **text columns**: at 2M rows an int column takes 0.03 s, a
  high-cardinality text column 2.45 s — ~80×. The causes are a Python-level `isinstance` pass over
  every value (`profiling._flags`), plus `nunique`/`value_counts` building hash tables over all
  distinct values.
- `spec.run_spec` is worse than indexing: it loads **every** source at once and keeps **every**
  step's output in `StepResult.frame`, all resident, and the app then holds that list in
  `APP.results` for the rest of the session.

**The immediate driver** is a PHQ dataset: ~20 tables, many multi-GB, to be used together. That is
impossible today and routine after this work.

---

## 2. The guarantee

**Everything keeps working exactly as it does now, faster.** Concretely, and testably:

1. **Every evidence dict is byte-identical**, or differs only where this document says so and says
   why. The copilot reads these; a changed shape is a changed prompt.
2. **Every `PROVENANCE_KEYS` set is unchanged.** `handlers._EXPECTABLE` is derived from them and
   `tests/test_agent_prompts.py` asserts the rendered tool description still matches. A dropped
   provenance field silently invalidates every `expect` block ever written.
3. **`checks.outcome.BLOCKING_FLAGS` keeps its exact meaning.** These are zero-conditions; an
   approximation that turns a zero into a near-zero would defeat the one gate in the system.
4. **No new approximation without saying so.** `approx_count_distinct` is available and sometimes
   necessary — but "the engine measures, it never estimates" is a claim this project makes, so any
   estimate must be labelled in the evidence dict itself, not just in a docstring.
5. **The agent's sandbox does not widen.** `session.py` gives the copilot no filesystem tools and
   `ops/sql.py` runs with `enable_external_access=False`. Both hold afterwards. See §6.1.
6. **The CLI and the app stay two renderers of one engine**, and continue to agree on every number.

**How it turned out.** (1) held, with three declared exceptions, all in `tests/test_golden.py` with
their reasons: backend type names, `inferred` on a date column DuckDB types and pandas did not, and
a pandas `object` column stringifying when it enters a typed store. (2), (3), (5) and (6) held
unchanged. (4) held by *not* approximating — §13 explains why `approx_count_distinct` turned out to
be unavailable rather than merely unlabelled.

Two evidence fields were changed on purpose rather than preserved: `top` broke ties by row order and
`samples` was "the first three rows", and neither is a fact about the data — a `LIMIT` with no
`ORDER BY` returns a different three each run once a scan goes parallel. Both are now deterministic
on both tiers, and the golden files were regenerated with the reason recorded.

---

## 3. The central decision: ingest into a DuckDB store

Two ways to give DuckDB the data. **Take the second.**

**(a) Views over the files.** `CREATE VIEW s AS SELECT * FROM read_csv_auto('data/x.csv')`. Nothing
is copied. But: every query re-parses the CSV (0.21 s vs 0.01 s per column-scoped query, and that
gap widens with width), and — fatally — **the agent's SQL would need file-reading rights**, because
its declared inputs *are* `read_csv` calls. That directly contradicts `ops/sql.py`, whose whole
posture is that `read_csv`/`read_parquet` are forbidden and external access is off.

**(b) An ingested store — `.portia/store.duckdb`.** At index time, each source is read once and
written into a DuckDB table. Then:

- Queries hit columnar storage: measured 20× faster on column-scoped reads, 5.6× smaller on disk.
- **The sandbox gets simpler, not weaker.** The data lives *inside the database*, so the agent's
  connection needs no external access at all. `enable_external_access=False` stays exactly as it is,
  and `read_csv` stays on the `FORBIDDEN` list, because nothing legitimate needs it any more.
- Ingestion is a real step, but a cheap one: 0.5 s for 396 MB.
- The `catalog` gains a fact it should arguably have had anyway: *when* a source was ingested, so a
  changed file on disk is detectable rather than silently stale.

**The cost is disk duplication**, and it must be stated to the user, not hidden. For the PHQ case
(~20 tables, multi-GB) the store will be materially smaller than the CSVs, but it is still a second
copy. `.portia/store.duckdb` belongs in `.gitignore`, like the rest of `.portia/`.

**Re-ingestion policy.** A source whose file's mtime or size has changed since ingestion is stale.
Re-index refreshes facts and preserves judgment — that rule (`catalog`, the update rule) extends
unchanged: re-ingesting refreshes the table, prose and roles survive.

> **Decided 2026-07-28: eager.** Ingestion happens in `catalog.index_source`, which then profiles
> the ingested table rather than the file. Indexing is already the moment the user expects work to
> happen, and `index-progress` in `DESIGN.md` already shows profiling as a distinct step, which
> ingestion joins. Measured cost on a 261 MB CSV: 0.6 s to ingest, and the store is 2.7× smaller
> than the source.

---

## 4. The abstraction: `Table`

The currency is no longer `pd.DataFrame` but a small wrapper — **not** a raw `DuckDBPyRelation`,
because the checks should not each learn DuckDB's API, and because a wrapper is where the
DuckDB → Snowflake seam lives (`TECH_STACK.md`).

*The shape below is what was specced; `core/table.py` is what was built, and it differs in two
ways worth knowing.* It holds **the query text, not a bound relation** — a relation belongs to the
connection that made it, and rebinding to a thread's own `con.cursor()` has to be free. And there
are **two exits, not one**: `head()` for rendering and `rows()` for evidence, because a DuckDB
`DATE` routed through pandas reaches the copilot as `2026-06-12 00:00:00`.

```python
# portia/core/table.py   (new)
@dataclass(frozen=True)
class Table:
    """A named, lazily-evaluated relation. Nothing is materialised until asked."""
    name: str
    relation: Any                 # duckdb.DuckDBPyRelation
    con: Any                      # the connection it belongs to

    @property
    def columns(self) -> list[str]: ...
    @property
    def dtypes(self) -> dict[str, str]: ...
    def count(self) -> int: ...                        # SELECT count(*)
    def head(self, n: int) -> pd.DataFrame: ...        # LIMIT n  — the only pandas exit
    def scalar(self, expr: str): ...                   # one aggregate, one value
    def sql(self, select: str) -> Table: ...           # derive a new relation
    def to_csv(self, path: Path) -> None: ...          # COPY … TO
    @classmethod
    def from_frame(cls, frame, name, con) -> Table: ...  # tests and fixtures
```

**The rule that keeps the guarantee: `head()` and `scalar()` are the only ways out.** If a check or
op calls `.df()` on a whole relation, the migration has failed at that line. A reviewer can grep for
it.

`core/io.load_frame` stays (fixtures, tests, small reads) and gains `core/io.load_table`. The
docstring in `core/io.py` already promises this seam — *"the return type is a DataFrame-like, and a
scale tier can hand back a lazy frame without any check changing"* — and this is that change, with
the honest correction that **the interfaces don't change but the implementations all do.**

**Connections and threads.** One connection per open project, held by the session. DuckDB
connections are not thread-safe; the UI runs blocking work through `asyncio.to_thread`, so every
threaded call must use `con.cursor()` to get its own handle. Getting this wrong produces
intermittent, hard-to-reproduce corruption — it is worth a test that hammers concurrent queries.

---

## 5. Module by module — *done, and the code is the record*

Every module named here was migrated. Listing what each one became is now a worse description of
the engine than the engine is: read `core/table.py`, `checks/`, `ops/` and `spec.py`. What is kept
below is only what the code cannot say about itself — the traps, and what measurement contradicted.

## 6. The three traps

*Each of these would have produced plausible but wrong behaviour if handled late. **Two of the
three were wrong as written** — the sandbox design was impossible, and type inference diverged three
ways rather than one. The suffix trap, §6.2, was correct and held. Read this section as the record
of what predicting-in-advance was actually worth: it named the right three places to look, and got
two of the three answers wrong.*

### 6.1 The escape hatch's sandbox

`ops/sql.py` today opens a **fresh in-memory connection** with `enable_external_access=False` and
registers only the step's declared inputs. That is two independent guarantees: the agent's SQL can
reach nothing but its declared tables, and it can touch no file.

With a store, the naive move — run the agent's SQL on the project connection — **breaks both**: the
agent could name any table in the store, not just its declared inputs.

**The design that preserves both.** Keep opening a separate, restricted connection per SQL step.
Give it access to exactly the declared inputs and nothing else. Options, in preference order:

1. ~~`ATTACH '…/store.duckdb' AS store (READ_ONLY)` on a connection with
   `enable_external_access=False`, then create **views for the declared inputs only**.~~
   **Infeasible — probed 2026-07-28, and it fails twice over.** DuckDB refuses `ATTACH` outright
   when `enable_external_access=False` (`Permission Error: Cannot access file`), so the attach and
   the filesystem lock cannot both be had. And even with external access left on, `store.anything`
   stays reachable by a schema-qualified name — `USE sandbox` hides undeclared tables from
   *unqualified* names only. That leaves `check_sql` as the sole barrier, and this module's whole
   posture is that the string check is bypassable and **the config is what actually holds**.
   (Two useful facts found on the way: `SET enable_external_access=false` *does* work at runtime and
   cannot be undone, and `DETACH` breaks any view defined over the detached database.)
2. **This is the implementation.** The declared inputs are materialised into the restricted
   connection, which then holds exactly them and nothing else — the one arrangement where the
   guarantee does not depend on reading the query correctly.

**The cost, stated plainly: SQL steps stay memory-bound.** Every other op is a relation; this one
crosses a process boundary, so its inputs are read into memory. Making it lazy needs step outputs to
live in the store (§12) *and* a parse-tree check on table references to replace what isolation
currently provides for free. Both are real work and neither is a migration task.

**The crossing also ate types**, which was not anticipated: pandas has no date type, so a `DATE`
input arrived in the sandbox as a `TIMESTAMP` and left as one, and the next step would have joined
`2026-06-12` against `2026-06-12 00:00:00`. Both crossings are now repaired from the schema either
side actually had — the boundary has to be crossed, but it should not also be a place where types
quietly change.

**This needs a test that tries to escape**: a step whose SQL names an undeclared table must fail,
and a step whose SQL calls `read_csv` must fail, exactly as they do today. `tests/test_ops_sql.py`
already covers the string half; the connection half needs equivalent coverage.

### 6.2 Column-collision suffixes are load-bearing

`checks/outcome.py` traces an output column back to the input that produced it using **pandas'
`_x`/`_y` merge suffixes** — `MERGE_SUFFIXES` is already a named constant with a docstring
explaining that insertion order is load-bearing. That is how `source_did_not_contribute` (a blocking
flag) is computed.

SQL has no such convention: a join with colliding names either errors or requires explicit aliasing.
**So the convention must become explicit rather than inherited** — `ops/join.py` aliases colliding
right-hand columns to `<name>_y` and left-hand to `<name>_x`, reproducing exactly what pandas did.

Do it deliberately and comment it as a compatibility choice, or `outcome` silently stops attributing
columns and a blocking flag stops firing. **Test:** the existing `tests/test_checks_outcome.py`
cases must pass unchanged.

### 6.3 Type inference is not identical

Probed on `data/mock/messy_customers.csv`:

| column | pandas | DuckDB | portia flags |
|---|---|---|---|
| `customer_id` | `int64` | `BIGINT` | agrees |
| `signup_amount` | `str` | `VARCHAR` | agrees — `numeric_stored_as_text` survives |
| `legacy_col` (all null) | `float64` | `VARCHAR` | **differs** |

The good news: the whitespace-and-numbers column stays text in both, so the flag portia most cares
about is preserved. The divergence is an all-null column, which `_infer_semantic` already normalises
to `"empty"` — so the *reported* value agrees. **Lock that down with a parity test rather than
trusting it.**

**Both decided 2026-07-28, and the probe above was incomplete — there were three divergences, not
one.** Measured across all six fixtures, the full list and what was done about each:

| divergence | decision |
|---|---|
| `legacy_col` — `float64` vs `VARCHAR` | As predicted. `_infer_semantic` normalises an all-null column to `"empty"`, so the *reported* value agrees. `dtype` is a declared golden exception. |
| **`stay_date` / `event_date` — `str` vs `DATE`** | **Not predicted.** DuckDB's sniffer types ISO dates; pandas kept them text. **Accepted**: a date is a date, `inferred` reads `datetime`, and the vocabulary is unchanged. Consequence to watch: `join._dtype_kind` now returns `datetime` for these, so a DATE key joined to a VARCHAR key raises `key_dtype_mismatch` where pandas said nothing — correct, since DuckDB does need the cast, but it is a new flag on old data. |
| **`N/A` and 17 other tokens** | **Not predicted, and the worst of the three.** pandas nulls its default NA set; DuckDB nulls only the empty string. `messy_customers.signup_amount` would have read 40 present values on one tier and 39 on the other — a **null rate that depends on which reader ran**. Fixed at the source: `core.io.NA_TOKENS` is pandas' set, passed to DuckDB as `nullstr`, with a test asserting both tiers null precisely those tokens and no others. |

- **`mixed_types`: redefined, per the recommendation.** It now means "some values parse as numeric
  and some do not", on **both** tiers. Worth knowing: the old definition was already dead on the
  path that matters — a CSV round-trip makes an `object` column uniformly text, so `mixed_types`
  only ever fired on an in-memory fixture and never on a file. The redefinition makes it fire where
  the problem actually lives, and it fires on `messy_customers.signup_amount` too, alongside
  `numeric_stored_as_text`. The overlap is intended: one says "this is really a number column",
  the other says "not all of it is".
- **Ingest typed, confirmed.** Every signal-carrying column stays `VARCHAR` under the sniffer —
  `signup_amount` (numbers with whitespace, plus `pending`) and `mixed_ref` (half numeric) both do.
  `tests/test_store.py` pins that rather than trusting it. Still verify on PHQ data.
- **Two evidence fields were made deterministic, deliberately, on both tiers.** `top` broke ties by
  row order and `samples` was "the first three rows". Neither is a fact about the data — half the
  fixture columns have a tied mode, and a `LIMIT` with no `ORDER BY` promises nothing, so DuckDB
  returns a different three each run once a scan goes parallel. `top` now breaks ties by value, and
  `samples` are **distinct and ordered** (ordering alone made them worse: `country` became
  `['DE', 'DE', 'DE']`). The golden files were regenerated for this; it is a change to what the
  copilot reads, and it is a change from an artifact to a measurement.

---

## 7. Parity testing — *built first, and it was the best decision here*

29 golden evidence files, written by the pandas engine before anything moved, and a test that
compares against them. The mechanism and its rules live in `tests/golden.py`; the one thing worth
repeating outside it is **why regenerating them is guarded**: they are evidence from an
implementation that could not have been wrong in the same way the new one is, and that is the whole
of their authority. `python -m tests.golden --regenerate` exists and says so before it runs.

What this bought, concretely: all ten end-to-end `spec` cases came out **byte-identical** after
every op, `run_spec`, and the outcome checks were rewritten. The abstraction bounded the blast
radius; the files are what made the swap *checkable*.

## 8. Order of work — *done*

Ten steps, landed across five branches (`duckdb-migration-spec` → `duckdb-engine-parity` →
`duckdb-checks-and-ops` → `ui-add-data` → `parquet-format`). Each branch tip passes its own suite.
The sequencing advice that held up: **freeze the evidence first**, and do the sandbox last so the
restricted-connection design is tested against a working store — which is how §6.1 was found to be
impossible before it was built on.

## 9. What stays pandas — deliberately

- **Fixtures.** They are the readable definition of the test data and they are tiny.
- **Anything under `head(PREVIEW_ROWS)`** — previews, samples, example rows. These are already
  capped and pandas is the convenient shape for rendering them.
- **`core/serialize.py`.** Evidence stays plain JSON-able Python; nothing about that changes.
- **The UI.** It renders small frames and always will — `DESIGN.md`'s `table-preview` is capped and
  states `showing N of M`.

---

## 10. Not in scope, and why

- **Snowflake.** The same `Table` abstraction is the seam it will use (`TECH_STACK.md`), and this
  migration should not close that door — but a second backend before the first one is proven is
  speculative work.
- **Distributed / out-of-core beyond DuckDB's own spilling.** DuckDB spills to disk; that is the
  answer until something demonstrates it is not.
- **Changing any evidence the copilot reads.** Explicitly a non-goal (§2). If the migration makes a
  *better* fact available, that is a separate change with its own prompt review.

---

## 11. A referentially-consistent subset — still worth building

The migration is worth doing regardless. But the test that motivated it — *can the copilot work out
how ~20 PHQ tables relate?* — may not need it, and it is worth knowing that before spending the time.

**The copilot never sees the data.** It sees profiles. So its behaviour on a 5 GB table and on a
faithful subset of that table is nearly identical, provided the subset preserves what the profile
reports: schemas, key overlap, spelling mismatches, null patterns, and fan-out.

That means a **referentially-consistent subset** — pick a set of entity ids, then slice *every* table
to rows reachable from them — tests the judgment question today, at 1/100th the size, and gives us a
fixture we can keep and score against an answer key like `hotels.answers.yaml`.

**Naive per-table sampling would not work**: independently sampled tables stop sharing keys, every
join looks empty, and the test measures nothing. The subset has to follow the foreign keys.

Two separable questions, and the migration only answered one of them:

1. *Can it reason about these schemas?* — **still open.** A subset answers it.
2. *Does portia hold up at real scale?* — **answered: yes**, within the ceiling §13 describes.

Scale is no longer the reason to want the subset. **Repeatability is** — a fixture you can re-run
in seconds and score against an answer key is what turns a copilot run from an anecdote into a
measurement, and `EVALUATION.md` has seven anecdotes.

---

## 12. Open questions

Answered by the work: ingestion is **eager** (§3); ingest is **typed** (§6.3); `mixed_types` was
**redefined** rather than dropped (§6.3); the sandbox is **isolation, not attachment** (§6.1).

Still open:

- **Exact vs approximate.** Written here as a footnote about `approx_count_distinct`; measurement
  made it the central one. §13.
- **`store.connect` sets no `memory_limit`**, so DuckDB helps itself to 75% of RAM. A 2 GB limit did
  the same work in the same wall time at 5.0 GB peak instead of 6.8 — but 2 GB was ample for that
  workload and might not be for a large sort. Decide against PHQ data.
- **Whether the store should also hold step outputs**, so a run's intermediates survive the session
  and `Runs` can show a real table rather than a saved report. Now also the prerequisite for making
  SQL steps lazy (§6.1), which is the one op still bounded by memory.

---

## 13. What measurement changed: a profile is still O(n) in memory

*Added 2026-07-28, after landing steps 1–3. This supersedes the impression §1 and §2 give.*

Profiling one CSV, end to end, peak RSS of the whole process (5 columns: two numeric, one
low-cardinality text, one all-distinct text, one date):

| CSV | pandas | DuckDB |
|---|---|---|
| 63 MB | 456 MB | 535 MB |
| 129 MB | 860 MB | 852 MB |
| 261 MB | 1569 MB | 1283 MB |

**Both grow linearly with the file.** DuckDB's slope is shallower — ~4.7× the file against pandas'
~5.6× — and it is **8× faster** (7.6 s → 0.9 s at 129 MB, which is a real and sufficient reason to
have done this). But the promise in §2 was that memory would stop tracking the input, and on this
workload it does not.

§1 is not wrong; it measured different aggregates. A profile needs three that are inherently
expensive, all exact, measured on 6M rows:

| aggregate | exact | approximate | accurate enough? |
|---|---|---|---|
| `count(DISTINCT)` × 5 cols | 658 MB | **161 MB** | **No — see below** |
| `quantile_cont([.25,.5,.75])` × 2 cols | 502 MB | **124 MB** | Yes — within 0.1% |
| `GROUP BY col` for `top`/`top_freq` | O(cardinality) | — | n/a |

One free win was taken already: asking for three quartiles as three expressions buffers the column
three times (650 MB), and as one list-valued aggregate buffers it once (326 MB), for identical
values. That is in the code.

**The rest is not a drop-in, and this is the finding.** `approx_count_distinct` is HyperLogLog, and
its error is fatal *specifically where the number feeds a flag*:

- `n_distinct` for a 200-value column came back as **203**. `constant` fires on `n_distinct == 1`.
- `n_distinct` for a 6,000,000-value key came back as **5,185,212 — 13.6% low**. `possible_key`
  fires on `n_distinct == n_rows`, and it is what fills `candidate_keys`, which is what the copilot
  reads when deciding what to join on.

So §2.4's rule — *label the estimate and it is honest* — **is not sufficient here.** A labelled
estimate is fine for a quartile, which is descriptive. An estimated `possible_key` is not a
labelled fact, it is a **wrong one**: a key that is not unique, presented as a candidate. That is
the failure mode this project exists to prevent, arriving through the back door of a performance
optimisation.

The shape of the answer is probably a split rather than a switch:

- **Keep exact** anything a flag depends on — `count(DISTINCT)` — and accept that it is O(cardinality).
  A cheaper exact test for the specific question `possible_key` asks (`count(DISTINCT c) = count(*)`)
  may exist, and would be worth more than approximating the count itself.
- **Approximate** the purely descriptive numbers — the quartiles — and label them in the evidence
  dict, which is a prompt change and gets its own review (§10).
- **Decide what `samples` and `top` are worth**: distinct-and-ordered samples cost a full
  `DISTINCT` (262 MB at 3M rows) where an unordered `LIMIT 3` costs nothing — but an unordered
  `LIMIT` is not reproducible, which is why they are the way they are (§6.3).

Until that is settled, **profiling remains bounded by cardinality, not by the store.** The PHQ test
should be run with that expectation rather than the one §1 sets.
