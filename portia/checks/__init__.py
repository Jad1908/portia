"""The deterministic checks layer.

Each check is a small pure function `check(inputs) -> structured evidence dict`
built on `portia.core`. Add new checks (join_report, entity resolution, …) as
modules here. Rendering for humans lives at the edge (see the check's
`render_*` and `portia.cli`), never inside a check.
"""

from portia.checks.profiling import profile_frame, profile_path, render_text

__all__ = ["profile_frame", "profile_path", "render_text"]
