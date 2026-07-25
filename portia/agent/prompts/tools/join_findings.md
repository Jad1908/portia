Measure what joining two sources on given keys would actually do: key overlap and
coverage, the relationship (1:1 / 1:many / many:many), fan-out, how many rows each
join type would produce and drop — plus example unmatched rows, null-key rows, and
worst fan-out keys.

Call this BEFORE deciding anything about a merge.

The findings are unranked: whether a dropped row matters is your call, not the
check's.
