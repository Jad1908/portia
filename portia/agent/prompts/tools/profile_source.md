<!-- placeholders: {profile_cap}. Filled from agent/handlers.card_vocabulary. -->
Measured facts about a table's columns: per column, its type, null rate, distinct count, min,
quartiles, max, mean, standard deviation, commonest value and how often it occurs, three sample
values, and quality flags.

The rung for numbers. Call it when you need them: interpreting a source, judging whether a key is
usable, quantifying a problem you are about to raise. It reads the facts indexing measured and
stored, so asking costs nothing, and 'facts' says "stored". Where nothing is stored it measures
now and 'facts' says "measured now": a table an earlier step produced, a spec nobody has built, a
warehouse table nobody has profiled. On a warehouse that first call scans the whole table on
someone's meter, and its facts are kept, so the next call is free. Say what the scan will cost
before you run it on a large table.

'source' is any of three things: an indexed source, a MODEL this project builds (just its name),
or a table an earlier step in the spec you are writing produced, written '<spec path>#<step id>'.
Use the last two to see what a table looks like AFTER a step ran: did the column you derived come
out as you meant, is the key still unique, is anything constant. That read is the audit every
layer ends with.

What comes back: 'n_rows' and 'n_columns' for the whole table, then 'columns': 'fields' once and
one row per column, at most {profile_cap} columns per call. A table of {profile_cap} columns or
fewer comes back whole. A wider one asked with no filter comes back as its card: columns counted
per type, every flagged column named, as `describe_source` gives them, and 'more' says how to ask.

Narrow with the filters `describe_source` takes: 'columns' (exact names, in the order you give),
'match' (a name pattern), 'type' or 'flag'. When more columns match than one call gives, 'more'
says how many were left out; name the next ones with 'columns'. A column name that does not exist
is refused, not skipped. A list with no 'more' is whole.

A table indexed before means, quartiles and samples were stored has them null, and says so in
'not_stored' with a note. Never derive one from the other facts. 'approximate' names a field
that is the engine's estimate, not an exact value.

How to read what comes back:

- A column whose distinct count equals the row count with no nulls is unique IN THIS EXTRACT.
  That is a fact, not a schema constraint, and a table whose real grain is two columns shows no
  unique column at all. When you believe you know the grain, declare it on the step; the engine
  checks it.
- Quartiles against the mean say whether a column is skewed, which decides mean against median
  for every fill and every aggregate on it.
- Samples are distinct values in sorted order, not the first rows. A value with leading spaces
  sorts first, which is where a sample earns its keep.
- A column with one distinct value, or min and max both zero, passed every gate and is still
  broken. Look for it on every model you build.
- A question across many columns or tables, such as which columns anywhere are mostly null, is
  one `query_data` on portia_columns, which holds these facts for every column.
- What earlier chats wrote about the table is on its card: `describe_source`.

These facts are unranked. Deciding which of them matter is your job.
