# Evaluation — how we know whether the copilot is any good

*Companion to `PLAN.md`. What we test against, what the current score is, and what is known-broken.
Update it whenever a fixture is run.*

> **Status as of 2026-08-02: eight runs, all failing, and the copilot has not been worked on since
> Run 8 (2026-07-29).** Everything built since — the pipeline, the compiled models, the app's
> rendering of them — has been engine and interface work, tested end to end rather than scored for
> output quality. So the diagnosis below is the current one, and it has not moved: **the engine is
> no longer the constraint; the copilot's judgment is.** The per-run narratives were compacted on
> 2026-08-02 to the findings that changed the code.
>
> **The one run that would move this forward:** `claude-opus-5`, reaching `chat ask` and getting as
> far as a `record_step`. It answers three open questions at once — does it use the SQL hatch, does
> it ask before acknowledging, and does the sequence read differently now that a correct move
> exists.

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

## The eight runs

Compacted 2026-08-02. Each row is one run; the column that matters is **how it got past the gate**,
because that is what each fix closed and where the next failure appeared.

| Run | Model | How it escaped | Table |
|---|---|---|---|
| 1 | haiku-4-5 | Concluded the spec couldn't express a two-hop join, advised the user to go use dbt, wrote a spec that crashed | ❌ crash |
| 2 | haiku-4-5 | Normalized one side of the spelling mismatch only; explained `no_matches` away as "limited temporal overlap" and declared the table ready | ❌ `event_name` 100% null |
| 3 | haiku-4-5 | Grain widened to `[booking_id, event_name]` — a **tautology** on the column the fan-out varies over | ❌ +480 (0.35%) |
| 4 | haiku-4-5 | Same tautology; the gate never fired at all | ❌ +5,240 (3.85%) |
| 5 | haiku-4-5 | Honest grain `[booking_id]`, gate fired — then wrote `acknowledge` **alone, with zero `AskUserQuestion` calls** | ❌ +5,240 (3.85%) |
| 6 | **opus-5** low | Never reached the gate (Ctrl-C during `index`) — but the indexing phase alone surfaced more of the answer key than Runs 1–5 combined | — incomplete |
| 7 | haiku-4-5 | Reverted to the tautology; **zero `op: sql` calls** with the hatch available | ❌ +5,240 (3.85%) |
| 8 | haiku-4-5 | First run on real data (23 PHQ sources, 4.8 GB). Planned a two-path join and **measured nothing** — zero `profile_source`, zero `join_findings` — then asked permission to measure | — read-only goal |

### What each escape taught, and what closed it

- **Run 1 → two engine defects, both fixed.** `record_step`'s description never mentioned that steps
  chain, and `_validate_step` checked that `transforms` existed but never looked inside. This is why
  `CLAUDE.md` forbids inline prompt text: one missing sentence made the copilot tell the user portia
  couldn't do the job.
- **Run 2 → self-assessment is worthless.** Asked "was that good?", the agent said yes at every step.
  Verification must compute post-conditions **in code**. This produced `checks/outcome.py` and the
  rule that a recorded step is immutable. Worth noting what passed: the brief named no columns and
  no keys, and it still derived `hotel_id` and matched `city` to `city_name` by meaning.
- **Run 3 → a grain claim can be widened until it passes.** `record_step.md` gained the sentence
  that a grain of "every column that makes the duplicates unique" is trivially true and measures
  nothing. Also: **`join_findings` now reaches a step's output** (`<spec>#<step id>`), which had
  made *"always measure before deciding"* impossible to obey from hop 2 onward. And the `expect`
  vocabulary is now generated from `PROVENANCE_KEYS`, and its *values* shape-checked.
- **Run 4 → the tautology again, and the table got worse.** Fixing the spelling makes Paris's second
  event reachable, so the fan-out doubles: 14 bookings → 18 rows. **A claim that cannot fail
  measures nothing, and verifying it is indistinguishable, in the output, from verifying something
  real.**
- **Run 5 → the loop worked and the table was still wrong.** The tautology did not reappear; the
  gate fired correctly. Then it acknowledged in one move with no human in between. But note what the
  instruction it skipped asks for: at the moment of refusal the agent holds `n_duplicated_keys` and
  example keys, **not the revenue effect**, and nothing hands it over. That is a missing
  measurement, not a missing instruction — `PLAN.md` → Next → *the consequence of a zero*.
- **Run 6 → the diagnosis changes.** On a bigger model, in indexing alone: it asked about the
  revenue outliers (never raised in any prior run), predicted the fan-out **before any join**,
  derived the grain from the goal unprompted (*"you said you model per hotel, so this needs
  aggregating first"*), diagnosed portia's own missing aggregate op rather than faking one, and
  offered options carrying consequences. Handed two garbled answers it said so and re-asked.
  **The judgment failures in Runs 1–5 read as capability, not architecture** — which is the more
  important half of the design being validated.
- **Run 7 → the hatch is necessary but not sufficient.** It removes the excuse; it does not create
  the judgment. `record_step`'s description names *"aggregating to a coarser grain"* as the hatch's
  purpose in as many words, and the model never called it. **"It had no way to do the right thing"
  is no longer available as a defence for any future failure on this fixture.** *(Caveat: Run 7 was
  auto-driven with canned answers and a facts-only catalog, so it is not a valid test of the asking
  behaviour.)* A candidate cause, unproven: `prompts.tool()` collapses every description to a single
  line, so `record_step.md` reaches the model as 6,038 characters with zero newlines — headings and
  the JSON example flattened. Filed in `BACKLOG.md`.
- **Run 8 → the engine got fast enough that the copilot's caution is the bottleneck.** Its "bridge"
  table had **56 rows**. Both joins it proposed match **0 keys**, established afterwards by two
  0.02 s queries. One was on a 4-value, 51%-null column called `LOCATION` whose values are
  `PRIME LOCATION`, `SECONDARY LOCATION`, … — a site-quality grade, not a place. It proposed it
  because the name reads like one, and never looked at a value or a distinct count, both of which
  are in `profile_source`'s ordinary output. **A candidate cause in our own prompt:**
  `profile_source.md` still opens with *"Expensive — the detailed rung"* and closes with *"Not for
  browsing"*, written when profiling meant pandas reading a whole file. What is still expensive is
  the **tokens of the returned evidence**, not the work; the prompt conflates them. `BACKLOG.md`.
  - It does **not** support building a layer that flags "suspicious" columns. That is exactly the
    judgment call the agent exists to make, and the distinct count that gives it away is already in
    the evidence. **The fix is to stop discouraging the call, not to make the call for it.**
  - *Caveat on that write-up:* only the last 90 lines of the transcript were kept, so the
    `describe_source`-only claim rests on that tail plus the absence of any profile in the output.

> **The engine changed underneath Runs 1–7** (2026-07-28) — they were scored against a pandas engine
> that no longer exists. **The scores still stand**, and that is measured rather than assumed: the
> DuckDB migration froze all 29 evidence dicts first and every end-to-end case came out
> byte-identical. Three things the copilot reads did change deliberately — `samples` are now distinct
> and ordered, `mixed_types` was redefined, and a date column reads `inferred: datetime`
> (`DUCKDB_MIGRATION.md` §6.3, §7).

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

### It states facts it was never given

Run 3's closing summary named a city and two event names that appear nowhere in its evidence,
inventing them to make a readable sentence. Run 5's rationale invented a column (`event_id`) that
does not exist in the data. `copilot.md` says every number must come from a tool result; that plainly
does not extend to *names*. The fix is more evidence in the grain examples — carry the row's other
columns — not a sterner prompt.

---

## The biggest untested thing: whether it asks at all

Runs 1 and 2 piped `yes y`, so nothing about the asking behaviour was measured. Runs 3–5 were driven
by hand, which settled the mechanical path: questions are generated, rendered, answered and routed
back, and both numbered picks and free text parse.

What replaced that gap is sharper. **Run 5 asked nothing at all** across an entire session in which
it hit a blocking flag, overrode it, and shipped a table with 3.85% too much revenue. Runs 3 and 4
each asked, but never about a fatal trap.

So the unmeasured thing is no longer *"are the questions good"* but *"does it ask when it matters"* —
`PLAN.md`: *"the questions-and-insights UX **is** the product"*.

**Run 6 answered the first half and not the second.** On a bigger model the questions were good by
the answer key's own standard. But it asked them all during *indexing*, and the session ended before
a single step was recorded. **Nobody has yet watched a capable model reach a blocking flag.**

Also still untested, and worth an hour: how it behaves when a human **disagrees** with it. Push back
on a recommendation, give a vague answer, tell it something that contradicts the data. None of that
is gradeable by the answer key, and none of it needs to be — the failure modes will be obvious.

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
a run costs by accident. **Record the model and effort with every result**: Run 6 is only comparable
to Run 5 because they differ in that and nothing else.

> **Note which phase a finding comes from.** `index` and `ask` are separate turns with separate
> transcripts, and Run 6 is a standing reminder that a run can produce excellent evidence in the
> first and never reach the second. A finding from indexing says nothing about the gate.

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
