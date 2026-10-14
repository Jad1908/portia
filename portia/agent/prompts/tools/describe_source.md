<!-- placeholders: {card}, {examples}, {notes}, {findings}, {match_cap}, {flags}. Filled from agent/handlers.card_vocabulary. -->
One table's card: what it is, what its columns mean, and everything anyone has written down
about it. No statistics.

Cheap, and the first thing to read about any table you are about to use. Enough to judge whether
a source is relevant, which columns could be keys, and how two tables might relate. Start here
when the one-line index in your brief is not enough.

What comes back:

- 'summary' is what someone wrote about the table, or null when nobody has read it yet.
- 'n_rows' and 'n_columns'. A null 'n_rows' comes with 'n_rows_note', which says why.
- 'columns' is every column with its name, type, role and flags: 'fields' names each position
  once, then one row per column. Only a table of {card} columns or fewer has it.
- A wider table has no column list. 'types' counts its columns per type, with their names, or the
  first {examples} as examples. 'flagged' names the columns behind each flag by the shorter side:
  'columns' lists the ones that carry it, 'all_but' the ones that do not. A flag carried by too
  many to name is a count, and its 'more' says how to list them. 'unflagged' counts the rest.
  Every column is counted.
- 'notes': dated sentences earlier chats left about this table, oldest first. The latest
  {notes} are here, and 'more' counts the rest. Read them before you define anything on it. A note
  saying the outcome column is a grade is the difference between a label and a column of zeros.
- 'findings': what earlier chats asked this table with a query and what it said, latest first,
  each once with 'tables', every table it is about. The latest {findings} are here and 'more'
  counts the rest. A card that leaves some out adds 'findings_by_table': each other table this
  table's findings are about, with how many. 'findings_with' naming one table answers the
  findings about the two instead of the card.

To list columns, narrow. 'columns' takes exact names. 'match' takes a name pattern: 'meter_*'
starts with meter_, '*office*' contains office, and case does not matter. 'type' takes a type as
the card names it. 'flag' takes one of: {flags}. Given together, all of them apply. The answer is
the matching columns alone, {match_cap} at a time. For any other question about columns, on this
table or across all of them, call query_data on portia_columns.

A list cut short always ends with 'more', which says how many were left out and how to ask for
them. A list with no 'more' is whole.

'source' is an indexed source or a MODEL this project builds, by name. After you record a step,
describe the model it built: that is how you check the table is what you meant before anything
reads it.

It describes ONE table and says nothing about what that table connects to. When the question is
which table you should be reading, ask `graph_lookup` first and come back here with its answer.
For the numbers, climb to `profile_source`.
