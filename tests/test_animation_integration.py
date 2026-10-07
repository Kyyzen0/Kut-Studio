"""Animation de bout en bout : inspecteur, timeline, Graph Editor, commandes, aperçu, export."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from ffmpeg_expr import evaluate as evaluate_expression

from core.animation import InterpolationType
from core.animation_targets import get_target
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.keyframe_editing import KeyframeRef, add_keyframe, keyframe_times, set_tangents, value_at
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, evaluate_transform

I = InterpolationType  # noqa: E741


def _project(tmp_path: Path) -> Project:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"\x00" * 64)
    asset = MediaAsset(id="a", path=str(media), name="source", duration=30.0, width=1920,
                       height=1080, fps=30.0, media_type="video")
    # Clip rogné à gauche (source_in = 2 s) et placé à 1,5 s : temps source,
    # temps de timeline et temps local au clip sont tous différents.
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=1.5, source_in=2.0,
                source_out=6.0, label="Clip", transform=ClipTransform())
    return Project(name="anim", width=1920, height=1080, fps=30.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    # Le faux média n'est pas lisible : on ne le donne jamais au lecteur Qt.
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = _project(tmp_path)
    win._reload_timeline_preserving_selection("c")
    win.history.reset(win.project)
    win.timeline_panel.selected_clip_id = "c"
    win.timeline_panel.selected_clip_ids = {"c"}
    win.on_clip_selected("c")
    return win


def _clip(win):
    return win.project.tracks[0].clips[0]


def _animate(win):
    project = win.project
    add_keyframe(project, "c", "opacity", 0.0, 1.0, interpolation=I.EASE_IN_OUT)
    add_keyframe(project, "c", "opacity", 2.0, 0.2, interpolation=I.BEZIER)
    add_keyframe(project, "c", "opacity", 4.0, 0.9)
    add_keyframe(project, "c", "position_x", 0.5, -0.5, interpolation=I.BEZIER)
    add_keyframe(project, "c", "position_x", 3.0, 0.5)
    ref = KeyframeRef("c", "position_x", get_target("position_x").curve(_clip(win)).keyframe_at(0.5).id)
    set_tangents(project, ref, out_slope=1.5)
    add_keyframe(project, "c", "scale", 0.0, 1.0, interpolation=I.EASE_OUT)
    add_keyframe(project, "c", "scale", 4.0, 2.0)
    add_keyframe(project, "c", "rotation", 1.0, 0.0, interpolation=I.HOLD)
    add_keyframe(project, "c", "rotation", 2.5, 45.0)
    win._reload_timeline_preserving_selection("c")


# --- Aperçu = export ----------------------------------------------------------------------------------


def _filter_graph(win, tmp_path) -> str:
    plan = build_render_plan(win.project)
    request = ExportRequest(plan, str(tmp_path / "o.mp4"), ExportFormat.MP4_H264,
                            ExportPreset("T", (1920, 1080), 20, "128k"), 30)
    engine = ExportEngine()
    engine._prepare_temporary_files(plan)
    try:
        command = engine._build_command(request)
    finally:
        engine._cleanup_temporary_files()
    return command[command.index("-filter_complex") + 1]


def test_preview_and_export_show_the_same_values_at_every_frame(window, tmp_path):
    _animate(window)
    applied = []
    window.preview_panel.apply_transform = lambda **values: applied.append(values)
    graph = _filter_graph(window, tmp_path)
    x_expression = graph.split("x='round((W-w)/2+(", 1)[1].split(")*1920.0", 1)[0]
    opacity_expression = graph.split("geq=", 1)[1].split("a='", 1)[1].split("'", 1)[0]
    rotation_expression = graph.split("rotate=a='", 1)[1].split("*0.0174", 1)[0]
    clip = _clip(window)
    for frame in range(0, 120, 7):                          # 0 à 4 s du clip, image par image
        local = frame / 30
        playhead = clip.timeline_start + local
        window.playhead_seconds = playhead
        window._sync_preview_to_timeline()
        shown = applied[-1]
        engine = evaluate_transform(clip.transform, clip.transform_keyframes, local, clip.duration)
        assert shown["opacity"] == pytest.approx(engine.opacity)
        assert shown["position_x"] == pytest.approx(engine.position_x)
        # L'export : mêmes valeurs, dans la variable de temps propre à chaque filtre.
        assert evaluate_expression(x_expression, t=playhead) == pytest.approx(engine.position_x, abs=1e-6)
        assert evaluate_expression(opacity_expression, T=local) == pytest.approx(engine.opacity, abs=1e-6)
        assert evaluate_expression(rotation_expression, t=local) == pytest.approx(engine.rotation, abs=1e-6)


def test_preview_uses_clip_local_time_not_source_time(window):
    """Régression : l'aperçu évaluait au temps du média source (faux sur un clip rogné)."""
    add_keyframe(window.project, "c", "opacity", 0.0, 0.0)
    add_keyframe(window.project, "c", "opacity", 4.0, 1.0)
    applied = []
    window.preview_panel.apply_transform = lambda **values: applied.append(values)
    window.playhead_seconds = 1.5 + 1.0                      # 1 s dans le clip, 3 s dans la source
    window._sync_preview_to_timeline()
    assert applied[-1]["opacity"] == pytest.approx(0.25)


def test_faithful_preview_segments_render_the_same_keyframes(window):
    _animate(window)
    jobs = window._preview_segment_jobs(2.0)
    layer = jobs[0].plan.video_layers[0]
    assert tuple(layer.transform_keyframes) == tuple(_clip(window).transform_keyframes)


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg requis")
def test_real_export_matches_the_animation_engine_pixel_by_pixel(qtbot, tmp_path):
    """Un aplat blanc dont l'opacité suit une courbe Bézier : la luminance exportée suit la courbe."""
    from test_export_integration import _wait_for_export

    white = tmp_path / "white.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=white:s=64x36:r=25:d=4", "-pix_fmt", "yuv420p", str(white)], check=True)
    asset = MediaAsset(id="w", path=str(white), name="w", duration=4.0, width=64, height=36,
                       fps=25.0, media_type="video")
    clip = Clip(id="c", asset_id="w", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=4.0)
    project = Project(name="px", width=64, height=36, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    add_keyframe(project, "c", "opacity", 0.0, 0.0, interpolation=I.BEZIER)
    add_keyframe(project, "c", "opacity", 3.0, 1.0)
    set_tangents(project, KeyframeRef("c", "opacity", clip.transform_keyframes[0].id), out_slope=0.0)
    output = tmp_path / "out.mp4"
    plan = build_render_plan(project)
    engine = ExportEngine()
    request = ExportRequest(plan, str(output), ExportFormat.MP4_H264, ExportPreset("T", (64, 36), 1, "96k"), 25)
    finished, failed = _wait_for_export(engine, timeout_ms=60000, start=lambda: engine.start(request))
    assert finished and not failed, failed
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output), "-vf", "scale=1:1:flags=area",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True,
    ).stdout
    curve_is_not_linear = False
    for frame in range(0, 100, 5):
        t = frame / 25
        opacity = value_at(clip, "opacity", t)
        expected = 255 * opacity                             # sortie « gray » en pleine échelle
        assert abs(raw[frame] - expected) <= 8, (frame, raw[frame], expected)
        curve_is_not_linear |= abs(opacity - min(1.0, t / 3)) > 0.08
    assert curve_is_not_linear                               # c'est bien la courbe Bézier, pas une droite


# --- Parcours débutant (inspecteur) ----------------------------------------------------------------------


def test_beginner_flow_diamond_playhead_value(window):
    panel = window.properties_panel
    window.seek_to_position(1.5 + 0.5)
    panel._apply_diamond_action("opacity", shift=False)    # 1. clic sur le losange : animation activée
    assert keyframe_times(_clip(window), ["opacity"]) == [0.5]
    assert panel._diamonds["opacity"].isChecked()
    window.seek_to_position(1.5 + 2.5)                       # 2. la tête bouge
    assert not panel._diamonds["opacity"].isChecked() and panel._diamonds["opacity"].animated
    panel._spin_boxes["opacity"].setValue(0.3)               # 3. on change la valeur → image-clé
    window._finalize_transform_session()
    assert keyframe_times(_clip(window), ["opacity"]) == [0.5, 2.5]
    assert value_at(_clip(window), "opacity", 1.5) == pytest.approx(0.65)
    # Navigation par les flèches de la ligne.
    previous_button, next_button = panel._keyframe_nav_buttons["opacity"]
    assert previous_button.isEnabled() and not next_button.isEnabled()
    previous_button.click()
    assert window.playhead_seconds == pytest.approx(2.0)
    # Second clic sur le losange : retire l'image-clé sous la tête.
    panel._apply_diamond_action("opacity", shift=False)
    assert keyframe_times(_clip(window), ["opacity"]) == [2.5]


def test_inspector_menu_offers_interpolation_copy_paste_and_graph(window):
    add_keyframe(window.project, "c", "scale", 0.0, 1.0)
    add_keyframe(window.project, "c", "scale", 2.0, 2.0)
    window._reload_timeline_preserving_selection("c")
    window.seek_to_position(1.5)
    menu = window.properties_panel.animation_menu("scale")
    texts = [action.text() for action in menu.actions()]
    assert any("Désactiver" in text or "Disable" in text for text in texts)
    window.on_keyframe_interpolation_requested("scale", "ease_in")
    assert get_target("scale").curve(_clip(window)).keyframes[0].interpolation is I.EASE_IN
    assert window.copy_animation("scale")
    window.seek_to_position(1.5 + 2.0)
    assert window.paste_animation("rotation") == 2            # échelle → rotation : types compatibles
    assert keyframe_times(_clip(window), ["rotation"]) == [2.0, 4.0]
    window.on_animation_toggled("c", "scale", False)
    assert not get_target("scale").get_keyframes(_clip(window))


# --- Commandes centralisées ---------------------------------------------------------------------------------


def test_commands_have_handlers_and_drive_the_keyframes(window):
    handlers = window._shortcut_handlers()
    for command in ("keyframe_add", "keyframe_remove", "keyframe_previous", "keyframe_next",
                    "keyframe_select_all", "keyframe_copy", "keyframe_paste", "graph_editor",
                    *(f"keyframe_interpolation_{k.value}" for k in I)):
        assert callable(handlers[command])
    window.active_animation_property = "position_x"
    window.seek_to_position(1.5 + 1.0)
    handlers["keyframe_add"]()
    window.seek_to_position(1.5 + 3.0)
    handlers["keyframe_add"]()
    assert keyframe_times(_clip(window), ["position_x"]) == [1.0, 3.0]
    handlers["keyframe_previous"]()
    assert window.playhead_seconds == pytest.approx(2.5)
    handlers["keyframe_next"]()
    assert window.playhead_seconds == pytest.approx(4.5)
    handlers["keyframe_interpolation_hold"]()
    assert get_target("position_x").curve(_clip(window)).keyframes[1].interpolation is I.HOLD
    assert handlers["keyframe_select_all"]() == 2
    handlers["keyframe_remove"]()                              # supprime la sélection
    assert keyframe_times(_clip(window), ["position_x"]) == []


def test_delete_key_removes_selected_keyframes_before_clips(window):
    add_keyframe(window.project, "c", "scale", 1.0, 2.0)
    window._reload_timeline_preserving_selection("c")
    window.select_all_keyframes()
    window._shortcut_handlers()["delete_clip"]()
    assert _clip(window).transform_keyframes == [] and window.project.tracks[0].clips


def test_every_keyframe_edit_is_one_undo_step(window):
    before = len(window.history)
    window.active_animation_property = "rotation"
    window.seek_to_position(1.5 + 1.0)
    window.add_keyframe_at_playhead()
    assert len(window.history) == before + 1
    window.undo_last()
    assert keyframe_times(_clip(window), ["rotation"]) == []
    window.redo_last()
    assert keyframe_times(_clip(window), ["rotation"]) == [1.0]


# --- Timeline -------------------------------------------------------------------------------------------------------


def test_timeline_selects_drags_with_snapping_and_records_one_entry(window, qtbot):
    from PySide6.QtCore import QPoint, Qt

    add_keyframe(window.project, "c", "scale", 1.0, 1.0)
    add_keyframe(window.project, "c", "opacity", 1.0, 0.5)
    window._reload_timeline_preserving_selection("c")
    window.timeline_panel.show()
    widget = window.timeline_panel.clip_widgets["c"]
    items = widget._keyframe_items()
    assert len(items) == 2                                    # un losange par propriété
    rect, frames = items[0]
    before = len(window.history)
    center = rect.center().toPoint()
    qtbot.mousePress(widget, Qt.LeftButton, Qt.NoModifier, center)
    assert {r.property_id for r in window.keyframe_selection} == {frames[0].property_name}
    scale = window.timeline_panel.pixels_per_second * window.timeline_panel.zoom
    qtbot.mouseMove(widget, center + QPoint(int(0.52 * scale), 0))
    qtbot.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, center + QPoint(int(0.52 * scale), 0))
    moved = keyframe_times(_clip(window), [frames[0].property_name])[0]
    frames_index = (1.5 + moved) * 30
    assert moved > 1.3 and abs(frames_index - round(frames_index)) < 1e-3   # aligné sur une image
    other = "opacity" if frames[0].property_name == "scale" else "scale"
    assert keyframe_times(_clip(window), [other]) == [1.0]                   # non sélectionné : immobile
    assert len(window.history) == before + 1


def test_timeline_shift_click_builds_a_multi_selection(window, qtbot):
    from PySide6.QtCore import Qt

    add_keyframe(window.project, "c", "scale", 1.0, 1.0)
    add_keyframe(window.project, "c", "scale", 2.0, 1.5)
    window._reload_timeline_preserving_selection("c")
    window.timeline_panel.show()
    widget = window.timeline_panel.clip_widgets["c"]
    (first, _), (second, _) = sorted(widget._keyframe_items(), key=lambda item: item[0].x())
    qtbot.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, first.center().toPoint())
    qtbot.mouseClick(widget, Qt.LeftButton, Qt.ShiftModifier, second.center().toPoint())
    assert len(window.keyframe_selection) == 2
    assert window.timeline_panel.selected_keyframes == window.keyframe_selection


# --- Graph Editor -------------------------------------------------------------------------------------------------------


def test_graph_editor_follows_the_selection_and_edits_with_one_history_entry(window, qtbot):
    from PySide6.QtCore import Qt

    add_keyframe(window.project, "c", "opacity", 0.0, 0.0, interpolation=I.BEZIER)
    add_keyframe(window.project, "c", "opacity", 2.0, 1.0)
    window._reload_timeline_preserving_selection("c")
    editor = window.open_graph_editor("opacity")
    qtbot.waitExposed(editor)
    assert editor.property_id == "opacity" and editor.curve() is not None
    canvas = editor.canvas
    keyframe = editor.curve().keyframes[1]
    point = canvas.to_screen(keyframe.time_seconds, keyframe.value).toPoint()
    before = len(window.history)
    qtbot.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, point)
    assert editor.selected_ids() == {keyframe.id}
    target = canvas.to_screen(keyframe.time_seconds, 0.6).toPoint()
    qtbot.mouseMove(canvas, target)
    qtbot.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, target)
    assert value_at(_clip(window), "opacity", 2.0) == pytest.approx(0.6, abs=0.02)
    assert len(window.history) == before + 1                   # un glisser = une entrée
    # Interpolation depuis la liste, poignée Bézier, cadrage, zoom, déplacement de vue.
    editor.select_ids({editor.curve().keyframes[0].id})
    editor.interpolation_combo.setCurrentIndex(editor.interpolation_combo.findData("bezier"))
    editor.interpolation_combo.activated.emit(editor.interpolation_combo.currentIndex())
    first = editor.curve().keyframes[0]
    (_kf, side, handle) = canvas._handles(editor.curve())[0]
    assert side == "out"
    lifted = canvas.to_screen(first.time_seconds + 0.5, 0.5)
    qtbot.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, handle.toPoint())
    qtbot.mouseMove(canvas, lifted.toPoint())
    qtbot.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, lifted.toPoint())
    assert editor.curve().keyframes[0].out_slope == pytest.approx(1.0, rel=0.2)
    view = (canvas.t0, canvas.t1)
    editor.frame_selected()
    assert (canvas.t0, canvas.t1) != view
    editor.frame_all()
    assert canvas.t0 < 0.0 < 2.0 < canvas.t1


def test_graph_editor_rubber_band_selects_and_double_click_adds(window, qtbot):
    from PySide6.QtCore import QPoint, Qt

    for t, v in ((0.0, 1.0), (1.0, 2.0), (3.0, 1.5)):
        add_keyframe(window.project, "c", "scale", t, v)
    window._reload_timeline_preserving_selection("c")
    editor = window.open_graph_editor("scale")
    qtbot.waitExposed(editor)
    canvas = editor.canvas
    plot = canvas._plot()
    qtbot.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(plot.left()) + 2, int(plot.top()) + 2))
    qtbot.mouseMove(canvas, QPoint(int(plot.right()) - 2, int(plot.bottom()) - 2))
    qtbot.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(int(plot.right()) - 2, int(plot.bottom()) - 2))
    assert len(editor.selected_ids()) == 3
    before = [value_at(_clip(window), "scale", t / 10) for t in range(31)]
    point = canvas.to_screen(2.0, value_at(_clip(window), "scale", 2.0)).toPoint()
    qtbot.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, point)
    assert len(keyframe_times(_clip(window), ["scale"])) == 4
    after = [value_at(_clip(window), "scale", t / 10) for t in range(31)]
    assert after == pytest.approx(before, abs=1e-6)            # ajouter n'altère pas la courbe


def test_graph_editor_zoom_and_pan(window, qtbot):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    add_keyframe(window.project, "c", "scale", 0.0, 1.0)
    add_keyframe(window.project, "c", "scale", 2.0, 2.0)
    editor = window.open_graph_editor("scale")
    canvas = editor.canvas
    span = canvas.t1 - canvas.t0
    center = QPointF(canvas.width() / 2, canvas.height() / 2)
    event = QWheelEvent(center, canvas.mapToGlobal(center), QPoint(0, 0), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    canvas.wheelEvent(event)
    assert canvas.t1 - canvas.t0 < span
    value_span = canvas.v1 - canvas.v0
    event = QWheelEvent(center, canvas.mapToGlobal(center), QPoint(0, 0), QPoint(0, 120),
                        Qt.NoButton, Qt.ShiftModifier, Qt.NoScrollPhase, False)
    canvas.wheelEvent(event)
    assert canvas.v1 - canvas.v0 < value_span
    t0 = canvas.t0
    qtbot.mousePress(canvas, Qt.MiddleButton, Qt.NoModifier, center.toPoint())
    qtbot.mouseMove(canvas, center.toPoint() + QPoint(60, 0))
    qtbot.mouseRelease(canvas, Qt.MiddleButton, Qt.NoModifier, center.toPoint() + QPoint(60, 0))
    assert canvas.t0 < t0


def test_projects_without_animation_behave_exactly_as_before(window, tmp_path):
    applied = []
    window.preview_panel.apply_transform = lambda **values: applied.append(values)
    window.seek_to_position(2.0)
    assert applied[-1]["opacity"] == 1.0 and applied[-1]["scale"] == 1.0
    graph = _filter_graph(window, tmp_path)
    assert "geq=" not in graph and "colorchannelmixer=aa=1" in graph


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg requis")
def test_real_export_keeps_a_full_frame_clip_centred_like_the_preview(qtbot, tmp_path):
    """Régression : le calque agrandi par ``rotate`` était posé par son coin (export décalé)."""
    from test_export_integration import _wait_for_export

    white = tmp_path / "white.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=white:s=64x36:r=25:d=1", "-pix_fmt", "yuv420p", str(white)], check=True)
    asset = MediaAsset(id="w", path=str(white), name="w", duration=1.0, width=64, height=36,
                       fps=25.0, media_type="video")
    clip = Clip(id="c", asset_id="w", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0,
                transform=ClipTransform(position_x=0.25))
    project = Project(name="px", width=64, height=36, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    output = tmp_path / "out.mp4"
    engine = ExportEngine()
    request = ExportRequest(build_render_plan(project), str(output), ExportFormat.MP4_H264,
                            ExportPreset("T", (64, 36), 1, "96k"), 25)
    finished, failed = _wait_for_export(engine, timeout_ms=60000, start=lambda: engine.start(request))
    assert finished and not failed, failed
    gray = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
    columns = [sum(gray[y * 64 + x] > 128 for y in range(36)) for x in range(64)]
    # Décalé d'un quart de largeur vers la droite, centré verticalement : colonnes
    # 16 à 63 couvertes sur toute la hauteur, colonnes 0 à 15 vides.
    assert all(c == 0 for c in columns[:14]) and all(c == 36 for c in columns[18:])


def test_a_very_long_animation_goes_through_a_filter_script_file(window, tmp_path, monkeypatch):
    """1 000 images-clés : le graphe dépasse la limite Windows et passe par un fichier."""
    from core import export_engine

    for index in range(400):
        add_keyframe(window.project, "c", "opacity", index * 0.01, (index % 10) / 10, interpolation=I.EASE_IN_OUT)
    plan = build_render_plan(window.project)
    request = ExportRequest(plan, str(tmp_path / "o.mp4"), ExportFormat.MP4_H264,
                            ExportPreset("T", (320, 180), 20, "128k"), 30)
    for major, option in ((9, "-/filter_complex"), (6, "-filter_complex_script"), (None, "-/filter_complex")):
        monkeypatch.setattr(export_engine, "_ffmpeg_major_version", lambda major=major: major)
        engine = ExportEngine()
        engine._prepare_temporary_files(plan)
        command = engine._build_command(request)
        assert "-filter_complex" not in command and option in command
        script = Path(command[command.index(option) + 1])
        assert script.is_file() and "geq=" in script.read_text(encoding="utf-8")
        assert len(" ".join(command)) < 32_767
        engine._cleanup_temporary_files()
        assert not script.exists()                                   # nettoyé avec les autres temporaires


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg requis")
def test_the_installed_ffmpeg_accepts_the_filter_script_option(tmp_path):
    from core.export_engine import filter_graph_arguments

    from core.animation import AnimationCurve, Keyframe
    from core.animation_ffmpeg import curve_expression
    from core.visual_effects import escape_filter_complex_commas

    files: list[str] = []
    # 1 000 images-clés : au-delà de la ligne de commande Windows ET de la
    # profondeur d'expression de FFmpeg si la somme n'était pas équilibrée.
    curve = AnimationCurve(Keyframe("o", i * 0.01, (i % 10) / 10, I.EASE_IN_OUT) for i in range(1000))
    expression = escape_filter_complex_commas(curve_expression(curve, minimum=0.0, maximum=1.0))
    graph = f"[0:v]format=gray,geq=lum='255*({expression})'[v]"
    assert len(graph) > 20_000
    arguments = filter_graph_arguments(graph, files)
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=d=0.1",
         *arguments, "-map", "[v]", "-f", "null", "-"], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    Path(files[0]).unlink()


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg requis")
def test_real_export_of_a_heavily_animated_clip(qtbot, tmp_path):
    from test_export_integration import _wait_for_export

    white = tmp_path / "white.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=white:s=64x36:r=25:d=5", "-pix_fmt", "yuv420p", str(white)], check=True)
    asset = MediaAsset(id="w", path=str(white), name="w", duration=5.0, width=64, height=36,
                       fps=25.0, media_type="video")
    clip = Clip(id="c", asset_id="w", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    project = Project(name="many", width=64, height=36, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    kinds = list(I)
    for index in range(500):
        add_keyframe(project, "c", "opacity", index * 0.01, (index % 7) / 6, interpolation=kinds[index % 6])
        add_keyframe(project, "c", "position_x", index * 0.01, ((index % 5) - 2) / 10, interpolation=kinds[index % 6])
    output = tmp_path / "out.mp4"
    engine = ExportEngine()
    request = ExportRequest(build_render_plan(project), str(output), ExportFormat.MP4_H264,
                            ExportPreset("T", (64, 36), 20, "96k"), 25)
    finished, failed = _wait_for_export(engine, timeout_ms=120000, start=lambda: engine.start(request))
    assert finished and not failed, failed
    assert output.stat().st_size > 0
