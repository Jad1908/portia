<!-- placeholders: {source} -->
Interpret the source {source!r} and place it among the ones already here.

Read the project context and this source's facts, then record what the data is
with `set_interpretation`: a summary, a role for every column, and a `note` for
anything a person building on it has to know first. Read it in the order the
system prompt gives: grain, keys, the outcome column and what its values actually
are, time coverage, nulls, categories, amounts, duplicates.

Then say how it relates to the rest of the project. Ask `graph_lookup` what is
already indexed, look for columns here that might be the same thing as a column
elsewhere, and measure those pairs with `measure_overlaps`, giving the reason you
picked each one. Pick pairs you can justify and do not sweep. Treat a zero as a
result: two columns that share no values are often the same thing needing a
mapping first.

Build nothing. Ask me only if the project context leaves something genuinely
ambiguous that would change what you write.
