"""Pytest bootstrap: make the ``ai_trader`` package importable from the repo root."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
