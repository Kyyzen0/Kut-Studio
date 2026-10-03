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


# ---------------------------------------------------------------------------
# Boîtes de dialogue, erreurs et messages de la barre d'état
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("language", "title", "text"), [
    ("fr", "Ajout impossible", "Média 'nope' introuvable dans le projet."),
    ("en", "Cannot add", "Media 'nope' not found in the project."),
    ("es", "No se pudo añadir", "Medio 'nope' no encontrado en el proyecto."),
])
def test_an_error_dialog_is_written_in_the_current_language(window, language, title, text):
    i18n.set_language(language)
    with i18n.strict_translations():
        window.add_asset_to_timeline("nope")
    assert window.message_boxes == [("critical", title, text)]


@pytest.mark.parametrize(("language", "title", "text"), [
    ("fr", "Enregistrement impossible", "Impossible d'enregistrer le projet :\n\ndisque plein"),
    ("en", "Cannot save", "Could not save the project:\n\ndisque plein"),
    ("es", "No se pudo guardar", "No se pudo guardar el proyecto:\n\ndisque plein"),
])
def test_the_save_error_keeps_the_core_message_and_translates_the_rest(window, monkeypatch, language, title, text):
    def refuse(*_args, **_kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr("ui.main_window_mixins.project_files.save_project", refuse)
    window.current_project_path = "/tmp/projet.kut"
    i18n.set_language(language)
    window.save_project_file()
    assert window.message_boxes == [("critical", title, text)]


@pytest.mark.parametrize(("language", "title", "name_filter"), [
    ("fr", "Importer des médias", "Médias (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)"),
    ("en", "Import media", "Media (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)"),
    ("es", "Importar medios", "Medios (*.mp4 *.mov *.avi *.mkv *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg)"),
])
def test_file_dialog_title_and_filter_follow_the_language(window, monkeypatch, language, title, name_filter):
    seen = []
    monkeypatch.setattr("ui.main_window.QFileDialog.getOpenFileNames",
                        lambda _parent, caption, _directory, filters: (seen.append((caption, filters)) or ([], "")))
    i18n.set_language(language)
    window.import_media_via_dialog()
    assert seen == [(title, name_filter)]


@pytest.mark.parametrize(("language", "title", "label", "history"), [
    ("fr", "Marqueur", "Nom du marqueur", "Renommer un marqueur"),
    ("en", "Marker", "Marker name", "Rename a marker"),
    ("es", "Marcador", "Nombre del marcador", "Renombrar un marcador"),
])
def test_input_dialogs_and_their_history_label_follow_the_language(window, monkeypatch, language, title, label, history):
    prompts = []
    monkeypatch.setattr("ui.main_window_mixins.timeline_editing.QInputDialog.getText",
                        lambda _parent, caption, text_label, **_kw: (prompts.append((caption, text_label)) or ("Intro", True)))
    i18n.set_language(language)
    window.add_marker_at(1.0)
    marker = window.project.markers[-1]
    window.rename_marker(marker.id)
    assert prompts == [(title, label)]
    assert marker.name == "Intro" and window.history.undo_label == history


def test_status_bar_messages_follow_the_language(window):
    i18n.set_language("en")
    window.statusBar().showMessage(i18n.translate("status.transition.deleted"))
    assert window.statusBar().currentMessage() == "Transition deleted."
    window._report_edit_refused(i18n.translate("status.clip.cut_none"))
    assert window.statusBar().currentMessage() == "No clip selected to cut"
