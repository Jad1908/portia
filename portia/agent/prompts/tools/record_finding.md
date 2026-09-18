Keep one thing this chat learned, where the next session will find it.

A finding is not a step and it is not a spec. Nothing re-runs it. It is what you would tell a
colleague who is about to work on these tables — the thing that would have saved you an hour if
somebody had told you first.

## What goes in it

    {"question": "at what tolerance do hotels match REF_PLACES localities?",
     "answer": "binning is unusable here — coverage is poor and dense cities blow up",
     "so": "matched on DIVISION centroids instead",
     "about": ["HOTELS.LATITUDE", "REF_PLACES.LATITUDE"],
     "from": [7, 9],
     "spec_name": "hotel_events"}

'question', 'answer' and 'so' are yours, in plain language. Write the answer as a sentence, not
as a number restated — the exact numbers are copied in beside it.

'so' says what it changed. If you cannot fill it in, this was a query rather than a finding.
Leave it in the log and move on; that is not a failure.

'about' is the tables and columns it concerns, as 'table.column' or a bare table name. This is
the entire read path: the finding shows up on `describe_source` for the tables named here, so
one with the wrong 'about' is one nobody will ever see. Name BOTH SIDES when it is about a
relationship. Names are checked against the catalog and an unknown one is refused.

'from' names the `query_data` calls it rests on, by the number `review_queries` gave them.

'spec_name' is the model this was in service of, and the finding joins that model's journal —
the one a person reads under the spec to see why it looks the way it does. If this chat recorded
a step on a spec, name that spec. Leave it out only when the work was not about building
anything.

## YOU DO NOT WRITE THE NUMBERS

The SQL and the result are copied out of the chat log verbatim, from the queries you named in
'from'. That is deliberate and it is not bookkeeping. By the time you are curating, a query may
be forty calls back; a measurement you restate from memory is a measurement you authored, and
that is the one thing you may never do. Your sentence and the measured numbers end up side by
side, where they can be read against each other.

## When to write one

From `review_queries`, before a reply in which you asked the data something — not the moment a
query comes back. Looking at your own questions as a set is what makes the judgment possible:
one query on its own always looks worth keeping, and three together usually turn out to be one
finding.
