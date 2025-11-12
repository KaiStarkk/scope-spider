"""Scope Spider backend package."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is always on sys.path, even when the backend is
# launched with `--app-dir backend`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

__all__ = []
