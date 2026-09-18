One spec as it is recorded, without running it: the sources it declares, every step verbatim
(op, inputs, SQL, transforms, expect, rationale, grain, acknowledge), which other models it reads
and which read it, and whether its compiled .sql is current.

Cheap. It reads a YAML file. `run_spec` re-executes every model behind a spec to measure it, so
read first and run only when you need today's numbers.

Every spec in the project is listed under **Pipeline** in your brief, so you never have to ask the
user which specs exist or where they are. Reach for this:
- before you add a step to a spec you did not write this turn — the step ids you may chain from and
  the sources it already declares are in here, and a step id is never something to guess;
- when the user asks what the pipeline does, or why a table looks the way it does;
- before naming a model as an input, to see what it produces and what it already reads.

The base answer always says how many findings sit under this spec and when its table was last
built. Two things ride along on request, because their absence should never read as "nothing":
`journal: true` adds what was asked on the way to this table, oldest first, and `measured: true`
adds what the last build measured about the table it produces, per column, from the catalog.

It is not on the disclosure ladder. The rungs answer questions about the data; this answers what
we decided to do to it, and nothing in it measures today's data. For that, run it or query it.
