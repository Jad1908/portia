"""portia — agent-assisted data harmonization. Deterministic engine first.

Package layout (see CLAUDE.md "Code conventions"):
- portia.core     — shared seams: loading (`load_table`) + serialization
- portia.checks   — the deterministic checks layer (profiling, join, …)
- portia.fixtures — kept mock data
- portia.cli      — play surfaces (`python -m portia.cli.<tool>`)
"""

from portia.checks import join_report, profile, profile_path
from portia.core import Table, load_frame, load_table, to_json

__all__ = ["Table", "load_table", "load_frame", "profile", "profile_path", "join_report", "to_json"]
