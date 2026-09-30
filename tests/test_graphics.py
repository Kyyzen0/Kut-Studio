"""Modèle, UI, persistance et export des calques graphiques (tâche 32)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.graphics import (
    GraphicOverlay,
    GraphicType,
    add_graphic_clip,
    update_graphic,
)
from core.project_model import Project


def _project() -> Project:
    return Project(name="Graphiques", width=320, height=180, fps=10.0)


def test_add_graphics_creates_one_track_and_validated_overlays(tmp_path) -> None:
    project = _project()
    title = add_graphic_clip(project, "text", timeline_start=1.25)
    rectangle = add_graphic_clip(project, "rectangle", timeline_start=2.0)

    assert [track.id for track in project.tracks] == ["G1"]
    assert title.graphic.type == GraphicType.TEXT
    assert rectangle.graphic.type == GraphicType.RECTANGLE
    assert len(project.media_assets) == 2

    updated = update_graphic(title, "fill_color", "#12ab34cc")
    assert updated.fill_color == "#12AB34CC"
    assert title.graphic.fill_color == "#12AB34CC"
    with pytest.raises(ValueError):
        update_graphic(title, "type", "solid")
    with pytest.raises(FileNotFoundError):
        add_graphic_clip(
            project, "image", timeline_start=0.0,
            source_path=str(tmp_path / "absente.png"),
        )


def test_generated_graphic_is_not_reported_as_missing() -> None:
    from core.library_organization import collect_missing_assets

    project = _project()
    add_graphic_clip(project, "solid", timeline_start=0.0)
    assert collect_missing_assets(project) == []


def test_add_graphic_never_mutates_a_locked_or_hidden_track() -> None:
    project = _project()
    first = add_graphic_clip(project, "text", timeline_start=0.0)
    original_track = project.tracks[0]
    original_track.locked = True
    second = add_graphic_clip(project, "rectangle", timeline_start=1.0)

    assert [clip.id for clip in original_track.clips] == [first.id]
    assert len(project.tracks) == 2
    assert project.tracks[1].id == "G2"
    assert project.tracks[1].clips == [second]

    project.tracks[1].visible = False
    third = add_graphic_clip(project, "solid", timeline_start=2.0)
    assert len(project.tracks) == 3
    assert project.tracks[2].id == "G3"
    assert project.tracks[2].clips == [third]


def test_graphics_roundtrip_and_legacy_project(tmp_path) -> None:
    from core.project_io import CURRENT_VERSION, load_project, save_project

    project = _project()
    clip = add_graphic_clip(project, "text", timeline_start=0.5, duration=3.0)
    update_graphic(clip, "text", "Un vrai titre")
    update_graphic(clip, "stroke_width", 4)
    target = tmp_path / "graphics.kut"
    save_project(project, str(target))

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["version"] == CURRENT_VERSION == 12
    assert raw["project"]["tracks"][0]["clips"][0]["graphic"]["text"] == "Un vrai titre"
    loaded = load_project(str(target))
    loaded_graphic = loaded.tracks[0].clips[0].graphic
    assert isinstance(loaded_graphic, GraphicOverlay)
    assert loaded_graphic.stroke_width == 4

    # Un fichier v11 n'a pas de clé ``graphic`` et doit rester ouvrable.
    raw["version"] = 11
    raw["project"]["tracks"][0]["clips"][0].pop("graphic")
    legacy = tmp_path / "legacy.kut"
    legacy.write_text(json.dumps(raw), encoding="utf-8")
    assert load_project(str(legacy)).tracks[0].clips[0].graphic is None


def test_render_plan_filter_graph_and_keyframes(qtbot, tmp_path) -> None:
    from core.export_engine import ExportEngine
    from core.filter_graph import fingerprint_plan
    from core.render_plan import build_render_plan
    from core.timeline_operations import set_clip_transform, set_transform_keyframe
    from core.visual_effects import ClipTransform

    project = _project()
    clip = add_graphic_clip(project, "text", timeline_start=0.25, duration=2.0)
    update_graphic(clip, "text", "Animé")
    set_clip_transform(
        project,
        clip.id,
        ClipTransform(position_x=0.2, position_y=0.3, scale=0.8, rotation=5.0),
    )
    set_transform_keyframe(project, clip.id, "position_x", 1.0, 0.6)
    plan = build_render_plan(project)

    assert len(plan.graphics_layers) == 1
    assert plan.graphics_layers[0].transform_keyframes[0].value == 0.6
    graph, label, _audio, inputs = ExportEngine._build_filter_complex(
        plan, 320, 180, 10, None
    )
    assert label.startswith("gout")
    assert "overlay=" in graph
    assert "drawtext" not in graph  # portable même sans libfreetype
    assert len(inputs) == 1
    assert Path(inputs[0]).is_file() and Path(inputs[0]).suffix == ".png"

    before = fingerprint_plan(plan, width=320, height=180, fps=10, quality="standard")
    update_graphic(clip, "fill_color", "#FF0000")
    after = fingerprint_plan(
        build_render_plan(project), width=320, height=180, fps=10, quality="standard"
    )
    assert before != after


def test_graphics_visibility_and_solo_are_respected() -> None:
    from core.graphics import ensure_graphics_track
    from core.project_model import Track
    from core.render_plan import build_render_plan

    project = _project()
    first = add_graphic_clip(project, "solid", timeline_start=0.0)
    g1 = ensure_graphics_track(project)
    g2 = Track(id="G2", name="G2", type="graphics", solo=True)
    project.tracks.append(g2)
    second = add_graphic_clip(project, "rectangle", timeline_start=0.0)
    # ``add_graphic_clip`` réutilise G1 : déplace le second vers G2 pour
    # vérifier le même filtrage solo que dans l'aperçu.
    g1.clips.remove(second)
    second.track_id = g2.id
    g2.clips.append(second)

    assert [layer.clip_id for layer in build_render_plan(project).graphics_layers] == [
        second.id
    ]
    g2.solo = False
    g2.visible = False
    assert [layer.clip_id for layer in build_render_plan(project).graphics_layers] == [
        first.id
    ]


def test_graphics_ui_creation_edit_and_undo(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    before = sum(len(track.clips) for track in window.project.tracks if track.type == "graphics")
    window.add_graphic_at_playhead("rectangle")
    graphic_tracks = [track for track in window.project.tracks if track.type == "graphics"]
    assert len(graphic_tracks) == 1
    assert len(graphic_tracks[0].clips) == before + 1
    clip = graphic_tracks[0].clips[-1]
    assert window.properties_panel._active_inspector_tab == 4
    assert window.properties_panel.graphics_group.isEnabled()

    window.on_graphic_property_changed(clip.id, "fill_color", "#FF5500")
    assert graphic_tracks[0].clips[-1].graphic.fill_color == "#FF5500"
    window.undo_last()
    restored = next(
        item for track in window.project.tracks for item in track.clips
        if item.id == clip.id
    )
    assert restored.graphic.fill_color != "#FF5500"
    window.redo_last()
    restored = next(
        item for track in window.project.tracks for item in track.clips
        if item.id == clip.id
    )
    assert restored.graphic.fill_color == "#FF5500"
    window.close()


def test_graphic_edits_are_coalesced_and_noop_is_ignored(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Une saisie continue ne doit ni copier ni reconstruire le projet en boucle."""
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.add_graphic_at_playhead("text")
    clip = next(
        item for track in window.project.tracks if track.type == "graphics"
        for item in track.clips
    )
    initial_steps = len(window.history._undo_stack)

    window.on_graphic_property_changed(clip.id, "text", "B")
    window.on_graphic_property_changed(clip.id, "text", "Bo")
    window.on_graphic_property_changed(clip.id, "text", "Bonjour")
    assert len(window.history._undo_stack) == initial_steps
    qtbot.wait(450)
    assert len(window.history._undo_stack) == initial_steps + 1

    # Réappliquer la même valeur ne crée aucune nouvelle session d'historique.
    window.on_graphic_property_changed(clip.id, "text", "Bonjour")
    qtbot.wait(450)
    assert len(window.history._undo_stack) == initial_steps + 1
    window.undo_last()
    restored = next(
        item for track in window.project.tracks if track.type == "graphics"
        for item in track.clips if item.id == clip.id
    )
    assert restored.graphic.text == "Votre titre"
    window.close()


def test_graphic_and_transform_edits_keep_separate_undo_steps(
    qtbot, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    from core.timeline_operations import find_clip
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.add_graphic_at_playhead("text")
    clip = next(
        item for track in window.project.tracks if track.type == "graphics"
        for item in track.clips
    )
    window.on_graphic_property_changed(clip.id, "text", "Titre propre")
    window.on_transform_property_changed(clip.id, "position_x", 0.4)

    window.undo_last()
    after_transform_undo = find_clip(window.project, clip.id)
    assert after_transform_undo.graphic.text == "Titre propre"
    assert after_transform_undo.transform.position_x == pytest.approx(0.0)
    window.undo_last()
    after_graphic_undo = find_clip(window.project, clip.id)
    assert after_graphic_undo.graphic.text == "Votre titre"
    window.close()


def test_locked_graphic_rejects_property_edits(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "preview"))
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.add_graphic_at_playhead("rectangle")
    track = next(track for track in window.project.tracks if track.type == "graphics")
    clip = track.clips[0]
    original = clip.graphic.fill_color
    track.locked = True
    window.on_graphic_property_changed(clip.id, "fill_color", "#FF0000")
    assert clip.graphic.fill_color == original
    window.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
def test_real_ffmpeg_export_of_graphics_only_project(tmp_path) -> None:
    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.render_plan import build_render_plan

    project = _project()
    clip = add_graphic_clip(project, "rectangle", timeline_start=0.0, duration=0.4)
    update_graphic(clip, "fill_color", "#E25A3C")
    plan = build_render_plan(project)
    output = tmp_path / "graphic.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset("Test", (320, 180), 28, "64k"),
        fps=10,
    )
    engine = ExportEngine()
    command = engine._build_command(request)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)

    assert completed.returncode == 0, completed.stderr
    assert output.is_file() and output.stat().st_size > 0
