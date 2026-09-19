# Prompts — every instruction the model is given

This directory is the **single home for injected instruction text**. If the copilot reads it,
it is written here, not embedded in a Python string somewhere. That exists so wording can be
diffed, reviewed and A/B'd without touching code — prompt text is the most
performance-sensitive and least stable part of the system, and it deserves to be edited like
prose rather than hunted through decorators.

The failure that motivated it: `record_step`'s description omitted one sentence about steps
chaining, and the copilot concluded portia couldn't express a two-hop join and told the user to
go use dbt instead. That sentence was buried in a decorator argument.

## Layout

| Path | What it is | When the model sees it |
|---|---|---|
| `copilot.md` | **L0** — what portia is for, the four moves, what to look at, the artifacts, the rhythm of a build, the modelling decisions (`docs/COPILOT.md`) | every request |
| `brief/template.md` | **L1** — the project brief's shape; `{project}`, `{groups}`, `{sources}` are filled by `agent/context.py` | every request |
| `brief/no_context.md` | what L1 says when the project is undescribed | when `project.yaml` is empty |
| `brief/no_sources.md` | what L1 says when nothing is indexed | when no source is indexed |
| `backend/<kind>.md` | the tail of L1 — **where the data lives**: which engine, which dialect, what a question costs, what the role and the scope allow (`docs/CONNECTOR.md` §2.9). One file per backend; `agent/context.py` picks by `core.backend.active()` | every request |
| `tools/<tool>.md` | one tool's description — what it does **and when to reach for it** | in the tool list |
| `tasks/<task>.md` | the opening instruction a CLI command sends | once, per invocation |
| `errors/<name>.md` | what a **refused** tool call says back — and what a held reply is told (`review_before_reply.md`, via the `Stop` hook in `agent/session.py`) | when the engine blocks a write, or holds a reply |
| `headless/instructions.md` | what a **host that is not portia** is told when it connects to `portia-mcp` (`cli/serve.py`): read the skill, read the brief, and the five rules that have to hold whether or not the skill was fetched | every request, in Claude Code |
| `headless/get_context.md` | `get_context` as a host is offered it: *call this first*, where the app's says *you already have this* | in the tool list, in Claude Code |
| `headless/signin/<kind>.md` | the last section of the brief a host pulls on a **warehouse** project: how this connection signs in, that a browser may open, that no credential is ever typed into the chat (`cli/serve.signing_in`). One file per kind: `browser`, `file`, `quiet`, `typed`, and `missing` when this machine has no such connection | with `get_context`, until a session is open |
| `errors/warehouse_needs_typing.md` | what a host's model reads in place of `SecretRequired`: never ask for the password, offer `browser` or `file` | when a tool reaches a warehouse that wants a secret typed |
| `errors/hand_edit.md`, `errors/raw_read.md` | what Claude Code's file tools are told when `portia-hook guard` refuses them (`cli/hook.py`): which portia tool owns the file, or which check answers the question | when a host's model edits a portia file or opens indexed data |

`errors/` exists because a refusal is read at the exact moment the model is choosing what to do
next, which makes its wording as load-bearing as any tool description — and the two refusals here
are the ones the hotel runs earned. `blocked_step.md` has to say "this is a zero, not a matter of
degree" convincingly enough that the model doesn't reach for the acknowledgement by reflex;
`immutable_step.md` replaced a message that said "pick another id", which was an instruction for
how to get around the rule. Both are filled with measured facts via `prompts.error(...)`.

Tool files are named for the tool. `agent/tools.py` loads them by name, and
`tests/test_agent_prompts.py` fails if a tool has no file or a file has no tool — so a rename
can't silently fall back to an empty description.

Task files are `str.format` templates; their placeholders are documented at the top of each.

## The rule

**Inline prompt text is forbidden anywhere in the codebase.** Not a module constant, not a
`@tool` description, not an f-string, not "just this once". If the model reads it, it is a file
in here.

Enforced by `tests/test_agent_prompts.py`, which fails on any non-docstring string literal over
200 characters anywhere in `portia/`, and separately on any `@tool` whose description isn't
`prompts.tool(...)`. Docstrings are exempt — they're written for us, not the model. If the scan
trips on something that genuinely isn't prompt text, that's worth a conversation rather than a
threshold bump.

**It has tripped twice, both times on Cypher** (`knowledge/query.py`, 2026-08-04). Neither was
prompt text and neither was a reason to raise the limit: the fix both times was to name the query's
parts as module constants, which is what `checks/join.py` already does with its overlap
expressions and reads better anyway. If a long literal is a query, name its pieces; if it is
something the model reads, it belongs in this directory.

## What deliberately stays in code

**JSON-schema field descriptions** (`{"type": "string", "description": "Indexed source name"}`)
stay in `agent/tools.py`. They are three-word labels structurally bound to a schema key, and
splitting them out would make the schema unreadable while adding nothing you'd want to iterate
on. The long-form prose — the part that decides whether the model reaches for a tool at all —
lives here.

## Writing tool descriptions

Say **when to call it**, not only what it does. That is what the model actually acts on, and
it's where the disclosure ladder is taught: a description should place its tool on the rungs in
`copilot.md` ("start here", "expensive — call it when you need the numbers themselves"). A tool
that doesn't say when it applies gets called at random or not at all.
