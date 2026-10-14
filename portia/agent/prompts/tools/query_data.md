Ask the data one question. Runs one SELECT over the tables you name and hands back the answer.
It writes nothing anyone will read: the whole result is kept for this chat, under the name the
answer gives it, and goes when the chat does.

THIS IS THE TOOL FOR A QUESTION. `record_step` is the tool for a decision. How many
rows match, what an intermediate join actually produces, whether a value exists,
what a match rate looks like at two tolerances, how the label splits: ask here.
Nothing is recorded, nothing is gated, nobody is interrupted, and ten of them is fine.

A ZERO IS AN ANSWER HERE. `n_rows: 0` means there are none, and that is often the
thing you were asked to find out. Say so. Do not go looking for a looser query.

## When to reach for it

After the ladder, not instead of it. `describe_source` says what a column means and
`profile_source` gives you its numbers; both are cheaper and answer most questions
about ONE table. Come here for a combination: two tables joined, a filter applied,
an aggregate over a subset, a candidate label's row counts side by side. Use it
freely before you decide anything.

## The call

    {{"question": "are there rank-100 events in Paris in summer 2025?",
     "inputs": ["EVENTS", "EVENT_PLACES"],
     "sql": "SELECT count(*) AS n FROM EVENTS e
             JOIN EVENT_PLACES p ON e.ID = p.EVENT_ID
             WHERE e.RANK = 100 AND p.NAME = 'Paris'
               AND e.START_DATE BETWEEN '2025-06-01' AND '2025-08-31'"}}

'inputs' is every table the query reads, by name: an indexed source, a model in
this project, '<spec>#<step id>' for a table an earlier step produced, or a result
this chat kept. The query runs on a connection holding exactly those and nothing else.

'sql' is one SELECT. Not two statements, not a write, not a file read. Quote a
column name that has spaces or mixed case with double quotes, exactly as the
catalog spells it.

'question' is required: one sentence saying what you want to find out.

## What comes back

    {{"result": "portia_result_kxbt", "n_rows": 89, "n_columns": 3,
     "shown": "1-20", "fields": ["city", "events", "share"],
     "rows": [["Paris", 574, 0.26], ["Lyon", 112, 0.05]],
     "more": "Rows 1-20 of 89. For the next ones ..."}}

'n_rows' and 'n_columns' are the whole result. 'fields' names the columns once, and
each row is a list of values in that order. Your question, SQL and inputs are not
sent back.

EVERY CUT SAYS SO. 'more' says how many rows were left out and how to go on.
'more_columns' does the same past the first 50 columns, and 'columns_shown' says
which ones you have. 'cut' names the columns where a long value was cut short. Read
those before you treat the rows as the whole answer.

'result' is the name this result is kept under, for this chat. Three uses:

- Another page. Call again with the same sql and inputs and an 'offset' (rows) or a
  'column_offset' (columns). It reads the kept copy, says so in 'paged', and nothing
  runs again: on a warehouse that is one bill instead of one per page.
- A question about the result. Put its name in 'inputs' and write a SELECT over it.
  A WHERE, a count or a GROUP BY over 89 rows you already have beats paging through
  them, and it does not touch the tables again.
- The review. `review_queries` lists each question beside its result's name.

When 'result' is null the result was too big to keep, and 'not_kept' says why. Then
another page runs the query again, and a count, a filter or fewer columns is the
better question.

A result's name and the question's number in `review_queries` are two different
things. Cite a question by its number in `record_finding`; read a result by its name.

Change the arguments, not the query. 'limit' (default 20) and 'offset' apply on top
of whatever your SQL says; a SELECT rewritten with its own OFFSET is a new question
and runs again. A big 'limit' gets as many whole rows as fit in one answer, and
'more' says where it stopped.

## portia's own records, as tables

Two more names work in 'inputs'. `portia_columns` is one row per column of every
source and model: {portia_columns}. `portia_tables` is one row per source and
model: {portia_tables}. They name things as `information_schema` does: a table is
`table_name` in both, a column is `column_name`. They hold the catalog's facts, never
the data, and answer what one `describe_source` per table cannot: which columns
anywhere are mostly null, which tables have a column named like `%_id`, which tables
changed since they were indexed. `flags` is a list, so ask
`list_contains(flags, 'high_null')`.

They, and the results this chat kept, run in DuckDB's SQL on this machine, whatever
the project's engine. On a warehouse project a query reads them or the warehouse's
tables, never both.

## What it does not do

It does not write to the spec, so nobody will run this query again. If the answer
changed what you built, or is something the next person on these tables needs,
keep it with `record_finding` from `review_queries` before you reply. If the
answer decided a transformation, build that with `record_step`.
