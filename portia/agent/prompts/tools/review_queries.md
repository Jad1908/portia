Every question you asked the data this chat with `query_data`, numbered, with what came back.

CALL THIS BEFORE EVERY REPLY IN WHICH YOU ASKED THE DATA ANYTHING. Not once at the end: a
conversation has no reliable end and nothing warns you which message is the last. A reply has
an end, and it is the moment you have just finished a piece of work and still remember what
you learned doing it.

Each query says whether it has been `kept` already, so calling this every turn is safe —
you only judge what is new, nothing is written twice, and there is nothing left pending.

## What to keep

Most of them are not findings. A finding is a question whose answer CHANGED WHAT HAPPENED
NEXT:

  - it decided a transformation ("so we matched on centroids instead")
  - it ruled an approach out ("so binning is not the way to do this")
  - it is something the next person working on these tables needs before they start
    ("EVENTS.ID is not unique in this extract — some ids repeat up to 421 times")

A query you ran to check your own arithmetic, or that returned what you already expected, is
not a finding. Leave it.

You do not have to keep anything, and on most turns you will not. A query you skip stays in the
chat log and costs nothing; it is simply not indexed against the tables it touched. There is no
cleanup and no pending state. Skipping one turn's worth is fine; never keeping anything means
the next session starts from zero.

## Then

Keep the ones that earned it with `record_finding`, naming them by their number. Several
queries can make one finding — a tolerance sweep is three questions and one thing learned.

## Curating an older chat

Pass `chat` to read a different one back. The numbers in it are still exact, because they come
out of the log rather than out of anyone's memory, so a chat from three weeks ago can be
curated today.
