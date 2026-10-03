# How portia gets measured

*The method behind portia's benchmark and its internal agent evaluation. Designed 2026-09-25; the
tooling under `devtools/bench/` was built on 2026-09-27 and is unit-tested. **No benchmark run has
happened yet.** When numbers exist they will be published beside this file with the logs, the case
files and the graders, so any number can be rechecked.*

---

## 1. What is being measured

A data scientist is dropped into forty tables they did not choose. The column names do not explain
themselves, data is missing, the keys do not compose, half the tables are useless, and there is a
modelling goal behind all of it. portia is the copilot for that situation: it helps them understand
the data, it asks what only they can answer, it builds a table they can train on, and it remembers
across sessions so nobody pastes the context back in every morning.

The benchmark measures that and nothing easier. A benchmark portia would pass exactly as well as a
plain Claude Code session measures the wrong thing.

## 2. Why not an existing benchmark

The public benchmarks were reviewed and found to test a different job. The pipeline benchmarks
(ELT-Bench, Spider 2.0's dbt track, DataClawEval) write every decision into the task, so there is
nobody to ask, and they are close to saturated. The interactive benchmarks (τ-bench, BIRD-Interact,
QuestBench) have a person to ask but build a query or settle a ticket, not a modelling table over
many sources. **No benchmark measures building a multi-step modelling table while a person settles
the ambiguities.** That gap is the argument for a benchmark of our own, and the reason its baseline
is plain Claude Code on the same files: a score with no baseline says nothing about portia.

## 3. Two layers

| | Public benchmark | Internal agent eval |
|---|---|---|
| Question | Does portia help, compared to the alternative? | Which part works, and why did this run fail? |
| Baseline | always plain Claude Code | usually an earlier portia |
| Cases | frozen, few, changed rarely | grow with every failure found |
| When | at a release | after every prompt or tool change |
| Measures | outcomes | components, single decisions, invariants |

The same runs can feed both. When the benchmark says portia lost, the eval is where you find out
which part failed.

## 4. Principles

1. **Truth is fixed before any run.** Every trap and the final table have an answer written down
   before the copilot sees the data.
2. **Grade what was produced, never whether the transcript reads well.** The table, the spec, the
   catalog, the answers given. A transcript is read when a grader says something failed.
3. **A model may extract or classify for a grader. It never judges truth.** Code verifies.
4. **Counts, never a composite.** One column per kind of evidence, k of N beside every number. A
   composite is a ranking that hides which trap fell out.
5. **Pinned and comparable.** Every run records model, effort, the portia commit, a digest of the
   prompts, the Agent SDK version and the data seed. Two runs are comparable only if exactly one
   of those differs, and the report refuses to put them side by side otherwise.
6. **Repeated.** N runs per cell, k of N reported, pass^k where it applies.
7. **Limits stated.** The scripted user is cooperative, so the numbers are a best case. Simulator
   mistakes are reported as their own count and never charged to the copilot.

## 5. The public benchmark

### 5.1 A case

- **The estate:** thirty to forty tables made from one clean base by a handful of *causes*, plus
  decoys, with the modelling goal stated the way a user would state it.
- **The threads,** each a fresh session with nothing pasted in: *orientation* ("here is the
  extract, I need to predict X, what do we have?"), *build* ("build the training table"), and
  *change*, days later in the story ("add this feature"), where nothing from before is repeated.
- **The simulated user's facts,** the things only a person knows, each with the words a question
  about it cannot avoid, and where it matters two variants of the answer.
- **The truth:** relevant tables, true join paths, each cause's consequences and their correct
  handling, and the final table computed from the clean base.

**The acceptance test for a case:** if plain Claude Code does as well as portia on it, the case is
too easy and gets reworked.

### 5.2 What is measured, per claim

| portia's claim | Measurement |
|---|---|
| Finds what matters in the estate | tables judged relevant against the true list; join paths found against the true paths; cost spent on decoys |
| Helps you understand the data | the orientation answer against the known estate |
| Builds the modelling table | grain, row count, target definition, leaky column absent, the user's exclusions applied, each cause handled, against the true table |
| Remembers across threads | the change thread keeps the build thread's corrections without being told again; tokens, tool calls and tables re-profiled before the first correct step |
| Cost | tokens with cached share, wall time, tool calls, confirmations; portia's indexing counts toward its total |

### 5.3 Memory, compared fairly

portia has its catalog, its findings journal, its specs, its knowledge graph and a brief pushed
into every chat. Claude Code has `CLAUDE.md`, its auto-memory, and whatever files it chose to
write. The baseline gets its best memory in two versions: *as shipped*, and *diligent*, where the
scripted user asks it to write down what it learned at the end of each thread. portia gets no such
instruction, because its memory is automatic. Beating the diligent version is the strong claim.

The memory effect is measured warm against cold inside each system: a later thread run after the
earlier ones, and again on a fresh copy as if they never happened. The tool advantage shows up in
both of portia's arms and cancels; what remains is memory.

### 5.4 The baseline, concretely

The Agent SDK runs Claude Code with its own preset system prompt and built-in tools. The same
permission callback portia uses routes its questions to the same scripted user and lets its writes
through, so the only differences between the arms are portia's tools, prompts and memory. What the
baseline gets is written into the case file, because fairness lives in those details: the tools by
name (`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep` by default), the brief as the project's
`CLAUDE.md`, its memory left on, no catalog.

### 5.5 Reporting

One table per model and effort. Rows are the claims above, columns are portia and the two baseline
versions, each cell k of N or a count. Simulator errors in their own column. A limitations
paragraph. Raw logs, case files, generator and graders published beside it. No composite.

## 6. The internal agent eval

**Fix everything except the thing you are testing, then grade what comes out.** Never the route's
shape: a good session can skip a check because an earlier query already showed the answer, and a
bad one can call the check and ignore it, so "a join must be preceded by the join check" is wrong
in both directions. What may be graded on a route is its *properties*: forbidden actions, cost,
and whether any evidence of a problem was in front of the copilot before it decided.

### 6.1 Invariants: the floor

Three rules that hold whatever route a session took, checked on every log. They catch rare, serious
failures and say nothing about quality.

- **Number provenance.** Every number in a reply appears in something the model saw: the prompts it
  read, the user's messages and answers, a tool result, or an argument it typed itself. The check
  decides nothing about truth; it asks whether the number is in that closed set. Rounding at the
  written precision matches ("~20%" for a result of 19%); a percentage the model worked out from
  two counts is flagged on purpose, because the rule is that the engine computes and the model
  copies. Misses are listed with the sentence around each and the nearest evidence value, never
  only counted. Number words and the right number attached to the wrong meaning are outside it,
  and that is stated.
- **No zero acknowledged with nobody asked**, outside autopilot, where the acknowledgement is the
  user's responsibility by design and is counted apart.
- **Nothing entered a spec unmeasured.** The engine already refuses this; the check confirms it.

### 6.2 Decision points

Build the project state right before one decision (indexed, and the user says "join the bookings
to the events"), run that one exchange ten times, and count the outcomes: measured the fan-out and
said so, measured and joined anyway, joined without measuring. Outcomes are predicates over what
the run left, the tally is k of N per combination, and no combination is called better than
another: which one the prompt should produce is the reader's call. Compare the counts before and
after a prompt edit. One exchange instead of a session, so dozens cost what a handful of full runs
cost.

### 6.3 Error analysis comes first

Read thirty to fifty full sessions by hand, write a free-form note on what went wrong in each,
group the notes into failure types, and count them. A check is written only for a failure type
that recurs and can be stated sharply.

## 7. Breaking data realistically

Public datasets are in every model's training data, and a company's warehouse is not. Real data
with a known right answer does not exist off the shelf. So the estates start from a clean
multi-table base whose meaning is known, broken **through causes**, never through sprinkled noise:

> In March, bookings moved to a new system. Booking ids after March carry a prefix. The old table
> is kept as a legacy copy, and February and March appear in both. The status vocabulary changed.
> Revenue after March excludes tax.

One cause, four linked problems, each with a known right answer. Errors correlated through a
shared cause look like real life; independent errors are a generator's fingerprint. Candidate
causes: a system migration, a second source system for one region, a merger, a backfill, re-keyed
identifiers, snapshot exports mistaken for events, a spreadsheet maintained by hand, a vendor feed
that changed format, stale copies left from an old extract, missingness with a reason, a leaky
column recorded after the outcome, dictionaries missing or split.

Every consequence is labelled with two questions: can the data reveal it, and can the data say how
to fix it? A case states its mix. If everything is revealed and fixed by the data, it is a cleaning
test, not a copilot test.

**Every trap has to be closable.** A trap the data settles can be graded on the final table. A trap
only a person can settle is closed either by the scripted user holding the fact or by the case
listing every acceptable ending. A trap closed neither way is a bug in the case, and a check runs
before any copilot run and fails on it.

**The variant trick.** The hotel fixture in this repository has two bookings at twenty times the
typical rate; a group booking and a data-entry error look identical in the data. Same data, two
scripts: in variant A the user says both are real and the true revenue is 136,240; in variant B
the user says the larger is a typo and the true revenue is 74,740. A copilot that never asks can
pass at most one, and only by luck. "Did it ask" is then measured through the table, without
judging the question.

Realism tests, after the first cases exist: no dumb rule recovers a trap; a classifier cannot tell
profiles of broken tables from profiles of real messy ones; a model shown the broken base cannot
name the public dataset; damage rates drawn from measured real estates.

## 8. The simulated user

A human touches a session in three ways, and each has a treatment:

- **Answers to questions** come from the case's fact table. Each fact lists *anchors*, the words a
  question about it cannot avoid; the fact with the most anchors in the question or its options
  answers, and no anchor anywhere gets a fallback ("your call, tell me what you assumed") that is
  **counted**, because a high count says the script is too thin. Every routing decision is logged
  with its reason, so a failed run can be blamed on the simulator or on the copilot.
- **Write confirmations** are auto-approved and logged as automatic, so no log claims a person
  decided.
- **Follow-up messages** are scripted, sent when the previous reply ends.

The script fixes the human so that any variation across runs comes from the model. The known cost
is that a scripted user is more cooperative than a real one, so the numbers are a best case, and a
small study with real data scientists on the same estates is the planned check on that.

## 9. What exists today

All under `devtools/bench/`, run from a checkout. Nothing here has run on a model yet.

```bash
# the floor over any log, or every log under a folder
python -m devtools.bench invariants path/to/.portia --strict --json

# a case: whether it can run, then one run through portia or through plain Claude Code
python -m devtools.bench check devtools/bench/cases/hotel.yaml
python -m devtools.bench run devtools/bench/cases/hotel.yaml --variant B --model claude-haiku-4-5
python -m devtools.bench run devtools/bench/cases/hotel.yaml --arm baseline-diligent

# one judgement, ten runs on fresh copies, the outcomes counted
python -m devtools.bench point devtools/bench/points/hotel-join.yaml --runs 10
```

A run leaves a folder with the project as it ended, one log per thread with the pins in its
header, and a `result.json` naming the case, the pins, each thread's counts and every routing
decision. Everything runs on the user's own Claude subscription through the Agent SDK's bundled
binary; a local model through the llama.cpp provider costs nothing and is the free shakedown.

Not built yet: the damage generator and the first forty-table case, which start from a taxonomy of
causes written down from real work; the graders over a case's true table; the first run.

## 10. References

- τ-bench and τ²-bench (simulated users, pass^k): [arXiv:2406.12045](https://arxiv.org/abs/2406.12045),
  [arXiv:2506.07982](https://arxiv.org/abs/2506.07982)
- BLADE (a set of acceptable analysis decisions as ground truth): [arXiv:2408.09667](https://arxiv.org/abs/2408.09667)
- QuestBench (asking graded by information need): [arXiv:2503.22674](https://arxiv.org/abs/2503.22674)
- ELT-Bench: [arXiv:2504.04808](https://arxiv.org/abs/2504.04808) · Spider 2.0: [repo](https://github.com/xlang-ai/Spider2) ·
  DataClawEval: [arXiv:2607.28033](https://arxiv.org/abs/2607.28033)
- BIRD-Interact (a two-stage simulated user): [arXiv:2510.05318](https://arxiv.org/abs/2510.05318) ·
  BEAVER (real enterprise schemas are harder): [arXiv:2409.02038](https://arxiv.org/abs/2409.02038)
- Breaking data: Jung et al. 2025, [Towards Realistic Error Models for Tabular Data](https://dl.acm.org/doi/full/10.1145/3774914) ·
  [BART](http://www.vldb.org/pvldb/vol9/p36-arocena.pdf) · [Valentine](https://arxiv.org/abs/2010.07386)
- Realism tests: [classifier two-sample tests](https://arxiv.org/abs/1610.06545) · [adversarial filters](https://arxiv.org/abs/2002.04108)
- Error analysis: [Hamel Husain on error analysis](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html)
