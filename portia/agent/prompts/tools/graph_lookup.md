<!-- placeholders: {lineage_hops}, {route_hops}, {routes} -->
How tables connect: the joins made between them, the columns compared, what a table is built from, and where a column came from. The other tools read one table; this one reads what lies between tables.

Use it when the question is which table, how two tables relate, or whether a join has already been made. On a new project it holds little. It fills as join checks, steps and findings are recorded, and in a project you have worked in before it is the first thing to read.

One question per call:

- `table`: its neighbours, one row per linked table, with `n_joins` and `n_compared` and a few of each.
- `table` and `neighbour`: every join and comparison between the two, with where each was made.
- `table` and `column`: the column's lineage, each way, with the step that did it.
- `table` and `to`: the routes between two tables, through joins and built-from links, fewest steps first.

Lineage is followed {lineage_hops} steps each way. A route is at most {route_hops} steps, and up to {routes} of them come back.

Reading a row: `columns`, `repeats` and `coverage` belong to the table you asked about, `other_*` to the other side. `repeats` is the most rows one key has on that side, which is where the grain changes between two tables. A join is one someone made (a join check, a step, a finding). A comparison only says two columns share values, and no route goes through one. `asked_because` is the reason written when it was made. `stale` on a row means the measurement predates a change to the table. Where a lineage stops, `derivation` says why: `unknown` is a value computed in the step (a count, a literal), `not_traced` a step portia could not read, with the reason in `lineage_unread` as one of `no_parser`, `not_parsed`, `not_a_query`, `pivot`, `columns_pattern`, `not_resolved`, `star_unknown`, `trace_failed`, `input_not_indexed`, `input_unread`, `input_missing`, `unknown_op`, `no_steps`. Read the step in the spec rather than guessing an origin.

If the graph's database is not running, say so once and go on with the other tools.
