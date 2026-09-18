<!-- placeholders: {step_id}, {readers}, {replacement} -->
Step {step_id} cannot be superseded by {replacement}, because {readers} still read it by name.
Retiring the id would leave those steps naming a table that no longer exists.

The straightforward fix is to **keep the id**. A replacement that reuses {step_id} takes its place
and its position, so everything reading it goes on reading it — and the whole spec re-runs with the
correction in it, so if the change breaks one of those steps you find out here rather than on
somebody else's build.

If the new step genuinely needs a different name, supersede the steps that read it first, working
downwards, or fix them in the same pass. The set you are changing has to be closed under "is read
by" — one condition, not a series of special cases.
