"""Presse-papiers d'images clés : la spécification de la propriété SOURCE est conservée à la copie, jamais résolue contre le
clip de destination (une propriété ``fx.*`` n'existe pas forcément sur lui)."""

from __future__ import annotations

import pytest

from core.animation import Keyframe
from core.effects_model import EffectType, create_effect
from core.keyframe_editing import add_keyframe, can_paste, copy_keyframes, keyframe_times, paste_keyframes
from core.project_model import Clip, MediaAsset, Project, Track


def clip_with(clip_id: str, effects, animation=()) -> Clip:
    clip = Clip(id=clip_id, asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    return clip


def two_clips() -> Project:
    asset = MediaAsset(id="a", path="/tmp/a.mp4", name="a", duration=60.0, width=1920, height=1080, fps=30.0,
                       media_type="video")
    a = clip_with("A", [create_effect(EffectType.BLUR, effect_id="b")],
                  [Keyframe("fx.b.intensity", 0.0, 0.0, id="a0"), Keyframe("fx.b.intensity", 2.0, 40.0, id="a1"),
                   ])
    b = clip_with("B", [create_effect(EffectType.VIGNETTE, effect_id="v")])
    return Project(name="P", media_assets=[asset], tracks=[Track(id="V1", name="V1", type="video", clips=[a, b])])


def test_single_fx_property_pastes_on_a_compatible_property_of_another_clip():
    project = two_clips()
    clipboard = copy_keyframes(project, "A", ["fx.b.intensity"])
    clip_b = project.tracks[0].clips[1]
    assert can_paste(clipboard, "fx.v.intensity", clip_b)                  # B n'a pas l'effet source
    created = paste_keyframes(project, "B", clipboard, 0.0, target_property="fx.v.intensity")
    assert len(created) == 2
    values = [k.value for k in clip_b.animation if k.property_name == "fx.v.intensity"]
    assert len(values) == 2 and max(values) <= 1.0 + 1e-9                  # bornées à la cible


def test_incompatible_kind_is_refused_and_nothing_is_written():
    project = two_clips()
    clipboard = copy_keyframes(project, "A", ["fx.b.intensity"])
    clip_b = project.tracks[0].clips[1]
    assert not can_paste(clipboard, "flip_h", clip_b)
    with pytest.raises(ValueError):
        paste_keyframes(project, "B", clipboard, 0.0, target_property="flip_h")
    assert clip_b.animation == []


def test_full_animation_to_a_clip_without_the_source_effect_fails_cleanly():
    project = two_clips()
    add_keyframe(project, "A", "opacity", 0.0, 0.5)
    clipboard = copy_keyframes(project, "A", ["opacity", "fx.b.intensity"])
    clip_b = project.tracks[0].clips[1]
    with pytest.raises(KeyError):
        paste_keyframes(project, "B", clipboard, 0.0)
    assert clip_b.animation == [] and keyframe_times(clip_b, ["opacity"]) == []    # rien d'écrit, même pas opacity


def test_non_fx_properties_still_paste_between_clips():
    project = two_clips()
    add_keyframe(project, "A", "opacity", 0.0, 0.5)
    clipboard = copy_keyframes(project, "A", ["opacity"])
    assert can_paste(clipboard, "opacity", project.tracks[0].clips[1])
    paste_keyframes(project, "B", clipboard, 0.0)
    assert keyframe_times(project.tracks[0].clips[1], ["opacity"]) == [0.0]
