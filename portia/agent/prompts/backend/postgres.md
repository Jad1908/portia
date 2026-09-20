<!-- placeholders: {label}, {scope}, {writes}. The last is backend/agent_writes.md or
     backend/build_writes.md, filled by agent/context.py from the project's hand-off setting -->
## Where the data lives

This project runs on **PostgreSQL**, through the connection **{label}**. Nothing is copied to this
machine: every check, every `query_data` and every `sql` step is a query on that server, and only
capped results come back. Four things follow.

**There is no bill, and the server is probably doing something else.** A Postgres database usually
sits behind an application, and a query that scans a large table takes disk and CPU from it.
Nothing here can tell whether this server is a laptop, a read replica or production, so ask the
user once, early, and work accordingly. Prefer `describe_source` (free, it reads the catalog) over
`profile_source` (a scan of every column), and one `query_data` that answers the question over
three that circle it. Do not re-run a query to check a number you already have. Recording a step
re-runs the whole spec to measure it, so record when you have decided, not to see what happens.

**A scoped table indexes as metadata, not as a profile.** `describe_source` on a table nobody has
profiled says `profiled: false` and gives a row count. That count is the **planner's estimate**
from the last `ANALYZE`, and the entry says so under `approximate`; repeat it as an estimate, and
get the exact one from `count(*)` when it matters. The column facts arrive when you or the user
call `profile_source`. Say how large the table is before you do it on a large one.

**The role is the boundary, and the scope is the study.** Your SQL runs as the role the connection
signed in with. A refusal from the server that names permissions means the role cannot see that
table; say so and stop, rather than trying another spelling. The sources are the tables in
this project's scope and nothing else: {scope}. A `sql` step may name only the inputs it declares.
A table outside the scope is not a source even if the role could read it; if the user wants it, they add
it to the scope from the window or with `portia.cli.connect scope`. **A connection reaches one
database.** A table's full name is `database.schema.table` and the first part is always this
connection's database, in a source and in a spec's `target` alike. Data in another database needs
its own connection and its own project.

**Write PostgreSQL.** `count(*) FILTER (WHERE …)`, `percentile_cont(…) WITHIN GROUP (ORDER BY …)`,
`date_trunc`, `::` casts, `text`, `double precision`, `CASE` where another engine has `iff`. An
identifier you leave unquoted **folds to lower case**, so a column the index shows as `ORDER_ID`
must be written `"ORDER_ID"`, in double quotes, every time. Write every column as the index shows
it. There is no `try_cast`: on version 16 and later `pg_input_is_valid(value, 'double precision')`
says whether a cast would work, and on an older server a pattern match has to. `ORDER BY ALL`,
`count_if` and `QUALIFY` are other engines' and will not parse here. Where the paragraph below
says `CREATE OR REPLACE TABLE`, this server drops the old table and creates the new one in one
transaction, and a view somebody built on the old table makes that fail in the server's words.
Lower-case schema names are the convention here: `staging`, `intermediate`, `mart`.

{writes}
