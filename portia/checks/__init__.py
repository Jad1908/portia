"""The deterministic checks layer.

Each check is a small pure function `check(inputs) -> structured evidence dict`
built on `portia.core`. Add new checks (entity resolution, …) as modules here.
Rendering for humans lives at the edge — each module has its own `render_text`;
import it from the module (e.g. `portia.checks.join.render_text`), never inside
a check.
"""

from portia.checks.join import join_findings, join_report
from portia.checks.outcome import outcome_report
from portia.checks.profiling import profile, profile_path

__all__ = ["profile", "profile_path", "join_report", "join_findings", "outcome_report"]
