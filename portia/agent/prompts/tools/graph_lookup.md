<!-- placeholders: {lineage_hops}, {route_hops}, {routes} — agent/graphcards.vocabulary, from knowledge/query.py. -->
**The map between tables.** How one table links to the others, how to get from one table to
another, and where a column came from. The other tools read one table on its own. This one reads
what lies between tables.

Ask it before you start climbing, when you do not yet know which table you need, then use
`describe_source` on the one it points you at. Ask it before you join two tables: a join someone
already made comes back from it.

The graph holds two kinds of record between tables, told apart by what made them:

- **A join**: a join that was actually done, kept when a join check ran (`join_findings`), when a
  step that joins was recorded, or when a finding whose query joins was kept. It has the key
  columns on each side (one or several, matched in order), the relationship, and `repeats`: the
  most rows one key has on that side. A side with `repeats` 1 is one row per key; more means some
  key appears that many times there.
- **A comparison**: two columns compared with `measure_overlaps`, to see whether they share values.
  Nobody has joined on it.

It answers one of four questions per call.

- **`table` alone: its neighbours.** One row per table linked to it: whether this table is
  `built_from` it, whether it `builds` it, the `groups` they share, its joins and its comparisons,
  with `n_joins` and `n_compared` saying how many there are and the card showing a few of each.
  Neighbours a pipeline has gone through (built from, building, joined) come first, then those only
  compared or grouped, each part in name order. The fields of a join row are `join_fields` and of
  a comparison row `compared_fields`, given once above the list.
- **`table` and `neighbour`: everything between two tables.** Every join, with where it was made
  (`found_in`, a step or a finding), and every comparison, with what both columns look like: role,
  how many distinct values, the commonest value or the range.
- **`table` and `column`: that column's lineage.** The columns it was built from and the columns
  built from it, up to {lineage_hops} steps each way with the step that did it, the files its values
  come from, the joins it is a key of, and every column it was compared with.
- **`table` and `to`: the routes.** Every way from one table to the other,
  in at most {route_hops} steps, through joins and through tables built from others, never through
  a comparison. Fewest steps first, then by name, up to {routes} of them. Each step says how its two tables link: built
  from, or joined on which columns, with the repeats on each side. With no route, the answer says
  so and lists what was compared on either table.

Read a row from the side of the table you asked about. `columns`, `repeats` and `coverage` are this
table's: the share of its rows whose key or value is found on the other side; `other_columns`,
`other_repeats` and `other_coverage` the other's. `origin` says what made it: `join_check`, `step`
or `finding` for a join, `measured` for a comparison (a comparison marked `join_check` was kept by
a join check before 2026-10-15). `asked_because` is the sentence written when it was made.

**How to navigate with it.** Start from the table's neighbours: the joins say how they are put
together, and `repeats` says where the grain changes, one row per building on one side and one per
building-hour on the other. Then ask for the route to the table that holds what the project is
about. A comparison is a lead, not a road: two hourly tables share every hour, so their timestamps
cover each other fully, and the repeats show one of them holds each hour once per site.

The numbers are reported, never ranked. A comparison with no shared values is a fact about two
columns, not a verdict on the tables: read its reason, and see `measure_overlaps` on what a zero
means.

A row marked `stale` was taken against a version of a table that has since changed. The number is
what it was, not what it is. A `stale` of null means a fingerprint is missing and nobody can tell.

Where a column's trail stops, `derivation` says why. `unknown`: portia read the step and found no
input column beneath the value, a count or a literal, so it was computed here; follow the `step`
pointer into the spec to see how. `not_traced`: portia could not read the step at all, so the
column may well be carried unchanged from an input and nobody can say which. The model then says
why in `lineage_unread`, at the step `lineage_unread_step`: `pivot` (a PIVOT or UNPIVOT, whose
columns are made from values or from a list of columns), `columns_pattern` (`COLUMNS(...)`, whose
columns depend on the data), `input_not_indexed`, `input_unread` (a table it reads was not traced
either), `input_missing`, `not_parsed`, `not_resolved`, `not_a_query`, `star_unknown`,
`trace_failed`, `unknown_op`, `no_steps` or `no_parser`. Do not guess an origin for either: read
the step in the spec.

Every list is cut at a fixed size. A cut list ends with `more`, which says how many were left out
and the narrower question that reaches them.

Reach for it when:

- you need to combine two tables and do not know what connects them;
- you are about to join two tables, to see whether someone already has;
- someone asks where a number in a built table came from;
- you have changed a source and want to know which models read it.

The graph is stored in a database that may not be running. If it is unavailable, say so once and
work from `describe_source` and `query_data` instead. Everything else still works.
