"""Shared pytest configuration for Kut-Studio."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The tests build Qt widgets but do not require an on-screen desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
