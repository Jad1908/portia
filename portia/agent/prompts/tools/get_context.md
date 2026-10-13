<!-- placeholders: {rows}. Filled from agent/handlers.card_vocabulary. -->
Re-read the project card: its description, its groups, and every source and model with its row
and column counts.

You ALREADY HAVE this in your system prompt. Call it only to pick up changes made during this
session: after a source is indexed or interpreted, after a step builds a model, or after a group
is recorded.

Each table is one row: its name, 'n_rows', 'n_columns', 'read' (someone wrote a summary of it),
'about' (the first sentence of that summary, or null) and 'stale' (true when it changed since it
was indexed, null when portia cannot tell). Each list shows up to {rows} tables, by name, and
'more' says how many were left out. 'match' (a name pattern, such as 'sales_*') and 'group'
narrow both lists, and then the answer is the lists alone. Every table's facts, notes included,
are also in portia_tables, through `query_data`.
