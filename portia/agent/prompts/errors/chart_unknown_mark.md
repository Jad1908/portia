`{mark}` is not a mark portia can draw. The ones it can: **{marks}**.

Pick by the question:

- `bar` — how much per category. The default for a `GROUP BY` over names.
- `line` / `trail` — how something moved along an ordered axis, usually time.
- `point` / `circle` / `square` — how two measures relate, one row per dot. Add `size`
  for a bubble chart.
- `area` — a magnitude along an ordered axis, when the filled shape is the point.
- `arc` — a pie or donut. Encode `theta` with the measure and `color` with the category.
  Use it for a handful of parts of one whole, not for twenty.
- `rect` — a heatmap. `x` and `y` are the two categories, `color` is the measure.
- `boxplot` / `errorbar` / `errorband` — a distribution per category. The rows have to
  be the raw values, not summaries you already aggregated.
- `tick` / `rule` — individual values on an axis, and reference lines.
- `text` — the value written where the mark would be. Encode `text` with the column.

`geoshape` and `image` are Vega-Lite's and not portia's: both need something from
outside the query — geometry, a URL — and the sandbox has no network.

If none of them fits, the question may not be a picture. `query_data` returns the same
rows as text, and a table of eight numbers is often the better answer.

Call it again with one of the marks above.
