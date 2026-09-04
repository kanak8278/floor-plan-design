"""Shared path setup for the CLI scripts.

`import _bootstrap` at the top of a script makes `fpeval` importable and sets the
cwd to the repo root, so the script behaves the same whether it is run from the
root or from `scripts/`.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.chdir(ROOT)
