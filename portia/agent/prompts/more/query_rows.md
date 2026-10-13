<!-- placeholders: {shown}, {n_rows}, {next_offset}, {result} — filled from agent/handlers.query_data. -->
Rows {shown} of {n_rows}. For the next ones, call `query_data` again with the same sql and inputs
and `offset: {next_offset}`. That reads {result}, the result this chat kept, so nothing runs again.
A bigger `limit` takes more rows at once, as many as fit in one answer.

If you are looking for something in the rest, ask {result} instead of paging: put it in 'inputs'
and write a SELECT over it. A WHERE, a count or a GROUP BY is usually the better question.
