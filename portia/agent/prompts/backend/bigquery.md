<!-- placeholders: {label}, {scope}, {writes} — the last is backend/agent_writes.md or
     backend/build_writes.md, filled by agent/context.py from the project's hand-off setting -->
## Where the data lives

This project runs on **BigQuery**, through the connection **{label}**. Nothing is copied to this
machine: every check, every `query_data` and every `sql` step is a query on Google's side, and only
capped results come back. Four things follow.

**Every question is billed by the bytes it scans, not by the time it takes.** A `LIMIT` does not
reduce the bill; naming columns does, because BigQuery stores columns apart and reads only the ones
a query touches. So `SELECT *` on a wide table costs the whole table however many rows you keep.
Name the columns you need, prefer `describe_source` (free, it reads the catalog) over
`profile_source` (a scan of every column), and one `query_data` that answers the question over
three that circle it. Do not re-run a query to check a number you already have. Recording a step
re-runs the whole spec to measure it, so record when you have decided, not to see what happens.

**A scoped table indexes as metadata, not as a profile.** `describe_source` on a table nobody has
profiled says `profiled: false` and gives a row count and a byte count from the table's own
metadata; the byte count is what a full scan of it would bill. The column facts arrive when you or
the user call `profile_source`. Say what it will cost before you do it on a large table. A
profile's three quartiles are **approximate** here (`APPROX_QUANTILES`) and the column says so
under `approximate`; repeat them as estimates, never as exact values.

**The credentials are the boundary, and the scope is the study.** Your SQL runs as the account the
connection signed in with, over the projects it can read. A refusal from BigQuery that names
permissions means that account cannot see the table; say so and stop, rather than trying another
spelling. Only the tables in this project's scope are sources — {scope} — and a `sql` step may
name only the inputs it declares. A table outside the scope is not a source even if the account
could read it; if the user wants it, they add it to the scope from the window or with
`portia.cli.connect scope`. What portia calls the *database* is the **project** and what it calls
the *schema* is the **dataset**: a table's full name is `project.dataset.table`, and a spec's
`target` is `project.dataset`.

**Write GoogleSQL.** `COUNTIF`, `SAFE_CAST`, `APPROX_QUANTILES`, `DATE_TRUNC`, `IF`, `STRING`,
`INT64`, `FLOAT64`, `TIMESTAMP`. **Quote identifiers with backticks**, never double quotes: a
double-quoted token is a string literal here, so `"id"` is the text *id* and not the column.
Dataset and table names are case-sensitive and are written exactly as the index shows them; column
names are matched without case. A join across datasets in different locations fails, so keep a
spec's inputs in one location. `FILTER (WHERE …)`, `ORDER BY ALL`, `try_cast` and `count_if` are
other engines' and will not parse here.

{writes}
