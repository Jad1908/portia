<!-- placeholders: {source} -->
Interpret the source {source!r}, and place it among the ones already here.

Read the project context and this source's facts, then record what the data is: a
prose summary and a role for every column, with `set_interpretation`.

Then say how it relates to the rest of the project. Ask `graph_lookup` what is
already indexed, look for columns in this source that might be the same thing as a
column elsewhere — a shared key, an entity under a different name, a code against
its label — and measure those pairs with `measure_overlaps`, giving the reason you
picked each one.

Pick pairs you can justify; do not sweep. And treat a zero as a result rather than
a dead end: two columns that share no values are often the same thing needing a
mapping first, which is exactly the work worth surfacing.

Ask me only if the project context leaves something genuinely ambiguous that would
change what you write.
