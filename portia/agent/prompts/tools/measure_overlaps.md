**Do these columns actually share values?** Measures several column pairs at once
and keeps the answers in the knowledge graph, so the next session starts with them
instead of asking again.

**You choose the pairs.** Nothing in portia picks them for you, and that is the
point: which relationships are worth a query is a judgment from *meaning*, and
code comparing ranges would discard `country_name` against `country_code` with
total confidence — the most important pair in a messy project.

Reach for it while you are reading new sources. You have just seen what each one
is and what its columns mean; that is the moment you know which pairs are worth
comparing, and the measurement is cheap. Pick the ones a real question would
depend on — likely keys, columns that look like the same thing under different
names, anything the project description implies should line up.

Each pair takes `left`, `left_column`, `right`, `right_column` and a **`reason`**:
one sentence on why you think these two are related. The reason is required and
it is stored beside the numbers, because of what a zero means:

> A measured zero means **these two columns share no values**. It does **not**
> mean they are unrelated. `France` against `FRA` measures zero and is the same
> thing after a mapping — and columns that need a mapping before they match are
> exactly the harmonization work portia exists for. So a zero on a pair you had a
> good reason to try is a **finding**, not a dead end: it usually means someone
> has to write a mapping. Say that to the user rather than dropping the pair.

What comes back is unranked, and there is no "best" pair. High coverage one way
and low the other is normal and means something specific — read both numbers.
`comparable_types: false` means the two columns are different types and never
match whatever the values are, which is a different problem from a genuine zero.

Do not use it to fish. A pair you cannot give a reason for is a pair you should
not be measuring.

If the graph's database is down the numbers still come back; they just are not
kept. `stored: false` says so.
