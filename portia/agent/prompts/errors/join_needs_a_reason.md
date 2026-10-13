<!-- no placeholders — agent/handlers.join_findings, called with no reason. -->
Every join check needs a `reason`: one sentence on why these two tables might join on these keys.

The check measures how the two key columns match, and that measurement is kept in the knowledge
graph beside your sentence, so the next chat finds it instead of measuring again. A match of zero
does not mean the tables are unrelated. A key spelled `FRA` on one side and `France` on the other
matches nothing and is a mapping job. The sentence is what lets whoever reads the zero later tell
that apart from a dead end.

Say what made you try this join: what you read in the summaries, the roles, or the question you
are answering. Then call it again with the reason.
