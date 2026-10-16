<!-- placeholders: {profile_cap} -->
Measured facts per column: type, null rate, distinct count, min, quartiles, max, mean, standard deviation, commonest value and its share, sample values, flags. `n_rows` and `n_columns` for the whole table.

Use it when you need the numbers: reading a source, judging a key, sizing a problem before raising it, checking a column you derived. `source` is a source, a model, or `<spec path>#<step id>` for the table a step produced. Stored facts cost nothing and `facts` says `stored`. Where nothing is stored (a step's table, an unprofiled warehouse table) it measures now and says `measured now`; on a warehouse that is a full scan on someone's meter, so say so before running it on a large table.

Up to {profile_cap} columns per call. A wider table asked with no filter comes back as its card; narrow with `columns`, `match`, `type` or `flag` as in `describe_source`. A name that does not exist is refused, not skipped.

Reading it: a distinct count equal to the row count with no nulls is unique in this extract, not a constraint, and a two-column grain shows no unique column at all. Samples are distinct values in sorted order, so a leading space sorts first. `not_stored` names facts an older index never kept; do not derive them from the others. `approximate` names an estimate.
