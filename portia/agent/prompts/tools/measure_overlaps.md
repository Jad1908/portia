Whether pairs of columns share values. Several pairs in one call, each kept in the graph so the next session starts with them.

Use it when you are reading new sources and can say why two columns might be the same thing: a key under two names, a code and its label, anything the project description implies should line up. Each pair takes `left`, `left_column`, `right`, `right_column` and a `reason`. A pair the graph already holds, measured against the tables as they are now, comes back from it (`from: graph`) without a query; only the rest are measured.

One row per pair. `left_coverage` is the share of left rows whose value is found on the right, `right_coverage` the reverse; `*_distinct` and `*_repeats` make a zero readable. Two tables can cover each other fully on a date column while the repeats show that one holds each date once and the other once per entity. `comparable_types: false` means the types never match whatever the values.

A measured zero means the columns share no values now, not that they are unrelated: `France` against `FRA` measures zero and is one thing after a mapping. A comparison is not a join and no route goes through it; to join two tables, check the join with `join_findings`.
