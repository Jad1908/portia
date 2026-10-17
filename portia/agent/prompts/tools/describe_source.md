<!-- placeholders: {card}, {examples}, {notes}, {findings}, {match_cap}, {flags} -->
One table's card: what it is, its columns with type, role and flags, the notes earlier chats left, and the findings about it. No statistics.

Read it before using a table you have not read this session. `source` is an indexed source or a model this project builds, by name; a model you just built is read the same way.

A table of {card} columns or fewer lists every column. A wider one lists none: `types` counts them per type with the first {examples} as examples, and `flagged` names the columns behind each flag by the shorter side (`columns` or `all_but`). To list columns on a wide table, narrow: `columns` (exact names), `match` (`*_id`, `*date*`), `type`, or `flag` (one of {flags}); {match_cap} at a time. A question across many columns or tables is a `query_data` on `portia_columns`.

`notes` are dated sentences earlier chats wrote, oldest first, the latest {notes}. `findings` are what earlier chats asked this table with a query, latest first, the latest {findings}; `findings_with` naming another table gives the findings about the pair. For the numbers, `profile_source`; for what the table connects to, `graph_lookup`.
