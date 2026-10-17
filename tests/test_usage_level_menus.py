"""Niveau d'usage des menus et des surfaces (ADR-0006, lot 4) : menus, menus contextuels, rail, barre de la timeline.

Fenêtre réelle (offscreen). La référence d'Avancé est ``tests/data/menus_before_usage_levels.json``, produite par
``tests/menu_order.py`` sur la fenêtre d'``origin/main`` d'avant le lot. Les raccourcis sont éprouvés par de vraies frappes
(fenêtre active) : un menu qui perd une entrée ne doit jamais perdre son raccourci.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from menu_order import (
    capture_menus, clip_menu_dump, full_dump, load_scenario, track_menu_dump, window_dump,
)
from PySide6.QtTest import QTest
from test_scopes import _window

from core.usage_level import UsageLevel
from core.user_settings import UserSettings, save_user_settings
from ui import i18n

GOLDEN = json.loads((Path(__file__).parent / "data" / "menus_before_usage_levels.json").read_text(encoding="utf-8"))
SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    i18n.set_language("fr")
    main = _window(qtbot, monkeypatch, tmp_path)
    main.set_usage_level("advanced")
    return main


def _texts(tree) -> list[str]:
    """Tous les libellés d'un arbre de menu (sous-menus compris), sans le raccourci."""
    out: list[str] = []
    for entry in tree:
        if isinstance(entry, dict):
            for title, children in entry.items():
                out.append(title)
                out.extend(_texts(children))
        elif entry != "-":
            out.append(re.sub(r" \[[^\]]*\]$", "", entry))
    return out


def _separators_are_clean(tree) -> bool:
    """Ni séparateur en tête ou en queue, ni deux de suite, dans le menu et dans ses sous-menus."""
    if tree and (tree[0] == "-" or tree[-1] == "-"):
        return False
    if any(a == "-" and b == "-" for a, b in zip(tree, tree[1:])):
        return False
    return all(_separators_are_clean(list(e.values())[0]) for e in tree if isinstance(e, dict))


# (menu, libellé, niveau minimal) : la table de docs/usage-levels.md, en français.
ENTRIES = [
    ("file_menu", "Nouveau", SIMPLE), ("file_menu", "Quitter", SIMPLE), ("help_menu", "À propos de Kut-Studio", SIMPLE),
    ("edit_menu", "Annuler", SIMPLE), ("edit_menu", "Supprimer le clip", SIMPLE),
    ("edit_menu", "Supprimer avec ripple", STUDIO),
    ("timeline_menu", "Marqueur", SIMPLE), ("timeline_menu", "Ajouter une piste audio", SIMPLE),
    ("timeline_menu", "Muet / actif", SIMPLE),
    ("timeline_menu", "Découper aux changements de plan", STUDIO),
    ("timeline_menu", "Générer les proxies des clips sélectionnés", STUDIO),
    ("timeline_menu", "Nouvelle séquence", ADVANCED), ("timeline_menu", "Convertir en composition", ADVANCED),
    ("timeline_menu", "Dupliquer la séquence", ADVANCED), ("timeline_menu", "Réglages Multicam…", ADVANCED),
    ("layers_menu", "Ajouter", SIMPLE), ("layers_menu", "Texte", SIMPLE),
    ("layers_menu", "Réinitialiser le recadrage", SIMPLE),
    ("layers_menu", "Forme", STUDIO), ("layers_menu", "Contrôleur (null)", STUDIO),
    ("layers_menu", "Calque d'effets (adjustment)", STUDIO), ("layers_menu", "Grouper", STUDIO),
    ("layers_menu", "Copier les calques", STUDIO), ("layers_menu", "Enregistrer la sélection comme preset…", STUDIO),
    ("layers_menu", "Zones de sécurité", STUDIO), ("layers_menu", "Repère central", STUDIO),
    ("layers_menu", "Aligner sur le cadre", STUDIO), ("layers_menu", "Ajouter un guide horizontal", STUDIO),
    ("layers_menu", "Flou de mouvement (séquence)", ADVANCED), ("layers_menu", "Suivi (tracking)…", ADVANCED),
    ("social_menu", "Nouveau projet réseaux sociaux…", SIMPLE), ("social_menu", "Zones de la plateforme", SIMPLE),
    ("social_menu", "Remplir le cadre (recadrer)", SIMPLE), ("social_menu", "Sous-titres automatiques", SIMPLE),
    ("social_menu", "Couverture à la tête de lecture", SIMPLE),
    ("social_menu", "Grille rythmique", STUDIO), ("social_menu", "Impact", STUDIO), ("social_menu", "Audio", STUDIO),
    ("social_menu", "Exporter en plusieurs formats…", STUDIO), ("social_menu", "Classement…", STUDIO),
    ("social_menu", "Photos importées", STUDIO),
    ("window_menu", "Page Montage", SIMPLE), ("window_menu", "Page Audio", SIMPLE),
    ("window_menu", "Réinitialiser la disposition", SIMPLE), ("window_menu", "Panneaux", SIMPLE),
    ("window_menu", "Espaces de travail", SIMPLE), ("window_menu", "Niveau d'usage", SIMPLE),
    ("window_menu", "Page Couleur", STUDIO), ("window_menu", "Afficher les scopes", STUDIO),
    ("window_menu", "Page Composition", ADVANCED), ("window_menu", "Éditeur de courbes", ADVANCED),
    ("window_menu", "Diagnostics de performance", ADVANCED),
]


def test_at_advanced_menus_and_surfaces_are_those_of_origin_main(window, monkeypatch):
    assert window._menu_level_unresolved == []
    assert full_dump(window, monkeypatch) == GOLDEN


@pytest.mark.parametrize("level", list(UsageLevel), ids=lambda level: level.value)
def test_each_level_shows_the_expected_menu_entries(window, level):
    window.set_usage_level(level)
    menus = window_dump(window)["menus"]
    for menu, text, minimum in ENTRIES:
        assert (text in _texts(menus[menu])) is level.at_least(minimum), (level, menu, text)
    assert all(_separators_are_clean(tree) for tree in menus.values())


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_every_gate_finds_its_entry_in_each_language(qtbot, monkeypatch, tmp_path, language):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path))
    save_user_settings(replace(UserSettings(), language=language, usage_level="simple"))
    main = _window(qtbot, monkeypatch, tmp_path)
    assert main._menu_level_unresolved == []
    i18n.set_language("fr")


@pytest.mark.parametrize("level", list(UsageLevel), ids=lambda level: level.value)
def test_every_command_action_stays_attached_to_the_window_at_every_level(window, level):
    window.set_usage_level(level)
    attached = {id(a) for a in window.actions()}
    commands = window.shortcuts.command_actions()
    assert commands and all(id(a) in attached for a in commands)


def _reachable(window) -> set[int]:
    """Identifiants des actions encore présentes dans l'arbre des menus de la barre."""
    found: set[int] = set()

    def walk(menu) -> None:
        for action in menu.actions():
            found.add(id(action))
            if action.menu() is not None:
                walk(action.menu())

    for bar_action in window.menuBar().actions():
        walk(bar_action.menu())
    return found


def _press(window, monkeypatch, command: str) -> bool:
    action = window.shortcuts.action(command)
    calls: list[int] = []
    monkeypatch.setitem(window.shortcuts._handlers, command, lambda: calls.append(1))
    QTest.keySequence(window, action.shortcut())
    return bool(calls)


@pytest.mark.parametrize("level", list(UsageLevel), ids=lambda level: level.value)
def test_shortcuts_fire_at_every_level_even_when_their_menu_entry_is_gone(window, monkeypatch, level):
    window.set_usage_level(level)
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    reachable = _reachable(window)
    gone = [command for command, action in window.shortcuts._actions.items()
            if action.shortcut().toString() and id(action) not in reachable]
    if level is SIMPLE:
        assert {"ripple_delete", "layer_group", "multicam_viewer", "toggle_scopes", "graph_editor"} <= set(gone)
    if level is ADVANCED:
        assert gone == []
    for command in gone:
        assert _press(window, monkeypatch, command), (level, command, "raccourci muet")
    window.hide()


def test_a_hidden_action_that_is_not_attached_would_lose_its_shortcut(window, monkeypatch):
    """Le test précédent a une dent : retirer l'action de la fenêtre ET du menu la rend muette."""
    window.set_usage_level("simple")
    action = window.shortcuts.action("ripple_delete")
    window.removeAction(action)
    assert id(action) not in _reachable(window)
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    calls: list[int] = []
    monkeypatch.setitem(window.shortcuts._handlers, "ripple_delete", lambda: calls.append(1))
    QTest.keySequence(window, action.shortcut())
    assert not calls
    window.hide()


def test_switching_level_hot_keeps_the_same_menus_actions_and_order(window):
    bar = [a.menu() for a in window.menuBar().actions()]
    layers = next(m for m in bar if m.objectName() == "layers_menu")
    originals = list(layers.actions())
    for level in (SIMPLE, STUDIO, SIMPLE, ADVANCED, STUDIO, ADVANCED):
        window.set_usage_level(level)
        assert [a.menu() for a in window.menuBar().actions()] == bar
    assert list(layers.actions()) == originals
    assert window_dump(window) == {k: GOLDEN[k] for k in ("menus", "toolbar", "rail", "pages")}


def test_hidden_entries_are_retranslated_and_come_back_translated(window):
    window.set_usage_level("simple")
    i18n.set_language("en")
    window.on_language_changed("en")
    window.set_usage_level("advanced")
    menus = window_dump(window)["menus"]
    assert i18n.translate("sequence.action.new") in _texts(menus["timeline_menu"])
    assert i18n.translate("menu.item.group_layers") in _texts(menus["layers_menu"])
    assert "Nouvelle séquence" not in _texts(menus["timeline_menu"])
    i18n.set_language("fr")


def test_a_level_never_enables_or_disables_an_action(window):
    def states() -> dict[str, bool]:
        return {action.text(): action.isEnabled() for bar_action in window.menuBar().actions()
                for action in bar_action.menu().actions() if not action.isSeparator()}

    baseline = {id(a): a.isEnabled() for a in window.shortcuts.command_actions()}
    placeholders = {name: enabled for name, enabled in states().items() if not enabled}
    assert placeholders                                               # Couper / Copier / Coller : désactivées à dessein
    for level in (SIMPLE, STUDIO, SIMPLE, ADVANCED):
        window.set_usage_level(level)
        assert {id(a): a.isEnabled() for a in window.shortcuts.command_actions()} == baseline
        assert {name for name, enabled in states().items() if not enabled} <= set(placeholders)


def test_the_timeline_toolbar_shows_studio_tools_from_studio_and_keeps_their_keys(window):
    panel = window.timeline_panel
    window.set_usage_level("simple")
    shown = window_dump(window)["toolbar"]
    assert shown == ["snap_button", "blade_button", "ripple_button", "marker_button"]
    window.shortcuts.trigger("tool_roll")                       # le raccourci agit par le bouton, caché ou non
    assert panel.tool == "roll" and panel.roll_button.isChecked()
    window.set_usage_level("studio")
    assert window_dump(window)["toolbar"] == GOLDEN["toolbar"] and panel.tool == "roll"
    window.set_usage_level("simple")
    assert panel.tool == "select" and not panel.roll_button.isChecked()      # un outil caché ne reste pas actif
    assert all(getattr(panel, name).isEnabled() for name in GOLDEN["toolbar"])


def test_a_recording_in_progress_keeps_its_button(window):
    panel = window.timeline_panel
    window.set_usage_level("simple")
    assert panel.record_button.isHidden()
    panel.record_button.blockSignals(True)
    panel.record_button.setChecked(True)
    panel.record_button.blockSignals(False)
    window.set_usage_level("simple")
    panel._apply_toolbar_level()
    assert not panel.record_button.isHidden()
    panel.record_button.setChecked(False)                         # fin de l'enregistrement (signaux compris)
    assert panel.record_button.isHidden()


def test_the_rail_shows_the_sections_of_the_level_and_never_disables_one(window):
    expected = {
        SIMPLE: ["media", "edit", "text", "audio", "graphics"],
        STUDIO: GOLDEN["rail"], ADVANCED: GOLDEN["rail"],
    }
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        assert window_dump(window)["rail"] == expected[level]
        assert all(b.isEnabled() for b in window.side_rail._buttons.values())
    window.side_rail.set_usage_level("expert")                  # valeur invalide : niveau inchangé
    assert window.side_rail.usage_level is ADVANCED


def _clip_menu(window, monkeypatch, clip_id: str):
    return clip_menu_dump(window, clip_id, capture_menus(monkeypatch))


@pytest.mark.parametrize("level", list(UsageLevel), ids=lambda level: level.value)
def test_the_clip_menu_per_level(window, monkeypatch, level):
    ids = load_scenario(window, monkeypatch)
    window.set_usage_level(level)
    plain = _clip_menu(window, monkeypatch, ids["plain"])
    multicam = _clip_menu(window, monkeypatch, ids["multicam"])
    texts = _texts(plain)
    for text in ("Couper au playhead", "Dupliquer", "Activer / désactiver", "Supprimer"):
        assert text in texts
    assert ("Supprimer et refermer" in texts) is level.at_least(STUDIO)
    assert ("Vitesse" in texts) is level.at_least(STUDIO)
    assert ("Courbe de vitesse" in texts) is level.at_least(ADVANCED) and ("Images intermédiaires" in texts) is level.at_least(ADVANCED)
    for text in ("Sens inverse", "Copier le temps", "Réinitialiser le temps"):
        assert (text in texts) is level.at_least(STUDIO)
    for text in ("Créer une séquence à partir de la sélection", "Convertir en composition", "Créer une séquence Multicam…"):
        assert (text in texts) is level.at_least(ADVANCED)
    assert ("Aplatir le segment Multicam" in _texts(multicam)) is level.at_least(ADVANCED)
    assert ("Ouvrir la séquence imbriquée" in _texts(multicam)) is level.at_least(ADVANCED)
    assert _separators_are_clean(plain) and _separators_are_clean(multicam)


def test_cancelling_the_clip_menu_does_nothing_at_any_level_and_a_choice_still_works(window, monkeypatch):
    import ui.timeline_panel_mixins.drag_tools as drag_tools

    ids = load_scenario(window, monkeypatch)
    panel = window.timeline_panel
    emitted: list[str] = []
    for name in ("nest_selection_requested", "composition_convert_requested", "multicam_create_requested",
                 "ripple_delete_requested", "duplicate_requested", "blade_cut_requested"):
        getattr(panel, name).connect(lambda *_a, name=name: emitted.append(name))
    choice: dict = {"text": None}

    class _Choosing(drag_tools.QMenu):
        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt
            return next((a for a in self.actions() if a.text() == choice["text"]), None)

    monkeypatch.setattr(drag_tools, "QMenu", _Choosing)
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        panel.open_clip_menu(ids["plain"], None)                 # annulé
        assert emitted == [], level
    window.set_usage_level("studio")
    choice["text"] = "Supprimer et refermer"
    panel.open_clip_menu(ids["plain"], None)
    assert emitted == ["ripple_delete_requested"]
    emitted.clear()
    window.set_usage_level("simple")
    choice["text"] = "Dupliquer"
    panel.open_clip_menu(ids["plain"], None)
    assert emitted == ["duplicate_requested"]


def test_the_track_menu_has_audio_extras_only_at_advanced(window, monkeypatch):
    load_scenario(window, monkeypatch)
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        texts = _texts(track_menu_dump(window, "A1"))
        assert "Hauteur de piste" in texts and "Renommer" in texts and "Supprimer" in texts
        for text in ("Courbe de volume", "Rôle de la piste", "Baisser sous…"):
            assert (text in texts) is (level is ADVANCED)
        assert _texts(track_menu_dump(window, "V1")) == ["Hauteur de piste", "Réduire ou développer", "Monter",
                                                        "Descendre", "Renommer", "Supprimer"]


def test_the_asset_menu_per_level_and_multicam_for_a_multiple_selection(window, monkeypatch):
    from menu_order import asset_menu_dump

    load_scenario(window, monkeypatch)
    shown = capture_menus(monkeypatch)
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        texts = _texts(asset_menu_dump(window, monkeypatch, shown))
        for text in ("Renommer…", "Déplacer vers", "Supprimer de la bibliothèque"):
            assert text in texts
        for text in ("Tags", "Proxy", "Espace colorimétrique"):
            assert (text in texts) is level.at_least(STUDIO), (level, text)
        assert any(t.endswith("Relier le fichier…") for t in texts) is level.at_least(STUDIO)

    class _Bin:
        selected_asset_ids = ["camA", "camB"]
        selected_asset_id = "camA"

    monkeypatch.setattr(window.project_panel, "_current_bin", lambda: _Bin())
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        texts = _texts(asset_menu_dump(window, monkeypatch, shown))
        assert (any(t.startswith("Créer une séquence Multicam") for t in texts)) is (level is ADVANCED), level


def test_a_locked_track_refuses_the_ripple_delete_at_every_level(window, monkeypatch):
    ids = load_scenario(window, monkeypatch)
    window.project.tracks[0].locked = True
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *_, **__: None)
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        window.timeline_panel.selected_clip_id = ids["plain"]
        window.shortcuts.trigger("ripple_delete")
        assert [c.id for c in window.project.tracks[0].clips] == ["plain"], level


def test_page_buttons_per_level(window):
    expected = {SIMPLE: ["edit", "audio"], STUDIO: ["edit", "color", "audio"], ADVANCED: GOLDEN["pages"]}
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        assert window_dump(window)["pages"] == expected[level], level
        assert all(button.isEnabled() for button in window.page_buttons.values())
    window.set_usage_level("simple")
    assert window.page_buttons["composition"].isHidden() and window.page_buttons["color"].isHidden()


@pytest.mark.parametrize("page,descend_to", [("composition", "simple"), ("composition", "studio"), ("color", "simple")])
def test_going_down_from_a_hidden_page_falls_back_to_edit_and_keeps_the_work(window, monkeypatch, page, descend_to):
    ids = load_scenario(window, monkeypatch)
    window.timeline_panel.selected_clip_id = ids["plain"]
    window.playhead_seconds = 2.0
    project = window.project
    window.switch_page(page)
    assert window.workspace.page == page
    window.set_usage_level(descend_to)
    assert window.workspace.page == "edit" and window.page_buttons["edit"].isChecked()
    assert window.project is project and window.timeline_panel.selected_clip_id == ids["plain"]
    assert window.playhead_seconds == 2.0
    window.set_usage_level("advanced")
    window.switch_page(page)                                       # le niveau supérieur rouvre la page
    assert window.workspace.page == page


def test_a_page_stays_when_the_new_level_still_allows_it(window):
    window.set_usage_level("studio")
    window.switch_page("color")
    window.set_usage_level("advanced")
    window.set_usage_level("studio")
    assert window.workspace.page == "color"


@pytest.mark.parametrize("section", ["effects", "transitions", "sequences", "templates"])
def test_going_down_from_a_hidden_rail_section_returns_to_media(window, section):
    window.side_rail.set_active(section)
    window._on_side_rail_changed(section)
    window.set_usage_level("simple")
    assert window.side_rail.active() == "media" and not window.side_rail._buttons["media"].isHidden()
    assert window.workspace.page == "edit"


def test_a_page_command_still_works_when_its_button_is_hidden(window):
    window.set_usage_level("simple")
    assert window.page_buttons["composition"].isHidden()
    window.shortcuts.trigger("page_composition")                   # même chemin que le raccourci de la commande
    assert window.workspace.page == "composition"
    action = window.shortcuts.action("page_composition")
    assert action in window.actions()
