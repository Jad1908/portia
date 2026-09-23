Ask the person you are working with one to four questions and wait for their answers. This is
the only way a question reaches them: they read the transcript, and a question typed into your
reply is a question nobody is stopped for.

Call it when something is ambiguous and the checks cannot settle it: which of two candidate keys
is the grain, whether a zero is the answer or a broken step, which spelling of a code list is the
one to keep, whether a table belongs in this pipeline at all. Do not guess and do not hard-stop.
Do not call it for something a rung of the ladder can answer; climb first, then ask about what
the numbers leave open.

Each question has a short `header`, the `question` in one sentence, and two to four `options`,
each with a `label` and a one-line `description` of what picking it means. The person can also
type their own answer. Set `multiSelect` only when more than one option can be true at once.

The answer comes back as `{question: chosen label or typed text}`. Act on it; do not ask the same
question again in another form.
