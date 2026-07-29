# The DuckDB migration — the scale tier

*Specced 2026-07-27. **Steps 1–3 landed 2026-07-28** on `duckdb-engine-parity` — golden files, the
`Table` abstraction and the store, and `checks/profiling.py` on SQL. Direction plus the parts that
genuinely need deciding up front — per `CLAUDE.md`, a plan gives vision, stack and watch-outs; the
specifics emerge from real work. The watch-outs here are unusually concrete because three of them
will silently corrupt behaviour if they are discovered late.*

> **What real work already changed in this document.** §1's headline — *"memory stops scaling with
> the file and starts scaling with the answer"* — **is not true of a portia profile**, measured. It
> holds for the aggregates it was measured on and fails for the three the profile actually needs.
> §13 has the numbers. Read it before quoting §1.

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

The mechanism that enforces all of this is in §7. Build it first.

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

Today the currency is `pd.DataFrame`. It becomes a small wrapper — **not** a raw
`DuckDBPyRelation`, because the checks should not each learn DuckDB's API, and because a wrapper is
where the pandas → DuckDB → Snowflake seam actually lives (`TECH_STACK.md`).

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

## 5. Module by module

Everything that touches pandas today, and what it becomes. Nothing here is optional; a half-migrated
path re-introduces the memory ceiling at whichever step is left behind.

### `core/`

| File | Change |
|---|---|
| `io.py` | Add `load_table(path, con)`; keep `load_frame` for small reads. `find_data_files` unchanged. Register new formats (`.parquet`) here, once, as today. |
| `table.py` | **New.** §4. |
| `present.py` | `frame_to_markdown` takes a `Table` and calls `head(PREVIEW_ROWS)`. `format_rate`, `inline`, `count`, `as_yaml` unchanged. |
| `serialize.py` | Unchanged in shape. `to_jsonable` must also coerce DuckDB's returned types (`decimal.Decimal`, `datetime.date`, `uuid.UUID`) — today it only anticipates numpy. **Add tests for those.** |

### `checks/` — diagnosis

**`profiling.py`.** The heaviest rewrite and the biggest win. One `SELECT` per table computes
`n_rows` plus, per column, `count(*)`, `count(col)`, `count(DISTINCT col)`, `min`, `max`; numeric
columns add `avg`, `stddev_samp`, `quantile_cont([.25,.5,.75])`; non-numeric add `mode()`/top-freq
via a small `GROUP BY … ORDER BY count DESC LIMIT 1`. `samples` becomes `SELECT col FROM t WHERE col
IS NOT NULL LIMIT 3`.

The flags, one by one, because two of them are traps:

| Flag | SQL |
|---|---|
| `all_null` | `count(col) = 0` |
| `constant` | `count(DISTINCT col) = 1` |
| `possible_key` | `count(col) = count(*) AND count(DISTINCT col) = count(*)` |
| `high_null` | `1 - count(col)/count(*) >= 0.5` |
| `high_cardinality` | `count(DISTINCT col)/count(col) >= 0.9`, text columns only |
| `leading_trailing_whitespace` | `count(*) FILTER (WHERE col <> trim(col)) > 0` |
| `numeric_stored_as_text` | `avg(CASE WHEN try_cast(col AS DOUBLE) IS NOT NULL THEN 1 ELSE 0 END) >= 0.9`, text only — **trap, see §6.3** |
| `mixed_types` | **No direct equivalent — see §6.3.** |

`null_rates()` becomes one `SELECT` of per-column null fractions; it stays split out for the same
reason as today (a post-condition should not pay for quantiles).

**`join.py`.** The module docstring already claims the right thing — *"computed from the key columns
alone… we can say a join explodes 50M rows to 2B without ever building it"* — and the implementation
finally delivers it. The algorithm translates directly:

```sql
WITH l AS (SELECT k, count(*) n FROM left  WHERE k IS NOT NULL GROUP BY k),
     r AS (SELECT k, count(*) n FROM right WHERE k IS NOT NULL GROUP BY k)
SELECT sum(l.n * r.n)  AS inner_rows,
       sum(l.n)        AS matched_left,
       max(r.n)        AS max_left_to_right, ...
FROM l JOIN r USING (k)
```
with `FULL OUTER JOIN` variants for `left_only` / `right_only` counts and `LIMIT 5` for
`sample_left_only` / `sample_right_only`. `join_findings`' example rows become
`SELECT … WHERE key NOT IN (SELECT …) LIMIT 3` — anti-joins, which DuckDB does well.
`_dtype_kind` maps DuckDB types to the same four kinds (`numeric`/`string`/`datetime`/`boolean`);
**the four kinds must not change**, because `key_dtype_match` drives a flag.

**`outcome.py`.** `n_rows`, `n_cols`, `empty`, `null_rates`, `all_null_columns` are aggregates.
`_grain_report` is `GROUP BY grain HAVING count(*) > 1` plus `ORDER BY count(*) DESC LIMIT 5` for
examples. `contribution`/`_attribute` is the delicate one — see §6.2. `describe_contribution` and
`describe_grain` are pure string formatting and do not change.

### `ops/` — execution

| File | Change |
|---|---|
| `base.py` | `OpResult.frame` → `OpResult.table`. Provenance dict untouched. |
| `join.py` | `pd.merge` → a SQL join producing a relation. `result_rows` needs `count(*)` on the result — cheap (0.4 s / 80M rows). `matches_prediction` still compares it to the prediction. **Collision suffixes become explicit — §6.2.** |
| `normalize.py` | `strip`→`trim`, `lower`→`lower`, `to_numeric`→`try_cast(col AS DOUBLE)`, `to_string`→`CAST(col AS VARCHAR)`. `n_changed` / `n_converted` / `n_failed` become `count(*) FILTER (…)`. `sample_failed` is `WHERE col IS NOT NULL AND try_cast(col) IS NULL LIMIT 5`. `fill` becomes `coalesce`. |
| `sql.py` | `check_sql` **unchanged** — same string guard, same `FORBIDDEN` list. `apply_sql` stops registering pandas frames and instead runs against the store. **§6.1 is about this function.** |

### `spec.py`

`run_spec` stops loading sources into a dict of frames and builds a dict of `Table`s instead.
`frames[step["id"]] = result.frame` becomes a registered relation, so chaining still works and
nothing is materialised. `StepResult.frame` → `.table`. `write_outputs` uses `COPY … TO`.
`render_markdown`'s preview uses `head()`.

### `catalog.py`

`index_source` gains ingestion (§3) and records `ingested_at` + the source file's `mtime`/`size`.
The update rule is unchanged: facts refresh, prose and roles are preserved.

### `agent/`

`handlers.py`: `_frame`/`_step_frame` become `_table`/`_step_table`. `profile_source` currently
re-reads the file on every call — against the store it becomes a set of aggregates, which is the
single biggest cost reduction in the copilot loop. **No tool signature changes and no prompt
changes**, which is the point.

### `ui/`

`engine.read_frame` → `read_table`; `components.table_preview` takes a `Table` and calls `head()`;
`workflow._source_frame` stops loading whole files to show 15 rows (a straight bug at multi-GB).
`APP.results` holding every step's output stops being a memory problem, because a `Table` is a
handle rather than data.

### `fixtures/` and `tests/`

Fixtures keep building pandas frames — they are tiny and they are the readable definition of the
test data. `Table.from_frame` bridges them. Most test bodies change one line.

---

## 6. The three traps

Each of these will produce *plausible but wrong* behaviour if handled late.

### 6.1 The escape hatch's sandbox

`ops/sql.py` today opens a **fresh in-memory connection** with `enable_external_access=False` and
registers only the step's declared inputs. That is two independent guarantees: the agent's SQL can
reach nothing but its declared tables, and it can touch no file.

With a store, the naive move — run the agent's SQL on the project connection — **breaks both**: the
agent could name any table in the store, not just its declared inputs.

**The design that preserves both.** Keep opening a separate, restricted connection per SQL step.
Give it access to exactly the declared inputs and nothing else. Options, in preference order:

1. `ATTACH '…/store.duckdb' AS store (READ_ONLY)` on a connection with `enable_external_access=False`,
   then create **views for the declared inputs only** and query those. Nothing else is nameable
   without a `store.` prefix, and that prefix can be refused by the existing string check.
2. If (1) proves leaky, materialise the declared inputs into the restricted connection with
   `CREATE TEMP TABLE … AS SELECT …` — correct and airtight, but it materialises, so it only holds
   for inputs small enough to fit. Acceptable as a fallback, not as the default.

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

## 7. Parity testing — build this first

This is the mechanism that makes "everything keeps working" checkable rather than hopeful.

1. **Freeze the current behaviour.** Before changing any implementation, add a test that runs every
   check and op over every fixture (`messy_customers`, `sales_*`, `hotels`/`otb`/`city_events`) with
   today's pandas code and writes the evidence dicts to `tests/fixtures/golden/*.json`. Commit them.
2. **Assert the new implementation reproduces them**, key by key. Differences are allowed only where
   this document says so, and each exception is an explicit, commented entry in the test.
3. **Keep both implementations alive during the migration**, selected by a parameter, so the golden
   tests run against both in CI until the pandas path is deleted.
4. **Add a scale test** that is skipped by default (marker: `slow`): generate ~1 GB, assert peak RSS
   stays under a cap. Without it, the ceiling creeps back in the first time someone adds a `.df()`.
5. **Add a concurrency test** for the cursor-per-thread rule (§4).

The golden files are the deliverable that makes this migration reversible. Write them on day one,
before touching an implementation.

---

## 8. Order of work

Each step ends green, and nothing after step 2 is a big-bang.

1. ~~**Golden files**~~ — **done 2026-07-28.** 29 cases, `tests/golden.py` + `tests/test_golden.py`.
   Cases are data and the backend is swappable, so each step adds a `kinds` entry to
   `DuckDBBackend` rather than a new test.
2. ~~**`core/table.py` + `load_table` + the store**~~ — **done 2026-07-28.**
3. ~~**`checks/profiling.py`**~~ — **done 2026-07-28.** 8× faster; the memory win is smaller than
   this document assumed — see §13. The rules (`_semantic`, `_flags`) are shared by both
   implementations rather than written twice, which is what keeps parity structural.
4. **`checks/join.py`** — unlocks `join_findings` on real data, which is what the copilot calls
   before deciding anything.
5. **`checks/outcome.py`** — §6.2 lives here.
6. **`ops/join.py`, `ops/normalize.py`** — with the explicit suffix convention.
7. **`ops/sql.py`** — §6.1, the sandbox. Do it after the rest so the restricted-connection design is
   tested against a working store.
8. **`spec.run_spec`** — relations end to end.
9. **`ui/` + `cli/`** — previews, `read_table`, `write_outputs`.
10. **Delete the pandas implementations** and the dual-path switch. Keep the golden files.

Steps 1–3 are worth a PR on their own: they are separable, they are most of the memory win for
indexing, and they let the PHQ test start.

---

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

## 11. Before starting: a cheaper answer to the PHQ question

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

Two separable questions, then:

1. *Can it reason about these schemas?* — a subset answers this now.
2. *Does portia hold up at real scale?* — this migration answers that.

---

## 12. Open questions

- ~~Eager vs lazy ingestion~~ — **eager** (§3).
- ~~Typed vs all-VARCHAR ingest~~ — **typed**, confirmed on the fixtures; still verify on PHQ (§6.3).
- ~~`mixed_types`: drop or redefine~~ — **redefined** (§6.3).
- **Exact vs approximate — now the migration's central open question, and bigger than it looked.**
  It was written as a footnote about `approx_count_distinct`. §13 is what measurement made of it.
- Whether the store should also hold **step outputs**, so a run's intermediates survive the session
  and `Runs` can show a real table rather than a saved report. Attractive, and out of scope here.

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
