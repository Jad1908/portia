"""Write the demo project: ``python -m devtools.demodata [directory]``."""

from __future__ import annotations

import sys
from pathlib import Path

from devtools.demodata import DEFAULT_DIR, write_demo

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    for path in write_demo(target):
        print(f"{path}  ({path.stat().st_size / 1e6:.1f} MB)")
