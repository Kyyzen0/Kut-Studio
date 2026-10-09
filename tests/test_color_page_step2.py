"""Page Couleur, étape 2 : nœuds en branches, qualifieur, sélection montrée, avant / après, bande des plans."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNodeGraph, MixerKind, as_graph
from core.color_qualifier import Qualifier
from core.color_render import Highlight
from core.workspace_state import PAGE_COLOR, PAGE_EDIT, DockArea, PanelId, page_default_state
from ui import i18n
from ui.color_page.node_editor import NodeEditor, layout
from ui.color_page.qualifier import QualifierEditor


def _branched() -> ColorNodeGraph:
    graph, second = as_graph(ColorGrade(exposure=0.2)).with_node_after("n1")
    graph, third = graph.with_branch(second, MixerKind.PARALLEL)
    graph, _after = graph.with_node_after(graph.mixers[0].id)
    graph, _top = graph.with_branch(_after, MixerKind.LAYER)
    return graph


# --- disposition et éditeur ------------------------------------------------------------------------------------------


def test_the_layout_gives_a_column_per_depth_and_a_row_per_branch():
    graph = _branched()
    places = layout(graph)
    parallel, layer = (next(m.id for m in graph.mixers if m.kind is kind) for kind in (MixerKind.PARALLEL, MixerKind.LAYER))
    first, second = (link.source for link in graph.inputs(parallel))
    assert places["n1"] == (0, 0) and places[first][0] == places[second][0] == 1
    assert places[first][1] < places[second][1], "la deuxième branche est dessous"
    assert places[parallel][0] == 2 and places[layer][0] == 4 and places[graph.sink.id] == places[layer]
    top = graph.inputs(layer)[1].source
    assert places[top][1] > places[graph.inputs(layer)[0].source][1], "en calques, l'entrée haute est dessinée dessous"


@pytest.fixture
def editor(qtbot):
    widget = NodeEditor()
    qtbot.addWidget(widget)
    widget.resize(640, 260)
    widget.show()
    return widget


def test_the_editor_draws_mixers_as_rounds_that_cannot_be_chosen(editor):
    graph = _branched()
    editor.set_graph(graph, "n2")
    assert len(editor._mixers) == 2 and len(editor._items) == len(graph.correctors)
    assert [item.node.id for item in editor._items if item.node.id == "n2"]
    assert graph.node_or_first(graph.mixers[0].id).id == "n1", "un mélangeur n'est jamais le nœud courant"


@pytest.mark.parametrize(("key", "kind"), [(Qt.Key_P, "parallel"), (Qt.Key_L, "layer")])
def test_alt_p_and_alt_l_add_a_branch_next_to_the_current_node(qtbot, editor, key, kind):
    editor.set_graph(_branched(), "n2")
    editor.setFocus()
    with qtbot.waitSignal(editor.branch_requested) as asked:
        qtbot.keyClick(editor, key, Qt.AltModifier)
    assert asked.args == ["n2", kind]
    menu = editor.build_menu("n2")
    texts = [action.text() for action in menu.actions()]
    assert i18n.translate(f"color.node.add_{kind}") in texts


def test_dragging_a_node_reorders_it_inside_its_serial_run_only(qtbot, editor):
    graph = _branched()
    editor.set_graph(graph, "n1")
    first = next(item for item in editor._items if item.node.id == "n1")
    second = next(item for item in editor._items if item.node.id == "n2")
    assert graph.serial_run("n1") == ["n1"], "n1 alimente deux branches : seul dans sa suite"
    first.setX(second.x() + 30)
    with qtbot.assertNotEmitted(editor.move_requested, wait=20):
        editor.drop(first)
    assert first.pos() == first.home


# --- qualifieur ------------------------------------------------------------------------------------------------------


@pytest.fixture
def qualifier(qtbot):
    widget = QualifierEditor()
    qtbot.addWidget(widget)
    widget.resize(420, 360)
    widget.show()
    return widget


def test_qualifying_a_node_starts_from_a_selection_of_everything(qtbot, qualifier):
    assert not qualifier.bars["hue"].isEnabled(), "sans qualifieur, les plages sont grisées"
    with qtbot.waitSignal(qualifier.changed) as changed:
        qualifier.enabled_check.setChecked(True)
    assert changed.args[0] == Qualifier() and not changed.args[0].restricts()
    assert qualifier.bars["hue"].isEnabled()


def test_fields_set_the_ranges_in_degrees_and_percent(qtbot, qualifier):
    qualifier.set_qualifier(Qualifier())
    with qtbot.waitSignal(qualifier.changed) as changed:
        qualifier.spins["hue_width"].setValue(40)
    assert changed.args[0].hue_width == 40.0
    with qtbot.waitSignal(qualifier.changed) as changed:
        qualifier.spins["sat_low"].setValue(30)
    assert changed.args[0].sat_low == pytest.approx(0.3)
    with qtbot.waitSignal(qualifier.changed) as changed:
        qualifier.spins["lum_low"].setValue(100)
    assert changed.args[0].lum_low == changed.args[0].lum_high == 1.0, "une borne poussée au-delà entraîne l'autre"


def test_dragging_the_hue_bar_turns_the_range(qtbot, qualifier):
    qualifier.set_qualifier(Qualifier(hue_center=10.0, hue_width=40.0))
    bar = qualifier.bars["hue"]
    start, end = QPointF(bar.width() * 0.1, 9), QPointF(bar.width() * 0.35, 9)
    with qtbot.waitSignal(qualifier.changed) as changed:
        bar.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, start, start, Qt.LeftButton, Qt.LeftButton,
                                        Qt.NoModifier))
        bar.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, end, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
    assert changed.args[0].hue_center == pytest.approx(10.0 + 0.25 * 360.0, abs=2.0)
    assert changed.args[0].hue_width == 40.0


# --- fenêtre ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _clip(window):
    clip = window.project.tracks[0].clips[0]
    window.on_clip_selected(clip.id)
    window.switch_page(PAGE_COLOR)
    return clip.id


def _value(window, clip_id):
    from core.timeline_operations import find_clip

    return find_clip(window.project, clip_id).color_grade


def test_the_colour_page_shows_the_clip_strip_under_the_timeline():
    state = page_default_state(PAGE_COLOR)
    assert state.is_visible(PanelId.CLIPS) and state.area_of(PanelId.CLIPS) is DockArea.BOTTOM
    assert not page_default_state(PAGE_EDIT).is_visible(PanelId.CLIPS)


def test_a_branch_from_the_panel_menu_is_one_step_and_becomes_the_current_node(window):
    clip_id = _clip(window)
    window.color_panel.add_actions["parallel"].trigger()
    graph = _value(window, clip_id)
    assert isinstance(graph, ColorNodeGraph) and graph.mixers[0].kind is MixerKind.PARALLEL
    assert window._color_node_id == "n2" and window.history.undo_label == i18n.translate("history.color.node_parallel")
    window.color_panel.add_actions["layer"].trigger()
    assert {mixer.kind for mixer in _value(window, clip_id).mixers} == {MixerKind.PARALLEL, MixerKind.LAYER}
    window.undo_last()
    window.undo_last()
    assert isinstance(_value(window, clip_id), (ColorGrade, type(None)))


def test_a_qualifier_burst_is_one_history_step_on_the_current_node(window, monkeypatch):
    clip_id = _clip(window)
    window.color_panel.nodes.add_requested.emit("n1")
    refreshed = []
    original = window._refresh_color_monitor
    monkeypatch.setattr(window, "_refresh_color_monitor", lambda cid: (refreshed.append(cid), original(cid)))
    steps = len(window.history.entries())
    editor = window.color_panel.qualifier
    editor.enabled_check.setChecked(True)
    editor.spins["hue_width"].setValue(60)
    editor.spins["hue_center"].setValue(200)
    window._finalize_color_history()
    graph = _value(window, clip_id)
    assert graph.corrector("n2").qualifier == Qualifier(hue_center=200.0, hue_width=60.0)
    assert graph.corrector("n1").qualifier is None
    assert len(window.history.entries()) == steps + 1
    assert window.history.undo_label == i18n.translate("history.color.qualifier") and len(refreshed) == 3


def test_showing_the_selection_changes_only_what_the_monitor_gets(window):
    clip_id = _clip(window)
    window.color_panel.qualifier.enabled_check.setChecked(True)
    window.color_panel.qualifier.spins["hue_width"].setValue(60)
    window._finalize_color_history()
    from core.timeline_operations import find_clip

    clip = find_clip(window.project, clip_id)
    assert window._monitor_color_grade(clip) is clip.color_grade
    window.on_color_highlight_toggled(True)
    shown = window._monitor_color_grade(clip)
    assert isinstance(shown, Highlight) and shown.node_id == "n1" and shown.graph == clip.color_grade
    other = window.project.tracks[0].clips[1]
    assert window._monitor_color_grade(other) is other.color_grade, "seul le clip de la page Couleur"
    window.switch_page(PAGE_EDIT)
    assert window._monitor_color_grade(clip) is clip.color_grade, "panneau fermé : le vrai étalonnage"


def test_before_after_splits_the_monitor_and_stops_on_the_edit_page(window):
    _clip(window)
    window._set_color_compare(0.5)
    overlay = window.preview_panel.overlay
    assert overlay.compare_split == 0.5 and window.color_panel.compare_button.isChecked()
    assert window.preview_panel._gpu_grade_split == 0.5
    overlay.compare_moved.emit(0.3)
    assert overlay.compare_split == pytest.approx(0.3) and window._color_compare == pytest.approx(0.3)
    window.switch_page(PAGE_EDIT)
    assert overlay.compare_split is None and window.preview_panel._gpu_grade_split == 0.0
    assert not window.color_panel.compare_button.isChecked()


class _SceneEvent:
    """Ce que l'overlay lit d'un évènement de scène : sa position, et l'accepter."""

    def __init__(self, x: float, y: float) -> None:
        self._pos = QPointF(x, y)

    def pos(self) -> QPointF:
        return self._pos

    def accept(self) -> None:
        pass


def test_the_compare_line_is_dragged_in_the_viewer(qtbot, window):
    overlay = window.preview_panel.overlay
    overlay.set_canvas(QRectF(0, 0, 400, 225), (1920.0, 1080.0))
    overlay.set_compare_split(0.5)
    assert overlay._hit(QPointF(200, 100)) == ("compare", "")
    assert overlay._hit(QPointF(300, 100)) != ("compare", "")
    overlay.mousePressEvent(_SceneEvent(201, 100))
    with qtbot.waitSignal(overlay.compare_moved) as moved:
        overlay.mouseMoveEvent(_SceneEvent(100, 100))
    overlay.mouseReleaseEvent(_SceneEvent(100, 100))
    assert moved.args[0] == pytest.approx(0.25) and overlay._drag is None


def test_without_the_gpu_monitor_the_monitor_only_tools_say_why(window):
    _clip(window)
    if window.preview_panel.gpu_view is not None:
        pytest.skip("moniteur GPU actif sur cette machine")
    assert not window.color_panel.compare_button.isEnabled()
    assert not window.color_panel.qualifier.highlight_button.isEnabled()
    assert window.color_panel.compare_button.toolTip() == i18n.translate("color.qualifier.highlight_unavailable")


def test_the_clip_strip_lists_the_video_shots_and_jumps_to_one(window):
    _clip(window)
    strip = window.clip_strip
    assert strip.isVisible()
    views = sorted((view for view in window.timeline_panel.clip_views if view.track_type == "video"),
                   key=lambda view: (view.start, view.track_id))
    assert [clip.id for clip in strip.clips] == [view.id for view in views]
    assert strip.current_id == window.project.tracks[0].clips[0].id
    target = strip.clips[-1].id
    strip.clip_requested.emit(target)
    assert window.properties_panel.selected_clip.id == target and strip.current_id == target
    window.color_panel.wheels.wheels["gain"].changed.emit(Wheel(y=0.2))
    window._finalize_color_history()
    assert next(clip for clip in strip.clips if clip.id == target).graded


def test_a_strip_tile_click_asks_for_its_clip(qtbot, window):
    _clip(window)
    tiles = window.clip_strip.tiles
    rect = tiles.tile_rect(1)
    with qtbot.waitSignal(window.clip_strip.clip_requested) as asked:
        qtbot.mouseClick(tiles, Qt.LeftButton, pos=rect.center().toPoint())
    assert asked.args == [window.clip_strip.clips[1].id]
