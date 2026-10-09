<!--
placeholders: {tool} — filled from agent/tools._interrupted. The first part of what the model reads
in place of a data call's result when the user pressed *Interrupt query* on its card
(`docs/CONVERSATION.md` §16). The parts after it are the other `interrupted_*.md` files.
-->
The user interrupted this `{tool}` call. Only this call: the conversation goes on, and you carry on
with it. This is not the Stop that ends a reply.

It has no result. Nothing it computed reached you, and no number from it may be used, estimated or
restated.

Do not send the same call again unless they ask for it. Read their reason and what portia measured
below, change what made the call expensive or wrong, and say in one sentence what you changed. If you
cannot tell what they want instead, ask them.
