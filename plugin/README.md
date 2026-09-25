# portia for Claude Code

portia's tools in your own Claude Code session: profile sources, check joins, ask
the data a question, draw a chart, and record a pipeline whose every step was run
and measured before it was written.

## Install

The plugin starts two commands, `portia-mcp` and `portia-hook`, and its skill uses a
third, `portia`. All three have to be on your `PATH` first:

```sh
uv tool install "portia[agent,ui,graph] @ git+https://github.com/Jad1908/portia"
claude plugin marketplace add Jad1908/portia
claude plugin install portia@portia
```

Add `snowflake` or `bigquery` inside the brackets for a warehouse. Then start a new Claude
Code session. The repository's `INSTALL.md` is the full guide, written so an agent can
follow it.

Open Claude Code in a folder that holds your data and say what you want from it.

## What it adds

- **The tools**, served by `portia-mcp`. Read-only ones can be allowed once with
  "always allow". The four that write (`record_step`, `record_finding`,
  `set_interpretation`, `set_group`) are worth keeping on "ask". Claude Code's
  prompt is the only confirmation here: portia's app asks before every save, and
  inside Claude Code portia does not, so a writing tool on "always allow" saves
  without asking anyone.
- **A skill**, `portia`, which is the working method portia's own copilot follows.
- **Two hooks.** One holds a reply once when Claude queried the data and did not
  review what it asked, which is how findings get kept. The other refuses Claude's
  file tools on portia's own files and on data portia has indexed, and names the
  portia tool to use. It guards the file tools only. A shell command is not
  refused, so "Claude never reads the raw data" is an instruction here and not a
  guarantee. In portia's app it is a guarantee, because that copilot has no file
  access at all. The same hook refuses `record_step` and `run_spec` in a reply
  that ran `portia index` or `portia connect scope`, until your next message:
  reading new data and building on it are two steps, and you start the second.

## Charts

A chart Claude draws goes to portia's window. If the window is closed the chart
waits on disk and Claude tells you so. Open it with:

```sh
portia ui --project .
```

The window follows the session within a couple of seconds: charts open as tabs,
steps appear on the canvas, findings in the journal, and the session itself is
listed as a read-only chat.

## Settings worth knowing

- **On Haiku, set `ENABLE_TOOL_SEARCH=false`.** Claude Code loads MCP tools on demand by
  default. Driven on 2026-09-19, Sonnet picked portia's tools up that way and Haiku did not: it
  found them, never called one, and went to the shell. With the variable set the same task ran
  through the tools in 10 turns.

- `MCP_TOOL_TIMEOUT` (milliseconds) is how long Claude Code waits for a tool. A
  profile of a very large file can outlast the default.
- Pressing Escape cancels the running tool, and portia cancels the query behind it.
