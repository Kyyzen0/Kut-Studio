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


# ---------------------------------------------------------------------------
# Motion graphics : calques, bibliothèque, éditeurs
# ---------------------------------------------------------------------------


def _menu_texts(menu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


@pytest.mark.parametrize(("language", "title", "add_button", "headers", "shape_names"), [
    ("fr", "CALQUES", "+ Ajouter", ["Calque", "Parent", "Durée"],
     ["Rectangle", "Rectangle arrondi", "Ellipse / cercle", "Ligne", "Polygone"]),
    ("en", "LAYERS", "+ Add", ["Layer", "Parent", "Duration"],
     ["Rectangle", "Rounded rectangle", "Ellipse / circle", "Line", "Polygon"]),
    ("es", "CAPAS", "+ Añadir", ["Capa", "Padre", "Duración"],
     ["Rectángulo", "Rectángulo redondeado", "Elipse / círculo", "Línea", "Polígono"]),
])
def test_the_layers_panel_follows_the_language_while_open(
    language_reset, qtbot, language, title, add_button, headers, shape_names
):
    from ui.layers_panel import LayersPanel

    panel = LayersPanel()
    qtbot.addWidget(panel)
    assert panel._title.text() == "CALQUES" and panel.add_button.text() == "+ Ajouter"
    i18n.set_language(language)                                  # le panneau est abonné : aucun appel explicite
    assert panel._title.text() == title and panel.add_button.text() == add_button
    assert [panel.tree.headerItem().text(column) for column in (2, 3, 4)] == headers
    shapes_menu = next(action.menu() for action in panel.add_button.menu().actions() if action.menu() is not None)
    assert _menu_texts(shapes_menu) == shape_names
    with i18n.strict_translations():
        panel.retranslate()


def test_the_graphics_library_retranslates_its_buttons_and_tooltips(language_reset, qtbot):
    from ui.graphics_library import GraphicsLibraryView

    view = GraphicsLibraryView()
    qtbot.addWidget(view)
    assert view.create_buttons["text"].text() == "Titre"
    assert view.create_buttons["text"].toolTip() == "Ajouter : Titre — Texte éditable"
    i18n.set_language("en")
    assert view.create_buttons["text"].text() == "Title"
    assert view.create_buttons["text"].toolTip() == "Add: Title — Editable text"
    assert view.import_image_button.toolTip() == "Create a graphic layer from an image"
    i18n.set_language("es")
    assert view.create_buttons["solid"].text() == "Relleno"


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_motion_graphics_editors_build_in_strict_mode_without_missing_keys(language_reset, qtbot, language):
    """Les éditeurs construisent tous leurs libellés : une clé absente d'une langue échoue au lieu de se replier."""
    from ui.compositing_editor import CompositingEditor
    from ui.graphics_editor import GraphicsEditor
    from ui.properties_widgets.advanced_transform import AdvancedTransformEditor
    from ui.text_style_editor import TextStyleEditor

    i18n.set_language(language)
    with i18n.strict_translations():
        widgets = [GraphicsEditor(), CompositingEditor(""), TextStyleEditor(), AdvancedTransformEditor()]
    for widget in widgets:
        qtbot.addWidget(widget)
    graphics, compositing, _text, advanced = widgets
    texts = {"en": ("Graphic layer", "Advanced transform"), "es": ("Capa gráfica", "Transformación avanzada")}[language]
    assert graphics.title() == texts[0]
    assert advanced.toggle.text() == texts[1]
    shape_labels = [graphics.shape_combo.itemText(i) for i in range(graphics.shape_combo.count())]
    assert shape_labels[1] == {"en": "Rounded rectangle", "es": "Rectángulo redondeado"}[language]
    mask_modes = [compositing.mask_mode.itemText(i) for i in range(compositing.mask_mode.count())]
    assert mask_modes == {"en": ["Add", "Subtract", "Intersection"], "es": ["Añadir", "Restar", "Intersección"]}[language]


def test_layer_attribute_choices_and_dialogs_follow_the_language(window, monkeypatch):
    from core.mograph_layers import ATTRIBUTE_KINDS

    seen = []

    def choose(_parent, title, label, items, *_args):
        seen.append((title, label, list(items)))
        return items[0], False

    monkeypatch.setattr("ui.main_window_mixins.motion_graphics.QInputDialog.getItem", choose)
    window._attribute_clipboard = object()
    i18n.set_language("en")
    window._paste_layer_attributes([_first_clip_id(window)])
    title, label, items = seen[0]
    assert (title, label) == ("Paste attributes", "Attributes to paste:")
    assert items == ["All", "Transform", "Effects and color", "Masks", "Animation (keyframes)"]
    assert len(items) == len(ATTRIBUTE_KINDS) + 1
    window._attribute_clipboard = None
    window._paste_layer_attributes([_first_clip_id(window)])
    assert window.statusBar().currentMessage() == "Copy a layer's attributes first."
