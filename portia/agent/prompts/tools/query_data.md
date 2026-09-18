Ask the data one question. Runs one SELECT over the tables you name, hands back the
answer, and writes nothing anywhere.

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

    {"question": "are there rank-100 events in Paris in summer 2025?",
     "inputs": ["EVENTS", "EVENT_PLACES"],
     "sql": "SELECT count(*) AS n FROM EVENTS e
             JOIN EVENT_PLACES p ON e.ID = p.EVENT_ID
             WHERE e.RANK = 100 AND p.NAME = 'Paris'
               AND e.START_DATE BETWEEN '2025-06-01' AND '2025-08-31'"}

'inputs' is every table the query reads, by name: an indexed source, a model in
this project, or '<spec>#<step id>' for a table an earlier step produced. The query
runs on a connection holding exactly those and nothing else.

'sql' is one SELECT. Not two statements, not a write, not a file read. Quote a
column name that has spaces or mixed case with double quotes, exactly as the
catalog spells it.

'question' is required: one sentence saying what you want to find out.

## What comes back

'n_rows' is how many rows the query produced. 'rows' is as many as you asked for,
20 by default, so a sample is never mistaken for the whole answer.

To see more, change the arguments, not the query. 'limit' takes more rows,
'offset' skips to the next page, and both apply on top of whatever your SQL says.
A big 'limit' on a wide result is refused for size; select fewer columns, not fewer
rows. If you want a number rather than rows, ask for one: count, sum and group by
are usually the better question anyway.

## What it does not do

It does not write to the spec, so nobody will run this query again. If the answer
changed what you built, or is something the next person on these tables needs,
keep it with `record_finding` from `review_queries` before you reply. If the
answer decided a transformation, build that with `record_step`.
