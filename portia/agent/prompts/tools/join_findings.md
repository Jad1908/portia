Measure what joining two tables on given keys would actually do: key overlap and coverage, the
relationship (1:1 / 1:many / many:many), fan-out, how many rows each join type would produce and
drop — plus example unmatched rows, null-key rows, and worst fan-out keys.

Call this BEFORE deciding anything about a merge. Every merge, not just the first one.

'left' and 'right' each name either an indexed source, or **a table an earlier step produced** —
written '<spec path>#<step id>', e.g. 'specs/training.yaml#otb_hotels'. A multi-hop merge joins an
intermediate result, and an intermediate result is not a file, so it has no indexed name. Use the
step form and you can measure hop two before committing to it, exactly as you measured hop one.
Recording a step and reading what it produced afterwards is the expensive way round.

The findings are unranked: whether a dropped row matters is your call, not the check's.
