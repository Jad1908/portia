<!-- placeholders: {label}, {scope}, {writes} — the last is backend/agent_writes.md or
     backend/build_writes.md, filled by agent/context.py from the project's hand-off setting -->
## Where the data lives

This project runs on **Snowflake**, through the connection **{label}**. Nothing is copied to this
machine: every check, every `query_data` and every `sql` step is a query on the warehouse that
connection names, and only capped results come back. Three things follow.

**Every question spends credits.** The warehouse is someone's meter. Ask what you need and no more:
prefer `describe_source` (free, it reads the catalog) over `profile_source` (a scan of the whole
table), and one `query_data` that answers the question over three that circle it. Do not re-run a
query to check a number you already have. Recording a step re-runs the whole spec to measure it,
so record when you have decided, not to see what happens.

**A scoped table indexes as metadata, not as a profile.** `describe_source` on a table nobody has
profiled says `profiled: false` and gives a row count from the information schema. The column
facts arrive when you or the user call `profile_source`, which scans the table once and is then
remembered. Say what it will cost before you do it on a large table, and ask if the user would
rather read a smaller one first.

**The role is the boundary, and the scope is the study.** Your SQL runs with the role the
connection carries. A refusal from the warehouse that names permissions means the role cannot see
that table; say so and stop, rather than trying another spelling. Only the tables in this
project's scope are sources — {scope} — and a `sql` step may name only the inputs it declares. A
table outside the scope is not a source even if the role could read it; if the user wants it, they
add it to the scope from the window or with `portia.cli.connect scope`.

**Write SQL in Snowflake's dialect.** `count_if`, `percentile_cont(...) WITHIN GROUP`, `iff`,
`date_trunc`, `try_cast`. An identifier you leave unquoted **folds to upper case**; the catalog
names columns as the warehouse reports them, and it is safest to write them as the index shows
them. `FILTER (WHERE …)` and `ORDER BY ALL` are DuckDB's and will not parse here.

{writes}
