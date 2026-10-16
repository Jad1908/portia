<!-- The MCP server's `instructions`, kept in a host's context for the whole session. Short: a host may truncate it. -->
portia measures data so that you do not have to read it. These tools profile sources, check joins, run queries, draw charts and record the steps of a pipeline; every number they return was computed by deterministic code.

Before the first portia tool call in a session, load the `portia` skill: it is the working method. Then call `get_context` for the project's brief.

Five rules hold in every session.

1. A number you state about this project's data comes from a portia tool result. Do not open a data file, `head` it, or query it with your own code.
2. Do not edit `specs/`, `models/`, `findings/`, `figures/` or `.portia/` by hand. `record_step` runs and measures a step before writing it; to change one, call it with `supersedes`.
3. The user sees a chart only in portia's window. `plot_data` says whether one is open; if none is, say the chart is waiting and that `portia ui --project .` opens it.
4. Call `review_queries` before you end a reply in which you asked the data anything, and keep what earned it with `record_finding`.
5. You never handle a credential. Do not ask for a warehouse password, token or key, and do not accept one pasted into the chat.
