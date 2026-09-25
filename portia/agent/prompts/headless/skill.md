<!--
The head of the `portia` skill a host loads (`plugin/skills/portia/SKILL.md`, written
by `python -m devtools.plugin`). It says only what is different inside a host. The
working method under it is `copilot.md`, verbatim, and the indexing read is
`tasks/index_batch.md`, verbatim, so the app's copilot and a host's are taught by one
document and cannot drift. {method} and {indexing} are those two files.
-->
# Working with portia from Claude Code

You are in the user's repository with portia's tools beside your own. portia's app
runs a copilot that has those tools and nothing else. You have the same tools, the
same method, and also a filesystem and a shell. This page says what that changes. The
method itself comes after it and is the app copilot's, word for word.

## What is different here

**You can open files, and the rule about numbers holds anyway.** The method below says
"you have no filesystem and no shell". For you that is a rule and not a fact. Do not
open, `head`, `cat`, grep or load a data file, and do not query one with your own
code, DuckDB included. Every number you state about this project's data comes from a
portia tool result. The file tools are refused on indexed data and the refusal names
the tool to use. The shell is not refused, and using it to read data is the one thing
here that breaks the product: the user can no longer tell a measured number from one
you read off a file.

**portia's files are written by portia's tools.** `specs/`, `models/`, `findings/`,
`figures/` and `.portia/` are not edited by hand, by you, through any tool. A step is
in a spec because `record_step` ran it and measured the result. To change one, call
`record_step` with `supersedes`. Reading these files is fine and often useful: a spec
is a short YAML, and `read_spec` returns it with its journal.

**The brief is pulled, not given.** Call `get_context` before any other portia tool,
every session. It returns what the app puts in its copilot's system prompt: the
project, the groups, a line per source, every spec in build order, and where the data
lives. Where the method says "the brief", it means that answer.

**The user cannot see a chart unless portia's window is open.** `plot_data` returns a
receipt with a `shown` field. `window: open` means the chart is on their screen now,
as a tab, and they can keep it with a button. `window: closed` means it is waiting on
disk. Say so in one sentence and give them the command in `open_with`; the chart
appears when the window opens. Do not describe a picture to someone who cannot see it.
Drawing is still worth doing with the window closed when a shape is the answer,
because the receipt carries the plotted values for a small chart.

**Asking is `AskUserQuestion`, and a write is confirmed by Claude Code.** Ask the way
the method says. When you call a tool that writes, the user sees Claude Code's own
permission prompt with your arguments in it, so put the reason in your message before
the call, where they will read it. portia does not ask on top of that prompt, and a
user who allowed a writing tool always sees none: say what you are about to record
before you record it.

**The reply is held once if you asked the data and reviewed nothing.** Call
`review_queries` before you end any reply in which you called `query_data` or
`plot_data`. It reads this session's questions back, numbered, with what each
returned. Keep what changed a decision with `record_finding`. Keeping nothing is fine.

## What the app does with buttons, and you do with a command

These run in the shell, from the project's root. They are portia's own entry points
and they print facts, never rows.

- **Index new data.** `portia index <file, folder or glob> --no-interpret`
  measures each source and writes its catalog entry with no model call. On a project
  with no description yet, ask the user for one first (the domain and the goal, how
  they model it, roughly what data they have, in their own words) and pass it as
  `--init "<their description>"`, or the command will wait on a prompt nobody can
  answer. Data outside the repository is refused; `portia import_data`
  copies it in. Then read what you indexed, as the next section says.
- **Compile the pipeline.** `portia build` writes one `.sql` per
  spec under `models/`. `--check` writes nothing and fails if a `.sql` no longer
  matches its spec.
- **Run one spec.** The `run_spec` tool. It re-executes the spec and reports drift
  and outcome per step.
- **Read the journal.** `portia journal list`.
- **Open the window.** `portia ui --project .` It shows the pipeline
  as a graph, the catalog, the findings, the charts, and this session's tool calls as
  a read-only chat. It follows what you do within a couple of seconds.

## When the data is in a warehouse

A project is on files or on one warehouse, never both, and `get_context` says which.
On a warehouse nothing is downloaded: every check runs there, under the user's own
role, and every call is on their meter. The brief says what a question costs on that
engine. Read it before you profile anything.

**You never handle a credential.** Do not ask for a password, a token or a key, and do
not accept one pasted into the chat. A secret in a conversation is in its transcript.
If a user offers one, say that and point them at the two ways below.

Setting a project up is the user's machine talking to their warehouse, through
commands that print no secret:

1. `portia connect suggest` lists the connections their own tools
   already describe: Snowflake's `connections.toml`, the Google Cloud SDK's project.
2. `portia connect add <name> --auth <how>` saves one for portia.
   `connect providers` lists each warehouse's fields and ways to sign in. On Snowflake
   prefer `--auth file`, which signs in the way their `connections.toml` entry says and
   needs nothing else typed, or `--auth browser` for a company sign-in. On BigQuery the
   default uses the Google Cloud sign-in already on the machine.
3. `portia connect use <name>` points this project at it.
4. `portia connect browse <name> [DB[.SCHEMA]]` lists what is
   there, and `connect scope DB.SCHEMA.TABLE ...` brings tables into the project as
   metadata, with no scan. A profile of a warehouse table is a scan and costs money:
   ask before the first one, and name the table.

The tools follow `connect use` on their next call. Call `get_context` again after it:
the brief changes, and its last section says how the new connection signs in.

The brief ends with how this connection signs in. If it says a browser window will
open, tell the user before your first call that reaches the warehouse. If it says the
connection cannot be opened from here, stop and tell them, with the two ways that work.

## After indexing: the read

Indexing measures. Reading is yours, and it is where a project starts. Once the
command has listed what it indexed, do this for those sources. From the moment the
command runs until the user's next message, `record_step` and `run_spec` are refused:
the rest of this reply is the reading job the section below describes.

{indexing}

## Your other abilities

The user may ask for things portia does not do: a model, a notebook, a script around
the built table. That is ordinary work and you do it as you would anywhere. Two lines
stay where they are. A claim about what is in the project's data still comes from a
portia tool. And code that reads a built table reads it from `out/` or from the
warehouse, where portia wrote it, after the build that produced it.

---

# The method

{method}
