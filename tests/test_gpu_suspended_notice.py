"""Aperçu GPU suspendu après un plantage : l'utilisateur est prévenu, avec le chemin du réglage qui le rétablit.

Le mode Auto reste sur le CPU tant qu'un plantage avec le GPU actif est connu (voir ``test_gpu_crash_guard.py``).
Jusqu'ici, ce repli ne se lisait que dans le diagnostic matériel, sous un identifiant brut : l'aperçu restait lent,
session après session, sans explication.
"""

from __future__ import annotations

import pytest

from core.gpu_backend import PreviewBackend, ResolvedBackend
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    return win


def _resolve_as(monkeypatch, reason: str) -> None:
    monkeypatch.setattr(
        "ui.main_window_mixins.hardware_preview.resolve_preview_backend",
        lambda *_a, **_k: ResolvedBackend(PreviewBackend.AUTO, "cpu", reason=reason, fallback_reason="détail"),
    )


def test_a_suspended_gpu_is_announced_with_the_way_back(window, monkeypatch):
    _resolve_as(monkeypatch, "previous_crash")
    window._apply_preview_backend()
    assert window.statusBar().currentMessage() == i18n.translate("preview.gpu_suspended")


def test_other_cpu_reasons_stay_quiet(window, monkeypatch):
    window.statusBar().clearMessage()
    _resolve_as(monkeypatch, "no_window_system")              # CI, plateforme sans fenêtre : rien à corriger
    window._apply_preview_backend()
    assert window.statusBar().currentMessage() != i18n.translate("preview.gpu_suspended")


def test_the_diagnostic_names_the_suspension_in_words(window, monkeypatch):
    _resolve_as(monkeypatch, "previous_crash")
    window._apply_preview_backend()
    text = window.hardware_diagnostics_text()
    assert i18n.translate("preview.reason.previous_crash") in text
    assert "previous_crash" not in text
