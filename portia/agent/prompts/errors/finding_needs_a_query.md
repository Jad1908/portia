A finding has to name the queries it came from, in `from` — their numbers, as `review_queries`
listed them.

That is where its numbers come from. You author the three sentences; portia copies the SQL and
the result out of the chat log verbatim, so the measurement in the finding is the measurement
that was taken rather than one recalled later. By the time you are curating, the query may be
forty calls back and on the far side of a compaction, and a number restated from memory is a
number you authored — which is the one thing you may never do.

Call `review_queries` to see this chat's queries with their numbers, then name the ones this
finding rests on:

    {"from": [7, 9], ...}

Several is normal. A tolerance sweep is three queries and one finding.
