# Evaluation — how we know whether the copilot is any good

*Companion to `PLAN.md`. This is where measurement lives: what we test against, what the
current score is, and what is known-broken. Update it whenever a fixture is run.*

**Status as of 2026-07-26: the copilot fails the hotel fixture.** Run 3 is the first
interactively-driven run and the first with the verification loop in place. It got closer than any
run before it and still shipped a table with double-counted revenue — by **widening its own grain
claim until the check passed**. See "Run 3" below; read it before trusting the gate.

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

---

## Run 3 (2026-07-26) — the first interactively-driven run. **Fails.**

`claude-haiku-4-5`, driven by hand, brief verbatim, ~$0.09 total. The first run in this project's
history where a human answered the questions. Spec: `specs/training_table.yaml`, 3 steps.

| | Run 2 | Run 3 |
|---|---|---|
| Hop 1 `otb → hotels` on `hotel_id` | ✅ | ✅ |
| Hop 2 `city ↔ city_name` + date | ✅ | ✅ |
| `city_spelling` — fatal | ⚠️ fixed one side | ⚠️ **stripped, never lowercased** |
| `event_fan_out` (Amsterdam) — fatal | ❌ | ⚠️ detected, **mis-described, shipped** |
| `fan_out_created_by_cleaning` — fatal | ❌ | ❌ never reachable (spelling half-fixed) |
| Asked about revenue outliers | ❌ | ❌ |
| Asked about the orphan booking | ❌ | ⚠️ noted in a rationale, never asked |
| Asked about anything measurable | ✅ none | ✅ none |
| Recorded a spec **unprompted** | n/a | ✅ |
| Declared a `grain` | n/a | ✅ every step |
| Spec runs | ✅ | ⚠️ runs, **two permanent drifts** |
| **Table is correct** | ❌ | ❌ **revenue inflated by 480 (0.35%)** |

### What went right, and it is not nothing

It recorded the spec without being asked (the L0 change works). It **declared a `grain` on every
step** — the open question that most worried me, answered yes. It climbed the ladder properly
(describe ×3 → profile ×3 → join_findings). It found the two-hop path with no column names in the
brief, recovered from a wrong `join_findings` call by reasoning that bookings must route through
hotels, chained steps correctly, and asked twice — both times about `should_ask_about` item 3
(how to represent multiple same-day events), never about anything the checks could answer.

**The gate also caught a fake aggregation.** Told to aggregate, and having no op for it, it tried
to record `op: normalize, transforms: []` with a rationale describing a group-by. The grain check
refused it, and it came back and said plainly that the tool only supports element-wise transforms.
A no-op step with a rationale claiming it aggregates is exactly the kind of thing that used to be
writable.

### How it got past the gate: it widened the claim

This is the finding. First attempt: `grain: [booking_id]` → refused, `grain_not_unique`, B0009
duplicated. Correct behaviour, and it did report it and ask. Then, after the user chose "accept
the multiplication", it recorded `grain: [booking_id, event_name]`.

That claim is a **tautology**. `event_name` is the column the fan-out varies over, so a grain
including it can essentially never fail on a fan-out. The run output says it all:

```
✓ grain ['booking_id', 'event_name'] is unique (15 rows)
! acknowledged: grain_not_unique
```

The check passed. The acknowledgement is vestigial — the flag it names never fired. **It did not
override the gate; it dissolved it.** This is the same move as rewriting `expect` to match the
result — which we made impossible — reappearing on the one field the agent authors. "The agent
authors the claim" was named as the loop's weakest joint before this run; it is now demonstrated.

### What actually shipped

`out/training_table.csv`, 15 rows, presented as clean. Two defects the user was never told about:

- **B0009's revenue is double-counted.** Table total 136,720 vs true 136,240. The user did consent
  to "row multiplication" — but it was framed as a modelling choice about booking-event
  interactions. Nobody said the word revenue. That is not informed consent, and it is why
  `record_step.md` now says to state what a zero does to their figures before acknowledging it.
- **The Marathon event is silently missing.** It only stripped `city_name`, so `" paris"` became
  `"paris"` and never matched `"Paris"`. Same half-fix as Run 2. The new gate did **not** catch it,
  and could not have: `city_events` *did* contribute (four other events matched), so
  `source_did_not_contribute` correctly stayed quiet. **A partial join failure is invisible to a
  zero-only blocking rule.** That is a real limit of the design, not a bug in it.

### It stated facts it was never given

Its closing summary: *"Booking B0009 on 2026-06-12 in **Paris** coincided with 2 events (**Tech
Summit and Marathon**)."* B0009 is **H004, Amsterdam**, and the two events are **Canal Festival
and Design Week**. Marathon is the event that matched nothing at all.

The evidence it held named the booking id and nothing else — `grain` was `[booking_id]`, so the
duplicate example was `{"booking_id": "B0009", "n_rows": 2}`. It invented the city and both event
names to make a readable sentence. `copilot.md` already says every number must come from a tool
result; that plainly does not extend to names, and the same instinct is on record in
`handlers.profile_source`'s docstring — *anything it can't measure, it will estimate*. The fix is
more evidence in the grain examples (carry the row's other columns), not a sterner prompt.

### Scored against `pass_criteria`

| Criterion | |
|---|---|
| found both hops, `city ↔ city_name` matched by meaning | ✅ |
| surfaced every fatal `must_surface` before recording a step | ❌ |
| recorded a spec that runs clean (no drift) | ❌ `transforms: 1` vs a list; `right_dropped: 0` vs 1 |
| asked about ≥1 `should_ask_about` topic | ✅ |
| asked about none of the `should_not_ask_about` topics | ✅ |

Both drifts are `expect`-vocabulary errors: it guessed `{"n_rows": 7}`, was rejected, guessed
`{"transforms": 1}` (a count, where the field is a list), and that one was accepted because the
validator checks the key exists, not the shape of its value. The vocabulary is now generated into
`record_step.md` from the ops (`handlers.step_vocabulary`), which removes the guessing; validating
an `expect` **value's shape** is not yet done and is in `BACKLOG.md`.

### What this run changed

- The `expect` vocabulary is in the tool description, generated from `PROVENANCE_KEYS` so it can't
  rot. Two round-trips and two permanent drifts came from it being discoverable only via rejection.
- `record_step.md` now says: there is no aggregation op, don't fake one; claim the grain the *work*
  needs and decide it before you see the result; and tell the user what a zero does to their
  numbers before acknowledging it.
- **`join_findings` now reaches a step's output** (`<spec>#<step id>`). It couldn't, which made
  *"always measure before deciding"* impossible to obey from hop 2 onward — the run tried it, was
  refused, and recorded blind instead. Measuring hop 2 up front on this same data returns
  `('2026-06-12','Amsterdam') n_left 1 × n_right 2` **and** the row
  `{'city_name': 'paris', 'event_name': 'Marathon'}` among the unmatched. Both fatal traps, in
  plain rows, before any write. Neither needed a new flag; the evidence was simply unreachable.
- Open, and the most important thing here: **a grain claim can be widened until it passes.** The
  candidate fix is a claim-free row-conservation fact — a left join whose output exceeds its left
  input multiplied rows, which is binary, has no tunable number, and cannot be dissolved by
  redefining anything. Whether that counts as a "zero" under the blocking rule is a design call.
  In `BACKLOG.md`.

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
