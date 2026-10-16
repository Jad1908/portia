<!-- placeholders: {engine_version}, filled by agent/context.py from duckdb.__version__ -->
## Where the data lives

This project's sources are files in the repo, read in place by **DuckDB {engine_version}**. Every check, query and `sql` step runs locally and can be repeated freely.

- SQL is DuckDB's dialect, this version. `read_csv`, `COPY`, `ATTACH` and the like are refused: a query reads the tables it declares in `inputs` and nothing else.
- A source name is its file's stem; two files with one stem are told apart by their folder, as `raw__orders`.
- Indexing profiled every source, so `profile_source` reads stored facts without scanning.
