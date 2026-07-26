# Evaluation — how we know whether the copilot is any good

*Companion to `PLAN.md`. This is where measurement lives: what we test against, what the
current score is, and what is known-broken. Update it whenever a fixture is run.*

**Status as of 2026-07-26: the copilot fails the hotel fixture, and the verification loop that
should catch it has been built but never run against the agent.** See "Current state" and "The
verification loop" below before assuming any part of the loop works end to end.

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

## The verification loop (2026-07-26) — what it closes, and what it does not

`checks/outcome.py` now measures the frame a step produced, and `record_step` executes the step
before writing it, so the measurement reaches the agent whether it asked for one or not. A step
that hits a zero — empty output, a column that went in with data and came out all-null, a source
that contributed nothing, a declared `grain` that isn't unique — is **not written**; overriding it
means putting `acknowledge: [<flag>]` in the YAML, where the human reads it in a diff.

**Reproduced against the engine, by hand, on the hotel data:**

| Reproduction | Before | Now |
|---|---|---|
| Run 2's exact pipeline (lowercase `city_events.city_name` only) | recorded; no drift; shipped | refused: `source_did_not_contribute`, `all_null_column` |
| Both sides normalized, joined with `grain: [hotel_id, stay_date]` | recorded; ~4% revenue inflation, looks plausible | refused: `grain_not_unique`, naming H004/Amsterdam **and** H002/Paris |
| A step naming a column that doesn't exist | written to the spec, crashed on re-run | refused at record time |

The second row is worth dwelling on: H004 on 2026-06-12 is `event_fan_out`, and H002 on the same
date is `fan_out_created_by_cleaning` — the trap that *only exists after the spelling is fixed*.
Both are caught after the join, by measurement, without the agent having to remember to re-run
`join_findings`.

> **Read that table for what it is.** It is evidence about the **engine** — that the measurement
> exists, fires on the right data, and cannot be routed around by predicting correctly. It is
> **not** evidence about the copilot. I constructed those specs by hand; no model was involved.

### What is still unmeasured

**The agent has not been run against this.** Everything above says the gate works when something
walks into it. It says nothing about the questions that actually decide whether this was worth
building:

- Does the agent **fix** a blocked step, or reach straight for `acknowledge`? The refusal text
  tells it not to acknowledge without telling the user first; whether that holds is untested.
- Does it declare a `grain` at all? Nothing forces it to, and an undeclared grain means the fan-out
  goes unmeasured. This is the loop's weakest joint: the mechanism is code, but the *claim* it
  measures is prose the model may simply not make.
- Given that no op can aggregate, does it report the block honestly and ask — or narrate its way
  around it, as Run 2 did with `no_matches`?

Those need a real run, driven by hand (**not** `yes y` — see below), scored against the answer key.
Until then the row above says "the trap is catchable", not "the copilot catches it".

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
python -m portia.cli.chat ask "Build me the one table I can train on."
python -m portia.cli.run specs/<whatever it wrote>.yaml --write out
```

**The prompt is the goal and nothing else.** It used to end "Record what you decide as a spec",
which was a bug in the test: writing the residue is what portia *is* (`PLAN.md` → "Every decision
is durable"), so a run only produces one if the operator remembered to ask is a run measuring the
operator. Recording now lives in `copilot.md`. Whether the agent does it unprompted is part of
what's being scored — do not put it back in the prompt.

Then score against `tests/fixtures/hotels.answers.yaml` by hand. **Scoring is manual today** —
automating it is in `BACKLOG.md` under the run-log item, and the answer key's `pass_criteria`
block is written to be machine-checkable when we get there.

> Piping `yes y` to answer prompts is *not* a valid run of the asking behaviour — the agent
> receives `"y"` as a free-text answer to every question. It is fine for checking the mechanical
> path; it tells you nothing about whether the questions were good.
