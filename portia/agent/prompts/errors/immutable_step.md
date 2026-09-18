<!-- placeholders: {step_id} -->
Step {step_id} is already recorded in this spec, and recording it again would be an accident rather
than an edit.

If you meant to record something new, give it its own id.

If you meant to **correct** {step_id}, say so: pass `supersedes` naming it, and the new step
replaces it in place. That is a real edit, not a workaround — the whole spec re-runs with the
correction in it, the result is measured, the same gate applies, and the version you replaced stays
in git history where the user reads it in the diff.

What you may not do is bump the id to escape a prediction that turned out wrong. A spec whose
`expect` blocks get quietly rewritten to match the result verifies nothing, and the drift signal is
worth more than any single step looking clean. If the prediction was wrong, say so plainly to the
user — that is a finding, and it is worth more than a clean-looking spec.
