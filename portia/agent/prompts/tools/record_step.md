<!-- placeholders: {expect_join}, {expect_normalize}, {expect_sql}, {hows}, {transform_ops}, {blocking_flags}, {layers}; literal braces doubled. -->
Execute a step, measure the table it produces, and append it to a spec: the re-runnable record of what was done to the data and why. Recording runs it, and nothing is written if a post-condition hits a zero ({blocking_flags}).

Use it for a decision the user has asked you to make real. A question goes to `query_data`, which runs the same SQL and writes nothing. A build is recorded one step at a time, over indexed sources, and pauses where the user should look before you go on.

The call: `spec_path`, `step`, and on a spec's first step `layer` (one of {layers}; omit for a flat project) and, on a warehouse, `target` (`DATABASE.SCHEMA`). `supersedes` names the step this one replaces, keeping its id; the whole spec re-runs with it.

One spec builds one table, named after its file; its steps are the working-out inside one query. A later step reads an earlier one by its id (as `left`, `right`, `input`, or in `inputs`), and a spec reads another spec's table by the model's plain name. Do not re-inline earlier work: copies of one expression drift.

    {{"id": "orders_with_customers", "op": "join",
      "left": "orders", "right": "customers", "keys": ["customer_id"], "how": "left",
      "grain": ["order_id"],
      "expect": {{"result_rows": 10, "left_dropped": 0}},
      "rationale": "left, not inner: 2 orders reference customers missing from the dimension."}}

`op` is `join` (`left`, `right`, `keys` or `left_on` and `right_on`, `how` one of {hows}), `normalize` (`input`, `transforms` as `{{"column", "op"}}` with op one of {transform_ops}; element-wise, the row count cannot change), or `sql` (`inputs` naming every table the query reads, `sql` one SELECT in the brief's dialect). `sql` is for what the other two cannot express: an aggregate, a dedupe, a filter, a derived column, a label, a window. A join reports what it dropped from each side; a sql step reports only the shape of what came out.

`expect` predicts what the op will report, and `run_spec` holds you to it. The fields are join: {expect_join}; normalize: {expect_normalize}; sql: {expect_sql}. Predict the counts from what the checks told you; drift is a result to say out loud, not a reason to rewrite the prediction. `grain` lists the columns one row is meant to be; the engine checks it, and a claim that fails blocks the step with the duplicated keys. `rationale` is the sentence a reviewer reads in the diff: what was decided and why.

What comes back: where the step was written, the model it builds, `outcome` (n_rows, n_cols, flags, the grain as measured, and `flagged` columns: all_null, newly_all_null, high_null), `inputs` with how many of each table's columns reached the output and whether any holds a value, `joins` the graph kept (one not kept says why: a side that is an earlier step's table or a CTE, `no_keys` for a cross join, or what the SQL reader could not read: `side_not_named`, `condition_not_equalities`, `column_side_unclear`, `kind_not_measured`, `not_parsed`, `no_parser`), and `drift`. A join can match nothing, leave one side's columns null, and still report the row count you predicted, so read it. Then read the model with `describe_source` and `profile_source` and write its summary before building on it.

A zero that is intended is acknowledged with `acknowledge: ["<flag>"]` and a rationale, after telling the user what the zero means in their terms. `empty_output` may be acknowledged up front when zero rows is a legitimate result.
