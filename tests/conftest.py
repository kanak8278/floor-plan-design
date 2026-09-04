"""Make `fpeval` importable and pin the working directory to the repo root.

Every script and test refers to data by repo-relative path (`data/ResPlan.pkl`,
`suite/*.json`), so the cwd has to be the root regardless of where pytest was
invoked from. Doing it here removes the `sys.path.insert(0, "src")` line that was
repeated at the top of every file.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# ROOT as well as SRC: `service` and `tests` are imported as top-level packages
# (`from service.store import ...`, `from tests.test_rules import ...`), and
# pytest's prepend import mode only puts `tests/` on the path, not the root.
for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.chdir(ROOT)
