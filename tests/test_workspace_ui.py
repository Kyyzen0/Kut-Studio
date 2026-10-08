"""Tests d'intégration du système de panneaux de Kut-Studio.

Ces tests exercent l'intégration Qt (fenêtre principale, gestionnaire
d'espace de travail) sans dépendre d'un écran réel : ils vérifient
surtout les invariants qui protègent l'architecture.

Invariant central : **un panneau n'est jamais dupliqué**. Détacher puis
rattacher doit rendre exactement le même objet, sinon deux timelines
divergeraient — ce qui est explicitement interdit.
"""


import pytest
from PySide6.QtWidgets import QMenu

from core.workspace_state import DockArea, PanelId
from ui import i18n
from ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    """Fenêtre principale isolée, sans toucher aux préférences réelles."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path))
    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1400, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_every_panel_is_registered(window):
    manager = window.workspace
    for panel in PanelId:
        assert panel in manager._panels


def test_panels_are_docked_by_default(window):
    manager = window.workspace
    assert not manager.is_floating(PanelId.TIMELINE)
    assert manager.is_visible(PanelId.TIMELINE)
    assert manager.is_visible(PanelId.VIEWER)
    assert manager.is_visible(PanelId.MEDIA)
    assert manager.is_visible(PanelId.INSPECTOR)


def test_timeline_has_a_usable_height(window):
    window.workspace.balance_vertical_split()
    assert window.timeline_panel.height() >= window.timeline_panel.minimumHeight()


# ---------------------------------------------------------------------------
# Détachement / rattachement
# ---------------------------------------------------------------------------


def test_float_panel_keeps_the_same_widget(window):
    manager = window.workspace
    timeline = window.timeline_panel
    manager.float_panel(PanelId.TIMELINE)
    assert manager.is_floating(PanelId.TIMELINE)
    assert manager._panels[PanelId.TIMELINE] is timeline


def test_float_panel_moves_it_out_of_the_dock_zone(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    assert not manager.state.visible_panels(DockArea.BOTTOM)


def test_dock_panel_restores_the_same_widget(window):
    manager = window.workspace
    timeline = window.timeline_panel
    manager.float_panel(PanelId.TIMELINE)
    manager.dock_panel(PanelId.TIMELINE)
    assert not manager.is_floating(PanelId.TIMELINE)
    assert manager._panels[PanelId.TIMELINE] is timeline


def test_dock_returns_content_to_its_host_before_destroying_window(window):
    """Rattacher une fenêtre ne doit pas orpheliner son panneau Qt."""
    manager = window.workspace
    timeline = window.timeline_panel
    manager.float_panel(PanelId.TIMELINE)

    manager.dock_panel(PanelId.TIMELINE)

    host = manager.host_of(PanelId.TIMELINE)
    assert host is not None
    assert timeline.parentWidget() is host
    assert host.layout().indexOf(timeline) >= 0
    assert PanelId.TIMELINE not in manager._windows


def test_dock_panel_cleans_up_the_window(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    assert PanelId.TIMELINE in manager._windows
    manager.dock_panel(PanelId.TIMELINE)
    assert PanelId.TIMELINE not in manager._windows


def test_state_survives_detach_and_dock(window):
    manager = window.workspace
    timeline = window.timeline_panel
    manager.float_panel(PanelId.TIMELINE)
    window.seek_to_position(2.0)
    timeline.select_clip(timeline.clip_views[0].id)
    playhead = timeline.playhead_seconds
    selected = timeline.selected_clip_id
    manager.dock_panel(PanelId.TIMELINE)
    assert timeline.playhead_seconds == playhead
    assert timeline.selected_clip_id == selected
    assert len(timeline.clip_widgets) == len(timeline.clip_views)


def test_project_is_the_single_source_of_truth(window):
    # La vue détachée et la vue dockée partagent le projet.
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    assert window.timeline_panel.project is window.project
    manager.dock_panel(PanelId.TIMELINE)
    assert window.timeline_panel.project is window.project


def test_toggle_panel_float_is_idempotent(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    manager.float_panel(PanelId.TIMELINE)
    assert manager.is_floating(PanelId.TIMELINE)
    assert len(manager._windows) == 1


# ---------------------------------------------------------------------------
# Visibilité
# ---------------------------------------------------------------------------


def test_hide_then_show_panel(window):
    manager = window.workspace
    manager.set_panel_visible(PanelId.INSPECTOR, False)
    assert not manager.is_visible(PanelId.INSPECTOR)
    manager.set_panel_visible(PanelId.INSPECTOR, True)
    assert manager.is_visible(PanelId.INSPECTOR)


def test_toggle_panel_flips_visibility(window):
    manager = window.workspace
    before = manager.is_visible(PanelId.MEDIA)
    manager.toggle_panel(PanelId.MEDIA)
    assert manager.is_visible(PanelId.MEDIA) is not before


def test_empty_area_is_hidden(window):
    manager = window.workspace
    manager.set_panel_visible(PanelId.INSPECTOR, False)
    assert not manager._zones[DockArea.RIGHT].isVisible()


def test_restoring_a_panel_reappears_in_its_zone(window):
    manager = window.workspace
    manager.set_panel_visible(PanelId.INSPECTOR, False)
    manager.set_panel_visible(PanelId.INSPECTOR, True)
    assert manager._zones[DockArea.RIGHT].isVisible()


# ---------------------------------------------------------------------------
# Maximisation
# ---------------------------------------------------------------------------


def test_maximize_hides_the_other_panels(window):
    manager = window.workspace
    manager.maximize_panel(PanelId.TIMELINE)
    assert manager.state.maximized is PanelId.TIMELINE
    assert manager.is_shown(PanelId.TIMELINE)
    # Les autres restent « ouverts » mais ne sont plus affichés.
    assert manager.is_visible(PanelId.VIEWER)
    assert not manager.is_shown(PanelId.VIEWER)
    assert not manager.is_shown(PanelId.MEDIA)


def test_maximize_keeps_panels_open_in_the_state(window):
    manager = window.workspace
    manager.maximize_panel(PanelId.TIMELINE)
    for panel in (PanelId.TIMELINE, PanelId.VIEWER, PanelId.MEDIA, PanelId.INSPECTOR):
        assert manager.state.get(panel).visible


def test_restore_brings_back_the_previous_layout(window):
    manager = window.workspace
    manager.maximize_panel(PanelId.VIEWER)
    manager.restore_layout()
    assert manager.state.maximized is None
    # Le mixeur reste replié : il n'a pas été ouvert par cette séquence.
    for panel in (PanelId.TIMELINE, PanelId.VIEWER, PanelId.MEDIA, PanelId.INSPECTOR):
        assert manager.is_visible(panel)


def test_maximize_toggle_restores(window):
    manager = window.workspace
    manager.maximize_panel(PanelId.TIMELINE)
    manager.maximize_panel(PanelId.TIMELINE)
    assert manager.state.maximized is None


def test_maximize_keeps_timeline_functional(window):
    manager = window.workspace
    manager.maximize_panel(PanelId.TIMELINE)
    window.seek_to_position(1.0)
    assert window.timeline_panel.playhead_seconds == 1.0
    assert window.timeline_panel.time_label.text() == "00:01"


# ---------------------------------------------------------------------------
# Réinitialisation
# ---------------------------------------------------------------------------


def test_reset_layout_restores_defaults(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    manager.set_panel_visible(PanelId.MEDIA, False)
    manager.reset_layout()
    assert not manager.is_floating(PanelId.TIMELINE)
    assert manager.is_visible(PanelId.MEDIA)


def test_reset_panel_size_uses_preferred_size(window):
    from core.workspace_state import DEFAULT_SIZE

    manager = window.workspace
    manager.reset_panel_size(PanelId.TIMELINE)
    assert manager.state.get(PanelId.TIMELINE).size == DEFAULT_SIZE[PanelId.TIMELINE]


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def test_workspace_is_persisted_on_close(window, tmp_path):
    manager = window.workspace
    manager.set_panel_visible(PanelId.INSPECTOR, False)
    manager.save()
    path = tmp_path / "workspace.json"
    assert path.exists()
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    panels = {entry["panel"]: entry for entry in payload["panels"]}
    assert panels["inspector"]["visible"] is False


def test_capture_reads_real_splitter_sizes(window):
    manager = window.workspace
    manager.balance_vertical_split()
    captured = manager.capture_state()
    assert captured.get(PanelId.TIMELINE).size > 0
    assert 0.1 <= captured.center_ratio <= 0.9


def test_opening_mixer_reserves_both_panel_minimum_heights(window, qtbot):
    """Timeline et mixeur restent réellement utilisables dès l'ouverture."""
    manager = window.workspace
    manager.set_panel_visible(PanelId.MIXER, True)
    qtbot.waitUntil(
        lambda: window.height() >= window.minimumHeight(), timeout=1000
    )
    qtbot.wait(20)

    zone = manager._zones[DockArea.BOTTOM]
    sizes = zone._splitter.sizes()
    assert len(sizes) == 2
    assert all(
        size >= widget.minimumSizeHint().height()
        for size, widget in zip(sizes, zone.widgets())
    )


def test_shutdown_releases_windows(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    manager.shutdown()
    assert manager._windows == {}
    assert manager._hosts == {}


# ---------------------------------------------------------------------------
# Cohérence d'état
# ---------------------------------------------------------------------------


def test_state_has_no_duplicate_source(window):
    # Une seule instance de chaque composant, quel que soit le nombre
    # de fenêtres.
    manager = window.workspace
    ids = [id(manager._panels[p]) for p in PanelId]
    assert len(set(ids)) == len(ids)


def test_actions_are_built_for_every_panel(window):
    manager = window.workspace
    for panel in PanelId:
        actions = manager.build_actions(panel)
        assert actions, f"aucune action pour {panel.value}"
        # Les séparateurs et sous-menus n'ont pas de libellé plat.
        labels = [
            a.text()
            for a in actions
            if not isinstance(a, QMenu) and not a.isSeparator()
        ]
        assert labels and all(labels)


def test_context_menu_offers_detach_and_attach(window):
    manager = window.workspace
    labels = [
        a.text()
        for a in manager.build_actions(PanelId.TIMELINE)
        if not isinstance(a, QMenu)
    ]
    assert "Détacher le panneau" in labels
    manager.float_panel(PanelId.TIMELINE)
    labels = [
        a.text()
        for a in manager.build_actions(PanelId.TIMELINE)
        if not isinstance(a, QMenu)
    ]
    assert "Rattacher" in labels


def test_context_menu_offers_every_dock_area(window):
    manager = window.workspace
    move_menus = [
        a
        for a in manager.build_actions(PanelId.TIMELINE)
        if isinstance(a, QMenu)
    ]
    assert len(move_menus) == 1
    areas = [entry.text() for entry in move_menus[0].actions()]
    assert len(areas) == len(DockArea)


def test_nested_panel_menu_is_not_reparented_into_its_parent_menu(window):
    """Un sous-menu doit rester un popup Qt, jamais un widget superposé."""
    host = window.workspace.host_of(PanelId.VIEWER)
    assert host is not None
    host.refresh_actions()
    menu = host._options._menu
    move_menu = next(action.menu() for action in menu.actions() if action.menu())

    # Le QMenu est créé par le gestionnaire avec la fenêtre comme parent.
    # Le reparentage au menu parent fait calculer une position locale et
    # provoque le chevauchement visible dans l'interface.
    assert move_menu.parentWidget() is window


def test_move_panel_changes_its_area(window):
    manager = window.workspace
    assert manager.state.area_of(PanelId.MEDIA) is DockArea.LEFT
    manager.move_panel(PanelId.MEDIA, DockArea.RIGHT)
    assert manager.state.area_of(PanelId.MEDIA) is DockArea.RIGHT
    assert manager.is_visible(PanelId.MEDIA)


def test_move_panel_keeps_the_same_widget(window):
    manager = window.workspace
    panel = window.project_panel
    manager.move_panel(PanelId.MEDIA, DockArea.RIGHT)
    assert manager._panels[PanelId.MEDIA] is panel


def test_move_panel_vacues_the_origin_area(window):
    manager = window.workspace
    manager.move_panel(PanelId.MEDIA, DockArea.RIGHT)
    # On compte les panneaux réellement présents, pas les enfants du
    # layout de zone (qui contient le QSplitter interne).
    assert manager._zones[DockArea.LEFT].widgets() == []
    assert not manager._zones[DockArea.LEFT].isVisible()
    assert len(manager._zones[DockArea.RIGHT].widgets()) == 2


def test_move_panel_docks_a_floating_panel_first(window):
    manager = window.workspace
    manager.float_panel(PanelId.MEDIA)
    manager.move_panel(PanelId.MEDIA, DockArea.RIGHT)
    assert not manager.is_floating(PanelId.MEDIA)
    assert manager.state.area_of(PanelId.MEDIA) is DockArea.RIGHT


def test_move_panel_to_same_area_is_a_noop(window):
    manager = window.workspace
    before = manager.state.get(PanelId.MEDIA)
    manager.move_panel(PanelId.MEDIA, DockArea.LEFT)
    assert manager.state.get(PanelId.MEDIA) == before


# ---------------------------------------------------------------------------
# Espaces de travail
# ---------------------------------------------------------------------------


def test_builtin_workspaces_are_offered(window):
    from core.workspace_state import BUILTIN_WORKSPACES

    offered = window.workspace.list_workspaces()
    for key in BUILTIN_WORKSPACES:
        assert key in offered


def test_apply_workspace_changes_visibility(window):
    manager = window.workspace
    assert manager.apply_workspace("audio") is True
    assert not manager.is_visible(PanelId.VIEWER)
    assert manager.is_visible(PanelId.TIMELINE)


def test_apply_unknown_workspace_is_a_noop(window):
    manager = window.workspace
    before = manager.state
    assert manager.apply_workspace("inexistant") is False
    assert manager.state == before


def test_apply_workspace_docks_floating_panels(window):
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    manager.apply_workspace("editing")
    assert not manager.is_floating(PanelId.TIMELINE)
    assert manager._windows == {}


def test_saved_workspace_round_trips(window):
    manager = window.workspace
    manager.set_panel_visible(PanelId.MEDIA, False)
    assert manager.save_workspace_as("Perso") is True
    manager.set_panel_visible(PanelId.MEDIA, True)
    assert manager.apply_workspace("Perso") is True
    assert not manager.is_visible(PanelId.MEDIA)


def test_blank_workspace_name_is_rejected(window):
    assert window.workspace.save_workspace_as("   ") is False


def test_hiding_center_collapses_the_zone(window):
    # Un vide noir au centre serait un défaut visuel : la zone doit
    # réellement se replier et laisser la place aux autres.
    manager = window.workspace
    manager.balance_vertical_split()
    manager.set_panel_visible(PanelId.VIEWER, False)
    assert manager._top_splitter.sizes()[1] == 0
    manager.set_panel_visible(PanelId.VIEWER, True)
    assert manager._top_splitter.sizes()[1] > 0


def test_no_two_actions_share_the_same_shortcut(window):
    """Deux actions sur le même raccourci le rendent ambigu : aucune ne part."""
    from collections import defaultdict

    from PySide6.QtGui import QAction

    by_shortcut: dict[str, list[str]] = defaultdict(list)
    for action in window.findChildren(QAction):
        for sequence in action.shortcuts():
            by_shortcut[sequence.toString()].append(action.text())
    duplicates = {key: names for key, names in by_shortcut.items() if len(names) > 1}
    assert duplicates == {}


# ---------------------------------------------------------------------------
# Langue : noms des panneaux, menu d'options, fenêtres détachées
# ---------------------------------------------------------------------------


@pytest.fixture
def language_reset():
    """À demander **avant** ``window`` : ``reset_for_tests`` retire les abonnés, ceux de la fenêtre compris."""
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _flat_labels(manager, panel):
    return [a.text() for a in manager.build_actions(panel) if not isinstance(a, QMenu) and not a.isSeparator()]


@pytest.mark.parametrize(("language", "detach", "attach", "move_to", "media", "left_area"), [
    ("en", "Detach panel", "Attach", "Move to…", "Media", "Left area"),
    ("es", "Desacoplar panel", "Acoplar", "Mover a…", "Medios", "Zona izquierda"),
    ("fr", "Détacher le panneau", "Rattacher", "Déplacer vers…", "Médias", "Zone gauche"),
])
def test_panel_menus_and_floating_windows_follow_the_language(
    language_reset, window, language, detach, attach, move_to, media, left_area
):
    """Menu d'options des panneaux, noms du menu Fenêtre et fenêtre détachée suivent la langue à chaud."""
    manager = window.workspace
    manager.float_panel(PanelId.TIMELINE)
    manager.dock_panel(PanelId.TIMELINE)
    manager.build_actions(PanelId.MEDIA)                  # actions mises en cache dans la langue d'origine
    i18n.set_language("es" if language == "en" else "en")  # une langue autre que la cible, puis la cible
    window._retranslate_ui()
    i18n.set_language(language)
    with i18n.strict_translations():
        window._retranslate_ui()
    assert detach in _flat_labels(manager, PanelId.MEDIA)
    move_menu = next(a for a in manager.build_actions(PanelId.MEDIA) if isinstance(a, QMenu))
    assert move_menu.title() == move_to
    assert left_area in [a.text() for a in move_menu.actions()]
    panel_names = [a.text() for a in window.window_menu.findChild(QMenu, "panels_menu").actions()]
    assert media in panel_names
    manager.float_panel(PanelId.TIMELINE)
    assert attach in _flat_labels(manager, PanelId.TIMELINE)
    floating = manager._windows[PanelId.TIMELINE]
    assert floating.windowTitle().startswith("Kut-Studio — ")
    assert floating._dock_button.toolTip() == i18n.translate("workspace.dock_tooltip")


def test_a_floating_window_is_retranslated_while_it_is_open(language_reset, window):
    manager = window.workspace
    manager.float_panel(PanelId.MIXER)
    floating = manager._windows[PanelId.MIXER]
    assert floating.windowTitle() == "Kut-Studio — Mixeur"
    i18n.set_language("en")
    assert floating.windowTitle() == "Kut-Studio — Mixer"
    assert floating._options._button.toolTip() == "Mixer panel options"
    assert floating._title_label.text() == "Mixer"


def test_saving_a_workspace_asks_and_confirms_in_the_current_language(language_reset, window, monkeypatch):
    texts = []
    monkeypatch.setattr("ui.main_window_mixins.workspace_actions.QInputDialog.getText",
                        lambda _parent, title, label, *a, **k: (texts.append((title, label)) or ("Mon espace", True)))
    monkeypatch.setattr("ui.main_window.QMessageBox.information",
                        lambda _parent, title, message, *a, **k: texts.append((title, message)))
    i18n.set_language("en")
    window.save_workspace_as()
    assert texts == [("Workspace", "Workspace name:"), ("Workspace", "Layout saved as “Mon espace”.")]
