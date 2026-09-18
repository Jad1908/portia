## Where the data lives

This project's sources are **files in the repo**, read in place by DuckDB. Every check, every
`query_data` and every `sql` step runs locally, costs nothing but time, and can be repeated freely.

- Write SQL in **DuckDB's dialect**. `read_csv`, `COPY`, `ATTACH` and the like are refused: a step
  reads the tables it declares in `inputs` and nothing else.
- A source name is its file's stem. Two files with one stem are told apart by their folder, as
  `raw__orders`; the index above names them as they are known.
- Indexing a source profiled it. `describe_source` always has facts behind it here.
