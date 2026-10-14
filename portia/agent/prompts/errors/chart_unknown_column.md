<!-- placeholders: {field}, {returned}. Filled from agent/handlers.plot_data, for a field the query did not return; {returned} is chart_columns_all.md or chart_columns_closest.md. -->
The spec encodes `{field}`, and the query did not return that column.

{returned}

This is refused rather than drawn without it, because Vega-Lite draws an empty axis for
a field it cannot find. The chart would look exactly as finished as the right one — it
would just answer a different question, silently, in front of the user.

Two ways out, and they are different fixes:

- The column is in the data but not in your `SELECT` list. Add it.
- You meant one of the names above, or another the query returned. Use it exactly as
  the query spells it.

Remember that the aggregate belongs in the SQL. If you were reaching for a column the
query would have to compute, write the `GROUP BY` and select the result under a name —
a field only says which column goes where, it never computes one.
