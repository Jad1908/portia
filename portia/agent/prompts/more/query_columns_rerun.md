<!-- placeholders: {shown}, {n_columns}, {next_column} — filled from agent/handlers.query_data, when the result was not kept. -->
Columns {shown} of {n_columns}. For the next ones, call `query_data` again with the same sql and
inputs and `column_offset: {next_column}`. This result was not kept, so that runs the query again:
naming the columns you want in the SELECT is usually the better question. `portia_columns` lists
every source's and model's columns, for finding them by name.
