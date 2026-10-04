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


# ---------------------------------------------------------------------------
# Fenêtre principale : barre supérieure, rail latéral, raccourcis, lecture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("language", "nav", "export", "settings", "saved", "unsaved", "title", "caption"), [
    ("fr", ["Éditer", "Médias", "Effets", "Couleur", "Audio", "Graphiques"], " Exporter", "Réglages",
     "●  Enregistré", "●  Non enregistré", "Mon montage", "SÉQUENCE"),
    ("en", ["Edit", "Media", "Effects", "Color", "Audio", "Graphics"], " Export", "Settings",
     "●  Saved", "●  Unsaved", "My edit", "SEQUENCE"),
    ("es", ["Editar", "Medios", "Efectos", "Color", "Audio", "Gráficos"], " Exportar", "Ajustes",
     "●  Guardado", "●  Sin guardar", "Mi montaje", "SECUENCIA"),
])
def test_the_top_bar_and_the_side_rail_follow_the_language(
    window, language, nav, export, settings, saved, unsaved, title, caption
):
    i18n.set_language(language)                                    # la fenêtre est abonnée : retraduction à chaud
    assert [button.text() for button in window.top_nav_buttons] == nav
    assert window.export_button.text() == export
    assert window.settings_button.text() == " " + settings and window.settings_button.toolTip() == settings
    assert window._sequence_caption.text() == caption
    window.project_dirty = True
    window._update_top_bar()
    assert window.saved_indicator.text() == unsaved
    window.project_dirty = False
    window._update_top_bar()
    assert window.saved_indicator.text() == saved
    assert window.project_label.text() == title
    media = window.side_rail._buttons["media"]
    assert media.toolTip() == nav[1] == media.accessibleName()
    with i18n.strict_translations():
        window._retranslate_ui()


def test_the_play_button_tooltip_keeps_its_state_when_the_language_changes(window):
    window.is_playing = True
    window._set_preview_play_icon(True)
    i18n.set_language("en")
    assert window.preview_panel.play_button.toolTip() == "Pause"
    window.is_playing = False
    window._set_preview_play_icon(False)
    assert window.preview_panel.play_button.toolTip() == "Play"
    i18n.set_language("es")
    assert window.preview_panel.play_button.toolTip() == "Reproducir"


def test_shortcut_tooltips_keep_the_current_shortcut_in_every_language(window):
    roll = window.timeline_panel.roll_button
    assert roll.toolTip() == "Roll (R) : déplace la coupe entre deux clips"
    i18n.set_language("en")
    assert roll.toolTip() == "Roll (R): moves the cut between two clips"
    i18n.set_language("es")
    assert roll.toolTip() == "Roll (R): mueve el corte entre dos clips"
    assert window.timeline_panel.blade_button.toolTip() == "Herramienta de cuchilla (B)"


# ---------------------------------------------------------------------------
# Timeline : en-têtes de piste, transitions
# ---------------------------------------------------------------------------


def _header_titles(window) -> list[str]:
    from PySide6.QtWidgets import QLabel

    from ui.timeline_widgets.track_header import TrackRowHeader

    titles = []
    for header in window.timeline_panel.findChildren(TrackRowHeader):
        titles += [label.text() for label in header.findChildren(QLabel) if label.text().startswith(("V", "A", "S"))]
    return titles


def test_track_headers_are_rebuilt_in_the_new_language(window):
    french = _header_titles(window)
    assert any("VIDÉO" in title for title in french)
    i18n.set_language("en")
    english = _header_titles(window)
    assert any("VIDEO" in title for title in english) and not any("VIDÉO" in title for title in english)
    i18n.set_language("es")
    assert any("VÍDEO" in title for title in _header_titles(window))


@pytest.mark.parametrize(("language", "label"), [("fr", "FONDU · 0.5s"), ("en", "CROSSFADE · 0.5s"),
                                                  ("es", "FUNDIDO · 0.5s")])
def test_transition_labels_on_the_timeline_follow_the_language(language_reset, language, label):
    from types import SimpleNamespace

    from ui.timeline_panel import TimelinePanel

    i18n.set_language(language)
    transition = SimpleNamespace(type=SimpleNamespace(value="crossfade"), duration=0.5)
    assert TimelinePanel._transition_label(transition) == label


# ---------------------------------------------------------------------------
# Visionneuse et export
# ---------------------------------------------------------------------------


def test_the_viewer_follows_the_language_and_remembers_what_it_shows(window):
    panel = window.preview_panel
    panel.show_no_active_clip()
    assert panel._title_label.text() == "Visionneuse"
    i18n.set_language("en")
    assert panel._title_label.text() == "Viewer"
    assert panel._tooltip_buttons[0][0].toolTip() == "Back 2 s"
    assert panel.empty_state.text() == "No clip under the playhead\nMove the playhead or select a clip in the timeline."
    panel.show_missing_media("Interview")
    assert panel.empty_state.text() == (
        "Interview has no source media yet\nImport or relink the file to display it in the viewer.")
    i18n.set_language("es")                                       # le message « média absent » garde le nom du clip
    assert panel.empty_state.text().startswith("Interview aún no tiene medio de origen")
    panel.show_no_active_clip()
    i18n.set_language("fr")
    assert panel.empty_state.text().startswith("Aucun clip sous la tête de lecture")


def test_the_export_panel_title_and_quality_choices_follow_the_language(language_reset, qtbot):
    from ui.export_panel import ExportPanel

    panel = ExportPanel()
    qtbot.addWidget(panel)
    quality = lambda: [panel.quality_combo.itemText(i) for i in range(panel.quality_combo.count())]  # noqa: E731
    assert panel.title_label.text() == "EXPORT DU PROJET" and quality() == ["Élevée", "Standard", "Basse"]
    panel.quality_combo.setCurrentIndex(2)
    i18n.set_language("en")
    assert panel.title_label.text() == "PROJECT EXPORT" and quality() == ["High", "Standard", "Low"]
    assert panel.quality_combo.currentIndex() == 2 and panel.quality_combo.currentData() == "low"
    assert panel.close_button.toolTip() == "Close the export"
    i18n.set_language("es")
    assert quality() == ["Alta", "Estándar", "Baja"]


# ---------------------------------------------------------------------------
# Inspecteur
# ---------------------------------------------------------------------------


def _inspector(qtbot):
    from ui.properties_panel import PropertiesPanel

    panel = PropertiesPanel(lambda *_args: None)
    qtbot.addWidget(panel)
    return panel


def _form_labels(panel, group) -> list[str]:
    from PySide6.QtWidgets import QFormLayout, QLabel

    form = group.layout()
    assert isinstance(form, QFormLayout)
    return [item.widget().text() for item in (form.itemAt(i, QFormLayout.LabelRole) for i in range(form.rowCount()))
            if item is not None and isinstance(item.widget(), QLabel)]


@pytest.mark.parametrize(("language", "tabs", "more", "group", "rows"), [
    ("fr", ["Clip", "Couleur", "Audio", "Effets"], "Outils spécialisés", "Clip sélectionné", ["Nom", "Durée", "Position"]),
    ("en", ["Clip", "Color", "Audio", "Effects"], "Specialized tools", "Selected clip", ["Name", "Duration", "Position"]),
    ("es", ["Clip", "Color", "Audio", "Efectos"], "Herramientas especializadas", "Clip seleccionado",
     ["Nombre", "Duración", "Posición"]),
])
def test_the_inspector_follows_the_language_while_open(language_reset, qtbot, language, tabs, more, group, rows):
    panel = _inspector(qtbot)
    i18n.set_language(language)                                  # abonné : aucun appel explicite
    visible_tabs = [panel.inspector_tab_buttons[i].text() for i in (0, 1, 3, 2)]
    assert visible_tabs == tabs
    assert panel.inspector_more_button.toolTip() == more == panel.inspector_more_button.accessibleName()
    clip_group = next(g for g, key in panel._group_titles if key == "inspector.clip.title")
    assert clip_group.title() == group
    assert _form_labels(panel, clip_group) == rows
    with i18n.strict_translations():
        panel.retranslate()


def test_the_inspector_specialized_tool_shown_in_the_more_button_is_retranslated(language_reset, qtbot):
    panel = _inspector(qtbot)
    panel._select_inspector_tab(4)
    assert panel.inspector_more_button.text() == "Graphiques"
    i18n.set_language("en")
    assert panel.inspector_more_button.text() == "Graphics"
    assert [action.text() for action in panel.inspector_more_button.menu().actions()] == [
        "Graphics", "Compositing", "Tracking"]


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_color_grading_group_builds_in_strict_mode(language_reset, qtbot, language):
    i18n.set_language(language)
    with i18n.strict_translations():
        panel = _inspector(qtbot)
    expected = {"en": "Color grading", "es": "Corrección de color"}[language]
    assert panel.color_group.title() == expected
    assert panel.color_lut_label.text() == {"en": "No LUT", "es": "Ninguna LUT"}[language]


# ---------------------------------------------------------------------------
# Bibliothèque et panneau Projet ; construction complète dans chaque langue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("language", "title", "scopes", "search", "chips", "import_text"), [
    ("fr", "Médias", ["Projet", "Favoris"], "Rechercher dans la bibliothèque…",
     ["Tous", "Vidéo", "Audio", "Images", "Utilisés", "Non utilisés", "Manquants"], "  Importer"),
    ("en", "Media", ["Project", "Favorites"], "Search the library…",
     ["All", "Video", "Audio", "Images", "Used", "Unused", "Missing"], "  Import"),
    ("es", "Medios", ["Proyecto", "Favoritos"], "Buscar en la biblioteca…",
     ["Todos", "Vídeo", "Audio", "Imágenes", "Usados", "No usados", "Ausentes"], "  Importar"),
])
def test_the_library_panel_follows_the_language(window, language, title, scopes, search, chips, import_text):
    panel = window.project_panel
    i18n.set_language(language)
    assert panel._title_label.text() == title
    assert [button.text() for button in panel.scope_tab_buttons] == scopes
    assert panel.search_field.placeholderText() == search
    assert [panel.filter_chips._buttons[chip_id].text() for chip_id, _key in panel.filter_chips._CHIPS] == chips
    assert panel.import_button.text() == import_text
    assert panel.media_count.text().split(" ", 1)[1] in {
        "fr": {"média", "médias"}, "en": {"media"}, "es": {"medio", "medios"}}[language]
    assert panel.folder_tree.header_label.text() == {"fr": "DOSSIERS", "en": "FOLDERS", "es": "CARPETAS"}[language]


@pytest.mark.parametrize("language", ["en", "es"])
def test_the_whole_main_window_builds_in_strict_mode_in_another_language(
    language_reset, qtbot, monkeypatch, tmp_path, language
):
    """Aucune clé manquante ni repli sur le français dans tout ce que la fenêtre construit à son ouverture."""
    from dataclasses import replace

    from core.user_settings import load_user_settings, save_user_settings
    from ui.main_window import MainWindow

    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    # La fenêtre applique la langue des préférences à son ouverture : c'est elle qu'il faut régler.
    save_user_settings(replace(load_user_settings(), language=language))
    i18n.set_language(language)
    with i18n.strict_translations():
        main = MainWindow()
        qtbot.addWidget(main)
        if getattr(main, "timeline_timer", None) is not None:
            main.timeline_timer.stop()
        main._retranslate_ui()
    texts = {"en": ("Export", "Search the library…", "Edit"), "es": ("Exportar", "Buscar en la biblioteca…", "Editar")}[language]
    assert main.export_button.text() == " " + texts[0]
    assert main.project_panel.search_field.placeholderText() == texts[1]
    assert main.top_nav_buttons[0].text() == texts[2]
