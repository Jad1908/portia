<!-- placeholders: {spec_steps}, {spec_findings}, {finding_queries}, {finding_chars} — filled from
     handlers.spec_vocabulary(). A literal brace in here must be doubled, or str.format will eat it. -->
One spec as it is recorded, without running it: its card. Cheap: it reads a YAML file and the
catalog. `run_spec` re-executes every model behind a spec to measure it, so read first and run only
when you need today's numbers.

Every spec in the project is listed under **Pipeline** in your brief, so you never have to ask the
user which specs exist or where they are. Reach for this:
- before you add a step to a spec you did not write this turn: the step ids you may chain from and
  the sources it already declares are on the card, and a step id is never something to guess;
- when the user asks what the pipeline does, or why a table looks the way it does;
- before naming a model as an input, to see what it produces and what it already reads.

The card gives the spec's layer and target, the sources it declares, the models it reads and the
models that read it, whether its compiled .sql is current and when its table was last built. Then
one row per step: its id, op and inputs, and the n_rows, n_cols, flags and drift that the last
recording or build measured. Those four are null when no run has measured this version of the spec,
and `steps_note` says why. Last come the findings recorded for this spec, listed by question, oldest
first. A list is `fields` once and then a row each. A list cut short says in `more` how many were
left out. A card lists up to {spec_steps} steps and the latest {spec_findings} findings.

Three narrower reads:
- `step`: one step in full, verbatim: its SQL or transforms, expect, rationale, grain and
  acknowledge, which later steps read it, and its last measured shape.
- `finding`: one finding in full, by the path the card lists. It gives the question, answer and so,
  the tables it is about and which of them changed since, and up to {finding_queries} queries it
  rests on, each with its SQL and its result up to {finding_chars} characters.
- `match`: only the steps whose id matches a pattern, * and ? as in a shell.

`measured: true` adds the built table's row and column counts, its columns counted by type, and
every flagged column named. For the columns themselves, call `describe_source` or `profile_source`
on the model's name: it is an indexed table like any source.

It is not on the disclosure ladder. The rungs answer questions about the data; this answers what we
decided to do to it, and nothing in it measures today's data. For that, run it or query it.
