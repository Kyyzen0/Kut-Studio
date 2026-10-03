"""Zones migrées vers l'i18n : le texte affiché suit la langue (à chaud pour les panneaux, à l'appel pour les dialogues).

Un test au moins par zone, sur une vraie fenêtre (Qt offscreen). Les clés de ces zones sont aussi couvertes par les
tests de parité (``tests/test_i18n_parity.py``) ; ici on vérifie le **câblage** : le widget lit bien la clé, dans la
langue courante, sans repli (mode strict).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from ui import i18n


@pytest.fixture
def language_reset():
    """À demander **avant** ``window`` : ``reset_for_tests`` retire les abonnés, ceux de la fenêtre compris."""
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


@pytest.fixture
def window(language_reset, qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow

    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    boxes: list[tuple[str, str, str]] = []

    def record(kind):
        def show(_parent, title, text, *_args, **_kwargs):
            boxes.append((kind, title, str(text)))
            return QMessageBox.Yes

        return show

    for kind in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(f"ui.main_window.QMessageBox.{kind}", record(kind))
    main = MainWindow()
    qtbot.addWidget(main)
    if getattr(main, "timeline_timer", None) is not None:
        main.timeline_timer.stop()
    main.message_boxes = boxes
    return main


def _first_clip_id(window) -> str:
    return window.timeline_panel.clip_views[0].id


# ---------------------------------------------------------------------------
# Historique Annuler / Rétablir
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("language", "label"), [
    ("fr", "Dupliquer le clip"),
    ("en", "Duplicate clip"),
    ("es", "Duplicar clip"),
])
def test_history_labels_are_recorded_in_the_current_language(window, language, label):
    i18n.set_language(language)
    window.timeline_panel.select_clip(_first_clip_id(window))
    window.duplicate_selected_clip()
    assert window.history.undo_label == label
    assert window.undo_action.text() == f"{i18n.translate('action.undo')} : {label}"


def test_a_label_with_a_name_field_is_formatted_in_every_language(window):
    labels = {}
    for language in ("fr", "en", "es"):
        i18n.set_language(language)
        labels[language] = i18n.translate("history.library.folder_create", name="Rushes")
    assert labels == {"fr": "Créer le dossier « Rushes »", "en": "Create folder “Rushes”",
                      "es": "Crear la carpeta «Rushes»"}
