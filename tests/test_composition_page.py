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


def test_the_key_colour_is_sent_only_once_it_is_a_whole_hexadecimal_colour(qtbot):
    from core.composition import KeyNode
    from ui.composition_page.inspector import NodeInspector

    inspector = NodeInspector()
    qtbot.addWidget(inspector)
    inspector.set_node(KeyNode("i"))
    sent = []
    inspector.changed.connect(lambda node, _field: sent.append(node.key.color))
    edit = inspector.key_color.lineEdit()
    edit.clear()
    qtbot.keyClicks(edit, "#zz00f")
    assert edit.text() == "#00f" and not sent, "lettres refusées, couleur incomplète gardée pour soi"
    qtbot.keyClicks(edit, "f7f")
    assert sent == ["#00FF7F"]


def _fake_renders(engine, tmp_path) -> list:
    """Rendus instantanés : seul l'ordonnancement est testé ici."""
    rendered: list = []

    def fake_render(job, token):
        rendered.append(job.key.clip_id)
        out = tmp_path / f"render-{len(rendered)}.mp4"
        out.write_bytes(b"\0" * 64)
        return str(out)

    engine.render_fn = fake_render
    engine._uses_default_render = False
    return rendered


def test_playback_reads_the_rendered_composition_and_undo_reuses_it(window, qtbot, monkeypatch, tmp_path):
    from core.composition_cache import cache_owner
    from core.sequences import nested_source_time
    from core.timeline_operations import find_clip

    _ids, result = _converted(window)
    clip_id = result.clip.id
    engine = window.preview_engine
    rendered = _fake_renders(engine, tmp_path)
    shown = []
    monkeypatch.setattr(window.preview_panel, "preview_at",
                        lambda path, t, playing=False: shown.append((path, t, playing)))
    window.is_playing = False
    clip = find_clip(window.project, clip_id)
    window.playhead_seconds = clip.timeline_start + 0.5
    inner = nested_source_time(clip, window.playhead_seconds)

    def ready():
        return window._composition_cache_source(find_clip(window.project, clip_id), inner)

    window._schedule_composition_caches(window.playhead_seconds)
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    chunk = ready()[0]
    assert cache_owner(clip_id) in rendered
    window.is_playing = True                               # la lecture lit le morceau, sans proxy
    try:
        window._sync_preview_core()
    finally:
        window.is_playing = False
    assert shown[-1][0] == chunk and shown[-1][1] == pytest.approx(inner)

    graph = find_clip(window.project, clip_id).composition.graph
    media = next(node for node in graph.nodes if isinstance(node, MediaNode))
    window.on_comp_node_selected(media.id)
    window.composition_panel.nodes.add_requested.emit("transform", media.id)
    assert ready() is None or ready()[0] != chunk, "composition modifiée : plus l'ancien morceau"
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    assert ready()[0] != chunk
    renders = len(rendered)
    window.undo_last()
    window._schedule_composition_caches(window.playhead_seconds)
    assert ready() is not None and ready()[0] == chunk, "annuler : le morceau d'avant resservi"
    assert len(rendered) == renders, "sans nouveau rendu"


def test_a_replaced_media_file_or_an_expired_chunk_is_not_served(window, qtbot, tmp_path):
    import os

    from core.sequences import nested_source_time
    from core.timeline_operations import find_clip

    _ids, result = _converted(window)
    clip_id = result.clip.id
    engine = window.preview_engine
    rendered = _fake_renders(engine, tmp_path)
    clip = find_clip(window.project, clip_id)
    media_id = next(node.asset_id for node in clip.composition.graph.nodes if isinstance(node, MediaNode))
    asset = next(asset for asset in window.project.media_assets if asset.id == media_id)
    asset.path = str(tmp_path / "source.mp4")
    (tmp_path / "source.mp4").write_bytes(b"v1")
    window.is_playing = False
    window.playhead_seconds = clip.timeline_start + 0.5
    inner = nested_source_time(clip, window.playhead_seconds)

    def ready():
        return window._composition_cache_source(find_clip(window.project, clip_id), inner)

    window._schedule_composition_caches(window.playhead_seconds)
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    first, renders = ready()[0], len(rendered)
    (tmp_path / "source.mp4").write_bytes(b"version 2")    # remplacé au même chemin
    os.utime(tmp_path / "source.mp4", ns=(1, 1))
    window._schedule_composition_caches(window.playhead_seconds)
    assert ready() is None or ready()[0] != first, "l'ancien rendu n'est plus servi"
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    assert ready()[0] != first and len(rendered) > renders

    window._comp_caches[clip_id].looked_up.clear()         # repasse par le cache disque
    engine.cache.ttl_seconds = 1e-6                        # morceau périmé
    assert ready() is None


def test_a_proxy_that_stops_being_served_refreshes_the_composition(window, qtbot, monkeypatch, tmp_path):
    """Le gestionnaire de proxys garde son état deux secondes : juste après un remplacement, il sert encore l'ancien
    proxy. La signature suit ce qui est réellement lu ; quand l'ancien proxy n'est plus servi, les morceaux sont
    refaits."""
    from core.sequences import nested_source_time
    from core.timeline_operations import find_clip

    _ids, result = _converted(window)
    clip_id = result.clip.id
    rendered = _fake_renders(window.preview_engine, tmp_path)
    clip = find_clip(window.project, clip_id)
    media_id = next(node.asset_id for node in clip.composition.graph.nodes if isinstance(node, MediaNode))
    asset = next(asset for asset in window.project.media_assets if asset.id == media_id)
    source, proxy = tmp_path / "source.mp4", tmp_path / "source.proxy.mp4"
    source.write_bytes(b"v1")
    proxy.write_bytes(b"proxy of v1")
    asset.path = str(source)
    served = {"proxy": True}
    monkeypatch.setattr(window, "_preview_resolver", lambda: (
        lambda path, need_audio=False: str(proxy) if path == str(source) and served["proxy"] else path))
    window.is_playing = False
    window.playhead_seconds = clip.timeline_start + 0.5
    inner = nested_source_time(clip, window.playhead_seconds)

    def ready():
        return window._composition_cache_source(find_clip(window.project, clip_id), inner)

    window._schedule_composition_caches(window.playhead_seconds)
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    stale = ready()[0]
    source.write_bytes(b"version 2")                       # remplacé ; le proxy, lui, est encore servi
    window._schedule_composition_caches(window.playhead_seconds)
    renders = len(rendered)
    served["proxy"] = False                                # deux secondes plus tard : l'original est lu
    window._schedule_composition_caches(window.playhead_seconds)
    assert ready() is None or ready()[0] != stale
    qtbot.waitUntil(lambda: ready() is not None, timeout=5000)
    assert ready()[0] != stale and len(rendered) > renders


def test_closing_the_window_on_the_composition_page_logs_no_shutdown_error(window, caplog):
    """La page Composition n'affiche pas l'inspecteur : son hôte n'a plus de parent Qt. La fermeture ne le détruit plus
    avant les étapes d'arrêt qui s'en servent (« analyse du flux optique ») : aucune ne journalise d'erreur."""
    import logging

    import shiboken6

    window.switch_page(PAGE_COMPOSITION)
    inspector = window.properties_panel
    assert not window.isAncestorOf(inspector), "l'inspecteur est hors de la page : c'est le cas couvert"
    with caplog.at_level(logging.ERROR, logger="ui.main_window"):
        assert window.close()
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []
    assert shiboken6.isValid(inspector.time_section.analyze_button), "détruit avec la fenêtre, jamais avant"
