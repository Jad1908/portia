# Evaluation — how we know whether the copilot is any good

*Companion to `PLAN.md`. What we test against, what the current score is, and what is known-broken.
Update it whenever a fixture is run.*

> **Status as of 2026-08-04: eight runs exist, and none of them is evidence about the copilot's
> judgment.** They were run to shake out the pipeline end to end — does a turn complete, does a spec
> get written, does the gate fire when it should, does the run log capture any of it. **No prompt
> work was done before them or between them.** Every instruction the model read was a first draft,
> and several were written against a pandas engine that no longer exists. A failing run therefore
> says the loop ran and produced a wrong table. It does not say the model cannot do the task, and it
> does not license a diagnosis of *why* — the runs were never designed to separate a prompt problem
> from a judgment problem, so any claim about which one it was is speculation.
>
> They are kept for one reason: a few of them found **real defects in portia's own code**, and those
> are listed below. Read them for that. The per-run narratives were **compacted hard on 2026-08-04**
> because they had accumulated causal reasoning that read like findings and was never tested as one.
>
> **What would be evidence:** a run against prompts someone has actually worked on, scored against an
> answer key, with model and effort recorded. That run has not happened, and until it does, nothing
> here should be used to argue what the copilot can or cannot do.
>
> **One thing has been observed since, and it is not a score either.** The PHQ indexing run of
> 2026-08-06 is written up below. It has no answer key and it grades nothing — but the pairs it
> chose are checkable against a real schema, and two of them are zeros that a provably-correct
> filter would have discarded. That is evidence about a **mechanism**, not about a model, and it is
> the only thing in this file that was not run against a fixture.

---

## The rule that shapes everything here

**Ground truth is cheap to *check* and expensive to *write*.**

Anything mechanically checkable — row counts, whether a key exists, whether a file parses — is free,
and correspondingly tells you little, because the engine could compute it anyway. Anything that
depends on what the user *wanted* has to be authored by hand, once per test case. There is no
version of this where the labels come for free; if there were, the labels would be the product.

So: the answer keys are the asset. The harness around them is cheap.

### What is *not* a correctness signal

Four things look like free verification and mostly aren't. Don't let them stand in for a score:

| Signal | What it actually is |
|---|---|
| `run_spec` drift vs `expect` | A **faithfulness** check, not a correctness one. The values in `expect` were already in the tool output the agent had just read, so a clean run means it copied numbers correctly. It catches hallucinated figures — and it caught a real bug — but an inner join and a left join *both* verify clean if you predict their row counts right. It is silent on the only decision that mattered. |
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

Tests something harder: whether the **agent** can work out how sources relate when nobody tells it.
`otb` knows only `hotel_id` and has no city; `hotels` carries both `hotel_id` and `city`, so it is
the only bridge; `city_events` is keyed on `city_name` + `event_date`. Two hops, and the second key
never appears in the fact table.

> **The brief in the answer key is the only context the copilot gets, and it must stay
> domain-level.** If a brief ever needs to name a column to make the task solvable, it is leaking the
> answer and the test measures reading comprehension instead of inference. This happened once
> already — see "A retracted result" below.

Planted traps, each independently scoreable: a fan-out visible in raw data (Amsterdam, two events
one day — double-counts revenue by ~4%, plausible enough to ship), a city-spelling mismatch
(`" paris"` vs `"Paris"`, plus `city` vs `city_name`), **a second fan-out that only appears after the
spelling is fixed** (Paris then also has two events that day — only a re-check after acting catches
it), an orphan booking, a hotel with no bookings, a city with no hotels, and two revenue outliers
that invite a judgement call without forcing one.

The truth for the hotel table: **14 rows, revenue 136,240, rooms_sold 147.**

---

## The eight pipeline shakedown runs

Compacted 2026-08-04. **These are not scores.** Each row records what the loop did on a first-draft
prompt, so that the defects they exposed have a reference. The "how it escaped" column is a
description of the mechanism, not a diagnosis of the model.

| Run | Model | How it escaped | Table |
|---|---|---|---|
| 1 | haiku-4-5 | Concluded the spec couldn't express a two-hop join, advised the user to go use dbt, wrote a spec that crashed | ❌ crash |
| 2 | haiku-4-5 | Normalized one side of the spelling mismatch only; explained `no_matches` away as "limited temporal overlap" and declared the table ready | ❌ `event_name` 100% null |
| 3 | haiku-4-5 | Grain widened to `[booking_id, event_name]` — a **tautology** on the column the fan-out varies over | ❌ +480 (0.35%) |
| 4 | haiku-4-5 | Same tautology; the gate never fired at all | ❌ +5,240 (3.85%) |
| 5 | haiku-4-5 | Honest grain `[booking_id]`, gate fired — then wrote `acknowledge` **alone, with zero `AskUserQuestion` calls** | ❌ +5,240 (3.85%) |
| 6 | **opus-5** low | Never reached the gate (Ctrl-C during `index`) | — incomplete |
| 7 | haiku-4-5 | Reverted to the tautology; **zero `op: sql` calls** with the hatch available | ❌ +5,240 (3.85%) |
| 8 | haiku-4-5 | First run on real data (23 PHQ sources, 4.8 GB). Planned a two-path join and **measured nothing** — zero `profile_source`, zero `join_findings` — then asked permission to measure | — read-only goal |

### What these runs actually found — defects in portia, not facts about models

This is the whole of what survives from them. Each item is a change to code or to a prompt file that
was made because a run surfaced it.

- **Run 1 → two engine defects, both fixed.** `record_step`'s description never mentioned that steps
  chain, and `_validate_step` checked that `transforms` existed but never looked inside. This is why
  `CLAUDE.md` forbids inline prompt text: one missing sentence made the copilot tell the user portia
  couldn't do the job.
- **Run 2 → asking the model to grade itself measures nothing.** Asked "was that good?", it said yes
  at every step. Verification must compute post-conditions **in code**. This produced
  `checks/outcome.py` and the rule that a recorded step is immutable.
- **Run 3 → a grain claim can be widened until it passes**, so a claim the agent authors cannot on
  its own be the gate. Three changes: `record_step.md` gained the sentence that a grain of "every
  column that makes the duplicates unique" is trivially true; **`join_findings` now reaches a step's
  output** (`<spec>#<step id>`), which had made *"measure before deciding"* impossible to obey from
  hop 2 onward; and the `expect` vocabulary is now generated from `PROVENANCE_KEYS` with its values
  shape-checked.
- **Run 5 → a missing measurement, not a missing instruction.** At the moment the gate refuses, the
  agent holds `n_duplicated_keys` and example keys but **not the revenue effect**, and nothing hands
  it over — so the instruction to tell the user what a total would be off by asks for a number that
  is nowhere in its evidence. `PLAN.md` → Next → *the consequence of a zero*.
- **Run 6 → portia had no aggregate op, and this is why `ops/sql.py` exists.** Handling the hotel
  fixture's fatal fan-out means reducing events to one row per city-date *before* joining. A model
  worked that out unaided, said there was no op for it, and stopped. That is a fact about portia's
  op set, not about the model. *(`ops/sql.py`, `tests/test_ops_sql.py` and
  `tests/test_agent_handlers.py` all cite this run for that reason — the citation is to the missing
  op, nothing else.)*
- **Run 8 → the one durable number: post-DuckDB, measurement is nearly free.** Two candidate joins
  over 100M rows were measured in **0.02 s each**. That is a fact about the engine, and it is what
  makes `profile_source.md`'s *"Expensive — the detailed rung"* / *"Not for browsing"* stale — that
  language was written when profiling meant pandas reading a whole file. What is still costly is the
  **tokens of the returned evidence**, not the work. Filed in `BACKLOG.md` as prompt work.
- **`prompts.tool()` collapses every description to one line** — `record_step.md` reaches the model
  as 6,038 characters with zero newlines, headings and JSON example flattened. A code fact, found
  while reading Run 7's transcript rather than proven by it. `BACKLOG.md`.

> **What is deliberately no longer here.** Earlier versions of this section argued from these runs
> about model capability, about which prompt line caused which behaviour, and about the engine
> having stopped being the constraint. None of that was tested — the runs held the prompts fixed at
> their first draft and varied nothing — so the arguments were unfalsifiable by construction. They
> were removed on 2026-08-04. The one that came closest to earning its keep, and still did not, was
> that a bigger model surfaced more of the answer key in Run 6 than Runs 1–5 combined: the model and
> the effort both changed at once against unimproved prompts, and the run ended before the gate.

> **The engine changed underneath Runs 1–7** (2026-07-28) — they ran against a pandas engine that no
> longer exists. The *evidence the agent saw* is nevertheless comparable across that boundary, and
> that is measured rather than assumed: the DuckDB migration froze all 29 evidence dicts first and
> every end-to-end case came out byte-identical. Three things the copilot reads did change
> deliberately — `samples` are now distinct and ordered, `mixed_types` was redefined, and a date
> column reads `inferred: datetime` (`DUCKDB_MIGRATION.md` §6.3, §7).

### The verification loop, reproduced against the engine by hand

`checks/outcome.py` measures the frame a step produced, and `record_step` executes the step before
writing it. A step that hits a zero is **not written**; overriding means putting
`acknowledge: [<flag>]` in the YAML, where the human reads it in a diff.

| Reproduction | Before | Now |
|---|---|---|
| Run 2's exact pipeline (lowercase one side only) | recorded; no drift; shipped | refused: `source_did_not_contribute`, `all_null_column` |
| Both sides normalized, `grain: [hotel_id, stay_date]` | recorded; ~4% revenue inflation, looks plausible | refused: `grain_not_unique`, naming H004/Amsterdam **and** H002/Paris |
| A step naming a column that doesn't exist | written, crashed on re-run | refused at record time |

The second row is the interesting one: both fan-outs are caught after the join, by measurement,
without the agent having to remember to re-run `join_findings`.

> **Read that table for what it is.** It is evidence about the **engine** — that the measurement
> exists, fires on the right data, and cannot be routed around by predicting correctly. It is **not**
> evidence about the copilot. Those specs were constructed by hand; no model was involved.

### The known limit of a zero-only blocking rule

Run 3 stripped `city_name` but never lowercased it, so `" paris"` became `"paris"` and never matched
`"Paris"` — and the gate could not have caught it, because `city_events` *did* contribute (four other
events matched). **A partial join failure is invisible to a zero-only blocking rule.** That is a real
limit of the design, not a bug in it, and it is why `BLOCKING_FLAGS` holds zeros only: the moment a
tunable number appears there, code is deciding what counts as bad.

### A gap in the evidence the agent is handed

Run 3's closing summary named a city and two event names that appear nowhere in its evidence,
inventing them to make a readable sentence. `copilot.md` requires every *number* to come from a tool
result; that plainly does not extend to *names*. The code conclusion is more evidence in the grain
examples — carry the row's other columns — not a sterner prompt.

---

## The PHQ indexing run — 2026-08-06

*The first time the copilot has been watched doing something on real, too-big-to-eyeball data.
Haiku 4.5 at low effort, 23 sources from a PHQ extract, one indexing turn. 51 tool calls, no
errors, 25 writes all approved, no questions asked, 533K input tokens in and 10K out, **$0.19**.*

**Read this as one observation, not a score.** There is no answer key for this project, so nothing
below is graded — what makes it worth writing down is that the pairs it chose are checkable against
the data by anyone who knows the schema, and the numbers beside them are the engine's.

### What it did

Read all 23 sources with `describe_source`, wrote an interpretation for each, chose **12 column
pairs** to measure out of the ~245,000 the schema permits, and put them in the graph with a reason
each. Then two groups.

Eight of the twelve are ordinary foreign keys and they measure like it — `EVENT_ID` into the master
event table at 100% left coverage, `GEO_ID` into geo and address at 99%, `LABEL_ID` into a 126-row
label dimension at 126/126. One is a partial that is worth knowing about: `PHQ_LABELS.LABEL` against
`LABELS.LABEL_NAME` shares 79 values and covers **12%** of the left rows, which is a name join that
half-works. One is a plain miss — `LOCATION_ID` against `DIVISION_PLACE_ID`, 7 shared values against
25 distinct on the right — and the measurement is what says so.

### The two that matter, and why they are the argument

The remaining two are the pairs that connect PHQ's event data to the hotel golden record, which is
**the entire point of this project**. Both measure exactly zero.

| pair | shared | comparable | distinct |
|---|---|---|---|
| `EVENT_DETAILS.LOCATION_ID` ↔ `golden.ESTABLISHMENT_CODE` | 0 | **false** | 3,461 / 34,214 |
| `VBPPRED_EVENTS.COUNTRY` ↔ `golden.GEOGRAPHIC_5_COUNTRY` | 0 | true | **17 / 179** |

The second is `KNOWLEDGE_GRAPH.md` §4.4's `France`/`FRA` case, on real data and found without being
looked for: same type, no shared values, seventeen distinct against a hundred and seventy-nine.
That shape is a code-versus-name mismatch and nothing else. The agent's recorded reason —
*"Country field in events should align with hotel country geography for location-event matching"* —
is what turns the zero from a dead end into the work item it is.

The first is worse, and it is the one that settles §6.5. `comparable_types: false` means those two
columns can never match whatever their values are — **a type check would have discarded that pair
with certainty, and been correct to.** §6.5's rejected prefilter design allowed exactly that class
of excluder on the grounds that "an excluder must be a proof", and warned in the abstract that a
proof can still be misleading. It is no longer abstract: the provable excluder would have thrown
away one of the two pairs the project exists to resolve.

**So the central mechanism held.** The agent picked from meaning, the engine measured, and the two
most valuable findings of the run are two zeros that no deterministic filter would have kept.

### What it did not do, and both are prompt problems

- **`graph_lookup` was never called.** Not once, in this run or either of the two before it. Three
  indexing runs, zero uses of the router — while `index_batch.md` tells it in as many words to use
  the graph as it goes, and §9.4's whole reason for building B before C was that at source 23 the
  agent needs it. This project *is* source 23, and it did the job from `describe_source` alone.
  Whether the graph would have improved the picks is untested; what is measured is that it was
  offered and not taken.
- **`profile_source` was never called either.** Twenty-three sources interpreted from L2 alone —
  meaning and flags, no statistics. Cheap, and the summaries came out plausible, but every claim in
  them rests on column names rather than on measured values.

One small defect in the prose, worth naming because it is the kind that is hard to see: a group
context guesses *"PHQ - probably 'Places of High Quality' or similar"*. Nothing gave it that
expansion. Speculating in a durable artifact where "I do not know what this stands for" was
available is the copilot doing exactly what `copilot.md` tells it not to.

### What this does not establish

No answer key, one model, one effort, one run, and the prompts are still the first drafts
`BACKLOG.md` has been asking to work on. It does not say Haiku is good enough, it does not compare
anything to anything, and it must not be cited as a score. What it does establish is narrower and
real: **the pairs-chosen-by-meaning mechanism produces findings a filter would have destroyed**, on
data big enough that nobody was going to find them by looking.


## What has not been measured

The asking behaviour. Runs 1, 2 and 7 were auto-driven with canned answers, so they say nothing
about it; Runs 3–5 were driven by hand, which settled only the **mechanical** path — questions are
generated, rendered, answered and routed back, and both numbered picks and free text parse.

Whether the copilot asks *when it matters*, and whether the questions are good, is unmeasured, and
`PLAN.md` calls that UX the product. Worth an hour whenever the copilot is next worked on: how it
behaves when a human **disagrees** with it — push back on a recommendation, give a vague answer, say
something that contradicts the data. None of that is gradeable by the answer key and none of it needs
to be.

## A retracted result

An earlier demo claimed the context layer was proven: the same merge that recommended a *left* join
context-blind recommended *inner* once the project brief was present, quoting the brief back.

**That result was invalid.** The brief ended with *"an order without a valid customer cannot be
billed"* — which is the answer to the very question being asked. The copilot was reading back a
planted conclusion, not reasoning. The plumbing it was meant to demonstrate (L1 pushed into the
system prompt rather than fetched) is still correct and still shipped; it is simply **not validated
by that demo**. The hotel fixture exists because of this mistake.

---

## Running it

```bash
# isolated project dir, brief taken verbatim from the answer key
mkdir sandbox/hotel-test && cp data/mock/{hotels,otb,city_events}.csv sandbox/hotel-test/
cd sandbox/hotel-test
python -m portia.cli.index --init "<brief from hotels.answers.yaml>" .
python -m portia.cli.chat ask "Build me the one table I can train on."
python -m portia.cli.run specs/<whatever it wrote>.yaml --write out
```

Both agent commands take `--model` and `--effort`, and each turn prints what it is about to spend.
The default is `claude-haiku-4-5` — the develop-on-a-small-model discipline (`PLAN.md`), and the one
a run costs by accident. **Record the model, the effort and the prompt revision with every result.**
Two runs are comparable only if they differ in exactly one of those, which is why the eight above
compare to nothing — several vary two at once.

> **Note which phase a finding comes from.** `index` and `ask` are separate turns with separate
> transcripts, and a run can end in the first without ever reaching the second. A finding from
> indexing says nothing about the gate.

**The prompt is the goal and nothing else.** It used to end "Record what you decide as a spec", which
was a bug in the test: writing the residue is what portia *is*, so a run that only produces one
because the operator remembered to ask is a run measuring the operator. Recording lives in
`copilot.md`. Whether the agent does it unprompted is part of what's being scored — do not put it
back in the prompt.

Then score against `tests/fixtures/hotels.answers.yaml` by hand. **Scoring is manual today**;
the answer key's `pass_criteria` block is written to be machine-checkable when we get there.

> Piping `yes y` to answer prompts is *not* a valid run of the asking behaviour — the agent receives
> `"y"` as a free-text answer to every question. It is fine for checking the mechanical path; it
> tells you nothing about whether the questions were good.

---

## The run log — what shipped, 2026-07-29

`portia/runlog.py` + `python -m portia.cli.runs {list,show}`. One JSONL per turn under
`.portia/runs/`, a header line naming model, effort, prompt, cwd and portia sha, teed at both edges
(`cli/chat.run_and_render` and `ui/turn`), no infrastructure. It exists because six runs had been
scored by hand from terminal transcripts, some pasted twice and some lost to a `^C`, and two runs got
conflated while writing them up.

Four things worth knowing that the spec did not say:

- **A second engine change was needed.** `APPROVAL` announced that a write had stopped for a yes/no
  and never said which was given, so *"how many writes were refused"* was not derivable from the
  stream at all. `events.APPROVAL_RESULT` now carries it. The engine always knew; it just wasn't
  saying.
- **The SDK's `input_tokens` is a trap.** It counts only the *uncached* input: a turn that sent
  14,651 tokens reported **17**, because the L0 system prompt and the L1 brief are pushed every turn
  and are precisely what the cache holds. `summary` reports the whole of it and says how much was
  cached. A run log quoting the raw field would have made every turn look cheap — in the artifact
  built to measure cost.
- **`show` replays through `cli/chat.render`**, so a past run reads the way it read live, plus the
  half the live terminal drops on purpose. The live renderer is unchanged, so the runs scored above
  stay comparable.
- **Comparison was cut deliberately.** A side-by-side of two runs was specced and dropped before it
  was built: it invites reading two columns of counts as better-and-worse, which is the one thing
  these numbers cannot support. Find the two runs in `list`, read them.

The app got the same thing the same day: a **Turns** section in the left pane, replayed in the middle
one, with `engine.turn_summary` *being* `runlog.summary`, so the window and `cli.runs` cannot quote
two different numbers for how often the copilot asked. Building it caught a real reading bug: drawing
both the question **and** its answer listed every question twice, which reads as the copilot having
asked it twice.

> **Be honest about what these are: cost and behaviour descriptors, not correctness.** Only the
> answer keys make a number mean anything, and they are still scored by hand. The log makes scoring
> *cheaper and repeatable*; it does not make it automatic.

**Not yet done:** no run has been scored *using* it, so its value is argued rather than demonstrated.

### Where the logs live, and what that costs

**Project-local, and that is the entire storage model.** `<project>/.portia/runs/*.jsonl` — no
central store, no index, nothing written outside the project. The reason is that a turn is only
interpretable beside the catalog it read and the spec it wrote; a global folder of transcripts naming
tables you then have to go find is worse than no folder. Four consequences:

- **Deleting a project deletes its turns.** There is no copy, and `sandbox/` is gitignored, so
  test-run logs are not recoverable from git either.
- **Nothing prunes.** No retention, no rotation, no delete path in either surface. Tool results are
  the bulk of the bytes (a two-tool turn with full profiles is ~8 KB).
- **Nothing aggregates across projects.** Deliberate: comparing runs means comparing them *on the
  same fixture against the same answer key*, which happens inside one project. The question that
  would justify aggregating — *"did this prompt fix help across every dataset?"* — should wait until
  at least one run has been scored using the log.
- **Reading another project needs no copying.** `--dir` takes an absolute path and `show` accepts a
  file path directly, and the header says what the log was.

The only user-level state portia writes is `~/.config/portia/recents.json` (`ui/engine.py`) —
recently-opened project **paths** and open times, eight of them. No run data, and it does not prune
dead paths, so a deleted project lingers there as a stale entry.
