<!-- placeholders: {shown}, {n_rows}, {next_offset} -->
Showing rows {shown} of {n_rows}. To see more, call `query_data` again with the SAME sql and a
bigger `limit`, or with `offset: {next_offset}` to continue from here.

Change the arguments, not the query. Rewriting the SELECT with your own LIMIT and OFFSET produces a
near-identical query each time and buys nothing — `limit` and `offset` are applied on top of
whatever your SQL already says.

If the result is wide, a large `limit` will be refused for size. Select fewer columns rather than
fewer rows when that happens: the answer to "which of these 89 events" is usually three columns and
all the rows, not every column and a third of them.
