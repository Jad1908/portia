<!-- placeholders: {portia_columns}, {portia_tables} -->
One SELECT over the tables you name, with the answer back as rows. Nothing is written to a spec.

Use it for any question the cards and profiles do not answer: a combination of tables, a filter, an aggregate over a subset, a candidate label's row counts side by side. Ten of them in a turn is fine. A zero is an answer: `n_rows: 0` means there are none.

`inputs` is every table the query reads: a source, a model, `<spec path>#<step id>`, a result this chat kept, or `portia_columns` and `portia_tables`. The query runs on a connection holding exactly those. `sql` is one SELECT in the dialect named under *Where the data lives*; quote a column with spaces or mixed case as the catalog spells it. `question` is one sentence.

`result` is the name the whole result is kept under for this chat. Page it with `offset` or `column_offset` (nothing runs again), or put the name in `inputs` and query it; both beat running the query again. `n_rows` and `n_columns` are the whole result; `rows` are lists in `fields` order and `shown` says which you have; `cut` names columns where a long value was cut; `not_kept` says why a result was too big to keep.

`portia_columns` is one row per column of every source and model: {portia_columns}. `portia_tables` is one row per table: {portia_tables}. Names follow `information_schema`: `table_name`, `column_name`, `data_type` (the engine's type), `type` (portia's reading), `flags` as a list (`list_contains(flags, 'high_null')`). They run on this machine in DuckDB whatever the project's engine, and on a warehouse a query reads them or warehouse tables, never both.
