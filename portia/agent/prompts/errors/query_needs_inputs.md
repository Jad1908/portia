`query_data` needs `inputs` — the list of tables your SELECT reads, by name.

The query runs on a connection holding exactly the tables you declare and nothing
else, so an undeclared table is not hidden, it is simply not there and the query
fails. Declaring them is what makes the sandbox a fact rather than a promise about
reading the SQL correctly.

A name may be an indexed source, another model in this project, or a step an
earlier spec produced. The same names work in `profile_source` and `join_findings`.

Call it again with `inputs`.
