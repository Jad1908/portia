Draw a chart the user can see. Runs one SELECT over the tables you name, puts the
result on screen as a chart in its own tab, and writes nothing anywhere.

SHOWING IS PART OF ANSWERING. After indexing you have measured every source in this
project and the user has read none of them, so you know more about this data than
they do. That gap is the reason half your questions are hard for them to answer. A
chart is how they catch up, and it is the single most useful thing an expert reads
before deciding how to model something.

The user does not have to ask for a picture. If the honest answer to their question
is a shape — a distribution, a trend, a comparison across more categories than a
sentence can carry — draw it and say what it shows.

## When to reach for it

It is `query_data` with the answer sent somewhere else. Same SELECT, same sandbox,
same rules about what SQL is allowed. The difference is where the rows go: there,
back to you as text; here, onto the screen.

So the choice is about who needs to see it.

- **You need the number** → `query_data`. A count, a match rate, whether a value
  exists. You are deciding something and the user does not need the picture.
- **They need to see it** → here. The answer is a shape, or it is a hundred numbers,
  or they are about to be asked a question they cannot answer without looking.

Draw when a sentence would flatten the answer:

- "most events are in five cities" hides that the sixth to the fortieth hold a third
  of the rows
- "prices range from 40 to 900" hides that they are bimodal
- "coverage improves with tolerance" hides where it stops improving

Do not draw three numbers. A bar chart of three bars is a sentence with axes on it.

## The call

    {"question": "how are events distributed across months in 2025?",
     "tab": "Events per month",
     "inputs": ["EVENTS"],
     "sql": "SELECT date_trunc('month', START_DATE) AS month, count(*) AS n
             FROM EVENTS
             WHERE START_DATE >= '2025-01-01'
             GROUP BY 1 ORDER BY 1",
     "vega": {"mark": "line",
              "encoding": {"x": {"field": "month"}, "y": {"field": "n"}}}}

'inputs' is every table the query reads, by name — an indexed source, another model
in this project, or '<spec>#<step id>'. The query runs on a connection holding
exactly those and nothing else.

'sql' is one SELECT, exactly as in `query_data`.

'vega' is **a Vega-Lite spec, and you write it.** Not a shortlist of channel names
portia maintains: the real grammar, so a layered chart, a palette, an axis format, a
legend title, a log scale, small multiples and a donut are all things you can just
write. Portia supplies `data` from the SELECT and fills in a field's `type` when you
leave it out. If you know Vega-Lite, you already know this argument.

    {"mark": {"type": "bar", "color": "#FF9900"},
     "encoding": {"x": {"field": "month", "type": "temporal", "axis": {"format": "%b"}},
                  "y": {"field": "sales", "title": "Revenue ($)"}}}

Layers are how two marks share axes — bars with a trend line over them, a rule at a
threshold, values written above the bars:

    {"layer": [{"mark": {"type": "bar", "color": "#FF9900"}},
               {"mark": {"type": "line", "color": "#146EB4", "strokeWidth": 2},
                "encoding": {"y": {"field": "trend"}}}],
     "encoding": {"x": {"field": "month"}, "y": {"field": "sales"}}}

Colours are yours. Write hex where you want a specific one, or set a whole palette
with a scale range, and the user's brand is a request you can now satisfy:

    "color": {"field": "region", "scale": {"range": ["#FF9900", "#146EB4", "#232F3E"]}}

Left alone, a chart takes portia's own palette and the app's light or dark theme, so
you only have to say something about colour when colour is the point.

## THE AGGREGATE GOES IN THE SQL, NEVER IN THE ENCODING

This is the one rule here that is not about taste, and it is the *only* thing the spec
will not do.

Every channel names a **column the SELECT returned**. Portia refuses `aggregate`,
`bin`, `timeUnit`, `calculate`, `transform`, `window`, `joinaggregate`, `regression`,
`loess`, `density`, `impute`, `expr` — anywhere in the spec, at any depth — because a
number a chart grammar worked out is a number no SELECT returned. It would not be in
the log, `review_queries` could not show it to you, and a finding resting on it would
be prose with nothing underneath.

Write the GROUP BY. Select the result under a name. Then name that column.

    right:  SELECT city, count(*) AS n FROM t GROUP BY 1   →  x: city, y: n
    wrong:  SELECT city FROM t                             →  x: {aggregate: "count"}

**A trend line is a column, not a transform.** Compute it in the SQL — a regression
in the project's dialect, or the window average you actually want — select it under a name,
and layer a line on it. That is one more expression in a query you were writing anyway, and the
number ends up on the record where every other number in this project lives.

## Marks

Vega-Lite's own list: `bar` `line` `point` `circle` `square` `area` `arc` `rect`
`boxplot` `errorbar` `errorband` `tick` `rule` `text` `trail`.

- `bar` — how much per category. The default for a GROUP BY over names.
- `line` / `trail` — how something moved along an ordered axis, usually time.
- `point` / `circle` / `square` — how two measures relate, one row per dot.
- `area` — a magnitude along an ordered axis, when the filled shape is the point.
- `arc` — a pie or donut. `theta` is the measure, `color` is the category.
- `rect` — a heatmap. `x` and `y` are the categories, `color` is the measure.
- `boxplot` / `errorbar` / `errorband` — a distribution, off the raw rows.
- `tick` / `rule` / `text` — values on an axis, reference lines, written values.

`geoshape` and `image` are the two portia cannot draw: both need something from
outside the query, and the sandbox has no network.

## Tabs

'tab' is the name on the tab, and it is the chart's identity.

DRAWING AGAIN UNDER A NAME ALREADY ON THE STRIP REPLACES THAT CHART. Same idiom as
`record_step`'s 'supersedes'. That is how you correct one — same name, better query —
and it is why you should pick a name you would be willing to reuse. Portia does not
guess which two charts are the same question; you say so by reusing the name.

A new name opens a new tab. The user can close tabs and reopen them, so a few is
fine. `chart 1`, `chart 2`, `chart 3` is a strip nobody can read.

## What you get back

A receipt: the tab, how many rows were plotted, and — for a chart small enough — the
plotted rows themselves, under 'plotted', holding just the columns your spec encodes.
That is what you narrate from.

For a bigger chart there is no 'plotted'. Instead the receipt says `"unpaired": true`
and gives you each encoded column on its own: the categories in it, or the two ends of
its range. **Read that field.** It means portia is telling you the categories and the
numbers separately and NOT which goes with which.

Your spec is not echoed back. You wrote it, and it is in the log verbatim. Neither are
the rows of a big chart — they went to the browser, which is the point: a 5,000-point
scatter would be refused as a tool result and would be no use to you as text anyway.

## Do not write a number you were not given

This is the one rule here that matters more than the rest of this file.

When the receipt is 'unpaired' you know the range runs 25.2 to 43.9 and you know the
three categories. You do NOT know which category is 43.9, and you cannot work it out.
Writing "Risk 1: 43.9%, Risk 2: 25.2%" is not a summary of the chart, it is a guess
that reads exactly like a measurement — and it has already been wrong, in the way that
matters most: it inverted a real finding while the correct chart sat on screen next to
it, and the user believed the caption.

So:

- Say what the receipt supports. "Failure rates range from 25% to 44% across the three
  risk categories" is true, useful, and costs you nothing.
- **If you want to name a value, ask for it.** One `query_data` call returns the rows
  and then you can name every one of them, correctly. It is one SELECT.
- Never fill a gap in a receipt by inference. A number you did not receive is a number
  you may not write. This is the same rule that stops you eyeballing a source: portia's
  whole claim is that every figure in your reply came from a measurement, and a chart
  is not an exception because you drew it.

The receipt is enough to say something true about what you drew. Say it. A chart
appearing with no comment leaves the user to work out why you drew it.

You also cannot see the picture. You know the rows and the spec you wrote; you do not
know what it looks like rendered, so do not describe colours, positions or which bar is
tallest unless the receipt says so.

## Where it sits

Beside `query_data` (L5), not above it — same door, different destination. It is not
a deeper rung and there is nothing to climb to from here.

It writes nothing. No spec, no catalog entry, no graph edge, nothing to approve and
nothing to undo. A chart that turns out to have changed a decision is kept by the
user, with a button, and what that writes is a finding.
