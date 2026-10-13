<!-- placeholders: {shown}, {n_rows}, {next_offset} — filled from agent/handlers.query_data, when the result was not kept. -->
Rows {shown} of {n_rows}. For the next ones, call `query_data` again with the same sql and inputs
and `offset: {next_offset}`, or a bigger `limit`. This result was not kept, so that runs the query
again.

If you are looking for something in the rest, a WHERE, a count or a GROUP BY is the better
question than another page.
