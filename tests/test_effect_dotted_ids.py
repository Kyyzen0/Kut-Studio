"""Identifiants d'effet à points : les effets « a » et « a.b » coexistent (ADR-0008, découpage par la droite).

Une courbe appartient à un effet par comparaison EXACTE de l'identifiant (``is_effect_property``), jamais par préfixe.
"""

from __future__ import annotations

import pytest

from core.animation import Keyframe
from core.effects_model import (
    EffectType,
    create_effect,
    is_effect_property,
    remap_fx_animation,
    remove_effect,
    set_clip_effects,
)
from core.keyframe_editing import clear_effect_animation, set_animation_enabled
from core.project_model import Clip, Project, Track
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes


def _dotted():
    clip = Clip(
        id="c",
        asset_id="x",
        track_id="t",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
        effects=[
            create_effect(EffectType.COLOR_CORRECTION, effect_id="a"),
            create_effect(EffectType.COLOR_CORRECTION, effect_id="a.b"),
        ],
    )
    clip.animation = [
        Keyframe("fx.a.brightness", 0.0, 0.0, id="a0"),
        Keyframe("fx.a.brightness", 2.0, 0.5, id="a1"),
        Keyframe("fx.a.b.brightness", 0.0, 0.1, id="b0"),
        Keyframe("fx.a.b.brightness", 2.0, 0.9, id="b1"),
        Keyframe("fx.a.b.contrast", 0.0, 1.0, id="b2"),
    ]
    track = Track(id="t", name="T", type="video")
    track.clips.append(clip)
    project = Project(name="P")
    project.tracks.append(track)
    return project, clip


def _ids(clip, effect_id):
    return sorted(
        k.id for k in clip.animation if is_effect_property(k.property_name, effect_id)
    )


def test_clearing_a_never_touches_a_dot_b():
    project, clip = _dotted()
    other = dict(clip.effects[1].params)
    assert clear_effect_animation(project, "c", "a", 1.0) == 1
    assert _ids(clip, "a") == [] and _ids(clip, "a.b") == ["b0", "b1", "b2"]
    assert clip.effects[1].params == other  # base de « a.b » intacte
    assert clip.effects[0].params["brightness"] == pytest.approx(0.25)


def test_clearing_a_dot_b_never_touches_a():
    project, clip = _dotted()
    before = dict(clip.effects[0].params)
    assert clear_effect_animation(project, "c", "a.b", 1.0) == 2
    assert _ids(clip, "a.b") == [] and _ids(clip, "a") == ["a0", "a1"]
    assert clip.effects[0].params == before


def test_removing_an_effect_and_remapping_do_not_mix_a_and_a_dot_b():
    _project, clip = _dotted()
    set_clip_effects(clip, remove_effect(clip.effects, "a"))
    assert _ids(clip, "a") == [] and _ids(clip, "a.b") == ["b0", "b1", "b2"]
    animation = _dotted()[1].animation
    remapped = remap_fx_animation(animation, {"a": "n1", "a.b": "n2"})
    assert sorted((k.property_name, k.id) for k in remapped) == sorted(
        [
            ("fx.n1.brightness", "a0"),
            ("fx.n1.brightness", "a1"),
            ("fx.n2.brightness", "b0"),
            ("fx.n2.brightness", "b1"),
            ("fx.n2.contrast", "b2"),
        ]
    )
    assert [k.id for k in remap_fx_animation(animation, {"a": "n1"})] == ["a0", "a1"]


def test_disabling_one_parameter_of_a_leaves_a_dot_b_alone():
    project, clip = _dotted()
    set_animation_enabled(project, "c", "fx.a.brightness", False, 1.0)
    assert _ids(clip, "a") == [] and _ids(clip, "a.b") == ["b0", "b1", "b2"]
    assert clip.effects[1].params["brightness"] == pytest.approx(0.0)


def test_the_inspector_offers_the_clear_entry_only_for_an_animated_effect(window):  # noqa: F811
    window.set_usage_level("advanced")
    _track, clip = _add_track_clip(window, "video", "v")
    clip.effects = [
        create_effect(EffectType.COLOR_CORRECTION, effect_id="a"),
        create_effect(EffectType.COLOR_CORRECTION, effect_id="a.b"),
    ]
    clip.animation = [Keyframe("fx.a.b.brightness", 0.0, 0.1, id="b0")]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    panel = window.properties_panel

    def clearing(effect_id):
        menu = panel.effect_animation_menu(effect_id, "brightness")
        return [
            a for a in menu.actions() if "Supprimer l'animation de l'effet" in a.text()
        ]

    assert not clearing("a") and clearing("a.b")
