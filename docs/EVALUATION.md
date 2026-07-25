# Evaluation — how we know whether the copilot is any good

*Companion to `PLAN.md`. This is where measurement lives: what we test against, what the
current score is, and what is known-broken. Update it whenever a fixture is run.*

**Status as of 2026-07-25: the copilot fails the hotel fixture.** See "Current state" below
before assuming any part of the loop works end to end.

---

## The rule that shapes everything here

**Ground truth is cheap to *check* and expensive to *write*.**

Anything mechanically checkable — row counts, whether a key exists, whether a file parses — is
free, and correspondingly tells you little, because the engine could compute it anyway. Anything
that depends on what the user *wanted* has to be authored by hand, once per test case. There is no
version of this where the labels come for free; if there were, the labels would be the product.

So: the answer keys are the asset. The harness around them is cheap.

### What is *not* a correctness signal

Four things look like free verification and mostly aren't. Don't let them stand in for a score:

| Signal | What it actually is |
|---|---|
| `run_spec` drift vs `expect` | A **faithfulness** check, not a correctness one. The values in `expect` were already in the tool output the agent had just read, so a clean run means it copied numbers correctly. It catches hallucinated figures — which is worth having; it caught a real bug — but an inner join and a left join *both* verify clean if you predict their row counts right. It is silent on the only decision that mattered. |
| Which disclosure rungs were pulled | Descriptive. There is no objectively correct sequence; it's a cost proxy. |
| Tokens and turns | Pure cost. Not quality in any sense. |
| How often it asked | Descriptive. "Asked three times" is neither good nor bad without knowing whether it should have. |

Drift *does* earn its keep on a different axis: re-running a spec months later against changed
source data, to catch a vendor's file shifting underneath you. That is **data monitoring**, not
agent evaluation. Don't conflate the two.

---

## The fixtures

### `sales_*` — engine correctness
`portia/fixtures/sales.py`. Two tables, hand-built so the join report's numbers are exactly
assertable. Tests that *the engine* reports a join correctly. Fully covered by unit tests.

### `hotels` / `otb` / `city_events` — agent derivation
`portia/fixtures/hotels.py`, answer key at `tests/fixtures/hotels.answers.yaml`.

Tests something harder: whether the **agent** can work out how sources relate when nobody tells
it. `otb` knows only `hotel_id` and has no city; `hotels` carries both `hotel_id` and `city`, so
it is the only bridge; `city_events` is keyed on `city_name` + `event_date`. Two hops, and the
second key never appears in the fact table.

> **The brief in the answer key is the only context the copilot gets, and it must stay
> domain-level.** If a brief ever needs to name a column to make the task solvable, it is leaking
> the answer and the test measures reading comprehension instead of inference. This happened once
> already — see "A retracted result" below.

Planted traps, each independently scoreable: a fan-out visible in raw data (Amsterdam, two events
one day — double-counts revenue by ~4%, plausible enough to ship), a city-spelling mismatch
(`" paris"` vs `"Paris"`, plus `city` vs `city_name`), **a second fan-out that only appears after
the spelling is fixed** (Paris then also has two events that day — only a re-check after acting
catches it), an orphan booking, a hotel with no bookings, a city with no hotels, and two revenue
outliers that invite a judgement call without forcing one.

---

## Current state — the hotel fixture

Two runs, both on `claude-haiku-4-5`. **Both fail.**

| | Run 1 | Run 2 (after bug fixes) |
|---|---|---|
| Hop 1 `otb → hotels` on `hotel_id` | ✅ | ✅ |
| Hop 2 `city ↔ city_name` + date | ⚠️ reasoned in prose, **gave up** | ✅ expressed and chained |
| `city_spelling` — fatal | ✅ found | ⚠️ found, **fixed one side only** |
| `event_fan_out` (Amsterdam) — fatal | ❌ | ❌ never reached |
| `fan_out_created_by_cleaning` — fatal | ❌ | ❌ never reached |
| Asked about revenue outliers | ❌ | ❌ |
| Asked about the orphan booking | ✅ | ❌ silently dropped it |
| Asked about anything measurable | ✅ none | ✅ none |
| Spec runs | ❌ crash | ✅ exits 0 |
| **Table is correct** | ❌ | ❌ **`event_name` 100% null** |

**Run 1** failed loudly: it concluded the spec format couldn't express a two-hop join, wrote a
degraded single-hop version, advised the user to go use SQL/dbt, and produced a spec that crashed.
Both causes were engine defects, now fixed (`record_step`'s description never mentioned that steps
chain; `_validate_step` checked that `transforms` existed but never looked inside).

**Run 2 fails silently, which is worse.** It normalized `city_events.city_name` to lowercase but
not `hotels.city`, so nothing matched at all. The join check emitted `no_matches` and
`low_overlap` — the loudest flags it has. The agent explained the resulting drift away as
"limited temporal overlap", first tried to **rewrite `expect` to match reality** (blocked only
because duplicate step ids are rejected), then declared *"Your training table is ready."* It
delivered a plausible table missing an entire data source.

**What passed is worth noting too:** the brief named no columns and no keys, and it still derived
`hotel_id`, worked out that `otb` has no city and hotels must bridge, and matched `city` to
`city_name` by meaning.

### What these failures are evidence for

- Self-assessment is worthless here. Asked "was that good?", the agent said yes at every step.
  Verification must compute post-conditions **in code** and let the agent only interpret them.
- `no_matches` should be a **hard stop**, not an advisory flag.
- A recorded step must be **immutable** — the attempt to edit `expect` after seeing the result
  should be explicitly refused, not accidentally blocked by duplicate-id checking.
- Model capability is a live variable: these runs are on a deliberately small model
  (`PLAN.md` → "Budget & model discipline"). Re-run on a larger one before concluding a failure
  is architectural.

---

## The biggest untested thing: whether the questions are any good

**Nobody has ever driven the copilot interactively.** Every run so far piped `yes y`, so the
agent received `"y"` as its answer to every question it asked. The questions were generated and
rendered correctly — the mechanical path works — but whether they are *good* questions, whether
it asks at the right moments, and how it behaves when a human **disagrees** with it is entirely
unmeasured.

That is the product thesis. `PLAN.md` says it outright: *"the questions-and-insights UX **is** the
product"*. Everything measured so far is the engine around it.

So the highest-value hour available is not building anything: it is sitting down with
`python -m portia.cli.chat` on the hotel fixture and actually arguing with it. Push back on a
recommendation. Give a vague answer. Tell it something that contradicts the data. None of that is
gradeable by the answer key, and none of it needs to be — the failure modes will be obvious.

## A retracted result

An earlier demo claimed the context layer was proven: the same merge that recommended a *left*
join context-blind recommended *inner* once the project brief was present, quoting the brief back.

**That result was invalid.** The brief I wrote ended with *"an order without a valid customer
cannot be billed"* — which is the answer to the very question being asked. The copilot was reading
back a planted conclusion, not reasoning. The plumbing it was meant to demonstrate (L1 pushed into
the system prompt rather than fetched) is still correct and still shipped; it is simply **not
validated by that demo**. The hotel fixture exists because of this mistake.

---

## Running it

```bash
# isolated project dir, brief taken verbatim from the answer key
mkdir /tmp/hotel-test && cp data/mock/{hotels,otb,city_events}.csv /tmp/hotel-test/
cd /tmp/hotel-test
python -m portia.cli.index --init "<brief from hotels.answers.yaml>" .
python -m portia.cli.chat ask "Build me the one table I can train on. Record what you decide as a spec."
python -m portia.cli.run specs/<whatever it wrote>.yaml --write out
```

Then score against `tests/fixtures/hotels.answers.yaml` by hand. **Scoring is manual today** —
automating it is in `BACKLOG.md` under the run-log item, and the answer key's `pass_criteria`
block is written to be machine-checkable when we get there.

> Piping `yes y` to answer prompts is *not* a valid run of the asking behaviour — the agent
> receives `"y"` as a free-text answer to every question. It is fine for checking the mechanical
> path; it tells you nothing about whether the questions were good.
