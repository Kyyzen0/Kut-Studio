"""Page Composition : convertir des calques, éditer les nœuds (ajout, liens, réglages), aperçu du nœud choisi."""

from __future__ import annotations

import pytest

from core.composition import OUTPUT_ID, CompositionView, MediaNode, MergeNode, TransformNode
from core.workspace_state import PAGE_COMPOSITION, PAGE_EDIT


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _converted(window):
    """Les deux clips de V1 et le clip de V2 convertis en une composition."""
    ids = [clip.id for track in window.project.tracks if track.type == "video" for clip in track.clips]
    window.timeline_panel._set_selection(ids, ids[0], announce=False)
    return ids, window.convert_selection_to_composition()


def test_converting_the_selection_opens_its_composition_and_undo_restores_the_layers(window):
    from ui import i18n

    steps = len(window.history.entries())
    ids, result = _converted(window)
    assert result is not None and window.workspace.page == PAGE_COMPOSITION
    assert window.history.undo_label == i18n.translate("history.comp.convert")
    assert len(window.history.entries()) == steps + 1
    video_clips = [clip for track in window.project.tracks if track.type == "video" for clip in track.clips]
    assert [clip.id for clip in video_clips] == [result.clip.id]
    panel = window.composition_panel
    assert panel.isVisible() and panel.splitter.isVisible() and panel.nodes.graph == result.clip.composition.graph
    window.undo_last()
    restored = sorted(clip.id for track in window.project.tracks if track.type == "video" for clip in track.clips)
    assert restored == sorted(ids)


def test_nodes_are_added_linked_and_tuned_with_one_step_per_gesture(window):
    _ids, result = _converted(window)
    panel = window.composition_panel
    clip_id = result.clip.id
    graph = result.clip.composition.graph
    first_media = next(node for node in graph.nodes if isinstance(node, MediaNode))
    window.on_comp_node_selected(first_media.id)
    steps = len(window.history.entries())
    panel.nodes.add_requested.emit("transform", first_media.id)
    from core.timeline_operations import find_clip

    graph = find_clip(window.project, clip_id).composition.graph
    added = window._comp_node_id
    assert isinstance(graph.node(added), TransformNode) and graph.input_of(added) == first_media.id
    assert len(window.history.entries()) == steps + 1
    inspector = panel.inspector
    spin = inspector._fields["transform"]["scale"][0]
    for value in (80.0, 70.0, 60.0):                       # une rafale sur un champ : une étape
        spin.setValue(value)
    node = find_clip(window.project, clip_id).composition.graph.node(added)
    assert node.transform.scale == pytest.approx(0.6) and len(window.history.entries()) == steps + 2
    window.on_comp_disconnect(added, 0)
    assert find_clip(window.project, clip_id).composition.graph.input_of(added) is None
    window.on_comp_connect(first_media.id, added, 0)
    window.on_comp_remove(added)
    graph = find_clip(window.project, clip_id).composition.graph
    assert not graph.has_node(added) and len(window.history.entries()) == steps + 5


def test_the_viewer_can_show_the_selected_node(window):
    _ids, result = _converted(window)
    merge = next(node for node in result.clip.composition.graph.nodes if isinstance(node, MergeNode))
    window.on_comp_node_selected(merge.id)
    assert window._preview_grade_overrides() is None, "sans la bascule : la sortie"
    window.composition_panel.view_button.click()
    assert window._preview_grade_overrides() == {result.clip.id: CompositionView(merge.id)}
    window.on_comp_node_selected(OUTPUT_ID)
    assert window._preview_grade_overrides() is None, "la sortie : rien à remplacer"
    window.switch_page(PAGE_EDIT)
    assert window._preview_grade_overrides() is None, "hors de la page Composition"


def test_a_new_composition_goes_to_the_playhead_and_a_double_click_reopens_it(window):
    end = max(clip.timeline_start + clip.duration for track in window.project.tracks for clip in track.clips)
    window.playhead_seconds = end                          # la tête de lecture est bornée à la fin du montage
    clip = window.new_composition()
    assert clip is not None and clip.composition is not None
    assert clip.timeline_start == pytest.approx(window.playhead_seconds)
    window.switch_page(PAGE_EDIT)
    window.timeline_panel.composition_open_requested.emit(clip.id)
    assert window.workspace.page == PAGE_COMPOSITION
    assert window.composition_panel.nodes.graph == clip.composition.graph


def test_the_node_view_lays_out_columns_and_finds_ports():
    from PySide6.QtCore import QPointF

    from core.composition import CompositionGraph, SolidNode
    from ui.composition_page.node_view import CompositionNodeView, layout

    graph = (CompositionGraph.empty().inserted(MediaNode("m", "v", 0.0, 0.0, 1.0)).inserted(TransformNode("t"), "m")
             .inserted(SolidNode("s")))
    places = layout(graph)
    assert places["m"][0] == 0 and places["t"][0] == 1 and places[OUTPUT_ID][0] == max(c for c, _r in places.values())
    view = CompositionNodeView()
    view.set_graph(graph, "t")
    merge = graph.input_of(OUTPUT_ID)
    item = view._items[merge]
    assert view.input_at(item.input_port(1)) == (merge, 1), "la fusion a deux entrées : fond, premier plan"
    assert view.output_at(view._items["m"].output_port()) == "m"
    assert view.output_at(view._items[OUTPUT_ID].output_port()) is None, "la sortie n'a pas de sortie"
    assert view.input_at(QPointF(-500, -500)) is None
