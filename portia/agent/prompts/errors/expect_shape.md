<!-- placeholders: {problems} -->
Step not recorded. An 'expect' block predicts the value the op will report, and these predictions
cannot match anything the run produces, because they are the wrong kind of thing:

{problems}

This is not a near miss to tighten later. A prediction of the wrong type can never come true, so
the step would report drift on every run, forever. Drift that always fires is drift everyone learns
to ignore — and that costs you the one signal that catches a vendor's file changing underneath you.
A permanently-drifting spec is worse than a spec that predicts nothing.

Predict the shape the op actually reported, or drop the field from 'expect' altogether. Predicting
three things accurately is worth more than predicting eight things loosely.
