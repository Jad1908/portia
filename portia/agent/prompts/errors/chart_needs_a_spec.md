`plot_data` needs a `vega` object — the Vega-Lite spec for the picture.

The smallest one that draws something:

    {{"mark": "bar", "encoding": {{"x": {{"field": "city"}}, "y": {{"field": "n"}}}}}}

Every field names a column your `SELECT` returned. Portia supplies `data` and fills in
a field's `type` when you leave it out, so a spec is usually two lines. Anything else
Vega-Lite accepts — layers, colours, scales, axis formats, small multiples — you can
write, except the transforms: the arithmetic goes in the SQL.
