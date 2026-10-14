<!-- placeholders: {error}. Filled from agent/handlers._vega_object, for a `vega` sent as text that does not parse as JSON. -->
`vega` arrived as text, and the text is not JSON ({error}), so nothing was drawn. Send the
Vega-Lite spec as a JSON object, not as a string holding one:

    {{"mark": "bar", "encoding": {{"x": {{"field": "city"}}, "y": {{"field": "n"}}}}}}
