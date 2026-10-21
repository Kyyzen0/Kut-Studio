"""ADR-0006 étape 2 : recadrage non destructif = un masque rectangle généré (`core/clip_crop.py`)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from core.clip_crop import (
    CROP_MASK_ID, MAX_CROP_SIDE, MIN_VISIBLE, Crop, CropError, clear_crop, crop_is_animated, crop_of, crop_to_mask,
    keep_crop_last, mask_to_crop, set_crop, with_crop,
)
from core.compositing import Compositing, Mask, MaskMode, MaskShape, build_ffmpeg_filters
from core.graphics import add_graphic_clip, update_graphic
from core.keyframe_editing import add_keyframe
from core.mograph_raster import MographRenderer, scene_for_plan
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform

pytestmark = pytest.mark.usefixtures("qapp")

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H = 160, 90
ASYMMETRIC = Crop(left=0.25, top=0.1, right=0.05, bottom=0.4)     # visible : x 40..152, y 9..54 (cadre 160 x 90)


def _project() -> Project:
    return Project(name="Crop", width=W, height=H, fps=10.0)


def _full_frame_layer(project: Project, color: str = "#FFFFFF"):
    clip = add_graphic_clip(project, "solid", timeline_start=0, duration=2)
    update_graphic(clip, "fill_color", color)
    update_graphic(clip, "width", W)
    update_graphic(clip, "height", H)
    return clip


def _render(project: Project, t: float = 0.0) -> QImage:
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    return MographRenderer(scene, W, H, fps=10.0, quality="export", motion_blur=plan.motion_blur).render(
        scene.top_level(), t, blend_modes=True)


def _alpha(image: QImage, x: int, y: int) -> int:
    return image.pixelColor(x, y).alpha()


# --- Fonctions pures ---------------------------------------------------------------------------------------------------


def test_crop_to_mask_and_back_round_trip():
    mask = crop_to_mask(ASYMMETRIC)
    assert (mask.shape, mask.mode, mask.id, mask.name) == (MaskShape.RECTANGLE, MaskMode.INTERSECT, CROP_MASK_ID, "Recadrage")
    assert mask.width == pytest.approx(0.7) and mask.height == pytest.approx(0.5)
    assert (mask.position_x, mask.position_y) == pytest.approx((0.6, 0.35))
    assert mask_to_crop(mask) == ASYMMETRIC
    for crop in (Crop(), Crop(0.95), Crop(0.0, 0.0, 0.0, 0.95), Crop(0.475, 0.0, 0.475, 0.0), Crop(0.1, 0.2, 0.3, 0.4)):
        assert mask_to_crop(crop_to_mask(crop)) == crop


def test_mask_to_crop_refuses_what_four_edges_cannot_express():
    base = crop_to_mask(ASYMMETRIC)
    from dataclasses import replace

    for change in ({"rotation": 5.0}, {"feather": 0.1}, {"expansion": 0.1}, {"opacity": 0.5}, {"inverted": True},
                   {"mode": MaskMode.ADD}, {"shape": MaskShape.ELLIPSE}, {"position_x": 0.1}):
        assert mask_to_crop(replace(base, **change, id=base.id)) is None, change


def test_bounds_come_from_the_model_and_are_never_clamped():
    for bad in (-0.001, MAX_CROP_SIDE + 0.001, float("nan"), float("inf"), "x"):
        with pytest.raises(CropError):
            Crop(left=bad)
    with pytest.raises(CropError):
        Crop(left=0.5, right=0.5)                        # il ne resterait rien
    with pytest.raises(CropError):
        Crop(top=0.9, bottom=0.1)
    Crop(left=1.0 - MIN_VISIBLE - MAX_CROP_SIDE + 0.0, right=MAX_CROP_SIDE)   # la limite exacte est permise
    assert crop_to_mask(Crop(left=0.95)).width == pytest.approx(0.05)


# --- Pixels : la convention de coordonnées, prouvée --------------------------------------------------------------------


def test_matte_pixels_follow_left_top_right_bottom():
    project = _project()
    layer = _full_frame_layer(project)
    layer.compositing = Compositing(masks=(crop_to_mask(ASYMMETRIC),))
    image = _render(project)
    inside = [(41, 10), (100, 30), (151, 53), (40, 9), (80, 30)]
    outside = [(39, 30), (152, 30), (100, 8), (100, 54), (5, 5), (158, 88), (20, 30), (156, 30)]
    assert all(_alpha(image, x, y) == 255 for x, y in inside), [(p, _alpha(image, *p)) for p in inside]
    assert all(_alpha(image, x, y) == 0 for x, y in outside), [(p, _alpha(image, *p)) for p in outside]
    # Les arêtes tombent exactement sur 25 % / 5 % / 10 % / 40 % du cadre de 160 x 90.
    assert (_alpha(image, 39, 30), _alpha(image, 40, 30)) == (0, 255)
    assert (_alpha(image, 151, 30), _alpha(image, 152, 30)) == (255, 0)
    assert (_alpha(image, 80, 8), _alpha(image, 80, 9)) == (0, 255)
    assert (_alpha(image, 80, 53), _alpha(image, 80, 54)) == (255, 0)


def test_a_lone_intersect_mask_crops_a_full_frame_layer_and_no_crop_changes_nothing():
    project = _project()
    layer = _full_frame_layer(project)
    assert _alpha(_render(project), 3, 3) == 255
    layer.compositing = Compositing(masks=(crop_to_mask(Crop(left=0.5)),))
    image = _render(project)
    assert (_alpha(image, 70, 40), _alpha(image, 90, 40)) == (0, 255)


def test_intersect_last_restricts_other_masks_but_intersect_first_would_not():
    project = _project()
    layer = _full_frame_layer(project)
    half = Mask(position_x=0.25, width=0.5, height=1.0, id="left-half")        # add : x 0..80
    layer.compositing = with_crop(Compositing(masks=(half,)), Crop(left=0.25))
    assert [m.id for m in layer.compositing.masks] == ["left-half", CROP_MASK_ID]
    image = _render(project)
    assert (_alpha(image, 20, 45), _alpha(image, 60, 45), _alpha(image, 100, 45)) == (0, 255, 0)   # 40..80 seulement
    # Contre-épreuve : rangé avant le masque « add », le recadrage s'y ajouterait (c'est pourquoi il reste en dernier).
    layer.compositing = Compositing(masks=(crop_to_mask(Crop(left=0.25)), half))
    image = _render(project)
    assert _alpha(image, 20, 45) == 255 and _alpha(image, 100, 45) == 255
    assert keep_crop_last(layer.compositing.masks)[-1].id == CROP_MASK_ID


def test_a_rotated_layer_carries_its_crop_with_it():
    project = _project()
    layer = _full_frame_layer(project)
    layer.transform = ClipTransform(rotation=180.0)
    layer.compositing = Compositing(masks=(crop_to_mask(Crop(left=0.25)),))
    image = _render(project)
    assert (_alpha(image, 140, 45), _alpha(image, 100, 45)) == (0, 255)      # le bord gauche du calque est à droite


def test_crop_matches_the_legacy_ffmpeg_geq_expression():
    filters = ",".join(build_ffmpeg_filters(Compositing(masks=(crop_to_mask(ASYMMETRIC),)), W, H))
    assert "between(X,40,152)" in filters and "between(Y,9,54)" in filters


# --- Modèle : un seul masque, les autres intacts -----------------------------------------------------------------------


def _video_project(*, locked: bool = False) -> Project:
    clip = Clip("c", "a", "V1", 0.0, 0.0, 2.0)
    return Project(
        name="Crop", width=W, height=H, fps=10.0,
        media_assets=[MediaAsset("a", "/x/a.mp4", "a", 2.0, W, H, 10.0, "video")],
        tracks=[Track("V1", "V1", "video", clips=[clip], locked=locked), Track("A1", "A1", "audio", clips=[Clip("s", "a", "A1", 0.0, 0.0, 1.0)])],
    )


def test_two_set_crop_calls_use_a_single_mask_and_leave_other_masks_alone():
    project = _video_project()
    clip = project.tracks[0].clips[0]
    mine = Mask(shape=MaskShape.ELLIPSE, width=0.4, name="Mine", id="deadbeef")
    clip.compositing = Compositing(masks=(mine,))
    set_crop(project, "c", Crop(left=0.1))
    set_crop(project, "c", Crop(left=0.2, top=0.3))
    masks = clip.compositing.masks
    assert [m.id for m in masks] == ["deadbeef", CROP_MASK_ID]
    assert masks[0] is mine
    assert crop_of(clip) == Crop(left=0.2, top=0.3)
    assert set_crop(project, "c", Crop()) is None            # nul : plus de masque
    assert clip.compositing.masks == (mine,)
    clear_crop(project, "c")                                  # sans effet, sans erreur
    assert clip.compositing.masks == (mine,)


def test_refusals_are_explicit():
    project = _video_project(locked=True)
    with pytest.raises(CropError, match="verrouillée"):
        set_crop(project, "c", Crop(left=0.1))
    with pytest.raises(CropError, match="verrouillée"):
        clear_crop(project, "c")
    project = _video_project()
    with pytest.raises(CropError, match="introuvable"):
        set_crop(project, "nope", Crop(left=0.1))
    with pytest.raises(CropError, match="seuls les clips"):
        set_crop(project, "s", Crop(left=0.1))
    assert project.tracks[0].clips[0].compositing.masks == ()


def test_the_stabilisation_crop_mask_is_not_touched_and_ids_do_not_collide():
    from core.tracking_bindings import CROP_MASK_ID as STAB_ID

    assert STAB_ID != CROP_MASK_ID
    project = _video_project()
    clip = project.tracks[0].clips[0]
    clip.compositing = Compositing(masks=(Mask(mode=MaskMode.INTERSECT, id=STAB_ID, name="Stabilisation"),))
    set_crop(project, "c", Crop(right=0.1))
    assert [m.id for m in clip.compositing.masks] == [STAB_ID, CROP_MASK_ID]
    clear_crop(project, "c")
    assert [m.id for m in clip.compositing.masks] == [STAB_ID]


def test_an_animated_crop_keeps_its_keyframes_and_set_crop_does_not_override_them():
    project = _video_project()
    clip = project.tracks[0].clips[0]
    set_crop(project, "c", Crop(left=0.25))
    assert not crop_is_animated(clip)
    add_keyframe(project, "c", f"mask.{CROP_MASK_ID}.width", 0.0, 0.75)
    add_keyframe(project, "c", f"mask.{CROP_MASK_ID}.width", 1.0, 0.5)
    assert crop_is_animated(clip)
    with pytest.raises(CropError, match="animé"):
        set_crop(project, "c", Crop(left=0.1))
    set_crop(project, "c", Crop())                           # réinitialiser reste possible, et emporte les images-clés
    assert not crop_is_animated(clip) and clip.compositing.masks == ()
    assert not [kf for kf in clip.animation if kf.property_name.startswith("mask.")]


def test_animated_crop_changes_the_matte_over_time():
    project = _project()
    layer = _full_frame_layer(project)
    layer.compositing = with_crop(layer.compositing, Crop(left=0.25))
    add_keyframe(project, layer.id, f"mask.{CROP_MASK_ID}.width", 0.0, 0.75)
    add_keyframe(project, layer.id, f"mask.{CROP_MASK_ID}.width", 1.0, 0.5)
    start, later = _render(project, 0.0), _render(project, 1.0)
    assert _alpha(start, 150, 45) == 255 and _alpha(later, 150, 45) == 0     # la largeur visible a diminué
    assert _alpha(start, 100, 45) == 255 and _alpha(later, 100, 45) == 255


def test_project_round_trip_keeps_the_crop_and_the_format_version(tmp_path):
    project = _video_project()
    project.tracks[0].clips[0].compositing = Compositing(masks=(Mask(id="deadbeef"),))
    set_crop(project, "c", ASYMMETRIC)
    path = tmp_path / "crop.kut"
    save_project(project, str(path))
    assert CURRENT_VERSION == 17                              # le format ne change pas
    restored = load_project(str(path))
    clip = restored.tracks[0].clips[0]
    assert crop_of(clip) == ASYMMETRIC
    assert [m.id for m in clip.compositing.masks] == ["deadbeef", CROP_MASK_ID]
    assert build_render_plan(restored).video_layers[0].compositing == clip.compositing


# --- FFmpeg réel : le même matte à l'export ----------------------------------------------------------------------------


def _ffmpeg_frame(project: Project, t: float, tmp_path: Path) -> QImage:
    from core.export_engine import ExportEngine, input_arguments

    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, 10, None)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=rgb24[probe];[{audio}]anullsink"
    out = tmp_path / f"frame-{t}.png"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return QImage(str(out))


def _first_lit(image: QImage, y: int | None = None, x: int | None = None) -> int:
    span = range(W) if y is not None else range(H)
    return next(i for i in span if image.pixelColor(i if y is not None else x, y if y is not None else i).red() > 128)


@needs_ffmpeg
def test_real_export_of_a_cropped_graphic_matches_the_qt_matte(tmp_path):
    project = _project()
    layer = _full_frame_layer(project)
    layer.compositing = with_crop(layer.compositing, ASYMMETRIC)
    qt, exported = _render(project), _ffmpeg_frame(project, 0.5, tmp_path)
    assert exported.pixelColor(100, 30).red() > 240 and exported.pixelColor(20, 30).red() < 16
    assert exported.pixelColor(100, 70).red() < 16 and exported.pixelColor(100, 3).red() < 16
    # Les quatre arêtes tombent au même pixel (à 1 près) dans l'export et dans le rastériseur.
    assert abs(_first_lit(exported, y=30) - 40) <= 1
    assert abs(_first_lit(exported, x=100) - 9) <= 1
    assert max(x for x in range(W) if exported.pixelColor(x, 30).red() > 128) in range(150, 153)
    assert max(y for y in range(H) if exported.pixelColor(100, y).red() > 128) in range(52, 55)
    assert _alpha(qt, 40, 30) == 255 and _alpha(qt, 39, 30) == 0


@needs_ffmpeg
def test_real_export_of_a_cropped_full_frame_video(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r=10:d=2",
                    "-pix_fmt", "yuv420p", str(source)], check=True)
    project = _project()
    project.media_assets.append(MediaAsset("a", str(source), "src", 2, W, H, 10, "video"))
    project.tracks.append(Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0, 0, 2)]))
    set_crop(project, "v", Crop(left=0.25, top=0.1, right=0.05, bottom=0.4))
    frame = _ffmpeg_frame(project, 0.5, tmp_path)
    assert frame.pixelColor(100, 30).red() > 235 and frame.pixelColor(20, 30).red() < 20
    assert frame.pixelColor(100, 70).red() < 20 and frame.pixelColor(156, 30).red() < 20
    assert abs(_first_lit(frame, y=30) - 40) <= 2


# --- Fenêtre principale : l'inspecteur ---------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = Project(name="Crop UI", width=1280, height=720, fps=25.0)
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def _selected_layer(win):
    from core.mograph_layers import layer_clips

    win.add_layer_at_playhead("shape", "rectangle")
    (layer,) = [clip for _i, _t, clip in layer_clips(win.project)]
    win._select_layer(layer.id)
    return layer


def test_inspector_fields_edit_the_crop_mask_in_one_history_step_and_undo(window):
    layer = _selected_layer(window)
    editor = window.properties_panel.compositing_group
    crop_editor = editor.crop_editor
    entries = len(window.history)
    for value in (10.0, 20.0, 25.0):                                  # une rafale : aperçu en direct
        crop_editor.spins["left"].setValue(value)
    crop_editor.spins["bottom"].setValue(40.0)
    assert crop_of(layer) == Crop(left=0.25, bottom=0.4)              # appliqué avant même la fin de la rafale
    window._finalize_pending_edit_sessions()
    assert len(window.history) == entries + 1
    labels = [editor.mask_list.item(i).text() for i in range(editor.mask_list.count())]
    assert len(labels) == 1 and labels[0].startswith("Recadrage")
    window.undo_last()
    (restored,) = [c for t in window.project.tracks for c in t.clips]
    assert crop_of(restored) is None
    assert len(window.history) == entries


def test_fields_limit_opposite_edges_instead_of_clamping_silently(window):
    _selected_layer(window)
    spins = window.properties_panel.compositing_group.crop_editor.spins
    spins["left"].setValue(60.0)
    assert spins["right"].maximum() == pytest.approx(35.0)            # 95 % - 60 %
    spins["right"].setValue(95.0)
    assert spins["right"].value() == pytest.approx(35.0)


def test_reset_button_and_menu_command_remove_only_the_crop_mask(window):
    layer = _selected_layer(window)
    editor = window.properties_panel.compositing_group
    editor.add_mask.click()                                           # un masque à moi
    mine = [m.id for m in layer.compositing.masks]
    assert len(mine) == 1
    editor.crop_editor.spins["top"].setValue(30.0)
    editor.add_mask.click()                                           # ajouté après : le recadrage reste en dernier
    assert [m.id for m in layer.compositing.masks][-1] == CROP_MASK_ID
    assert len(layer.compositing.masks) == 3
    window._finalize_pending_edit_sessions()
    entries = len(window.history)
    editor.crop_editor.reset_button.click()
    window._finalize_pending_edit_sessions()
    assert crop_of(layer) is None and len(layer.compositing.masks) == 2 and mine[0] in [m.id for m in layer.compositing.masks]
    assert len(window.history) == entries + 1
    # Commande de menu : sans recadrage elle ne fait rien ; avec, elle le retire en une étape.
    assert window.reset_selected_crop() is False and len(window.history) == entries + 1
    set_crop(window.project, layer.id, Crop(left=0.2))
    window._select_layer(layer.id)
    assert window.reset_selected_crop() is True
    assert crop_of(layer) is None and len(window.history) == entries + 2


def test_widget_shows_a_hand_edited_or_animated_crop_as_read_only(window):
    layer = _selected_layer(window)
    editor = window.properties_panel.compositing_group
    crop_editor = editor.crop_editor
    set_crop(window.project, layer.id, Crop(left=0.2))
    window._select_layer(layer.id)
    window._reload_timeline_preserving_selection(layer.id)
    assert crop_editor.spins["left"].isEnabled() and crop_editor.spins["left"].value() == pytest.approx(20.0)
    add_keyframe(window.project, layer.id, f"mask.{CROP_MASK_ID}.width", 0.0, 0.8)
    window._reload_timeline_preserving_selection(layer.id)
    assert not crop_editor.spins["left"].isEnabled() and not crop_editor.hint.isHidden()
    assert crop_editor.reset_button.isEnabled()


# --- Revue : bords au-delà de 95 %, verrou de calque, inspecteur non éditable -------------------------------------------


def test_a_mask_with_an_edge_beyond_95_percent_is_not_a_crop_and_is_never_clamped():
    from dataclasses import replace

    base = crop_to_mask(Crop(left=0.1))
    too_far = replace(base, position_x=0.98, width=0.04, id=base.id)            # bord gauche à 0,96
    assert mask_to_crop(too_far) is None
    assert mask_to_crop(replace(base, position_x=0.975, width=0.05, id=base.id)) == Crop(left=0.95)   # 0,95 : toujours un recadrage


def test_a_locked_graphic_layer_refuses_set_and_clear_crop():
    from core.mograph_layers import set_layer_locked

    project = _project()
    layer = _full_frame_layer(project)
    set_layer_locked(project, layer.id, True)
    with pytest.raises(CropError, match="verrouillé"):
        set_crop(project, layer.id, Crop(left=0.1))
    with pytest.raises(CropError, match="verrouillé"):
        clear_crop(project, layer.id)
    assert crop_of(layer) is None


def test_inspector_crop_fields_are_disabled_on_locked_track_and_audio_clip(window):
    from core.mograph_layers import set_layer_locked

    layer = _selected_layer(window)
    crop_editor = window.properties_panel.compositing_group.crop_editor
    assert crop_editor.spins["left"].isEnabled()
    track = next(t for t in window.project.tracks if any(c.id == layer.id for c in t.clips))
    track.locked = True
    window._reload_timeline_preserving_selection(layer.id)
    assert not any(spin.isEnabled() for spin in crop_editor.spins.values()) and not crop_editor.reset_button.isEnabled()
    track.locked = False
    set_layer_locked(window.project, layer.id, True)                                                   # verrou de calque
    window._reload_timeline_preserving_selection(layer.id)
    assert not crop_editor.spins["left"].isEnabled()
    set_layer_locked(window.project, layer.id, False)
    window._reload_timeline_preserving_selection(layer.id)
    assert crop_editor.spins["left"].isEnabled()
    window.project.media_assets.append(MediaAsset("au", "/x/a.wav", "a", 2.0, 0, 0, 0.0, "audio", has_audio=True))
    audio = Track("A9", "A9", "audio", clips=[Clip("aud", "au", "A9", 0.0, 0.0, 1.0)])
    window.project.tracks.append(audio)
    window._reload_timeline_preserving_selection("aud")
    window._restore_clip_selection("aud")
    assert not any(spin.isEnabled() for spin in crop_editor.spins.values())


def test_the_reset_button_stays_short_so_it_does_not_widen_the_inspector(qtbot):
    """CI Debian (DejaVu Sans, +20 % de police) : « Réinitialiser le recadrage » imposait 226 px et dépassait l'inspecteur de 10 px.
    Le bouton porte le texte court ; la phrase complète reste dans le nom accessible et l'infobulle."""
    from ui import i18n
    from ui.properties_widgets.crop_editor import CropEditor

    editor = CropEditor()
    qtbot.addWidget(editor)
    assert editor.reset_button.text() == i18n.translate("crop.reset_short")
    assert len(editor.reset_button.text()) < len(i18n.translate("crop.reset"))
    assert editor.reset_button.accessibleName() == i18n.translate("crop.reset")
