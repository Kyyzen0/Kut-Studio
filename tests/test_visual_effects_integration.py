"""Tests d'intégration pour les transformations visuelles (tâche 13).

Ce fichier étend les tests unitaires de `test_visual_effects.py` avec
les scénarios qui impliquent d'autres modules métier :

- persistance complète du transform et des images-clés ;
- compatibilité des anciens projets (.kut v1 / v2) ;
- opérations de clé et reset ;
- rejet des clips audio / sous-titre ;
- trims qui rognent correctement les images-clés ;
- duplication qui propage le transform ;
- roundtrip Undo/Redo.
"""

from __future__ import annotations

import json
import shutil

import pytest

from core import export_engine
from core.edit_history import ProjectHistory
from core.export_engine import (
    ExportEngine,
    ExportFormat,
    ExportRequest,
)
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import evaluate_transform
from core.timeline_operations import (
    duplicate_clip,
    find_clip,
    remove_transform_keyframe,
    reset_clip_transform,
    set_clip_transform,
    set_transform_keyframe,
    trim_clip_left,
    trim_clip_right,
)
from core.visual_effects import ClipTransform, TransformKeyframe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip_with_transform(
    clip_id: str,
    asset_id: str,
    track_id: str,
    duration: float = 5.0,
    timeline_start: float = 0.0,
    transform: ClipTransform | None = None,
    keyframes: list[TransformKeyframe] | None = None,
) -> Clip:
    return Clip(
        id=clip_id,
        asset_id=asset_id,
        track_id=track_id,
        timeline_start=timeline_start,
        source_in=0.0,
        source_out=duration,
        transform=transform or ClipTransform(),
        transform_keyframes=list(keyframes or []),
    )


def _make_project_with_video() -> Project:
    asset = MediaAsset(
        id="asset-v",
        path="/tmp/v.mp4",
        name="V",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    clip = _make_clip_with_transform(
        clip_id="vclip",
        asset_id="asset-v",
        track_id="V1",
        duration=5.0,
        transform=ClipTransform(position_x=0.2, scale=1.3, opacity=0.8),
        keyframes=[
            TransformKeyframe(property_name="scale", time_seconds=1.0, value=0.7),
            TransformKeyframe(property_name="opacity", time_seconds=2.0, value=0.4),
        ],
    )
    track = Track(id="V1", name="V1", type="video", clips=[clip])
    return Project(
        name="Demo",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset],
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def test_roundtrip_persistence_preserves_transform_and_keyframes(tmp_path):
    project = _make_project_with_video()
    out_path = tmp_path / "demo.kut"
    save_project(project, str(out_path))
    reloaded = load_project(str(out_path))
    assert reloaded.name == "Demo"
    assert len(reloaded.tracks) == 1
    clip = reloaded.tracks[0].clips[0]
    assert clip.transform.position_x == pytest.approx(0.2)
    assert clip.transform.scale == pytest.approx(1.3)
    assert clip.transform.opacity == pytest.approx(0.8)
    assert len(clip.transform_keyframes) == 2
    names = sorted(kf.property_name for kf in clip.transform_keyframes)
    assert names == ["opacity", "scale"]


def test_v2_project_still_loads_without_visual_fields(tmp_path):
    """Un projet v2 ne contient pas transform/keyframes : valeurs par défaut."""
    v2_payload = {
        "format": "kut-studio-project",
        "version": 2,
        "project": {
            "id": "legacy",
            "name": "Legacy",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "background_color": "#000000",
            "assets": [],
            "tracks": [],
        },
    }
    p = tmp_path / "legacy.kut"
    p.write_text(json.dumps(v2_payload), encoding="utf-8")
    reloaded = load_project(str(p))
    assert reloaded.tracks == []


def test_v1_project_still_loads_without_visual_fields(tmp_path):
    """Compatibilité ascendante : projets v1 sans bloc transform."""
    v1_payload = {
        "format": "kut-studio-project",
        "version": 1,
        "project": {
            "id": "v1",
            "name": "v1",
            "width": 1280,
            "height": 720,
            "fps": 30.0,
            "background_color": "#000000",
            "assets": [],
            "tracks": [],
        },
    }
    p = tmp_path / "v1.kut"
    p.write_text(json.dumps(v1_payload), encoding="utf-8")
    reloaded = load_project(str(p))
    assert reloaded.name == "v1"


# ---------------------------------------------------------------------------
# Opérations
# ---------------------------------------------------------------------------


def test_set_clip_transform_updates_only_transform_field():
    project = _make_project_with_video()
    set_clip_transform(
        project,
        "vclip",
        ClipTransform(position_x=0.5, scale=2.0, rotation=45.0, opacity=1.0),
    )
    clip = find_clip(project, "vclip")
    assert clip.transform.position_x == pytest.approx(0.5)
    assert clip.transform.scale == pytest.approx(2.0)
    assert clip.transform.rotation == pytest.approx(45.0)
    assert clip.asset_id == "asset-v"
    assert clip.duration == pytest.approx(5.0)


def test_set_clip_transform_rejects_audio_clip():
    project = _make_project_with_video()
    audio_asset = MediaAsset(
        id="asset-a",
        path="/tmp/a.wav",
        name="A",
        duration=4.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    audio_clip = Clip(
        id="aclip",
        asset_id="asset-a",
        track_id="A1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
    )
    audio_track = Track(id="A1", name="A1", type="audio", clips=[audio_clip])
    project.media_assets.append(audio_asset)
    project.tracks.append(audio_track)
    with pytest.raises(ValueError):
        set_clip_transform(project, "aclip", ClipTransform(scale=2.0))


def test_set_clip_transform_rejects_subtitle_clip():
    project = _make_project_with_video()
    sub_clip = Clip(
        id="sclip",
        asset_id="",
        track_id="S1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
        text="Hello",
    )
    sub_track = Track(id="S1", name="S1", type="subtitle", clips=[sub_clip])
    project.tracks.append(sub_track)
    with pytest.raises(ValueError):
        set_clip_transform(project, "sclip", ClipTransform(opacity=0.5))


def test_set_transform_keyframe_replaces_at_same_time():
    project = _make_project_with_video()
    set_transform_keyframe(project, "vclip", "scale", 1.0, 0.9)
    set_transform_keyframe(project, "vclip", "scale", 1.0, 0.4)
    clip = find_clip(project, "vclip")
    matches = [
        kf
        for kf in clip.transform_keyframes
        if kf.property_name == "scale" and kf.time_seconds == 1.0
    ]
    assert len(matches) == 1
    assert matches[0].value == pytest.approx(0.4)


def test_set_transform_keyframe_rejects_out_of_clip_duration():
    project = _make_project_with_video()
    with pytest.raises(ValueError):
        set_transform_keyframe(project, "vclip", "scale", 10.0, 0.5)


def test_remove_transform_keyframe_removes_only_target_entry():
    project = _make_project_with_video()
    remove_transform_keyframe(project, "vclip", "scale", 1.0)
    clip = find_clip(project, "vclip")
    scales = [kf for kf in clip.transform_keyframes if kf.property_name == "scale"]
    assert scales == []
    opacities = [kf for kf in clip.transform_keyframes if kf.property_name == "opacity"]
    assert len(opacities) == 1


def test_reset_clip_transform_returns_to_identity_and_clears_keyframes():
    project = _make_project_with_video()
    reset_clip_transform(project, "vclip")
    clip = find_clip(project, "vclip")
    assert clip.transform == ClipTransform()
    assert clip.transform_keyframes == []


# ---------------------------------------------------------------------------
# Trims
# ---------------------------------------------------------------------------


def test_trim_left_shifts_keyframes_in_place():
    project = _make_project_with_video()
    set_transform_keyframe(project, "vclip", "scale", 1.0, 0.4)
    set_transform_keyframe(project, "vclip", "scale", 2.0, 0.6)
    # Trim gauche : timeline_start = 1.0 → source_in = 1.0, delta = +1.0.
    # Les keyframes suivent le média : 1.0s et 2.0s deviennent 0.0s et 1.0s
    # (l'ancien moteur les décalait dans le mauvais sens, à 2.0s et 3.0s).
    before = evaluate_transform(ClipTransform(), find_clip(project, "vclip").transform_keyframes, 1.5).scale
    trim_clip_left(project, "vclip", 1.0)
    clip = find_clip(project, "vclip")
    times = sorted(kf.time_seconds for kf in clip.transform_keyframes if kf.property_name == "scale")
    assert times == [0.0, 1.0]
    assert evaluate_transform(ClipTransform(), clip.transform_keyframes, 0.5).scale == pytest.approx(before)


def test_trim_right_drops_keyframes_beyond_new_duration():
    project = _make_project_with_video()
    set_transform_keyframe(project, "vclip", "scale", 2.5, 0.5)
    set_transform_keyframe(project, "vclip", "scale", 2.7, 0.6)
    # Le clip passe de 5.0s à 3.0s (timeline end = timeline_start + 3.0).
    set_clip_transform(project, "vclip", ClipTransform())  # no-op visible
    trim_clip_right(project, "vclip", 3.0)
    clip = find_clip(project, "vclip")
    kept = [kf for kf in clip.transform_keyframes if kf.property_name == "scale"]
    assert all(kf.time_seconds <= 3.0 for kf in kept)


# ---------------------------------------------------------------------------
# Duplication
# ---------------------------------------------------------------------------


def test_duplicate_propagates_transform_and_keyframes():
    project = _make_project_with_video()
    duplicate_clip(project, "vclip", timeline_start=10.0)
    track = project.tracks[0]
    assert len(track.clips) == 2
    duplicated = track.clips[1]
    assert duplicated.transform == find_clip(project, "vclip").transform
    assert len(duplicated.transform_keyframes) == 2


# ---------------------------------------------------------------------------
# Render plan
# ---------------------------------------------------------------------------


def test_render_plan_propagates_transform_and_keyframes():
    project = _make_project_with_video()
    plan = build_render_plan(project)
    video_layers = plan.video_layers
    assert len(video_layers) == 1
    layer = video_layers[0]
    assert layer.transform.scale == pytest.approx(1.3)
    assert len(layer.transform_keyframes) == 2


# ---------------------------------------------------------------------------
# ExportEngine : présence des expressions animées
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(monkeypatch):
    """Crée un ``ExportEngine`` avec un binaire FFmpeg simulé."""
    monkeypatch.setattr(
        shutil, "which", lambda _: "/usr/bin/ffmpeg"
    )
    monkeypatch.setattr(export_engine, "_ffmpeg_path", "/usr/bin/ffmpeg")
    return ExportEngine()


def test_export_engine_includes_animated_rotation_and_position(engine, tmp_path):
    project = _make_project_with_video()
    # Ajoute un asset "physique" sur disque pour ne pas casser
    # ``_prepare_temporary_files``. Le fichier n'est pas lu, mais
    # ``build_render_plan`` parcourt les assets liés.
    fake_source = tmp_path / "v.mp4"
    fake_source.write_bytes(b"")
    project.media_assets[0] = MediaAsset(
        id=project.media_assets[0].id,
        path=str(fake_source),
        name="V",
        duration=5.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    set_clip_transform(
        project,
        "vclip",
        ClipTransform(position_x=0.3, rotation=90.0, opacity=0.6, scale=1.5),
    )
    # Une animation = au moins deux keyframes (un seul keyframe est une valeur constante).
    set_transform_keyframe(project, "vclip", "rotation", 0.0, 90.0)
    set_transform_keyframe(project, "vclip", "rotation", 2.0, 180.0)
    set_transform_keyframe(project, "vclip", "position_x", 0.0, 0.3)
    set_transform_keyframe(project, "vclip", "position_x", 1.0, 0.6)
    plan = build_render_plan(project)
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "out.mp4"),
        preset=type("P", (), {"resolution": (1920, 1080), "crf": 18})(),
        format=ExportFormat.MP4_H264,
    )
    # On contourne la vérification d'existence du dossier parent
    # (pytest tmp_path existe déjà).
    (tmp_path / "out.mp4").parent.mkdir(parents=True, exist_ok=True)
    cmd = engine._build_command(request)
    filter_complex = None
    for index, arg in enumerate(cmd):
        if arg == "-filter_complex":
            filter_complex = cmd[index + 1]
            break
    assert filter_complex is not None
    assert "rotate=" in filter_complex
    # ``rotate`` et ``overlay`` exposent le temps via ``t``. ``T`` ne
    # fonctionnerait que dans ``geq`` pour l'opacité.
    assert "lt(t," in filter_complex
    assert "overlay=" in filter_complex


# ---------------------------------------------------------------------------
# Undo / Redo
# ---------------------------------------------------------------------------


def test_undo_redo_roundtrip_of_transform_changes():
    project = _make_project_with_video()
    history = ProjectHistory()
    history.reset(project)

    set_clip_transform(project, "vclip", ClipTransform(scale=2.5))
    history.record(project, "Modifier le mouvement")

    set_transform_keyframe(project, "vclip", "scale", 1.5, 0.3)
    history.record(project, "Ajouter une image-clé")

    # Remonte la pile undo jusqu'à l'état initial.
    while history.can_undo:
        history.undo()
    restored = history._project
    assert restored.tracks[0].clips[0].transform.scale == pytest.approx(1.3)

    # Restore tout : on doit retrouver la dernière modification.
    while history.can_redo:
        history.redo()
    final = history._project
    assert final.tracks[0].clips[0].transform.scale == pytest.approx(2.5)
    assert any(
        kf.property_name == "scale"
        and kf.time_seconds == 1.5
        and kf.value == pytest.approx(0.3)
        for kf in final.tracks[0].clips[0].transform_keyframes
    )


# ---------------------------------------------------------------------------
# Sanity check : les valeurs par défaut préservent l'ancien comportement
# ---------------------------------------------------------------------------


def test_default_transform_preserves_identity_for_legacy_clips():
    asset = MediaAsset(
        id="asset",
        path="/tmp/x.mp4",
        name="x",
        duration=2.0,
        width=1280,
        height=720,
        fps=30.0,
        media_type="video",
    )
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="clip",
                asset_id="asset",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=2.0,
            )
        ],
    )
    project = Project(
        name="p",
        width=1280,
        height=720,
        fps=30.0,
        media_assets=[asset],
        tracks=[track],
    )
    plan = build_render_plan(project)
    layer = plan.video_layers[0]
    assert layer.transform.scale == pytest.approx(1.0)
    assert layer.transform.opacity == pytest.approx(1.0)
    assert layer.transform_keyframes == ()
