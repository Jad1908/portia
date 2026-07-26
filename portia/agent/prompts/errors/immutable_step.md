<!-- placeholders: {step_id} -->
Step {step_id} is already recorded, and steps are append-only. A recorded prediction cannot be
revised: a spec whose expectations get edited to match the result verifies nothing, and the drift
signal it exists to produce is worth more than any single step looking clean.

Do not work around this by bumping the id. If the prediction turned out wrong, that is a finding —
say so plainly to the user. If the *work* needs correcting, record the corrected work as a new step
with its own id and its own honest `expect`. If the step should never have been written at all,
that is an edit to the YAML the user makes by hand.
