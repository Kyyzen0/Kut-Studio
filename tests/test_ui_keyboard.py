"""Navigation au clavier : dialogues, formulaires de l'inspecteur, menus, et raccourcis globaux intacts.

Règles vérifiées (voir ``docs/ui-small-windows-and-keyboard.md``) :

* un dialogue a **un seul** bouton par défaut ; Échap ferme, Entrée valide ;
* un bouton-icône de dialogue est focalisable et nommé ;
* dans la fenêtre principale, aucun bouton, curseur ou liste non éditable ne prend le focus **au clic** (sinon Espace
  déclencherait le dernier bouton cliqué au lieu de lire), mais Tab les atteint tous, dans l'ordre d'affichage ;
* la barre de menus se parcourt aux flèches et chaque menu a une lettre mnémonique unique par langue.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QTextEdit,
    QWidget,
)

from tools import ui_audit as audit
from ui import i18n


@pytest.fixture(scope="module")
def main_window(tmp_path_factory):
    """Fenêtre principale (projet riche, 1280 × 720, scopes masqués), créée une fois par module."""
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("keyboard")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    window = audit.make_main_window(1280, 720, scopes=False)
    yield window
    window.close()
    patch.undo()


@pytest.fixture
def window(main_window):
    """La fenêtre du module, remise à zéro : clip sélectionné, onglet Clip, lecture arrêtée, aucun focus restant."""
    if main_window.is_playing:
        main_window.toggle_play()
    focused = QApplication.focusWidget()
    if focused is not None:  # un champ de saisie resté focalisé par un test précédent ignorerait les raccourcis
        focused.clearFocus()
    main_window.setFocus()
    audit.select_first_clip(main_window)
    main_window.properties_panel._select_inspector_tab(0)
    audit.open_library_section(main_window, "media")
    return main_window


def _dialog_names(window) -> list[str]:
    return [name for name, _factory in audit.dialog_factories(window)]


@pytest.fixture
def dialog_of(window):
    """Construit un dialogue de l'application par son nom (affiché, fermé à la fin du test)."""
    opened: list[QDialog] = []

    def make(name: str) -> QDialog:
        factory = dict(audit.dialog_factories(window))[name]
        dialog = factory()
        dialog.show()
        dialog.activateWindow()
        audit.settle(window)
        opened.append(dialog)
        return dialog

    yield make
    for dialog in opened:
        try:
            dialog.close()
        except RuntimeError:  # déjà détruit
            pass


DIALOGS = [
    "Préférences", "Préférences complètes", "Gestionnaire de tags",
    "Enregistrer un preset d'effet", "Enregistrer une transition",
    "Créer une séquence Multicam", "Résultat de la synchronisation", "Réglages Multicam",
]


def _is_closed(dialog) -> bool:
    """Fermé (ou déjà détruit : les Préférences se détruisent à leur fermeture)."""
    try:
        return not dialog.isVisible()
    except RuntimeError:
        return True


# --- dialogues : bouton par défaut, accessibilité, Échap / Entrée -----------------------------------------------


@pytest.mark.parametrize("name", DIALOGS)
def test_every_dialog_has_exactly_one_default_button(dialog_of, name):
    """Régression : le bouton par défaut des Préférences était « Retirer » (éditeur de raccourcis), celui du
    gestionnaire de tags « Choisir une couleur… » : Entrée dans un champ déclenchait une action sans rapport."""
    dialog = dialog_of(name)
    defaults = audit.default_buttons(dialog)
    assert len(defaults) == 1, [button.text() for button in defaults]


@pytest.mark.parametrize("name", DIALOGS)
def test_dialog_default_button_is_the_validation_or_close_button(dialog_of, name):
    dialog = dialog_of(name)
    default = audit.default_buttons(dialog)[0]
    if name.startswith("Préférences"):
        assert default is dialog.close_button
    elif name == "Gestionnaire de tags":
        assert default is dialog.create_button
    elif name == "Réglages Multicam":
        assert default is dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close)   # on ne valide rien : on ferme
    else:
        assert default is dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)


@pytest.mark.parametrize("name", DIALOGS)
def test_dialog_controls_are_reachable_by_tab_and_icon_buttons_are_named(dialog_of, name):
    dialog = dialog_of(name)
    assert not [w for w in audit.tab_sequence(dialog) if isinstance(w, QScrollArea)], "arrêt de Tab invisible"
    findings = audit.audit_dialog(name, dialog)
    assert [str(f) for f in findings] == []


@pytest.mark.parametrize("name", DIALOGS)
def test_escape_closes_every_dialog(dialog_of, name):
    dialog = dialog_of(name)
    assert dialog.isVisible()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert _is_closed(dialog)


def test_tag_manager_icon_buttons_are_focusable_and_named_in_every_language(window, qtbot):
    from core.library_organization import LibraryOrganization
    from ui.library_organization_widgets import TagManagerDialog

    organization = LibraryOrganization(window.project)
    names = {}
    original = i18n.current_language()
    try:
        for language in ("fr", "en", "es"):
            i18n.set_language(language)
            dialog = TagManagerDialog(organization)
            qtbot.addWidget(dialog)
            if not organization.list_tags():
                organization.create_tag("Tag clavier")
                dialog.refresh()
            row = next(iter(dialog._tag_rows.values()))
            assert len(row.buttons) == 3
            for button in row.buttons:
                assert button.focusPolicy() & Qt.TabFocus, button.toolTip()
                assert button.accessibleName()
            names[language] = [button.accessibleName() for button in row.buttons]
    finally:
        i18n.set_language(original)
    assert len({tuple(value) for value in names.values()}) == 3  # le nom suit la langue


def test_enter_in_the_tag_name_field_creates_the_tag_and_never_opens_the_colour_picker(dialog_of, monkeypatch):
    from PySide6.QtWidgets import QColorDialog

    dialog = dialog_of("Gestionnaire de tags")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("le sélecteur de couleur ne doit pas s'ouvrir sur Entrée")

    monkeypatch.setattr(QColorDialog, "getColor", forbidden)
    before = len(dialog._organization.list_tags())
    dialog.name_field.setFocus()
    QTest.keyClicks(dialog.name_field, "Nouveau tag")
    QTest.keyClick(dialog.name_field, Qt.Key_Return)
    assert len(dialog._organization.list_tags()) == before + 1
    assert dialog.isVisible()  # la validation du formulaire ne ferme pas le dialogue


def test_enter_in_the_shortcut_search_does_not_remove_a_shortcut(dialog_of):
    """Régression : Entrée dans les Préférences déclenchait « Retirer » sur le raccourci sélectionné."""
    dialog = dialog_of("Préférences complètes")
    editor = dialog.shortcuts_editor
    dialog.tabs.setCurrentIndex(dialog._tab_order.index("shortcuts"))
    audit.settle(dialog)
    editor.select_command("tool_blade")
    manager = editor._manager
    assert manager.shortcut_map.sequences("tool_blade") == ("B",)
    editor.search_edit.setFocus()
    QTest.keyClick(editor.search_edit, Qt.Key_Return)
    assert manager.shortcut_map.sequences("tool_blade") == ("B",)
    assert _is_closed(dialog)  # Entrée valide le dialogue : « Fermer » est son bouton par défaut


def test_enter_in_a_preset_dialog_validates_only_with_a_name(dialog_of):
    dialog = dialog_of("Enregistrer un preset d'effet")
    dialog.name_edit.setFocus()
    dialog.name_edit.clear()
    QTest.keyClick(dialog.name_edit, Qt.Key_Return)
    assert dialog.isVisible()  # nom vide : le dialogue reste ouvert
    dialog.name_edit.setText("Mon preset")
    QTest.keyClick(dialog.name_edit, Qt.Key_Return)
    assert dialog.result() == QDialog.Accepted


@pytest.mark.parametrize("name", ["Enregistrer un preset d'effet", "Enregistrer une transition"])
def test_tab_leaves_the_description_editor(dialog_of, name):
    """Régression : Tab insérait une tabulation dans la description au lieu de passer au champ suivant."""
    dialog = dialog_of(name)
    editor = dialog.description_edit
    editor.setFocus()
    audit.settle(dialog)
    QTest.keyClick(editor, Qt.Key_Tab)
    audit.settle(dialog)
    assert "\t" not in editor.toPlainText()
    assert QApplication.focusWidget() is not editor


def test_escape_closes_the_export_page_and_its_close_button_is_named(window):
    """La page d'export est un dialogue plein écran : Échap la ferme comme la croix, qui a un nom accessible."""
    window.show_export()
    audit.settle(window)
    closed = []
    window.export_panel.close_requested.connect(lambda: closed.append(True))
    QTest.keyClick(window.export_panel.launch_button, Qt.Key_Escape)
    assert closed
    assert [str(f) for f in audit.unnamed_icon_buttons(window.export_panel)] == []
    window.show_editor()


def test_accessibility_translations_cover_every_language_with_the_same_placeholders():
    from ui.i18n_accessibility import ACCESSIBILITY_TRANSLATIONS

    assert len(ACCESSIBILITY_TRANSLATIONS) > 10
    for key, entry in ACCESSIBILITY_TRANSLATIONS.items():
        assert set(entry) == {"fr", "en", "es"}, key
        assert all(text.strip() for text in entry.values()), key
        assert len({tuple(sorted(re.findall(r"{(\w+)}", text))) for text in entry.values()}) == 1, key
    original = i18n.current_language()
    try:
        for language in ("fr", "en", "es"):
            i18n.set_language(language)
            assert i18n.translate("a11y.tag.rename") == ACCESSIBILITY_TRANSLATIONS["a11y.tag.rename"][language]
    finally:
        i18n.set_language(original)


# --- inspecteur : focus, ordre de tabulation, noms ----------------------------------------------------------------------


def _form_controls(panel):
    return [
        widget for widget in panel.findChildren(QWidget)
        if (isinstance(widget, (QAbstractButton, QSlider)) or (isinstance(widget, QComboBox) and not widget.isEditable()))
        and not widget.objectName().startswith("qt_")
    ]


@pytest.mark.parametrize("tab,name", audit.INSPECTOR_TABS, ids=[name for _i, name in audit.INSPECTOR_TABS])
def test_inspector_controls_take_tab_but_never_the_focus_on_click(window, tab, name):
    """Un contrôle qui prend le focus au clic vole Espace / J / K / L : ils ne seraient plus lus par la fenêtre."""
    panel = window.properties_panel
    panel._select_inspector_tab(tab)
    audit.settle(window)
    assert audit.keyboard_gaps(panel) == []
    for widget in _form_controls(panel):
        assert widget.focusPolicy() & Qt.TabFocus, (name, widget.objectName(), widget.text() if hasattr(widget, "text") else "")
        assert not widget.focusPolicy() & Qt.ClickFocus, (name, widget.objectName())


@pytest.mark.parametrize("tab,name", audit.INSPECTOR_TABS, ids=[name for _i, name in audit.INSPECTOR_TABS])
def test_inspector_tab_order_follows_the_display_order(window, tab, name):
    panel = window.properties_panel
    panel._select_inspector_tab(tab)
    audit.settle(window)
    window.activateWindow()
    first = panel.inspector_tab_buttons[0]
    sequence = audit.tab_sequence(window, start=first, within=panel)
    assert len(sequence) > 5, f"onglet {name} : Tab n'a visité que {len(sequence)} contrôles"
    assert not [w for w in sequence if isinstance(w, QScrollArea)], "arrêt de Tab invisible sur une zone défilante"
    content = panel.scroll_area.widget()
    assert audit.tab_order_violations(sequence, content) == []
    reached = set(sequence)
    missing = [w for w in audit.interactive_controls(panel) if w.focusPolicy() & Qt.TabFocus and w not in reached]
    assert missing == [], [audit.widget_path(w, 3) for w in missing]


def test_tab_visits_the_graphics_group_before_the_movement_group(window):
    """Régression : le groupe graphique est créé après « Mouvement » mais affiché au-dessus ; Tab suivait la création."""
    ids = audit._clip_ids(window, lambda track, clip: getattr(clip, "graphic", None) is not None)
    window._restore_clip_selection(ids[0])
    panel = window.properties_panel
    panel._select_inspector_tab(4)
    audit.settle(window)
    sequence = audit.tab_sequence(window, start=panel.inspector_tab_buttons[0], within=panel)
    graphics = [i for i, w in enumerate(sequence) if panel.graphics_group.isAncestorOf(w)]
    movement = [i for i, w in enumerate(sequence) if panel.movement_group.isAncestorOf(w)]
    assert graphics and movement and max(graphics) < min(movement)


def test_inspector_icon_buttons_have_accessible_names(window):
    panel = window.properties_panel
    for tab in (0, 4):
        panel._select_inspector_tab(tab)
        audit.settle(window)
        assert [str(f) for f in audit.unnamed_icon_buttons(panel)] == []
    ids = audit._clip_ids(window, lambda track, clip: track.type == "subtitle")
    window._restore_clip_selection(ids[0])
    panel._select_inspector_tab(4)
    audit.settle(window)
    assert [str(f) for f in audit.unnamed_icon_buttons(panel)] == []  # les neuf tuiles d'alignement comprises


def test_keyframe_button_names_follow_the_language(window):
    panel = window.properties_panel
    original = i18n.current_language()
    try:
        names = {}
        for language in ("fr", "en"):
            i18n.set_language(language)
            from ui.properties_panel import PropertiesPanel

            fresh = PropertiesPanel(lambda *_a: None)
            names[language] = fresh._diamonds["position_x"].accessibleName()
            fresh.deleteLater()
        assert names["fr"] != names["en"]
        assert "image-clé" in names["fr"] and "keyframe" in names["en"]
    finally:
        i18n.set_language(original)
    assert panel is not None


def test_multiline_editors_do_not_trap_tab(window):
    for editor in (*window.findChildren(QTextEdit), *window.findChildren(QPlainTextEdit)):
        if editor.isReadOnly():
            continue
        assert editor.tabChangesFocus(), audit.widget_path(editor, 3)


# --- fenêtre principale : les raccourcis globaux restent intacts ------------------------------------------------------


def test_editing_workspace_has_no_button_taking_the_focus_on_click(window):
    """Garde-fou : un bouton, curseur ou liste non éditable qui prendrait le focus au clic volerait les raccourcis.

    L'espace de montage seulement : la page d'export est un formulaire, où Espace / Entrée doivent activer le bouton.
    """
    for section in audit.LIBRARY_SECTIONS:
        audit.open_library_section(window, section)
        offenders = [
            audit.widget_path(w, 4) for w in _form_controls(window)
            if w.focusPolicy() & Qt.ClickFocus and not window.export_panel.isAncestorOf(w)
        ]
        assert offenders == [], f"section {section} : {offenders}"
    audit.open_library_section(window, "media")


def test_clicking_an_inspector_button_leaves_space_to_the_playback_shortcut(window):
    """Régression : après un clic sur « Ajouter » (Suivi), Espace déclenchait de nouveau le bouton au lieu de lire."""
    panel = window.properties_panel
    window.show_tracking_panel()
    audit.settle(window)
    window.activateWindow()
    button = window.tracking_panel.add_button
    assert button.isEnabled()
    before = len(window.tracking_panel.selected_ids())
    QTest.mouseClick(button, Qt.LeftButton)
    audit.settle(window)
    assert QApplication.focusWidget() is not button
    trackers_after_click = window.tracking_panel.tracker_list.count()
    QTest.keyClick(window, Qt.Key_Space)
    assert window.is_playing
    assert window.tracking_panel.tracker_list.count() == trackers_after_click  # le bouton n'a pas été re-déclenché
    window.toggle_play()
    assert before >= 0 and panel is not None


def test_playback_shortcuts_still_run_with_focus_on_a_focusable_inspector_control(window):
    """Focus (au clavier) sur un bouton ou un curseur de l'inspecteur : les touches de commande que ce contrôle
    ne consomme pas continuent d'atteindre la fenêtre (K lit, V choisit l'outil de sélection…)."""
    panel = window.properties_panel
    panel._select_inspector_tab(1)
    audit.settle(window)
    window.activateWindow()
    focusable = [panel.color_curve_reset_button, *[w for w in panel.findChildren(QSlider) if w.isVisibleTo(panel)]]
    for control in focusable:
        control.setFocus(Qt.TabFocusReason)
        audit.settle(window)
        assert QApplication.focusWidget() is control
        QTest.keyClick(control, Qt.Key_K)
        assert window.is_playing, control.objectName()
        QTest.keyClick(control, Qt.Key_K)
        assert not window.is_playing
        window.timeline_panel.set_tool("select")
        QTest.keyClick(control, Qt.Key_B)
        assert window.timeline_panel.tool == "blade"
        QTest.keyClick(control, Qt.Key_B)
        assert window.timeline_panel.tool == "select"


def test_a_focused_button_keeps_its_own_space_key(window):
    """Au clavier (Tab), un bouton focalisé garde Espace : c'est le comportement attendu d'un formulaire."""
    panel = window.properties_panel
    panel._select_inspector_tab(1)
    audit.settle(window)
    window.activateWindow()
    button = panel.color_curve_reset_button
    clicked = []
    button.clicked.connect(lambda: clicked.append(1))
    button.setFocus(Qt.TabFocusReason)
    audit.settle(window)
    QTest.keyClick(button, Qt.Key_Space)
    assert clicked == [1]
    assert not window.is_playing


def test_typing_in_an_inspector_field_never_triggers_global_commands(window):
    panel = window.properties_panel
    panel._select_inspector_tab(0)
    audit.settle(window)
    window.activateWindow()
    spin = panel._spin_boxes["position_x"]
    assert spin.isEnabled()
    spin.setFocus(Qt.TabFocusReason)
    audit.settle(window)
    tool = window.timeline_panel.tool
    QTest.keyClicks(spin.lineEdit(), "bvrk ")
    assert not window.is_playing
    assert window.timeline_panel.tool == tool


# --- menus ----------------------------------------------------------------------------------------------------


def _mnemonic(title: str) -> str | None:
    """Lettre mnémonique (« & » + lettre, sans les « && » littéraux), sans accent ni casse."""
    match = re.search(r"(?<!&)&(?!&)(.)", title)
    if match is None:
        return None
    letter = unicodedata.normalize("NFD", match.group(1)).encode("ascii", "ignore").decode("ascii")
    return letter.casefold() or match.group(1).casefold()


ROOT_MENU_KEYS = ("menu.file", "menu.edit", "menu.view", "menu.timeline", "menu.layers", "menu.window", "menu.help")


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_menu_mnemonics_are_unique_in_every_language(language):
    original = i18n.current_language()
    try:
        i18n.set_language(language)
        titles = {key: i18n.translate(key) for key in ROOT_MENU_KEYS}
        letters = {key: _mnemonic(title) for key, title in titles.items()}
        assert all(letters.values()), f"menu sans mnémonique : {titles}"
        assert len(set(letters.values())) == len(letters), f"mnémoniques en double : {letters}"
    finally:
        i18n.set_language(original)


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_the_real_menu_bar_has_one_mnemonic_per_menu(window, monkeypatch, language):
    original = i18n.current_language()
    monkeypatch.setattr("ui.main_window.save_user_settings", lambda *_a: None)
    try:
        window._apply_settings(replace(window._settings_snapshot(), language=language))
        audit.settle(window)
        titles = [action.menu().title() for action in window.menuBar().actions() if action.menu() is not None]
        assert len(titles) >= 5
        letters = [_mnemonic(title) for title in titles]
        assert all(letters), titles
        assert len(set(letters)) == len(letters), titles
    finally:
        window._apply_settings(replace(window._settings_snapshot(), language=original))


def test_menu_bar_is_navigable_with_the_arrow_keys(window):
    bar = window.menuBar()
    actions = [action for action in bar.actions() if action.menu() is not None]
    bar.setFocus()
    bar.setActiveAction(actions[0])
    audit.settle(window)
    assert bar.activeAction() is actions[0]
    QTest.keyClick(bar, Qt.Key_Right)
    assert bar.activeAction() is actions[1]
    QTest.keyClick(bar, Qt.Key_Left)
    assert bar.activeAction() is actions[0]
    bar.setActiveAction(None)


def test_menu_titles_resolve_to_unique_alt_shortcuts(window, monkeypatch):
    """Chaque titre de menu « &X » se résout en mnémonique **Alt + X**, sans doublon, dans la langue courante.

    On ne simule pas la touche : la plateforme ``offscreen`` ne livre pas de façon fiable un Alt + lettre à une fenêtre
    qu'elle n'active pas (l'ancienne version échouait en CI sous Ubuntu et Windows alors que le menu est correct). On
    vérifie la **résolution** du titre en raccourci par Qt, ce que la barre de menus enregistre. Sous macOS Qt ne
    résout pas les mnémoniques par défaut : on l'active le temps du test, de sorte que la vérification est la même
    partout (aucun saut), puis on rétablit le réglage de la plateforme.
    """
    from PySide6.QtGui import qt_set_sequence_auto_mnemonic

    original = i18n.current_language()
    monkeypatch.setattr("ui.main_window.save_user_settings", lambda *_a: None)
    qt_set_sequence_auto_mnemonic(True)
    try:
        window._apply_settings(replace(window._settings_snapshot(), language="en"))
        titles = [action.menu().title() for action in window.menuBar().actions() if action.menu() is not None]
        sequences = [QKeySequence.mnemonic(title).toString() for title in titles]
        assert all(sequences), f"titre sans mnémonique : {titles}"
        assert len(set(sequences)) == len(sequences), f"mnémoniques en double : {dict(zip(titles, sequences))}"
        assert QKeySequence.mnemonic("&File") == QKeySequence("Alt+F")
        file_menu = window.findChild(QWidget, "file_menu")
        assert QKeySequence.mnemonic(file_menu.title()) == QKeySequence("Alt+F")
    finally:
        qt_set_sequence_auto_mnemonic(sys.platform != "darwin")     # réglage par défaut de la plateforme
        window._apply_settings(replace(window._settings_snapshot(), language=original))