"""Shared foundation seams every tool builds on: loading and serialization.

Nothing project-specific lives here — just the two things every check needs to
do the same way (get a DataFrame from a path; emit compact JSON evidence).
"""

from portia.core.io import find_data_files, load_frame, supported_suffixes
from portia.core.serialize import round_float, to_json, to_jsonable

__all__ = [
    "load_frame",
    "find_data_files",
    "supported_suffixes",
    "round_float",
    "to_json",
    "to_jsonable",
]
