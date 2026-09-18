<!-- placeholders: {expect_join}, {expect_normalize}, {expect_sql}, {hows}, {transform_ops},
     {blocking_flags}, {layers} — filled from handlers.step_vocabulary(); the ops and the spec
     format own these lists, not this file. A literal brace in here must be doubled, or
     str.format will eat it. -->
Execute a decided step, measure the table it produces, and append it to a spec: the
re-runnable record of what was done to the data and why.

RECORDING RUNS IT. This is not a save. The step executes before anything is written,
and what comes back tells you what the produced table looks like: row count, any
column that came out entirely null, whether each input put values into the output.
Read that block. A join can match nothing, leave one side's columns null, and still
report exactly the row count you predicted.

THIS IS THE TOOL FOR A DECISION. For a question, use `query_data`, which runs the
same SQL, writes nothing and interrupts nobody. A step that exists to find something
out is a step nobody will run again and everybody has to read.

THEN AUDIT WHAT YOU BUILT. After a step lands, `describe_source` and `profile_source`
open the model by its name. Read the columns you derived, check nothing is constant
or all zero, look at the label's distribution, write the model's summary with
`set_interpretation`, and tell the user what the table is. A layer ends there. Do
not start the next one in the same breath.

## Steps chain, specs chain

A step's output is stored under its 'id'. A later step reads it by naming that id
wherever it names a table: 'left' or 'right' for a join, 'input' for a normalize, in
the 'inputs' list for a sql step. That is how multi-hop work is built: join A to B,
then join THAT result to C.

A sql step names its inputs twice and both must match. What is in 'inputs' is what
exists as a table in the query. Declare ['chicago_prepared'] and write FROM
chicago_prepared. Naming it '<spec path>#<step id>' also works and is reduced to the
bare id in both places.

Specs read each other by plain name. To use another spec's table, name it:
'left': 'stg_orders'. No path, no dependency list. Portia finds the spec that
produces that name and works out the run order. The same name works in
`join_findings`, `profile_source` and `query_data`, so you can measure a model
before you build on it.

Never re-inline earlier work into a later step. Four copies of one expression drift.

## One spec builds one table

A spec produces exactly one table, named after its file, and compiles to one .sql
file someone will read. Its steps are the working-out inside that one query.

So decide, before you record:

- A NEW SPEC when the table is something a person would ask for by name, or more
  than one downstream table needs it.
- A NEW STEP in the spec you are writing when it only exists to get to that spec's
  table.

Say which you chose and why in the 'rationale'. Read a spec with `read_spec` before
you add to one you did not write this turn; every spec is listed under Pipeline in
your brief, and a step id is never something to guess. Model names are unique across
the project; name the table for what it IS.

## The step

    {{"id": "orders_with_customers", "op": "join",
      "left": "orders", "right": "customers", "keys": ["customer_id"], "how": "left",
      "grain": ["order_id"],
      "expect": {{"result_rows": 10, "left_dropped": 0}},
      "rationale": "left, not inner: 2 orders reference customers missing from the
                    dimension table. An inner join would silently drop real orders."}}

'op' is 'join', 'normalize' or 'sql'.

join: 'left', 'right', 'keys' (or 'left_on' and 'right_on' when the names differ),
'how' as one of {hows}. It is 'keys', not 'on'; 'on' is a reserved boolean in YAML.

normalize: 'input', and 'transforms' as a list of {{"column": <name>, "op": <one of:
{transform_ops}>}}. Element-wise: values change in place and the row count cannot.

sql: 'inputs', the tables the query reads, and 'sql', one SELECT over them.

## 'sql' is for the work join and normalize cannot express

Aggregating to a coarser grain, deduplicating, filtering rows, deriving a column,
defining a label, a window. Write it as SQL rather than approximating it; a
normalize with an empty transforms list does nothing however the rationale reads.

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

Every table the query names must be in 'inputs'. Only those are visible to it.
Written in the dialect the brief names under Where the data lives, one SELECT (or
WITH ... SELECT). Reading files, writing, attaching, stages and extensions are refused.

Prefer join and normalize where they fit: a join reports what it dropped from each
side, and a sql step only reports the shape of what came out.

## 'expect' predicts only what the op reports

These are the fields each op measures, and the only ones 'expect' may name. An
expectation on anything else is rejected, because it would drift forever.

  join: {expect_join}
  normalize: {expect_normalize}
  sql: {expect_sql}

Predict the value the op will report, in its shape, from what the checks told you.

## 'grain' says what one row is

A list of output columns naming what one row is meant to be. You claim it, the
engine measures it at any arity, and a claim that does not hold blocks the step and
shows you the duplicated keys. Declare it whenever a table has a grain; for a
training table, always.

Claim the grain the work needs, decided before you see the result. If a join
multiplies rows, fix the multiplication or raise it with the user. Widening the
claim until it passes verifies a tautology.

## What refuses to be written

A step whose output hits a zero is not recorded: {blocking_flags}. None is a
threshold. Fix the step and record it again.

If a zero is genuinely intended, add 'acknowledge': ['<flag>'] with a 'rationale'
saying why, after telling the user what the zero means in their terms: how many
rows, which figures move, what a total would be off by.

'empty_output' is the exception, because a filter that matches nothing has told
you something true. If zero rows would be a legitimate result of the table you are
building, declare 'acknowledge': ['empty_output'] up front and it records either
way. That is a claim that zero is plausible, not a prediction. If you were asking a
question rather than building a table, do not record at all; use `query_data`.

## Correcting a step you already recorded

Pass 'supersedes' with the id of the step this one replaces. Keep the same id where
you can: everything reading it goes on reading it, and replacing a step another one
reads under a new name is refused.

    {{"spec_path": "specs/training.yaml",
     "supersedes": "hotel_join",
     "step": {{"id": "hotel_join", "op": "join", ..., "rationale": "was joining on NAME, which
              is not unique across countries. Joining on the id pair instead."}}}}

A replacement is a real recording: the whole spec re-runs with it in place and the
same gate applies. The version you replaced stays in git history.

Do not supersede a step to make a wrong prediction look right. A wrong 'expect' is
something to say out loud.

## 'layer', and the stop it marks

If this project is organised in layers, set 'layer' on the spec's first step, as one
of {layers}. It describes the table, not the step.

  staging       one lightly-cleaned copy per raw source. Names, types, whitespace,
                dates. Nothing joined, nothing aggregated, no label.
  intermediate  combinations on the way to an answer: joins, aggregates, labels.
  mart          the tables people actually query, at the grain the model needs.

These are kinds, not ranks. A staging table is not a lesser mart table.

A layer is also where you stop. Build the staging tables, audit each one, tell the
user what they hold, and wait for them before the intermediate layer. Then the same
again. A whole pipeline recorded in one run is a pipeline nobody has looked at, and
in one real session that is how a mart shipped with every row of one city at zero.

Many projects do not need layers. Two sources and one join is a flat project: leave
'layer' out and every model sits in one folder. That is the normal case. Propose
layers when several raw sources each need their own cleanup, or when more than one
table reads the same intermediate result. Ask before committing the repo to a shape.
The stop after each table applies either way.

## 'target', on a warehouse

On a warehouse project, set 'target' on the spec's first step: 'DATABASE.SCHEMA',
where this table is created. It is the spec's, like 'layer', and yours to choose per
table: different tables can go different places, and nothing defaults it. Which
database and which schema, and whether recording creates the table now or the user's
Run does, is in the brief's *Where the data lives*. A spec keeps one target; to move
a table, the user edits the file.
