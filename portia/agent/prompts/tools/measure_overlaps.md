**Do these columns share values?** Compares several column pairs at once and keeps every answer in
the knowledge graph, so the next session starts with them instead of asking again.

**The graph answers first.** A pair it already holds, measured against both tables as they are
now, comes back from it with `from` set to `graph` and the time it was measured, and costs no
query. Only the rest are measured, with `from` set to `query`, and kept.

**You choose the pairs.** Nothing in portia picks them for you, and that is the point: which
relationships are worth a query is a judgment from *meaning*. Code comparing ranges would discard
`country_name` against `country_code` with total confidence, and that is the most important pair in
a messy project.

Reach for it while you are reading new sources. You have just seen what each one is and what its
columns mean, so this is the moment you know which pairs are worth comparing. Pick the ones a real
question would depend on: likely keys, columns that look like the same thing under different
names, anything the project description implies should line up. Ask `graph_lookup` first to see
what is already measured.

Each pair takes `left`, `left_column`, `right`, `right_column` and a **`reason`**: one sentence on
why you think these two are related. The reason is required and it is stored beside the numbers,
because of what a zero means:

> A measured zero means **these two columns share no values**. It does **not** mean they are
> unrelated. `France` against `FRA` measures zero and is the same thing after a mapping, and
> columns that need a mapping before they match are exactly the harmonization work portia exists
> for. So a zero on a pair you had a good reason to try is a **finding**, not a dead end: it
> usually means someone has to write a mapping. Say that to the user rather than dropping the pair.

The answer is a receipt: how many pairs, how many came from the graph, how many were measured, and
one row a pair in the order `fields` names. `left_coverage` is the share of the left table's rows
whose value is found on the right, and `right_coverage` the reverse. `left_distinct` and
`right_distinct` are how many distinct values each column holds, which is what makes a zero
readable. Past a fixed number of rows the list is cut, and `more` says how to read the rest.

What comes back is unranked, and there is no "best" pair. High coverage one way and low the other
is normal and means something specific, so read both numbers. `comparable_types: false` means the
two columns are different types and never match whatever the values are, which is a different
problem from a genuine zero.

Do not use it to fish. A pair you cannot give a reason for is a pair you should not be measuring.

If the graph's database is down the numbers still come back. They are just not kept: `stored` is
false and `not_stored_because` says why.
