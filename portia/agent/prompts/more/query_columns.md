<!-- placeholders: {shown}, {n_columns}, {next_column}, {result} — filled from agent/handlers.query_data. -->
Columns {shown} of {n_columns}. For the next ones, call `query_data` again with the same sql and
inputs and `column_offset: {next_column}`; it reads {result}, so nothing runs again. If you know
which columns you want, name them in a SELECT over {result}. `portia_columns` lists every
source's and model's columns, for finding them by name.
