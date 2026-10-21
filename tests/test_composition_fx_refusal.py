"""La conversion en composition refuse un clip aux effets animés (ADR-0008) : la composition rend ses effets à leur valeur de
base (`_effect_chain(node.effects, None, …)`), la conversion perdrait l'animation en silence."""

from __future__ import annotations

import copy

import pytest

from core.animation import Keyframe
from core.composition_ops import CompositionError, convert_to_composition
from core.effects_model import EffectType, create_effect, has_live_effect_animation
from core.project_model import Clip, MediaAsset, Project, Track


def project_with(animation) -> Project:
    asset = MediaAsset("a", "/tmp/a.mp4", "a", 5.0, 96, 54, 25.0, "video")
    clip = Clip("c", "a", "V1", 0.0, 0.0, 2.0)
    clip.effects = [
        create_effect(EffectType.VIGNETTE, effect_id="v", params={"intensity": 0.4})
    ]
    clip.animation = list(animation)
    return Project(
        "p",
        width=96,
        height=54,
        fps=25.0,
        media_assets=[asset],
        tracks=[Track("V1", "V1", "video", clips=[clip])],
    )


def test_an_animated_effect_refuses_the_conversion_and_changes_nothing():
    project = project_with(
        [Keyframe("fx.v.intensity", 0.0, 0.1), Keyframe("fx.v.intensity", 1.0, 0.9)]
    )
    before = copy.deepcopy(project)
    with pytest.raises(CompositionError, match="effets animés"):
        convert_to_composition(project, ["c"])
    assert project == before and [c.id for c in project.tracks[0].clips] == ["c"]


@pytest.mark.parametrize(
    "animation",
    [
        [],
        [
            Keyframe("opacity", 0.0, 0.5),
            Keyframe("graphic.width", 0.0, 3.0),
        ],  # images clés qui ne sont pas des fx
        [Keyframe("fx.gone.intensity", 0.0, 0.5)],  # orpheline : l'effet n'existe pas
        [Keyframe("fx.broken", 0.0, 0.5)],  # nom mal formé
    ],
)
def test_the_conversion_still_works_without_a_live_fx_keyframe(animation):
    project = project_with(animation)
    assert not has_live_effect_animation(project.tracks[0].clips[0])
    result = convert_to_composition(project, ["c"])
    assert [c.id for c in project.tracks[0].clips] == [result.clip.id]
