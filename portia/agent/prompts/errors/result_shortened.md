<!-- placeholders: {listed}, {examples} — filled from agent/tools._shortened. -->
This answer was too long to send whole, so it is shortened. The call itself did what it does:
nothing here means it failed.

Every list in it, and every map of names to values, longer than {listed} entries is given as its
`count`, its `first` and `last` {examples} entries in the order the report had them, and, for
numbers, their `spread` (min, q25, median, q75, max). Where the answer holds a table's columns,
they are grouped by type instead: per type its count, its first and last names, and tallies and
spreads across its columns. Everything else is as it was. Nothing was left out without being
counted.

A spread says how the values vary, not which name holds which value; do not give one of its
numbers to a column you have not read. To read particular columns, call `profile_source` on the
table, or on 'spec#step', with 'columns' naming them.
