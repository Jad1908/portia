**Which table should you be looking at, and where did this column come from.**

This is not a deeper look at one source — it is the map. Ask it *before* you start
climbing, when you don't yet know which of the project's tables is the one you need.
Then use `describe_source` on whatever it points you at.

Give it a **table** (a source name or a model name) and you get tables back: what
that table reads, what reads it, the groups it belongs to, and which other tables
have a measured overlap with it. Deliberately not a list of column pairs — the
point is to narrow the field, not to dump the neighbourhood.

Give it a table **and a column** and you get that column's lineage: which column
it was derived from, which op did it and at which step, what is built from it
downstream, and the source files its values ultimately come from. Nothing else in
portia can answer that.

Reach for it when:

- you need to combine two things and don't know what connects to what;
- someone asks where a number in a built table came from;
- you are about to interpret a source and want to know what it already relates to;
- you have changed a source and want to know which models read it.

The numbers on an overlap are reported, never ranked. A measured overlap of zero
means *these two columns share no values* — it does **not** mean they are
unrelated. `France` and `FRA` overlap by zero and are the same thing after a
mapping. If a pair looks meaningful and measures zero, that is a harmonization
job, not a dead end; read the reason recorded on the edge, and climb to
`profile_source` to see the actual values.

The graph is stored in a database that may not be running. If it is unavailable,
say so and work from `get_context` and `describe_source` instead — everything
else still works.
