<!-- placeholders: {reserved}, {tables} — filled from agent/handlers._select. -->
{reserved} is portia's own record, kept on this machine, and {tables} is in the warehouse, so one
query cannot read both. Nothing ran.

Ask {reserved} in a query of its own: it runs in DuckDB's SQL on this machine, whatever the
project's engine. Then write the warehouse query with what it told you, naming only warehouse
tables in 'inputs'.
