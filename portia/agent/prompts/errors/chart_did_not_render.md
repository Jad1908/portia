There is no picture of `{tab}`, because the browser could not draw it. The renderer said:

{message}

That is Vega-Lite refusing the spec you wrote, after portia's own check passed it. The
query ran and its rows are fine. Correct the spec and call `plot_data` again under the
same tab name, which replaces the broken chart in place. Then look again if you need to.

Tell the user the chart failed if your reply mentions it. They are looking at the error.
