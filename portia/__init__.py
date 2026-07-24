"""portia — agent-assisted data harmonization. Deterministic engine first.

Package layout (see CLAUDE.md "Code conventions"):
- portia.core     — shared seams: loading (`load_frame`) + serialization
- portia.checks   — the deterministic checks layer (profiling, join, …)
- portia.fixtures — kept mock data
- portia.cli      — play surfaces (`python -m portia.cli.<tool>`)
"""

from portia.checks import profile_frame, profile_path
from portia.core import load_frame, to_json

__all__ = ["load_frame", "profile_frame", "profile_path", "to_json"]
