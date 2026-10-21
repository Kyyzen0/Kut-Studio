"""Moniteur : n'importe quel clip visuel se sélectionne d'un clic (ADR-0006, étape 1, lot 1a)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, QRectF

from core.graphics import GraphicType, add_graphic_clip
from core.mograph_layers import scene_for_project, viewer_pick_boxes
from core.project_model import Clip, MediaAsset, Project, Track


def _project(tracks=()) -> Project:
    project = Project(name="Pick", width=1280, height=720, fps=25.0)
    project.tracks.extend(tracks)
    return project


def _video(
    clip_id: str, track_id: str, start: float = 0.0, length: float = 4.0
) -> Clip:
    return Clip(clip_id, "asset", track_id, start, 0.0, length)


def _ids(project: Project, t: float = 1.0) -> list[str]:
    return [
        cid for cid, _w, _b in viewer_pick_boxes(scene_for_project(project), project, t)
    ]


def _text(project: Project, track: Track, clip_id: str, **kwargs) -> Clip:
    return add_graphic_clip(
        project,
        GraphicType.TEXT,
        timeline_start=0.0,
        duration=4.0,
        track=track,
        clip_id=clip_id,
    )


# -- fonction pure ------------------------------------------------------------------------------


def test_visible_video_is_pickable_and_boxes_are_the_whole_frame():
    project = _project([Track("V1", "V1", "video", clips=[_video("v", "V1")])])
    ((cid, _world, box),) = viewer_pick_boxes(scene_for_project(project), project, 1.0)
    assert cid == "v" and box == (1280.0, 720.0)


@pytest.mark.parametrize(
    "case", ["hidden", "locked", "disabled", "nested", "before", "after"]
)
def test_excluded_video_clips(case):
    clip = _video("v", "V1", start=0.0, length=2.0)
    track = Track("V1", "V1", "video", clips=[clip])
    t = 1.0
    if case == "hidden":
        track.visible = False
    elif case == "locked":
        track.locked = True
    elif case == "disabled":
        clip.enabled = False
    elif case == "nested":
        clip.sequence_id = "seq"
    elif case == "before":
        clip.timeline_start, t = 3.0, 1.0
    elif case == "after":
        t = 2.5
    assert _ids(_project([track]), t) == []


def test_a_soloed_video_track_hides_the_other_video_tracks_from_the_picker():
    """Revue de #143 : avec un solo, le rendu ne montre que la piste en solo ; un clip invisible plus haut ne doit pas voler le clic."""
    low = Track("V1", "V1", "video", clips=[_video("under", "V1")], solo=True)
    high = Track("V2", "V2", "video", clips=[_video("over", "V2")])
    project = _project([low, high])
    assert _ids(project) == ["under"]
    low.solo = False
    assert _ids(project) == ["under", "over"]


def test_videos_come_before_graphics_even_when_the_graphics_track_has_a_lower_index():
    project = _project()
    graphics = Track("G1", "G1", "graphics")
    video = Track("V1", "V1", "video", clips=[_video("v", "V1")])
    project.tracks.extend([graphics, video])  # G d'index 0 < V d'index 1
    _text(project, graphics, "t")
    assert _ids(project) == ["v", "t"]


def test_higher_video_track_is_on_top():
    low = Track("V1", "V1", "video", clips=[_video("low", "V1")])
    high = Track("V2", "V2", "video", clips=[_video("high", "V2")])
    assert _ids(_project([low, high])) == ["low", "high"]  # le dernier est dessus


# -- _hit sur un ViewerOverlay construit à la main ----------------------------------------------


def _overlay(boxes, selection=None):
    from ui.viewer_overlay import ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    overlay.set_layer_boxes(boxes)
    overlay.selection = selection
    return overlay


def _selection(clip_id, world, box):
    from ui.viewer_overlay import SelectionGeometry

    return SelectionGeometry(
        clip_id,
        world,
        (1, 0, 0, 1, 0, 0),
        box,
        {
            "position_x": 0.0,
            "position_y": 0.0,
            "scale": 1.0,
            "anchor_x": 0.5,
            "anchor_y": 0.5,
            "scale_x": 1.0,
        },
    )


IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FRAME = ("v", IDENT, (1280.0, 720.0))
TEXT = (
    "t",
    (1.0, 0.0, 0.0, 1.0, 400.0, 300.0),
    (200.0, 100.0),
)  # canvas (400..600, 300..400) -> scène /2


def test_hit_click_on_a_video_selects_it():
    overlay = _overlay([FRAME])
    assert overlay._hit(QPointF(100, 100)) == ("select", "v")
    assert overlay._hit(QPointF(-50, -50)) is None  # hors cadre : rien


def test_hit_selected_full_frame_video_lets_the_text_above_be_picked():
    overlay = _overlay([FRAME, TEXT], _selection(*FRAME))
    assert overlay._hit(QPointF(215, 160)) == ("select", "t")
    assert overlay._hit(overlay.to_scene(100.0, 100.0)) == ("move", "v")


def test_hit_selection_box_without_anything_above_moves():
    overlay = _overlay([FRAME, TEXT], _selection(*TEXT))
    assert overlay._hit(QPointF(215, 160)) == ("move", "t")
    # Une vidéo EN DESSOUS de la sélection ne la vole pas.
    assert overlay._hit(QPointF(285, 190)) == ("move", "t")


def test_hit_selected_layer_partly_covered_by_a_layer_above_gives_way_there():
    low = ("a", IDENT, (400.0, 300.0))
    high = ("b", (1.0, 0.0, 0.0, 1.0, 200.0, 0.0), (400.0, 300.0))
    overlay = _overlay([low, high], _selection(*low))
    assert overlay._hit(QPointF(150, 75)) == ("select", "b")  # recouvrement
    assert overlay._hit(QPointF(20, 75)) == ("move", "a")  # partie libre


def test_hit_handles_keep_absolute_priority():
    selection = _selection(*TEXT)
    overlay = _overlay([FRAME, TEXT, ("o", IDENT, (1280.0, 720.0))], selection)
    handle = overlay._handle_points(selection)["tl"]
    assert overlay._hit(handle) == ("tl", "t")


def test_hit_selected_container_not_in_boxes_still_moves():
    overlay = _overlay([FRAME], _selection("group", *TEXT[1:]))
    assert overlay._hit(QPointF(215, 160)) == ("move", "group")


# -- fenêtre principale ---------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        "ui.main_window.QMessageBox.information", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = Project(name="UI", width=1280, height=720, fps=25.0)
    win.project.media_assets.append(
        MediaAsset("asset", "/tmp/clip.mp4", "clip", 6, 1280, 720, 25.0, "video")
    )
    win.project.tracks.append(
        Track("V1", "V1", "video", clips=[_video("v", "V1", 0.0, 6.0)])
    )
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def test_clicking_a_video_in_the_monitor_selects_it_without_moving_the_playhead(window):
    window.playhead_seconds = 2.0
    window._refresh_viewer_graphics()
    overlay = window.preview_panel.overlay
    assert [cid for cid, _w, _b in overlay.layer_boxes] == ["v"]
    center = overlay.to_scene(640.0, 360.0)
    assert overlay._hit(center) == ("select", "v")
    _click_scene(
        window, 640.0 - 200.0, 360.0 - 100.0
    )  # vrai clic Qt (hors poignée d'ancrage au centre)
    assert window._primary_selected_id() == "v"
    assert window.playhead_seconds == pytest.approx(2.0)
    assert overlay.selection is not None and overlay.selection.clip_id == "v"
    # Une fois sélectionné, un clic au même endroit le déplace (geste inchangé).
    assert overlay._hit(overlay.to_scene(100.0, 100.0)) == ("move", "v")


# -- de vrais clics sur la fenêtre principale (le défaut de #143 : la zone cliquable, pas le choix du clip) --------


def _click_scene(window, canvas_x: float, canvas_y: float) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    panel = window.preview_panel
    scene_point = panel.overlay.to_scene(canvas_x, canvas_y)
    pixel = panel.graphics_view.mapFromScene(scene_point)
    QTest.mouseClick(
        panel.graphics_view.viewport(),
        Qt.LeftButton,
        Qt.NoModifier,
        QPoint(pixel.x(), pixel.y()),
    )


def test_real_clicks_select_among_overlapping_full_frame_videos_and_a_graphic(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    window.project.tracks.append(
        Track("V2", "V2", "video", clips=[_video("v2", "V2", 0.0, 6.0)])
    )
    graphics = Track("G1", "G1", "graphics")
    window.project.tracks.append(graphics)
    _text(window.project, graphics, "t")
    window._timeline_index = None
    window._reload_timeline_preserving_selection()
    window.playhead_seconds = 2.0
    window._refresh_viewer_graphics()
    overlay = window.preview_panel.overlay
    assert [cid for cid, _w, _b in overlay.layer_boxes][:2] == ["v", "v2"]
    text_centre = next(
        (w[4] + b[0] / 2 + 100.0, w[5] + b[1] / 2 + 60.0)
        for cid, w, b in overlay.layer_boxes
        if cid == "t"
    )
    # Deux vidéos plein cadre se recouvrent (nombre pair) : le clic doit quand même arriver à la surcouche.
    pixel = window.preview_panel.graphics_view.mapFromScene(
        overlay.to_scene(10.0, 10.0)
    )
    assert overlay in window.preview_panel.graphics_view.items(pixel)
    _click_scene(window, 10.0, 10.0)
    assert (
        window._primary_selected_id() == "v2"
    )  # la vidéo la plus haute de l'ordre de rendu
    assert window.playhead_seconds == pytest.approx(2.0)
    _click_scene(
        window, *text_centre
    )  # (hors du centre, qui est la poignée d'ancrage de la vidéo) recouvre aussi les deux vidéos : 3 formes
    assert window._primary_selected_id() == "t"
    assert window.playhead_seconds == pytest.approx(2.0)
    _click_scene(window, 10.0, 710.0)  # hors du calque : la vidéo la plus haute
    assert window._primary_selected_id() == "v2"
    assert window.playhead_seconds == pytest.approx(2.0)
    # Un clic hors du cadre (dans la vue) ne livre rien à la surcouche et ne change pas la sélection.
    panel = window.preview_panel
    outside = panel.graphics_view.mapFromScene(overlay.to_scene(-20.0, -20.0))
    if panel.graphics_view.viewport().rect().contains(outside):
        QTest.mouseClick(
            panel.graphics_view.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(outside.x(), outside.y()),
        )
        assert window._primary_selected_id() == "v2"
