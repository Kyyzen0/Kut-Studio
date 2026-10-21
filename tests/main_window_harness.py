"""Fenêtre principale pour les tests de comportement des gestionnaires (mixins de ``MainWindow``).

Hors écran, configuration isolée (presets et réglages écrits dans ``config_dir``, jamais dans la vraie configuration),
horloge de timeline arrêtée. Les boîtes de dialogue sont **enregistrées** au lieu de bloquer : un test lit ce qui a été
présenté à l'utilisateur (titre, texte) et choisit la réponse d'une question ou d'une saisie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMessageBox


@dataclass
class Dialogs:
    """Boîtes présentées pendant le test, et réponses à donner."""

    shown: list[tuple[str, str, str]] = field(default_factory=list)   # (genre, titre, texte)
    answer: QMessageBox.StandardButton = QMessageBox.Yes
    text_reply: tuple[str, bool] = ("", False)
    inputs: list[tuple[str, str]] = field(default_factory=list)       # (titre, texte proposé)

    def of_kind(self, kind: str) -> list[tuple[str, str]]:
        return [(title, text) for shown_kind, title, text in self.shown if shown_kind == kind]


def install_dialogs(monkeypatch) -> Dialogs:
    dialogs = Dialogs()

    def recorder(kind: str):
        def show(_parent, title, text, *_args, **_kwargs):
            dialogs.shown.append((kind, str(title), str(text)))
            return dialogs.answer if kind == "question" else QMessageBox.Ok
        return show

    for kind in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, kind, recorder(kind))

    def get_text(_parent, title, _label, *_args, text: str = "", **_kwargs):
        dialogs.inputs.append((str(title), str(text)))
        return dialogs.text_reply

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    return dialogs


def build_window(qtbot, monkeypatch, config_dir: Path):
    """``MainWindow`` sur le projet d'exemple : V1 (intro, plan_a), V2 (b_roll), A1 (vide), S1 (subtitle_01)."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(config_dir))
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.timeline_timer.stop()
    return window


def track(window, track_id: str):
    return next(item for item in window.project.tracks if item.id == track_id)


def track_ids(window) -> list[str]:
    return [item.id for item in window.project.tracks]


def menu_action(window, text_key: str) -> QAction:
    """Action de menu au libellé traduit ``text_key`` (celle que l'utilisateur clique)."""
    matches = [action for action, key in window._translated_actions if key == text_key]
    assert len(matches) == 1, f"{len(matches)} action(s) pour {text_key}"
    return matches[0]
