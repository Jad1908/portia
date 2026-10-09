<!-- placeholders: {n_columns}, {listed}, {examples} — filled from agent/tools._by_type. -->
This table has {n_columns} columns, too many to send one line each, so they are grouped by type.
Every column is counted in exactly one group and the counts add up to {n_columns}. Nothing was
left out, but a big group does not name every column in it.

Groups come in the order their type first appears in the table. A group of {listed} or fewer
lists its columns in full. A bigger one gives its count, its first and last {examples} names in
table order, and each other field across all of its columns: a flag or a role as how many columns
carry each, a rate or a count as its spread (min, q25, median, q75, max), and min and max as the
lowest and highest value any of its columns holds.

To read a column on its own, call `profile_source` with 'columns' naming it. A spread says how a
group's columns vary, not which column holds which number; do not give one of its numbers to a
column you have not profiled.
