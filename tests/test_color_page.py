"""Page Couleur : roues, éditeur de nœuds, pages Montage / Couleur, et la fenêtre qui relie tout au projet."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph, as_graph
from core.color_wheels import puck_from_wheel
from core.workspace_state import (
    PAGE_COLOR,
    PAGE_EDIT,
    DockArea,
    PanelId,
    load_page_state,
    load_workspace_state,
    page_state_path,
)
from ui import i18n
from ui.color_page.node_editor import NODE_GAP, NODE_WIDTH, NodeEditor
from ui.color_page.wheels import HUE_SCREEN_ANGLE, ColorWheel, ColorWheels

# --- roues ----------------------------------------------------------------------------------------------------------


def _mouse(kind: QEvent.Type, pos: QPointF, modifiers=Qt.NoModifier) -> QMouseEvent:
    button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    held = Qt.LeftButton if kind != QEvent.MouseButtonRelease else Qt.NoButton
    return QMouseEvent(kind, pos, pos, button, held, modifiers)


def _drag(widget, start: QPointF, end: QPointF, modifiers=Qt.NoModifier) -> None:
    widget.mousePressEvent(_mouse(QEvent.MouseButtonPress, start))
    widget.mouseMoveEvent(_mouse(QEvent.MouseMove, end, modifiers))
    widget.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, end))


@pytest.fixture
def wheel(qtbot):
    widget = ColorWheel("gain")
    qtbot.addWidget(widget)
    widget.resize(180, 240)
    return widget


def _towards(widget: ColorWheel, hue: float, amount: float) -> QPointF:
    """Point à ``amount`` du rayon utile, dans la direction de la teinte ``hue`` sur l'anneau."""
    center, _radius = widget._disc()
    angle = math.radians(hue + HUE_SCREEN_ANGLE)
    travel = widget._travel()
    return QPointF(center.x() + amount * travel * math.cos(angle), center.y() - amount * travel * math.sin(angle))


def test_pushing_the_puck_towards_the_red_of_the_ring_raises_red_without_changing_the_level(qtbot, wheel):
    center, _radius = wheel._disc()
    with qtbot.waitSignal(wheel.changed) as emitted:
        _drag(wheel, center, _towards(wheel, 0.0, 0.6))
    value = emitted.args[0]
    assert value.r > 0 > value.g and value.g == pytest.approx(value.b) and value.y == 0.0
    assert abs(value.r + value.g + value.b) < 1e-9
    hue, radius = puck_from_wheel(value)
    assert radius == pytest.approx(0.6, abs=0.02) and min(hue, 360 - hue) < 2


def test_the_puck_moves_relative_to_the_press_and_shift_is_four_times_finer(qtbot, wheel):
    """Cliquer ne fait pas sauter le palet : seul le déplacement compte ; avec Maj il est divisé par quatre."""
    center, _radius = wheel._disc()
    off_center = _towards(wheel, 120.0, 0.7)
    wheel.mousePressEvent(_mouse(QEvent.MouseButtonPress, off_center))
    wheel.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, off_center))
    assert wheel.wheel == Wheel(), "un clic seul ne change rien"
    target = _towards(wheel, 240.0, 0.8)
    _drag(wheel, center, target, Qt.ShiftModifier)
    hue, radius = puck_from_wheel(wheel.wheel)
    assert radius == pytest.approx(0.2, abs=0.02) and hue == pytest.approx(240.0, abs=2)


def test_the_dial_sets_the_level_and_a_double_click_resets_one_or_the_other(qtbot, wheel):
    dial = wheel._dial_rect()
    _drag(wheel, dial.center(), dial.center() + QPointF(dial.width() / 4, 0))
    assert wheel.wheel.y == pytest.approx(0.5, abs=0.01)
    center, _radius = wheel._disc()
    _drag(wheel, center, _towards(wheel, 60.0, 0.5))
    wheel.mouseDoubleClickEvent(_mouse(QEvent.MouseButtonDblClick, center))
    assert wheel.wheel == Wheel(y=wheel.wheel.y) and wheel.wheel.y == pytest.approx(0.5, abs=0.01), "la couleur seule"
    wheel.mouseDoubleClickEvent(_mouse(QEvent.MouseButtonDblClick, dial.center()))
    assert wheel.wheel == Wheel()


def test_the_four_wheels_sit_in_a_row_when_wide_and_two_by_two_in_a_column(qtbot):
    wheels = ColorWheels()
    qtbot.addWidget(wheels)
    wheels.resize(900, 220)
    wheels.show()
    qtbot.waitUntil(lambda: wheels._columns == 4)
    wheels.resize(400, 520)
    qtbot.waitUntil(lambda: wheels._columns == 2)
    grid = wheels._grid
    assert [grid.getItemPosition(grid.indexOf(wheels.wheels[name]))[:2] for name in ("lift", "gamma", "gain", "offset")] \
        == [(0, 0), (0, 1), (1, 0), (1, 1)]


# --- éditeur de nœuds ------------------------------------------------------------------------------------------------


def _graph(count: int = 3) -> ColorNodeGraph:
    return ColorNodeGraph.serial(ColorNode(f"n{index + 1}", ColorGrade(exposure=0.1 * index)) for index in range(count))


@pytest.fixture
def editor(qtbot):
    widget = NodeEditor()
    qtbot.addWidget(widget)
    widget.resize(520, 160)
    widget.show()
    return widget


def test_the_editor_draws_the_chain_in_order(editor):
    graph = _graph().moved("n3", 0)
    editor.set_graph(graph, "n1")
    assert [item.node.id for item in sorted(editor._items, key=lambda item: item.x())] == ["n3", "n1", "n2"]
    assert [item.position for item in sorted(editor._items, key=lambda item: item.x())] == [0, 1, 2]


def test_choosing_a_node_repaints_without_rebuilding_the_scene(qtbot, editor):
    graph = _graph()
    editor.set_graph(graph, "n1")
    items = list(editor._items)
    with qtbot.waitSignal(editor.node_selected) as chosen:
        editor.request_select("n2")
    assert chosen.args == ["n2"]
    editor.set_graph(graph, "n2")
    assert editor._items == items, "même graphe : le nœud cliqué n'est pas détruit pendant son propre clic"


def test_dropping_a_node_past_its_neighbour_asks_to_move_it(qtbot, editor):
    editor.set_graph(_graph(), "n1")
    first = editor._items[0]
    first.setX(first.x() + NODE_WIDTH + NODE_GAP + 10)
    with qtbot.waitSignal(editor.move_requested) as moved:
        editor.drop(first)
    assert moved.args == ["n1", 1]


def test_a_node_dropped_back_in_place_stays_without_a_request(qtbot, editor):
    editor.set_graph(_graph(), "n1")
    second = editor._items[1]
    home = second.x()
    second.setX(home + 8)
    with qtbot.assertNotEmitted(editor.move_requested, wait=20):
        editor.drop(second)
    assert second.x() == home


@pytest.mark.parametrize(("key", "modifiers", "signal", "expected"), [
    (Qt.Key_S, Qt.AltModifier, "add_requested", ["n2"]),
    (Qt.Key_D, Qt.ControlModifier, "toggle_requested", ["n2"]),
    (Qt.Key_Delete, Qt.NoModifier, "remove_requested", ["n2"]),
])
def test_keyboard_commands_act_on_the_current_node(qtbot, editor, key, modifiers, signal, expected):
    editor.set_graph(_graph(), "n2")
    editor.setFocus()
    with qtbot.waitSignal(getattr(editor, signal)) as emitted:
        qtbot.keyClick(editor, key, modifiers)
    assert emitted.args == expected


def test_the_last_node_cannot_be_deleted_from_the_keyboard_or_the_menu(qtbot, editor):
    editor.set_graph(_graph(1), "n1")
    with qtbot.assertNotEmitted(editor.remove_requested, wait=20):
        qtbot.keyClick(editor, Qt.Key_Delete)
    menu = editor.build_menu("n1")
    remove = [action for action in menu.actions() if action.text() == i18n.translate("color.node.remove")]
    assert remove and not remove[0].isEnabled()


def test_renaming_asks_for_the_name_and_trims_it(qtbot, editor, monkeypatch):
    editor.set_graph(_graph(), "n1")
    monkeypatch.setattr(editor, "_ask_label", lambda current: "  Peau chaude  ")
    with qtbot.waitSignal(editor.rename_requested) as renamed:
        editor.request_rename("n2")
    assert renamed.args == ["n2", "Peau chaude"]


# --- fenêtre : pages -------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def test_the_colour_page_has_its_layout_and_the_edit_page_gets_its_own_back(window):
    if window._scopes_visible:
        window.toggle_scopes_visible()                         # Montage sans scopes : la page Couleur les montre
    state = window.workspace.state
    assert state.area_of(PanelId.INSPECTOR) is DockArea.RIGHT and not state.is_visible(PanelId.COLOR)
    window.switch_page(PAGE_COLOR)
    state = window.workspace.state
    assert window.workspace.page == PAGE_COLOR and window.page_buttons[PAGE_COLOR].isChecked()
    assert state.area_of(PanelId.INSPECTOR) is DockArea.LEFT and state.is_visible(PanelId.COLOR)
    assert not state.is_visible(PanelId.MEDIA)
    assert window._scopes_visible and window.properties_panel._active_inspector_tab == 1
    assert window.side_rail.active() == "color"
    window.workspace.set_panel_visible(PanelId.INSPECTOR, False)         # la page Couleur garde ce choix
    window.switch_page(PAGE_EDIT)
    state = window.workspace.state
    assert state.area_of(PanelId.INSPECTOR) is DockArea.RIGHT and state.is_visible(PanelId.INSPECTOR)
    assert state.is_visible(PanelId.MEDIA) and not state.is_visible(PanelId.COLOR)
    assert not window._scopes_visible, "les scopes du Montage, tels qu'on les y avait laissés"
    window.switch_page(PAGE_COLOR)
    assert not window.workspace.state.is_visible(PanelId.INSPECTOR)


def test_each_page_keeps_its_layout_in_its_own_file(window, tmp_path):
    config = tmp_path / "config"
    window.switch_page(PAGE_COLOR)
    window.workspace.save()
    assert page_state_path(PAGE_EDIT, config).name == "workspace.json"
    edit, color = load_workspace_state(config), load_page_state(PAGE_COLOR, config)
    assert not edit.is_visible(PanelId.COLOR) and edit.area_of(PanelId.INSPECTOR) is DockArea.RIGHT
    assert color.is_visible(PanelId.COLOR) and color.area_of(PanelId.INSPECTOR) is DockArea.LEFT


def test_a_zone_width_is_kept_for_the_panel_that_is_in_it(window):
    """Page Couleur : l'inspecteur est à gauche, la couleur à droite. Leur largeur se garde pour eux ; les Médias
    (repliés) gardent la leur (avant, la gauche était toujours attribuée aux Médias, la droite à l'inspecteur)."""
    window.switch_page(PAGE_COLOR)
    media = window.workspace.state.get(PanelId.MEDIA).size
    top = window.workspace._top_splitter
    total = sum(top.sizes())
    top.setSizes([330, total - 330 - 470, 470])
    state = window.workspace.capture_state()
    left, _center, right = top.sizes()
    assert state.get(PanelId.INSPECTOR).size == left and state.get(PanelId.COLOR).size == right
    assert state.get(PanelId.MEDIA).size == media


def test_resetting_the_layout_on_the_colour_page_gives_the_colour_layout(window):
    window.switch_page(PAGE_COLOR)
    window.workspace.set_panel_visible(PanelId.COLOR, False)
    window.workspace.reset_layout()
    assert window.workspace.state.is_visible(PanelId.COLOR)
    assert window.workspace.state.area_of(PanelId.INSPECTOR) is DockArea.LEFT


def test_the_rail_and_the_shortcut_commands_switch_pages(window):
    window.side_rail._on_clicked("color")
    assert window.workspace.page == PAGE_COLOR
    window.side_rail._on_clicked("media")                        # un outil de montage : retour au Montage
    assert window.workspace.page == PAGE_EDIT and window.workspace.is_visible(PanelId.MEDIA)
    window.shortcuts.trigger("page_color")
    assert window.workspace.page == PAGE_COLOR
    window.side_rail._on_clicked("edit")
    assert window.workspace.page == PAGE_EDIT and window.side_rail.active() == "edit"


def test_the_scopes_preference_stays_the_edit_page_one(window):
    if window._scopes_visible:
        window.toggle_scopes_visible()
    window.switch_page(PAGE_COLOR)
    assert window._scopes_visible and window._settings_snapshot().scopes_visible is False


# --- fenêtre : nœuds et roues ----------------------------------------------------------------------------------------


def _video_clip(window):
    clip = window.project.tracks[0].clips[0]
    window.on_clip_selected(clip.id)
    return clip.id


def _grade_value(window, clip_id):
    from core.timeline_operations import find_clip

    return find_clip(window.project, clip_id).color_grade


def test_nodes_added_from_the_panel_are_graded_one_at_a_time_and_undone_step_by_step(window):
    clip_id = _video_clip(window)
    window.switch_page(PAGE_COLOR)
    panel = window.color_panel
    assert [node.id for node in panel.nodes.graph.nodes] == ["n1"]
    panel.nodes.add_requested.emit("n1")
    graph = _grade_value(window, clip_id)
    assert isinstance(graph, ColorNodeGraph) and window._color_node_id == "n2"
    assert window.history.undo_label == i18n.translate("history.color.node_add")
    assert panel.node_caption.text().startswith(i18n.translate("color.panel.node_caption", number="02", count="02"))
    window.on_color_grade_field_changed(clip_id, "exposure", 0.5)          # l'inspecteur règle le nœud courant
    window._finalize_color_history()
    graph = _grade_value(window, clip_id)
    assert graph.node("n2").grade.exposure == 0.5 and graph.node("n1").grade.exposure == 0.0
    assert window.properties_panel.color_node_label.isVisible()
    panel.nodes.request_select("n1")
    assert window.properties_panel.color_field_spins["exposure"].value() == 0.0, "l'inspecteur montre le nœud 01"
    window.undo_last()
    assert _grade_value(window, clip_id).node("n2").grade.exposure == 0.0
    window.undo_last()
    assert isinstance(_grade_value(window, clip_id), (ColorGrade, type(None))), "un seul nœud : un réglage, comme avant"
    assert [node.id for node in window.color_panel.nodes.graph.nodes] == ["n1"]


def test_a_wheel_drag_is_one_history_step_and_reaches_the_monitor(window, monkeypatch):
    """Le projet d'exemple n'a pas de média (le moniteur s'arrête sur « média absent ») : on vérifie que chaque
    mouvement rafraîchit le moniteur, et que le moniteur prend un graphe de nœuds comme un réglage."""
    clip_id = _video_clip(window)
    window.switch_page(PAGE_COLOR)
    refreshed = []
    original = window._refresh_color_monitor
    monkeypatch.setattr(window, "_refresh_color_monitor", lambda cid: (refreshed.append(cid), original(cid)))
    steps = len(window.history.entries())
    for amount in (0.1, 0.2, 0.3):
        window.color_panel.wheels.wheels["gamma"].changed.emit(Wheel(y=amount))
    window._finalize_color_history()
    assert len(window.history.entries()) == steps + 1
    assert window.history.undo_label == i18n.translate("history.color.wheel", wheel="Gamma")
    assert _grade_value(window, clip_id).gamma == Wheel(y=0.3)
    assert refreshed == [clip_id] * 3, "le moniteur suit chaque mouvement de la roue"
    graph, _ = as_graph(_grade_value(window, clip_id)).with_node_after("n1")
    window.preview_panel.set_color_grade(graph)
    assert window.preview_panel._gpu_grade is graph


def test_node_commands_from_the_panel_toggle_rename_move_and_delete(window):
    clip_id = _video_clip(window)
    window.switch_page(PAGE_COLOR)
    editor = window.color_panel.nodes
    editor.add_requested.emit("n1")
    editor.add_requested.emit("n2")
    editor.toggle_requested.emit("n2")
    assert not _grade_value(window, clip_id).node("n2").enabled
    assert window.history.undo_label == i18n.translate("history.color.node_bypass")
    editor.rename_requested.emit("n3", "Look")
    editor.move_requested.emit("n3", 0)
    assert [node.id for node in _grade_value(window, clip_id).order()] == ["n3", "n1", "n2"]
    editor.remove_requested.emit("n1")
    editor.remove_requested.emit("n2")
    value = _grade_value(window, clip_id)
    assert isinstance(value, ColorNodeGraph) and value.nodes[0].label == "Look", "un nœud nommé reste un graphe"


def test_delete_in_the_node_editor_deletes_the_node_not_the_clip(qtbot, window):
    clip_id = _video_clip(window)
    window.switch_page(PAGE_COLOR)
    editor = window.color_panel.nodes
    editor.add_requested.emit("n1")
    editor.setFocus()
    qtbot.keyClick(editor, Qt.Key_Delete)
    assert isinstance(_grade_value(window, clip_id), ColorGrade)
    from core.timeline_operations import find_clip

    assert find_clip(window.project, clip_id) is not None


def test_a_locked_clip_shows_its_nodes_without_letting_them_change(window):
    clip_id = _video_clip(window)
    window.switch_page(PAGE_COLOR)
    window.project.tracks[0].locked = True
    window._reload_timeline_preserving_selection(clip_id)
    window.on_clip_selected(clip_id)
    assert window.color_panel.nodes.graph is not None and not window.color_panel.nodes.isEnabled()
    assert not window.color_panel.wheels.isEnabled()
    window.on_color_node_add("n1")                             # même sans passer par l'éditeur désactivé
    assert not isinstance(_grade_value(window, clip_id), ColorNodeGraph)


def test_the_colour_panel_opened_on_the_edit_page_shows_the_selected_clip(window):
    """Fermé, le panneau ne suit pas chaque sélection (rien à peindre) ; ouvert depuis *Fenêtre › Panneaux*, il se
    remet sur le clip affiché."""
    clip_id = _video_clip(window)
    assert window.workspace.page == PAGE_EDIT and not window.color_panel.isVisible()
    window.toggle_panel(PanelId.COLOR)
    assert window.color_panel.isVisible()
    graph = window.color_panel.nodes.graph
    assert graph is not None and graph == as_graph(_grade_value(window, clip_id))


def test_no_video_clip_means_no_nodes(window):
    window.switch_page(PAGE_COLOR)
    window.properties_panel.show_clip(None)
    assert window.color_panel.nodes.graph is None and window.color_panel.hint.isVisibleTo(window.color_panel)


def test_the_colour_page_follows_the_language(window):
    window.switch_page(PAGE_COLOR)
    try:
        i18n.set_language("en")
        window._retranslate_ui()
        assert window.page_buttons[PAGE_EDIT].text() == "Edit"
        assert window.color_panel.header.title_label.text() == "Color"
        assert window.color_panel.wheels.wheels["lift"].toolTip().startswith("Lift: the blacks")
    finally:
        i18n.set_language("fr")
        window._retranslate_ui()
    assert window.page_buttons[PAGE_EDIT].text() == "Montage"


def test_a_clip_keeps_its_grade_when_it_is_split_into_nodes():
    """Garde-fou du modèle utilisé par la page : découper en nœuds ne change pas l'image tant qu'on n'a rien réglé."""
    from core.export_engine import _build_color_grade_filters

    grade = ColorGrade(exposure=0.3, gain=Wheel(y=0.1))
    graph, _ = as_graph(grade).with_node_after("n1")
    assert _build_color_grade_filters(graph) == _build_color_grade_filters(grade)
