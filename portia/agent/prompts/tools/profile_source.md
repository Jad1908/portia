One table's full measured facts: per column, dtype, null rate, distinct count,
min and max, quartiles, sample values and quality flags.

The rung for numbers. Call it when you need them: interpreting a source, judging
whether a key is usable, quantifying a problem you are about to raise, choosing
mean against median. On a local project the measurement is cheap; what costs is the
answer's size in your context, so name 'columns' on a wide table rather than reading
all of it. On a warehouse project it is a scan of the whole table on someone's
meter, and the first call on a scoped table is what turns its `profiled: false`
into facts. Say what the scan will cost before you run it on a large table.

'source' is any of three things: an indexed source, a MODEL this project builds
(just its name; running its spec is how the table is reached), or a table an
earlier step in the spec you are writing produced, written '<spec path>#<step id>'.
Use the last two to see what a table looks like AFTER a step ran: did the column
you derived come out as you meant, is the key still unique, is anything constant.
That read is the audit every layer ends with.

'columns' returns only the columns you name; everything else is still measured, so
n_rows and n_cols describe the whole table. Naming a column that does not exist is
refused rather than skipped.

How to read what comes back:

- A column whose distinct count equals the row count with no nulls is unique IN
  THIS EXTRACT. That is a fact, not a schema constraint, and a table whose real
  grain is two columns shows no unique column at all. When you believe you know the
  grain, declare it on the step; the engine checks it.
- Quartiles against the mean say whether a column is skewed, which decides mean
  against median for every fill and every aggregate on it.
- A column with one distinct value, or min and max both zero, passed every gate and
  is still broken. Look for it on every model you build.
- Notes earlier chats left about this table ride along under 'notes'. Read them.

These facts are unranked. Deciding which of them matter is your job.
