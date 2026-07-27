"""Shared foundation seams every tool builds on: loading, serialization, display.

Nothing project-specific lives here — just the three things every surface needs
to do the same way (get a DataFrame from a path; emit compact JSON evidence;
render a measured value for a human).
"""

from portia.core.io import find_data_files, load_frame, supported_suffixes
from portia.core.present import as_yaml, count, format_rate, inline, scalar
from portia.core.serialize import round_float, to_json, to_jsonable

__all__ = [
    "load_frame",
    "find_data_files",
    "supported_suffixes",
    "round_float",
    "to_json",
    "to_jsonable",
    "format_rate",
    "count",
    "inline",
    "scalar",
    "as_yaml",
]
