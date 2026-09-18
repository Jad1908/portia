`{key}` computes, and it is at `{path}` in the spec you sent.

This is the one thing a chart spec here will not do, and it is not about taste.

A number a chart grammar worked out is a number no `SELECT` returned. It is in no chat
log, `review_queries` cannot show it to you, and a finding resting on it would be prose
with nothing underneath. Everything else in Vega-Lite is yours — marks, layers, scales,
palettes, axes, legends, formats, small multiples. This is the edge of that.

Refused anywhere in the spec: `aggregate` `bin` `timeUnit` `calculate` `transform`
`window` `joinaggregate` `regression` `loess` `density` `quantile` `impute` `fold`
`pivot` `lookup` `sequence` `expr` `signal` `op`.

**Write it in the SQL instead and name the result.** You are already writing the query;
this is one more expression in it.

    aggregate  →  SELECT city, count(*) AS n FROM t GROUP BY 1
    bin        →  SELECT floor(price / 50) * 50 AS bucket, count(*) AS n FROM t GROUP BY 1
    timeUnit   →  SELECT date_trunc('month', at) AS month, ... GROUP BY 1
    regression →  a window or regression expression in the project's dialect, selected as `trend`
    calculate  →  the expression, in the SELECT list, under a name

Then encode the column you named: `{{"field": "n"}}`.

Call it again with the arithmetic in the query.
