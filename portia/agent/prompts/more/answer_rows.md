<!-- placeholders: {shown}, {n_rows}, {next_offset}, {result} — filled from agent/handlers.review_queries. -->
Rows {shown} of {n_rows}. The rest are in {result}, which this chat kept: call `query_data` with
inputs ["{result}"], sql "SELECT * FROM {result}" and `offset: {next_offset}`, or ask it a
narrower question.
