"""Page Couleur, étape 3 : les limites levées (outils du moniteur sans GPU, pipette), fenêtres, flou et netteté."""

from __future__ import annotations

import pytest

from core.color_render import Compare, Highlight
from core.workspace_state import PAGE_COLOR, PAGE_EDIT


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


def _qualify(window):
    window.color_panel.qualifier.enabled_check.setChecked(True)
    window.color_panel.qualifier.spins["hue_width"].setValue(60)
    window._finalize_color_history()


# --- outils du moniteur, sans GPU ------------------------------------------------------------------------------------


def test_the_monitor_tools_are_available_without_the_gpu_monitor(window):
    """Avant l'étape 3, *Afficher la sélection* et avant / après étaient grisés sans moniteur GPU."""
    _clip(window)
    assert window.color_panel.compare_button.isEnabled()
    assert window.color_panel.qualifier.highlight_button.isEnabled()


def test_the_faithful_preview_shows_the_selection_and_the_comparison(window):
    """Sans GPU (ou pour un montage hors de sa couverture), les segments fidèles montrent ce que le moniteur GPU montre :
    la sélection, l'avant / après ; rien quand les outils sont arrêtés ou hors de la page Couleur."""
    clip_id = _clip(window)
    assert window._preview_grade_overrides() is None
    _qualify(window)
    window.on_color_highlight_toggled(True)
    shown = window._preview_grade_overrides()[clip_id]
    assert isinstance(shown, Highlight) and shown.node_id == "n1"
    window._set_color_compare(0.4)
    compared = window._preview_grade_overrides()[clip_id]
    assert isinstance(compared, Compare) and isinstance(compared.value, Highlight)
    assert compared.split == pytest.approx(0.4), "plan plein cadre : la part du cadre est celle du clip"
    jobs = window._preview_segment_jobs(0.0)
    layer = next(layer for job in jobs for layer in job.plan.video_layers if layer.clip_id == clip_id)
    assert isinstance(layer.color_grade, Compare), "les segments planifiés portent ce qu'on montre"
    window.on_color_highlight_toggled(False)
    assert window._preview_grade_overrides()[clip_id].value is window.project.tracks[0].clips[0].color_grade
    window.switch_page(PAGE_EDIT)
    assert window._preview_grade_overrides() is None, "hors de la page Couleur, le vrai étalonnage"


def test_moving_the_compare_line_refreshes_the_faithful_segments_once_it_stops(qtbot, window, monkeypatch):
    clip_id = _clip(window)
    refreshed: list[str] = []
    monkeypatch.setattr(window, "_refresh_color_monitor", refreshed.append)
    for split in (0.3, 0.35, 0.4):
        window._set_color_compare(split)
    assert refreshed == [], "pas un rendu par mouvement"
    qtbot.waitUntil(lambda: refreshed == [clip_id], timeout=2000)


def test_the_compare_line_is_converted_into_the_picture_of_a_moved_clip(window):
    """Un clip réduit de moitié et centré occupe le cadre de 25 % à 75 % : le trait au quart du cadre tombe au bord
    gauche de son image, au milieu du cadre au milieu de son image."""
    panel = window.preview_panel
    panel._applied_scale = 0.5
    assert panel.compare_split_in_layer(0.5) == pytest.approx(0.5)
    assert panel.compare_split_in_layer(0.25) == pytest.approx(0.0, abs=1e-6)
    assert panel.compare_split_in_layer(0.625) == pytest.approx(0.75)
    panel._applied_scale = 1.0
    assert panel.compare_split_in_layer(0.3) == pytest.approx(0.3)



# --- pipette ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def blue_window(window, tmp_path):
    """La fenêtre, son premier clip pointé sur un vrai média bleu uni (le projet d'exemple n'a pas de fichiers)."""
    from render_probe import lavfi_video

    clip = window.project.tracks[0].clips[0]
    asset = next(item for item in window.project.media_assets if item.id == clip.asset_id)
    media = lavfi_video(tmp_path / "blue.mp4", "color=c=0x3366CC:d=10", size=(64, 36), seconds=10.0)
    asset.path, asset.width, asset.height = str(media), 64, 36
    return window


def test_the_eyedropper_sets_the_qualifier_from_a_click_in_the_viewer(qtbot, blue_window):
    window = blue_window
    clip_id = _clip(window)
    window.color_panel.qualifier.pick_button.click()
    overlay = window.preview_panel.overlay
    assert overlay.pick_mode, "le prochain clic dans le viewer prend la couleur"
    width, height = window.project.width, window.project.height
    steps = len(window.history.entries())
    overlay.color_picked.emit(width / 2, height / 2, False)
    assert not overlay.pick_mode and not window.color_panel.qualifier.pick_button.isChecked(), "une prise par clic"
    from core.timeline_operations import find_clip

    def qualified():
        grade = find_clip(window.project, clip_id).color_grade
        return bool(getattr(grade, "correctors", None)) and grade.correctors[0].qualifier is not None

    qtbot.waitUntil(qualified, timeout=10000)
    qualifier = find_clip(window.project, clip_id).color_grade.correctors[0].qualifier
    assert qualifier.use_hue and abs(qualifier.hue_center - 220.0) < 6, qualifier
    assert len(window.history.entries()) == steps + 1
    from ui import i18n

    assert window.history.undo_label == i18n.translate("history.color.pick")


def test_a_click_outside_the_clip_picture_says_so(blue_window, monkeypatch):
    window = blue_window
    _clip(window)
    monkeypatch.setattr(window.preview_panel, "canvas_to_media", lambda *args: None)
    window.color_panel.qualifier.pick_button.click()
    window.preview_panel.overlay.color_picked.emit(1.0, 1.0, False)
    from ui import i18n

    assert window.statusBar().currentMessage() == i18n.translate("status.color.pick_outside")


def test_leaving_the_colour_page_stops_the_eyedropper(window):
    _clip(window)
    window.color_panel.qualifier.pick_button.click()
    window.switch_page(PAGE_EDIT)
    assert not window.preview_panel.overlay.pick_mode


# --- fenêtres, flou --------------------------------------------------------------------------------------------------


def _windows_tab(window):
    panel = window.color_panel
    panel.tabs.setCurrentWidget(panel.windows)
    return panel.windows


def _node(window, clip_id):
    from core.timeline_operations import find_clip

    return find_clip(window.project, clip_id).color_grade.correctors[0]


def test_a_window_is_added_from_its_tab_and_edited_in_the_viewer(qtbot, window):
    from core.compositing import MaskShape

    clip_id = _clip(window)
    editor = _windows_tab(window)
    steps = len(window.history.entries())
    editor.add_buttons[MaskShape.ELLIPSE].click()
    node = _node(window, clip_id)
    assert [w.shape for w in node.windows] == [MaskShape.ELLIPSE]
    assert len(window.history.entries()) == steps + 1
    from ui import i18n

    assert window.history.undo_label == i18n.translate("history.color.window_add")
    overlay = window.preview_panel.overlay
    assert overlay.window is not None and overlay.window.window_id == node.windows[0].id, "le viewer l'édite"
    assert overlay.selection is None, "la fenêtre remplace les poignées du clip"
    for _ in range(3):                                             # un glisser : plusieurs mouvements, une étape
        overlay.window_dragged.emit(node.id, node.windows[0].id, {"position_x": 0.3, "width": 0.25})
    overlay.window_released.emit(node.id, node.windows[0].id)
    moved = _node(window, clip_id).windows[0]
    assert (moved.position_x, moved.width, moved.id) == (0.3, 0.25, node.windows[0].id)
    assert len(window.history.entries()) == steps + 2
    assert editor.spins["width"].value() == pytest.approx(25.0), "les champs suivent le viewer"
    window.color_panel.tabs.setCurrentWidget(window.color_panel.wheels)
    assert overlay.window is None, "hors de l'onglet Fenêtres, le viewer rend les poignées du clip"


def test_window_fields_and_blur_are_one_history_step_per_burst(qtbot, window):
    from core.compositing import MaskShape

    clip_id = _clip(window)
    editor = _windows_tab(window)
    editor.add_buttons[MaskShape.RECTANGLE].click()
    steps = len(window.history.entries())
    for value in (10.0, 12.0, 15.0):
        editor.spins["feather"].setValue(value)
    editor.invert_check.setChecked(True)
    window._finalize_color_history()
    edited = _node(window, clip_id).windows[0]
    assert edited.feather == pytest.approx(0.15) and edited.inverted
    assert len(window.history.entries()) == steps + 1
    detail = window.color_panel.detail
    for value in (1.0, 2.5, 4.0):
        detail.spins["blur"].setValue(value)
    detail.spins["sharpen"].setValue(0.5)
    window._finalize_color_history()
    node = _node(window, clip_id)
    assert (node.blur, node.sharpen) == (4.0, 0.5) and len(window.history.entries()) == steps + 2
    window.undo_last()
    assert _node(window, clip_id).blur == 0.0


def test_deleting_a_window_drops_its_keyframes_and_tracking_links(qtbot, window):
    from dataclasses import replace

    from core.animation import Keyframe
    from core.compositing import MaskShape, mask_property_id
    from core.timeline_operations import find_clip
    from core.tracking_model import ClipTracking, TrackLink, TrackTarget

    clip_id = _clip(window)
    editor = _windows_tab(window)
    editor.add_buttons[MaskShape.ELLIPSE].click()
    window_id = _node(window, clip_id).windows[0].id
    clip = find_clip(window.project, clip_id)
    clip.animation = [Keyframe(mask_property_id(window_id, "position_x"), 0.0, 0.4), Keyframe("opacity", 0.0, 1.0)]
    clip.tracking = replace(clip.tracking or ClipTracking(),
                            links=(TrackLink(tracker_ids=("t1",), target=TrackTarget.MASK, mask_id=window_id),))
    window._record_history("préparation")                        # comme une liaison posée par le panneau Tracking
    editor.remove_button.click()
    clip = find_clip(window.project, clip_id)
    assert not getattr(clip.color_grade, "correctors", (None,))[0] or not clip.color_grade.correctors[0].windows
    assert [kf.property_name for kf in clip.animation] == ["opacity"]
    assert not clip.tracking.links
    window.undo_last()
    clip = find_clip(window.project, clip_id)
    assert clip.color_grade.correctors[0].windows[0].id == window_id
    assert len(clip.animation) == 2 and clip.tracking.links, "Annuler rend fenêtre, images-clés et liaison"
    window.redo_last()
    clip = find_clip(window.project, clip_id)
    assert [kf.property_name for kf in clip.animation] == ["opacity"] and not clip.tracking.links, \
        "Rétablir : l'étape enregistrée est déjà nettoyée"


def test_show_selection_works_for_a_window_without_qualifier(window):
    from core.compositing import MaskShape

    clip_id = _clip(window)
    _windows_tab(window).add_buttons[MaskShape.RECTANGLE].click()
    window.color_panel.windows.highlight_button.click()
    assert window.color_panel.qualifier.highlight_button.isChecked(), "un seul état, deux boutons"
    clip = window.project.tracks[0].clips[0]
    shown = window._monitor_color_grade(clip)
    assert isinstance(shown, Highlight) and shown.node_id == _node(window, clip_id).id
    jobs = window._preview_segment_jobs(0.0)
    layer = next(layer for job in jobs for layer in job.plan.video_layers if layer.clip_id == clip_id)
    assert isinstance(layer.color_grade, Highlight), "les segments fidèles montrent la sélection de la fenêtre"


def test_the_viewer_handles_turn_a_gesture_into_the_typed_values():
    """Le geste se mesure sur la fenêtre montrée (animation, tracking) et se reporte sur la saisie."""
    from PySide6.QtCore import QRectF, Qt

    from ui.viewer_overlay import ViewerOverlay, WindowGeometry

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 200, 100), (200.0, 100.0))
    shown = {"position_x": 0.5, "position_y": 0.5, "width": 0.4, "height": 0.4, "rotation": 0.0, "feather": 0.0,
             "expansion": 0.0, "opacity": 1.0}
    base = dict(shown, position_x=0.3, width=0.2)            # le tracking a déplacé et agrandi la fenêtre montrée
    geometry = WindowGeometry("n1", "w", (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (200.0, 100.0), "rectangle", shown, base)
    none = Qt.KeyboardModifier.NoModifier

    def drag(mode, start, end, window=geometry):
        return overlay._window_values({"mode": mode, "window": window, "start": start}, end, none)

    assert drag("w_move", (100, 50), (120, 60)) == pytest.approx({"position_x": 0.4, "position_y": 0.6})
    assert drag("w_r", (140, 50), (160, 50)) == pytest.approx({"width": 0.3}), "0,4 → 0,6 montré, ×1,5 saisi"
    assert drag("w_rotate", (100, 0), (150, 50))["rotation"] == pytest.approx(90.0)
    polygon = WindowGeometry("n1", "w", geometry.world, geometry.box, "polygon", shown, base,
                             points=((0.0, -0.5), (0.5, 0.5), (-0.5, 0.5)))
    assert drag("w_p0", (100, 30), (110, 30), polygon)["points"][0] == pytest.approx((0.125, -0.5))
    overlay.set_window(geometry)
    assert overlay._window_hit(overlay.to_scene(100, 50)) == "w_move"
    assert overlay._window_hit(overlay.to_scene(140, 30)) == "w_tr"


def test_removing_a_node_drops_the_state_of_its_windows_and_reset_clears_its_blur(qtbot, window):
    from dataclasses import replace

    from core.animation import Keyframe
    from core.compositing import MaskShape, mask_property_id
    from core.timeline_operations import find_clip
    from core.tracking_model import ClipTracking, TrackLink, TrackTarget

    clip_id = _clip(window)
    _windows_tab(window).add_buttons[MaskShape.RECTANGLE].click()
    first = _node(window, clip_id)
    window_id = first.windows[0].id
    window.on_color_node_add(first.id)                              # le nœud fenêtré devient supprimable
    clip = find_clip(window.project, clip_id)
    clip.animation = [Keyframe(mask_property_id(window_id, "width"), 0.0, 0.3)]
    clip.tracking = replace(clip.tracking or ClipTracking(),
                            links=(TrackLink(tracker_ids=("t1",), target=TrackTarget.MASK, mask_id=window_id),))
    window._record_history("préparation")
    window.on_color_node_remove(first.id)
    clip = find_clip(window.project, clip_id)
    assert not clip.animation and not clip.tracking.links, "le nœud emporte l'état de ses fenêtres"

    second = clip.color_grade if not hasattr(clip.color_grade, "correctors") else clip.color_grade.correctors[0]
    node_id = getattr(second, "id", "n1")
    window.on_color_node_selected(node_id)
    window.color_panel.detail.spins["blur"].setValue(3.0)
    window.color_panel.detail.spins["sharpen"].setValue(1.0)
    window._finalize_color_history()
    node = _node(window, clip_id)
    assert (node.blur, node.sharpen) == (3.0, 1.0)
    window.on_color_node_reset(node.id)
    grade = find_clip(window.project, clip_id).color_grade
    reset = grade.correctors[0] if hasattr(grade, "correctors") else None
    assert reset is None or (reset.blur, reset.sharpen) == (0.0, 0.0), "réinitialisé : plus de flou ni de netteté"
    assert window.color_panel.detail.spins["blur"].value() == 0.0
