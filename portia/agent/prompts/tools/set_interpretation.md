Record what a source IS: a short prose summary, and a role for each column (e.g.
identifier, measure, timestamp, category, free_text).

This is durable — it becomes the project's memory, and a future session reads it
instead of re-deriving. Writes judgment only; it never alters a measured fact.

Pass 'summary', 'roles' (a column -> role object), or both.
