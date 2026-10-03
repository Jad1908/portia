Measure what joining two tables on given keys would actually do: key overlap and coverage, the
relationship (1:1 / 1:many / many:many), fan-out, how many rows each join type would produce and
drop — plus example unmatched rows, null-key rows, and the keys repeated most on either side.

Call this BEFORE deciding anything about a merge. Every merge, not just the first one.

'left' and 'right' each name an indexed source, **another model in this project** (just its name),
or **a table an earlier step produced** — written '<spec path>#<step id>', e.g.
'specs/training.yaml#reservations_hotels'. A multi-hop merge joins an intermediate result, and an
intermediate result is not a file, so it has no indexed name. Use these and you can measure hop two
before committing to it, exactly as you measured hop one. Recording a step and reading what it
produced afterwards is the expensive way round.

An example row shows its key columns first, then a few more, because a row as wide as the table
buries the one column that explains why it did not match. 'example_row_columns' says which ones
you got — do not read the width of an example row as the width of the table. Name 'left_columns'
or 'right_columns' to choose the rest yourself; the keys are included either way.

'fan_out' means a row of the left table would come out more than once, so a left or inner join
returns more rows than the left puts in and a total over a left column comes out too high.
'extra_left_copies' counts those rows. The right table's rows repeating is what every lookup does:
'relationship' says many:1 and 'extra_right_copies' counts it. Put the table whose rows you are
keeping on the left.

The findings are unranked: whether a dropped row matters is your call, not the check's.
