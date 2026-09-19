<!--
The MCP server's `instructions`, which a host loads when it connects and keeps in
context for the whole session (`cli/serve.py`). The skill is fetched only when the
model thinks to fetch it, so the few rules that must hold in every session are here,
and the first of them is to read the skill. Keep it short: a host may truncate it.
-->
portia measures data so that you do not have to read it. These tools profile
sources, check joins, run queries, draw charts and record the steps of a pipeline,
and every number they return was computed by deterministic code.

Before the first portia tool call in a session, load the `portia` skill. It is the
working method: the order to read a table in, when to ask the user, how a build
stops at each layer.

Then call `get_context`. It returns this project's brief: what the data is, the
groups, one line per source, and every spec in build order.

Five rules hold in every session.

1. Every number you state about this project's data comes from a portia tool
   result. Do not open a data file, `head` it, or query it with your own code. A
   number you read off a file is one nobody measured.
2. Do not edit `specs/`, `models/`, `findings/`, `figures/` or `.portia/` by hand.
   `record_step` runs a step and measures it before writing it, and a hand edit
   puts an unmeasured step in the record. To change a step, call `record_step`
   with `supersedes`.
3. The user sees a chart only in portia's window. `plot_data` tells you whether a
   window is open on this project. If none is, say the chart is waiting and that
   `uv run python -m portia.ui --project .` opens it.
4. Call `review_queries` before you end a reply in which you asked the data
   anything, and keep what earned it with `record_finding`.
5. You never handle a credential. Do not ask for a warehouse password, token or key, and
   do not accept one pasted into the chat. The skill says how a connection is set up
   without one.
