<!--
placeholders: {facts} — the compact JSON `handlers.interrupted_facts` returned, measured after the
interrupt (`checks/sql_joins.py`). Every code in it is explained here, because no sentence about a join
lives in the check.
-->
What portia measured after the interrupt, from the inputs and not from the query's result:

{facts}

- `inputs`: each declared table's row count. `from` says where the count came from: `catalog` is what
  the catalog already held, `counted` was counted just now, `unresolved` means the name did not
  resolve to a table, `unknown` means nothing holds it. `approximate` marks a count that is the
  database's estimate.
- `joins`: each join in your SQL between two named sides (a declared input or a CTE of the same
  statement) on column equalities only. `left_rows` and `right_rows` are each side's rows,
  `left_keys` and `right_keys` the distinct non-null keys, `keys_on_both` the keys found on both
  sides, `left_repeats` and `right_repeats` how many times a key appears on that side at least and at
  most, and `rows` the exact number of rows that join produces on its own, for its kind. A join later
  in a chain is measured between the two tables its condition names.
- `not_read`: joins portia could not measure, and why: `side_not_named` (a side is a subquery or a
  name that is neither an input nor a CTE), `condition_not_equalities` (the condition holds something
  other than column = column joined by AND), `column_side_unclear` (portia cannot tell which table a
  key column belongs to), `kind_not_measured` (NATURAL, SEMI, ANTI, ASOF, POSITIONAL, or a comma
  join), `measure_failed` (counting it raised, with the error), `no_parser` and `not_parsed` (the
  statement could not be read). For those, the sizes above are the general facts only.

These are facts about the inputs. Which of them made the call expensive or wrong is your judgment.
