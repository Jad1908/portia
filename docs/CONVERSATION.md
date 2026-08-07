# Conversation — the loop stops being one turn

*Specified 2026-08-07. Not built. Read `VISION.md`'s V0 section first: it is where the single-turn
boundary was drawn on purpose, and §11 here is the reversal.*

**Status:** designed. **§12 phase 1 is done** — the SDK behaviour is measured, not assumed, and it
**reversed §8** (see the table there; `interrupt()` cancels the parked callback and the elaborate
resolve-first protocol this document first specified is unnecessary). Phases 2–6 are not built.

## 1. The gap

`session.run` sends one prompt and closes the client. There is no "actually, redo that as an inner
join" — a follow-up is a new turn, and its only memory is what is on disk.

That is not nothing. The catalog and the spec *are* memory, deliberately, and `copilot.md` already
instructs the agent to write to them as it goes so that a session cut short leaves behind what it
had settled. The residue survives.

**What does not survive is the evidence, and the evidence is the expensive half.** A turn that
profiled three sources, read a `join_findings`, ruled out one key on the strength of a null rate and
proposed another has, at the end, written a step and a rationale. The profile is gone. The findings
are gone. The key it rejected and the reason it rejected it are gone — that reasoning was never an
artifact and was never meant to be one. So the follow-up re-climbs the ladder to arrive back where
the last turn ended, and pays for the climb: a profile still scales with cardinality
(`DUCKDB_MIGRATION.md` §13), and re-reading a wide source is the single most expensive thing the
agent does.

The second cost is subtler and worse. **A one-shot turn is under pressure to finish**, so it
front-loads: pull everything that might be needed, decide everything decidable, write it all down.
That is the opposite of how `copilot.md` asks the agent to work — climb only when you need to, ask
three good questions rather than twenty. The one-shot boundary and the ladder discipline pull
against each other, and today the boundary wins.

## 2. The one structural fact

`session.py:137`:

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query(prompt)
    async for message in client.receive_response():
```

The client's lifetime is the generator's lifetime. Everything else in this document is downstream of
moving that boundary.

Verified against the installed SDK (`claude-agent-sdk` 0.2.128) rather than assumed:

- **`receive_response()` is a convenience over `receive_messages()`** that returns at the first
  `ResultMessage` and nothing more (`client.py:607`). It does not close or consume the client.
- **`query()` is a transport write** (`client.py:287`) — it serializes one user message onto the
  subprocess's stdin. Calling it again on a connected client is the SDK's supported multi-turn
  shape, not a trick.
- **`interrupt()` signals; it does not disconnect** (`client.py:317`). A client is usable after one.
- **`ResultMessage.session_id` exists** (`types.py:1234`), alongside `num_turns`. Recording it costs
  one key in the `RESULT` event.
- **`get_context_usage()` returns `totalTokens`, `maxTokens`, `percentage`** (`client.py:510`) — the
  data behind `/context`.

**And measured rather than read** (`sandbox/spike/`, 2026-08-07): two `query()` calls on one live
client share a `session_id` and the second answers from the first's tool results without re-calling
them, so context genuinely carries. Every interrupt tested produced a `ResultMessage` and left the
client usable. §8 has the rest, including the prediction it overturned.

## 3. Vocabulary — a run, a chat, an indexing

Three artifacts, three words. Today there are two words for three things, and one of them is
already overloaded.

`state.py` draws the line it needs and states why: a **run** executed a spec and was saved as
markdown; a **turn** was the copilot working and was logged as events. *"Collapsing them into one
list would make 'run' mean two things in the pane that exists to say what portia knows about."*

**The distinction is right. The words are not — and the code says so out loud:**

- `runlog.RUNS_DIR = "runs"` writes **turns** into `.portia/runs/`, and carries a comment noting the
  collision with the project-root `runs/` that holds spec-run reports.
- `portia/cli/runs.py` is a module named *runs* whose own docstring says it "reads turns that
  already happened", and whose usage examples read `.portia/runs/…`.

So "turn" was invented to keep "run" from meaning two things, and it half-worked: the *word*
separated, the *paths and the module names* did not.

**Three decisions.**

1. **A copilot conversation is a `chat`.** "Turn" is the SDK's term of art for one exchange, and
   under §5 the log's unit becomes the whole conversation — a file holding six exchanges is not a
   turn, it is a chat. It is also the stronger opposition: *run* and *turn* are near-synonyms in
   ordinary English, and this is a pane whose job is telling someone what portia knows about.
   *Run* against *chat* needs no explaining.

2. **One human message plus the agent's work in response is an `exchange`.** "Turn" retires from
   portia's vocabulary entirely, because the word was doing two jobs and that is the whole
   complaint. The SDK's `num_turns` keeps its name — it is the SDK's field, not ours. *"Message"
   was considered and dropped: a message is what the human sends, and the unit has to include the
   reply.*

3. **Indexing is not a chat and gets its own history.** Under §6 it stays a job, and a job and a
   conversation have different rhythms, different lifetimes and different reasons to be reopened.
   The **right** pane already knows this — `state.TABS = (CHAT, INDEX)`, two streams, on the stated
   reasoning that interleaving them made each harder to read than it is alone. The **left** pane
   does not: one `TURN` list holds both. That is the gap this closes.

Paths and names follow:

| today | becomes | holds |
| --- | --- | --- |
| `runs/` (project root) | unchanged | spec-run reports, markdown |
| `.portia/runs/` | `.portia/chats/` | copilot conversations, JSONL |
| — | `.portia/indexing/` | indexing jobs, JSONL |
| `state.TURN` (one list) | `state.CHAT_LOG` + `state.INDEXING` | two pinned left-pane lists |
| `state.Turn` | `state.Exchange` | one exchange inside a chat |
| `cli/runs.py` | `cli/chats.py` | reads both; a flag filters to one |

**Old logs are read, never migrated.** The new reader keeps reading `.portia/runs/`; nothing writes
there again. It is a few lines, and it means the one real evaluation on PHQ data (`EVALUATION.md`,
2026-08-06) does not vanish because a word changed. portia does not rewrite files in someone's
project to suit its own rename.

**This lands first and alone (§12), and it is honest before the engine changes** — because a
one-shot turn *is* a chat with exactly one exchange. The rename needs nothing from §4–§7 and gives
those diffs a vocabulary to be written in.

## 4. Decision — a chat dies with the process

**A live `ClaudeSDKClient`, held open across exchanges.** Not `resume=<session_id>`.

Decided *against* durability, which is the uncomfortable direction for this project — every other
thing portia produces is a file in the repo. Three reasons it still went this way:

1. **The prompt cache is the budget.** Nearly all of a portia exchange's input is the pushed L0/L1
   system prompt, and the run log already caught that the SDK's `input_tokens` excludes the cached
   part — a 14,651-token turn reported 17 (`PLAN.md` → the run log). A live client keeps that cache
   warm for the whole chat. Re-spawning per exchange re-establishes it each time, and a cold resume
   past the cache TTL re-reads the entire history at full price. On Claude Pro that decides it.
2. **Resume is unverified here.** portia sets `setting_sources=[]` and a per-project `cwd`. Whether
   the SDK persists a session those settings can find, and where, is a spike nobody has run. It is
   not obviously hard; it is not known.
3. **Nothing is lost that the project claims to keep.** The durable artifacts are still written as
   the chat goes. What dies with the window is the reasoning, which portia has never promised to
   keep and `runlog` records as events regardless.

**`session_id` is recorded in the chat log's header from day one anyway.** It costs one field, and
it is what makes "reopen this chat" a later addition rather than a rewrite. Recording it is not a
commitment to using it.

## 5. Decision — the log's unit is the chat

**One JSONL under `.portia/chats/` per chat**, with a `prompt` event opening each exchange, rather
than one file per exchange linked by a chat id.

The log's own argument settles it. `runlog.py` keeps logs project-local on the reasoning that *a
turn is only interpretable beside the catalog it read* — a global folder of transcripts pointing at
tables you would have to go find is worse than no folder. The same sentence applies one level down:
an exchange is only interpretable beside the ones before it. Six files that have to be reassembled
in the right order to mean anything is the shape that argument rejects.

It also keeps `summary` useful. It counts rungs pulled, questions asked, writes refused, ops chosen
— all cost-and-behaviour descriptors, never scores. Those counts are worth having *per chat*; per
exchange they mostly say "one".

Two consequences, both real work:

- **Model and effort move out of the header onto each exchange.** `set_model` exists and a chat may
  legitimately start on Haiku and escalate. A header field that can change mid-file is a lie.
- **`cli/chats.py` and the app's left pane change.** A row is a chat now, not an exchange.

## 6. Decision — the chat stream only

The `INDEX` tab's batch interpretation stays one-shot. It is a job, not a conversation: the app
starts it on the operator's behalf, it has a defined end, and nothing about it invites a follow-up
that the existing re-read note (`APP.asking`) does not already serve. §3.3 is the surface half of
this same decision.

It also keeps at most one *conversational* client alive, which is what makes §9's `busy` rework
tractable.

**It does not mean only one subprocess exists.** An indexing job spawns its own client while the
chat sits open and idle. That is two subprocesses on one account, and it is allowed: they are
independent sessions and `APP.busy` prevents two *in-flight* messages regardless. If it turns out to
cost something, that is a measurement, not a prediction.

## 7. Decision — compose always, send only when idle

The human has had exactly two ways to speak, and both are agent-initiated: answering
`AskUserQuestion`, and allowing or denying a write. A chat box is the third channel and the first
one the human opens. The rule:

- **The box is always editable.** You may type while a message is in flight. Half a thought written
  while the agent works is the normal case, and a disabled textarea throws it away.
- **Send is dark while a message is in flight.** There is no queue. A queued message would have to
  arrive either before or after whatever the agent does next, and neither answer is defensible when
  what it does next might be to ask you a question.
- **Interrupt is an explicit button.** It is the only way to make a composed message go now.

This dissolves a problem the first draft of this spec worried about. **A pending question is
in-flight**, so while a question form is on screen Send is dark and the form is the only live
channel. There is never a moment with two boxes that both look like the place to answer — which
matters, because the agent only reads one of them, and the CLI already treats free text at a
question as a verbatim answer (`cli/chat.py:90`).

The draft has to survive a re-render, which is a solved problem here rather than a new one:
`Decision.draft` exists for exactly this reason (`state.py:105` — an event arriving mid-answer
redraws the form without throwing away a half-written objection), and `APP.goal` is already bound
state.

## 8. Interrupt — measured, and not what this document first said

**Verified 2026-08-07** against `claude-agent-sdk` 0.2.128, three rounds in `sandbox/spike/`, on a
real project with three indexed sources. **This section was written wrong first and the failed
version is kept**, because it is a good example of reasoning that reads well from the source and
does not survive running it.

**What was predicted.** Mid-question, the loop is parked inside portia's own `can_use_tool` callback
awaiting a future only the human resolves (`ask.py:48`). So `interrupt()` could not free it: the
callback would never return, no `ResultMessage` would be emitted, and `receive_response()` — which
"continues indefinitely" if no result arrives (`client.py:583`) — would hang the generator. The
prescription that followed was that **interrupt must resolve every pending decision before it
signals**, and specifically must *resolve* rather than *cancel*, because a cancelled future raises
`CancelledError` into the SDK, "which is an unhandled path".

**Both halves are wrong, and the second one is wrong in an instructive way.**

`interrupt()` frees the loop by **cancelling the parked `can_use_tool` task**. `CancelledError`
propagates out of the awaited future; the future ends `done() and cancelled()`; the SDK emits
`ResultMessage(subtype='error_during_execution')`; the client stays healthy and the next exchange
succeeds. Cancellation is not an unhandled path — **it is the SDK's own mechanism**, and the design
this document proposed was an elaborate way of doing by hand what the library already does.

Measured, in order:

| | |
| --- | --- |
| two `query()` calls on one live client | both succeed, one `session_id`, context carried — the second answered from the first's tool result without calling a tool |
| `interrupt()` mid-tool | `ResultMessage`, `subtype='error_during_execution'` |
| `interrupt()` parked in `can_use_tool` | callback task **cancelled**; `ResultMessage`, same subtype |
| the awaited future afterwards | `done=True, cancelled=True` |
| an exchange after any interrupt | `subtype='success'` — the session is not poisoned |

**So the rule is the opposite of the one first written here.** Interrupt signals; it does not have
to resolve anything first, and there is no ordering constraint on the button. What portia owes is
not preparation but **reaction**: the `Decision` row whose future was just cancelled has to render
as *interrupted*, because a question form left sitting on screen looking answerable, backed by a
future nobody will ever read, is precisely the "silently does the wrong thing" this project forbids.

**`turn._resolve_orphans` needs no change**, which the spike checked rather than assumed. Its guard
is `if not row.future.done()`, and after an interrupt the future is already `done()` — so it does
nothing, and there is no double-cancel.

**One hazard survives, and it was never the SDK's.** `record_step` *runs the op and then writes the
spec* (`handlers.py`), so an interrupt can land between a completed execution and an unwritten
decision. That is portia's own sequencing and the measurements above say nothing about it. It is why
§7 keeps interrupt explicit and never something a keystroke does implicitly — a half-completed
durable write has to be the result of a deliberate act.

## 9. What the app changes

Less than it looks, because `ask.py`'s injected `answer`/`confirm` already do the hard part.

- **The left pane grows a second history.** `Chats` and `Indexing`, both pinned below the tree for
  the reason Turns is pinned today — `.portia/` is not walked. Two lists, per §3.3.
- **`state.start_turn` wipes the transcript** (`state.py:520`, `stream.rows = []`). Under a chat,
  rows accumulate and the wipe moves to an explicit *new chat*.
- **`state.Turn` becomes `state.Exchange` and stops being the unit of the pane.** A `Chat` holds the
  rows, the session id, and its exchanges. Cost and tokens are per exchange and want a running
  total — reported, never judged (`runlog.py`), so a total is a fact and a verdict on it is not.
- **`APP.busy` means "a client exists" today** (`state.py:441`, *a turn is live anywhere*). It has
  to come to mean **a message is in flight**, or an open chat permanently blocks indexing.
- **`get_context_usage` is worth surfacing** and is allowed to be: token counts are measured facts.
  A chat accumulates portia's tool results, which are large, and the operator should be able to see
  that happening. **No policy on top of it** — see §13.
- **Ending a chat is a control that has to exist**, because §4 means an abandoned one holds a
  subprocess. New chat, project close, and window close all end it.

## 10. What the prompts change

`copilot.md` is written for a unit of work that ends, and one line of it should be read carefully
before anything is edited:

> *a session that gets cut short should still leave behind everything it had settled*

**That argument gets stronger, not weaker.** A chat dies with the process (§4), so the case for
recording as you go rather than in a batch at the end is exactly what it was. Nobody should relax it
on the grounds that there is now more room.

What does change is the front-loading pressure §1 names: a chat makes "do one thing, say what you
found, come back" available where it previously was not. That is a small edit and a real one.

**It is also unmeasured, and this document does not get to claim it works.** `PLAN.md` records that
the prompts have never been worked on and that the eight shakedown runs found defects in portia's
code and nothing about the copilot's judgment. Change the prompt; do not write down a prediction
about what it does.

## 11. The reversal in `VISION.md`

`VISION.md` says, of V0:

> *It must not present a chat box that implies a conversation the engine cannot hold; a follow-up
> that silently loses context is worse than an honest boundary.*

**The rule was right and stays right.** What is being removed is its premise — the engine can hold
the conversation now — not its content. A follow-up that silently loses context is *still* worse
than an honest boundary, which is why §9 requires the end-of-chat control to be visible and §4
requires the window to be honest that closing it ends the thread.

Kept beside the reversal, per this repo's convention, because it is an unusual case: most reversed
arguments here failed on contact with reality. This one did not fail at all. It correctly described
a boundary and correctly refused to fake past it, and the right response was to move the boundary.

`BACKLOG.md` parked this as "not a prerequisite for the app", which was true of V0. Its entry now
points here and keeps that sentence, along with what it got wrong: a fresh turn keeps the artifacts
and loses the evidence (§1).

## 12. Build order

Cheapest and most uncertain first, which here is the same thing.

1. ~~**Verify the interrupt path.**~~ **Done 2026-08-07** (`sandbox/spike/`, three rounds). A
   `ResultMessage` arrives in every case, the client survives, and the finding **reversed §8** — the
   resolve-before-interrupt protocol specified there is unnecessary, because the SDK cancels the
   parked callback itself. Nothing below is blocked.
2. **The rename, alone, with no behaviour change** (§3). A pure vocabulary diff — directories,
   module names, state names, docs — reviewable on its own and honest before the engine moves,
   because a one-shot turn is a chat with one exchange.
3. **The engine seam.** A `Conversation` owning the client, with `send(prompt)` yielding events to
   its `ResultMessage`. **`session.run` survives as a one-message wrapper over it** — that is what
   keeps `cli/index.py`, `cli/chat.py`'s three subcommands and `ui/turn.start` working untouched,
   and it means the seam can land and be tested before any surface uses it.
4. **The log.** One file per chat, `prompt` events, model and effort per exchange, `session_id` in
   the header.
5. **The app.** `Chat` in `state.py`, the two left-pane histories, the send rule, the interrupt
   button, the end control, `busy` reworked, context usage on screen.
6. **The prompt edit** (§10), last and alone, so it is not moving while anything else is.

A terminal REPL (`chat repl`) is the natural sixth surface and is **not** required by any of the
above — the CLI's existing one-shot commands are a one-message chat and keep working. Left for
`BACKLOG.md`.

## 13. Open

- **Context growth is unmeasured and gets no policy until it is.** portia's tool results are large —
  a profile of a wide table is the biggest thing the agent ever reads — and a chat accumulates them.
  Claude Code auto-compacts and portia does not control it. `get_context_usage` makes it observable
  (§9). Building a trimming or summarizing policy before watching a real chat hit the ceiling would
  be inventing a threshold, which is the mistake this project has already reversed once.
- **Whether two live subprocesses cost anything** (§6). Allowed until measured otherwise.
- **Reopening a chat** (§4). `session_id` is recorded; nothing reads it. Needs the resume spike
  before it is more than a field.
- **What a chat means for `EVALUATION.md`.** A scoreable unit was a turn; the answer keys are
  written against one. This does not resolve that and should not pretend to.
