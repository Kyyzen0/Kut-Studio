"""Un fichier ``.kut`` qui contient des images clés ``fx.*`` s'ouvre et fonctionne **dans cette version, sans interface** pour elles
(ADR-0008, étapes 2 et 3) : rien ne lève, une image clé d'un effet absent (orphelin) ne change rien au rendu. Le cas vérifié :
un effet qui existe ET un effet absent."""

from __future__ import annotations

import pytest

from core.animation import Keyframe
from core.animation_targets import animation_curves, targets_for_clip
from core.effects_model import EffectType, create_effect
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.filter_graph import fingerprint_plan
from core.keyframe_editing import (
    can_paste,
    copy_keyframes,
    curve_of,
    is_animated,
    keyframe_times,
    paste_keyframes,
    remove_keyframes,
    KeyframeRef,
    set_value_at,
    value_at,
)
from core.mograph_layers import scene_for_project
from core.preview_segments import build_segment_job
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.timeline_operations import cut_clip, duplicate_clip

FX_KEYS = [
    Keyframe("fx.b.contrast", 0.0, 0.5, id="k1"),
    Keyframe("fx.b.contrast", 2.0, 1.5, id="k2"),
    Keyframe("fx.gone.contrast", 1.0, 1.9, id="k3"),            # l'effet n'existe pas
    Keyframe("fx.v.intensity", 1.0, 0.8, id="k4"),
]
ORPHAN_ONLY = [FX_KEYS[2]]
WITHOUT_ORPHAN = [FX_KEYS[0], FX_KEYS[1], FX_KEYS[3]]


def build(tmp_path, with_fx: bool | list = True) -> Project:
    media = tmp_path / "m.mp4"
    if not media.exists():
        media.write_bytes(b"x" * 100)
    asset = MediaAsset(id="a", path=str(media), name="m", duration=20.0, width=320, height=180, fps=25.0, media_type="video",
                       has_audio=False)
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=4.0)
    clip.effects = [create_effect(EffectType.COLOR_CORRECTION, effect_id="b", params={"contrast": 1.0}),
                    create_effect(EffectType.VIGNETTE, effect_id="v", params={"intensity": 0.4})]
    clip.animation = [Keyframe("graphic.width", 0.0, 10.0, id="g0")] + (list(FX_KEYS if with_fx is True else with_fx) if with_fx else [])
    return Project(name="R", width=320, height=180, fps=25.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


@pytest.fixture
def loaded(tmp_path) -> Project:
    path = tmp_path / "fx.kut"
    save_project(build(tmp_path, True), path)
    return load_project(path)


def frame_graph(project: Project) -> str:
    request = ExportRequest(render_plan=build_render_plan(project), output_path="/x/o.mp4", format=ExportFormat.MP4_H264,
                            preset=ExportPreset("T", (320, 180), 18, "64k"), fps=25)
    command = ExportEngine().build_frame_command(request, 0.5)
    return command[command.index("-filter_complex") + 1]


def test_the_loaded_project_keeps_every_keyframe(loaded):
    clip = loaded.tracks[0].clips[0]
    assert sorted(kf.property_name for kf in clip.animation) == sorted(["graphic.width"] + [k.property_name for k in FX_KEYS])


def test_an_orphan_changes_nothing_in_the_export_command(tmp_path, loaded):
    """Une image clé d'un effet absent ne change pas le graphe ; celles des effets présents l'animent (étape 3)."""
    plain = build(tmp_path, False)
    assert frame_graph(build(tmp_path, ORPHAN_ONLY)) == frame_graph(plain)
    assert frame_graph(loaded) == frame_graph(build(tmp_path, WITHOUT_ORPHAN))
    assert frame_graph(loaded) != frame_graph(plain) and "eval=frame" in frame_graph(loaded)
    plan = build_render_plan(loaded)
    assert plan.video_layers and plan.video_layers[0].effects == tuple(loaded.tracks[0].clips[0].effects)


def test_the_fingerprints_and_segment_jobs_compute(tmp_path, loaded):
    plan = build_render_plan(loaded)
    assert fingerprint_plan(plan)
    job = build_segment_job(loaded, 0, quality="standard")
    assert job.key.params_hash


def test_the_registry_helpers_tolerate_the_keyframes(loaded):
    clip = loaded.tracks[0].clips[0]
    assert set(animation_curves(clip)) == {"graphic.width", "fx.b.contrast", "fx.gone.contrast", "fx.v.intensity"}
    assert [t.id for t in targets_for_clip(clip, "video")] == [t.id for t in targets_for_clip(
        Clip(id="x", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0), "video")]
    assert scene_for_project(loaded) is not None                  # mograph_scene lit Clip.animation sans connaître fx.*


def test_cut_and_duplicate_with_an_existing_and_an_absent_effect(loaded):
    left, right = cut_clip(loaded, "c", 1.5)
    assert any(k.property_name == "fx.b.contrast" for k in left.animation + right.animation)
    copy = duplicate_clip(loaded, right.id)
    assert copy.effects[0].id == "b" and any(k.property_name == "fx.b.contrast" for k in copy.animation)


def test_the_keyframe_editing_functions_work_on_the_existing_effect_and_refuse_the_absent_one(loaded):
    assert is_animated(loaded.tracks[0].clips[0], "fx.b.contrast")
    assert value_at(loaded.tracks[0].clips[0], "fx.b.contrast", 1.0) == pytest.approx(1.0)
    assert curve_of(loaded.tracks[0].clips[0], "fx.v.intensity").keyframes
    assert keyframe_times(loaded.tracks[0].clips[0], ["fx.b.contrast", "fx.v.intensity"]) == [0.0, 1.0, 2.0]
    clipboard = copy_keyframes(loaded, "c", ["fx.b.contrast"])
    assert clipboard and can_paste(clipboard, "fx.b.contrast", loaded.tracks[0].clips[0])
    assert can_paste(clipboard, "position_x", loaded.tracks[0].clips[0])               # flottant vers flottant
    assert not can_paste(clipboard, "flip_h", loaded.tracks[0].clips[0])               # flottant vers booléen
    created = paste_keyframes(loaded, "c", clipboard, 1.0, target_property="fx.b.contrast")
    assert len(created) == 2
    set_value_at(loaded, "c", "fx.b.contrast", 3.0, 99.0)                              # borné à 2
    assert value_at(loaded.tracks[0].clips[0], "fx.b.contrast", 3.0) == 2.0
    with pytest.raises(KeyError):
        curve_of(loaded.tracks[0].clips[0], "fx.gone.contrast")                       # l'orphelin n'est pas une propriété
    with pytest.raises(KeyError):
        set_value_at(loaded, "c", "fx.gone.contrast", 0.0, 1.0)
    refs = [KeyframeRef("c", "fx.b.contrast", k.id) for k in loaded.tracks[0].clips[0].animation
            if k.property_name == "fx.b.contrast"]
    assert remove_keyframes(loaded, refs, local_time=0.0) == len(refs)
    assert loaded.tracks[0].clips[0].effects[0].params["contrast"] is not None


def test_an_orphan_does_not_stop_the_other_effects_from_working(loaded):
    clip = loaded.tracks[0].clips[0]
    from core.effect_targets import effective_effect_parameters

    color, vignette = clip.effects
    assert effective_effect_parameters(clip, color, 1.0)["contrast"] == pytest.approx(1.0)
    assert effective_effect_parameters(clip, vignette, 0.0)["intensity"] == 0.8
