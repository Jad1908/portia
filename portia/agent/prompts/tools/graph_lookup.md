**Which table should you be looking at, and where did this column come from.**

This is not a deeper look at one source — it is the map. Ask it *before* you start
climbing, when you don't yet know which of the project's tables is the one you need.
Then use `describe_source` on whatever it points you at.

Give it a **table** (a source name or a model name) and you get its
neighbourhood: what that table reads, what reads it, the groups it belongs to,
and — grouped under each neighbour — every measured overlap with it. Each pair
carries both columns, what each one looks like (role, how many distinct values,
its commonest value or its range), the numbers, and the sentence whoever asked
for it wrote down.

Those column facts are there because they are what makes the number readable. A
column with 4 distinct values sharing nothing with one that has 56 is close to a
non-event. 179 country names sharing nothing with 16 country names is a mapping
job. Both are 'shared 0'.

You get the columns that carry a relationship, not every column. `n_columns`
says how many the table has, so a short list is never a small table. For the
whole list with roles and flags, that is `describe_source`.

**`precedents` is the part to read when you are indexing.** It names columns of
this table that nothing has ever measured, where a column of the same name
somewhere else in the project HAS been measured against something — and it tells
you what against, and what the numbers were. Five tables' `EVENT_ID` compared to
`EVENTS.ID` and yours never checked is the shape of it.

That is not a suggestion and nothing is scoring the match. It is a pairing this
project already committed to, replayed where it has not been applied. Deciding
whether yours is the same thing is still your call, and a column named the same
as another is regularly a different thing. What it saves you is remembering, by
the twelfth source, what you decided at the second.

Give it a table **and a column** and you get that column's lineage: which column
it was derived from, which op did it and at which step, what is built from it
downstream, and the source files its values ultimately come from. Nothing else in
portia can answer that.

A column marked `derivation: unknown` is one whose trail stops there — a count, a
literal, something with no input column beneath it. That is a real answer, not a
gap in the graph: read it as *this value was computed here*, and follow the
`step` pointer into the spec if you need to know how. Do not guess at an origin
for it, and do not report it as coming from a file.

Reach for it when:

- you need to combine two things and don't know what connects to what;
- someone asks where a number in a built table came from;
- you are about to interpret a source and want to know what it already relates to;
- you have changed a source and want to know which models read it.

Nothing here is ordered by how good it looks. Name order, always.

An overlap marked `stale` was taken against a version of a file that has since
changed; the number is what it was, not what it is. A `stale` of null means one
end recorded no fingerprint, so it cannot be told either way.

The numbers on an overlap are reported, never ranked. A measured overlap of zero
means *these two columns share no values* — it does **not** mean they are
unrelated. `France` and `FRA` overlap by zero and are the same thing after a
mapping. If a pair looks meaningful and measures zero, that is a harmonization
job, not a dead end; read the reason recorded on the edge, and climb to
`profile_source` to see the actual values.

The graph is stored in a database that may not be running. If it is unavailable,
say so and work from `get_context` and `describe_source` instead — everything
else still works.
