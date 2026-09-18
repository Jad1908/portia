The spec encodes `{field}`, and the query did not return that column.

What it returned: **{columns}**

This is refused rather than drawn without it, because Vega-Lite draws an empty axis for
a field it cannot find. The chart would look exactly as finished as the right one — it
would just answer a different question, silently, in front of the user.

Two ways out, and they are different fixes:

- The column is in the data but not in your `SELECT` list. Add it.
- You meant one of the names above. Use it exactly as written, including its case.

Remember that the aggregate belongs in the SQL. If you were reaching for a column the
query would have to compute, write the `GROUP BY` and select the result under a name —
a field only says which column goes where, it never computes one.
