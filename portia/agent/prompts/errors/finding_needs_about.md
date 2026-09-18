A finding needs `about` — the tables and columns it concerns, so that somebody working on
those tables later actually finds it.

This is the whole read path. A finding is surfaced by `describe_source` on the tables named
here, grouped by the other table involved. One with an empty `about` is filed under nothing
and will never be seen again, which is worse than not writing it.

Use `table.column` where the finding is about specific columns, or a bare table name where it
is about the table as a whole:

    "about": ["HOTELS.LATITUDE", "REF_PLACES.LATITUDE"]
    "about": ["EVENTS"]

Name both sides when the finding is about a relationship. That is what puts it in the right
group for a reader coming at it from either end.
