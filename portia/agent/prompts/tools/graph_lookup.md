<!-- placeholders: {lineage_hops}, {route_hops}, {routes} — agent/graphcards.vocabulary, from knowledge/query.py. -->
**The map between tables.** How one table links to the others, how to get from one table to
another, and where a column came from. The other tools read one table on its own. This one reads
what lies between tables.

Ask it before you start climbing, when you do not yet know which table you need, then use
`describe_source` on the one it points you at. Ask it before you measure a relationship. Every
measurement portia has taken is kept here, from `measure_overlaps` and from `join_findings`, with
the reason it was taken.

It answers one of four questions per call.

- **`table` alone: its neighbourhood.** One row per table linked to it, in name order: whether this
  table `reads` it, whether it is `read_by` it, the `groups` they share, and the column pairs
  measured between them. A neighbour's pairs are rows in the order `pair_fields` names, given once
  above the list. `n_pairs` says how many pairs there are, and the card shows a few of each.
- **`table` and `neighbour`: everything between two tables.** Every measured pair between them,
  with what both columns look like: role, how many distinct values, the commonest value or the
  range.
- **`table` and `column`: that column's lineage.** The columns it was built from and the columns
  built from it, up to {lineage_hops} steps each way with the step that did it, the files its values
  come from, and every column it was compared with.
- **`table` and `to`: the routes.** Every way from one table to the other, in
  at most {route_hops} steps, through models that read tables and through measured pairs that share
  values. Fewest steps first, then by name, up to {routes} of them. Each step says how its two
  tables link.

Read a pair from the side of the table you asked about. `coverage` is the share of this table's
rows whose value is found in the other column, and `other_coverage` is the reverse. `origin` says
which tool took it: `measured` for `measure_overlaps`, `join_check` for `join_findings`.
`asked_because` is the sentence written when it was taken.

The numbers are reported, never ranked. A pair that shares no values means *these two columns
share no values*. It does **not** mean they are unrelated. `France` and `FRA` share nothing and are
the same thing after a mapping. If a pair looks meaningful and measures zero, that is a
harmonization job, not a dead end. Read its reason, and climb to `profile_source` for the values.

A route goes only through pairs that share values. No route means nothing measured links the two
tables in that many steps. It does not mean they cannot be joined: measure the pair you think links
them.

A pair marked `stale` was taken against a version of a file that has since changed. The number is
what it was, not what it is. A `stale` of null means a fingerprint is missing and nobody can tell.

**`precedents` is the part to read when you are indexing.** It names columns of this table that
nothing has measured, where a column of the same name elsewhere has been measured against
something, and says against what, by which table, with what result. It is not a suggestion and
nothing is scoring the match. It is a pairing this project already made, replayed where it has not
been applied. Whether your column is the same thing is still your call.

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
- you are about to measure a relationship, to see whether it already has been;
- someone asks where a number in a built table came from;
- you have changed a source and want to know which models read it.

The graph is stored in a database that may not be running. If it is unavailable, say so once and
work from `describe_source` and `query_data` instead. Everything else still works.
