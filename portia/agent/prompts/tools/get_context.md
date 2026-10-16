<!-- placeholders: {rows} -->
The project card again: description, groups, every source and model with row and column counts, and `stale` where a table changed since it was indexed.

It is already in your system prompt. Call it after something changed in this session: a source indexed or interpreted, a step that built a model, a group recorded. `match` (a name pattern, `sales_*`) and `group` narrow the lists; each shows up to {rows} tables. The same facts, notes included, are in `portia_tables` through `query_data`.
