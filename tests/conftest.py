"""Shared pytest configuration for Kut-Studio."""

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The tests build Qt widgets but do not require an on-screen desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_kut_studio_config(monkeypatch, tmp_path):
    """Empêche les tests de lire ou modifier les presets de l'utilisateur."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
