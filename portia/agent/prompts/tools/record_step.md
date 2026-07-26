<!-- placeholders: {expect_join}, {expect_normalize}, {expect_sql}, {hows}, {transform_ops},
     {blocking_flags} — filled from handlers.step_vocabulary(); the ops own these lists, not this
     file. A literal brace in here must be doubled, or str.format will eat it. -->
Execute a decided step, measure the table it produces, and append it to the spec — the durable,
re-runnable record of what was done to the data and why.

RECORDING RUNS IT. This is not a save: the step is executed before anything is written, and the
result tells you what the produced table actually looks like — row count, any column that came out
entirely null, and whether each input source actually put values into the output. Read that block.
A join can match nothing, leave every column from one side null, and still report exactly the row
count you predicted; the numbers agreeing is not the same as the table being right.

STEPS CHAIN: a step's output is stored under its 'id', and a later step may name that id as its
'left', 'right' or 'input' to receive the resulting table. That is how multi-hop work is built —
join A to B, then join THAT result to C. You do not need an external tool for this. Naming it
'<spec path>#<step id>' works too and is the same thing — that is the form the read-only checks
need, so one habit works everywhere.

## The step

A complete one, for shape:

    {{"id": "orders_with_customers", "op": "join",
      "left": "orders", "right": "customers", "keys": ["customer_id"], "how": "left",
      "grain": ["order_id"],
      "expect": {{"result_rows": 10, "left_dropped": 0}},
      "rationale": "left, not inner: 2 orders reference customers missing from the
                    dimension table. An inner join would silently drop real orders."}}

'op' is 'join', 'normalize' or 'sql'.

join fields: 'left', 'right', 'keys' (or 'left_on' and 'right_on' when the key columns are named
differently), and 'how', one of: {hows}. Note 'keys', not 'on' — 'on' is a reserved boolean in YAML.

normalize fields: 'input', and 'transforms' as a list of
{{"column": <name>, "op": <one of: {transform_ops}>}} — the key is 'op', not 'transform'.
These are element-wise column transforms: they change values in place and cannot change the number
of rows. Use them for cleaning, not reshaping.

sql fields: 'inputs', the list of tables the query reads, and 'sql', one SELECT over them.

## 'sql' — the step for work the other two ops can't express

join and normalize cover the common cases. When the work needs something they cannot do —
**aggregating to a coarser grain**, deduplicating, filtering rows, deriving a column — write it as
SQL rather than approximating it. A normalize step with an empty 'transforms' list does not become
an aggregate because the rationale says it does; that is a step that does nothing, and it will be
recorded as having done nothing.

    {{"id": "events_per_city_date", "op": "sql",
      "inputs": ["city_events"],
      "sql": "SELECT city_name, event_date, COUNT(*) AS n_events,
                     SUM(expected_attendance) AS total_attendance
              FROM city_events GROUP BY 1, 2",
      "grain": ["city_name", "event_date"],
      "expect": {{"result_rows": 5}},
      "rationale": "Collapse events to one row per city-date so the join to bookings
                    cannot fan out. Loses individual event names, which the user
                    agreed to trade for a table at booking grain."}}

Every table the query names must be listed in 'inputs' — indexed sources or earlier step ids, the
same names you would use anywhere else. Only those are visible to the query, so a table you forgot
to declare is an error rather than a silent dependency.

DuckDB dialect, and it must be a single SELECT (or WITH … SELECT). Reading files, writing files,
attaching databases and installing extensions are all refused: a step reads the tables it declares
and nothing else. You have no filesystem access, and this is not a way to get some.

Prefer join and normalize where they fit. They report far more about what happened — a join tells
you what it dropped from each side; a SQL step can only tell you the shape of what came out.

## 'expect' — predict only what the op actually reports

These are the only fields each op measures, and therefore the only ones an 'expect' block may
name. An expectation on anything else is rejected, because it would drift on every run forever and
teach everyone to ignore drift.

  join: {expect_join}
  normalize: {expect_normalize}
  sql: {expect_sql}

Predict the *value the op will report*, matching its shape — 'transforms' is the list of transform
records, not a count of them. Base every figure on what the check told you, not on what you hope.

## 'grain' — state what one output row is

A list of output columns naming what one row of the result is meant to be. You state the claim;
the engine measures whether the table is really unique on it and shows you the duplicated keys if
not. Declare it whenever a step is meant to produce a table at a particular grain — for a training
table, always.

Claim the grain the WORK needs, decided before you see the result. If a join multiplies rows, the
answer is to fix the multiplication or to raise it with the user — never to widen the claim until
it passes. A grain of "every column that makes the duplicates unique" is trivially true and
measures nothing; you have then verified a tautology and reported it as a clean table.

## What refuses to be written

A step whose output hits a zero is not recorded: {blocking_flags}. None of these is a threshold —
they are zeros, so there is nothing to weigh. Fix the step and record it again.

If a zero is genuinely intended, add 'acknowledge': ['<flag>'] with a 'rationale' saying why. Tell
the user what the zero means for their data *in their terms* — how many rows, which figures move,
what a total would be off by — and get their answer before you acknowledge it. "Accept the
multiplication" is not an informed answer if they were never told it double-counts revenue.

STEPS ARE APPEND-ONLY. A recorded step cannot be revised and its id cannot be reused. If a
prediction turns out wrong, that is a finding to report, not an id to bump.
