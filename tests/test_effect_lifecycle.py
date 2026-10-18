"""Cycle de vie des images clés d'effet ``fx.*`` (ADR-0008, décision 7) : suppression + annulation, préréglage, collage
d'attributs, coupe, duplication, aplatissement multicam. Une image clé d'effet ne survit pas à son effet, et ne vise jamais
un autre effet que le sien."""

from __future__ import annotations

import pytest

from core.animation import Keyframe
from core.effect_targets import ANIMATABLE_EFFECT_PARAMS, effective_effect_parameters
from core.effects_library import EffectCategory, apply_preset_to_clip, make_preset
from core.effects_model import (
    EffectType,
    add_effect_to_clip,
    create_effect,
    effects_with_unique_ids,
    remove_effect_from_clip,
    set_clip_effect_enabled,
)
from core.mograph_layers import copy_attributes, paste_attributes
from core.multicam_ops import flatten_multicam_clip
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting.session import Session
from core.timeline_operations import cut_clip, duplicate_clip
from tests.test_multicam_core import _with_segment


@pytest.fixture(autouse=True)
def _blur_is_a_registered_vehicle(monkeypatch):
    """Ces tests portent sur le cycle de vie des courbes (modèle), pas sur le rendu : le flou, empilable et de bornes 0 à 50,
    leur sert de véhicule. Il n'est pas dans la liste enregistrée (étape 5 de l'ADR) : on l'y met le temps du test."""
    monkeypatch.setitem(ANIMATABLE_EFFECT_PARAMS, EffectType.BLUR, ("intensity",))


def names(clip: Clip) -> list[str]:
    return [kf.property_name for kf in clip.animation]


def video_project(*clips: Clip, track_type: str = "video") -> Project:
    asset = MediaAsset(id="a", path="/tmp/a.mp4", name="a", duration=60.0, width=1920, height=1080, fps=30.0,
                       media_type="video")
    return Project(name="P", media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type=track_type, clips=list(clips))])


def animated_clip(clip_id: str = "c", start: float = 0.0) -> Clip:
    clip = Clip(id=clip_id, asset_id="a", track_id="V1", timeline_start=start, source_in=0.0, source_out=10.0)
    clip.effects = [create_effect(EffectType.BLUR, effect_id="b", params={"intensity": 2.0}),
                    create_effect(EffectType.VIGNETTE, effect_id="v")]
    clip.animation = [Keyframe("fx.b.intensity", 0.0, 0.0, id="b0"), Keyframe("fx.b.intensity", 10.0, 40.0, id="b1"),
                      Keyframe("fx.v.intensity", 0.0, 0.2, id="v0"), Keyframe("graphic.width", 0.0, 9.0, id="g0")]
    return clip


# ---------------------------------------------------------------------------
# Suppression d'un effet, puis annulation
# ---------------------------------------------------------------------------


def test_removing_an_effect_removes_its_keyframes_only():
    project = video_project(animated_clip())
    clip = project.tracks[0].clips[0]
    remove_effect_from_clip(project, "c", "b")
    assert [e.id for e in clip.effects] == ["v"]
    assert sorted(names(clip)) == ["fx.v.intensity", "graphic.width"]


def test_undo_brings_back_the_effect_and_its_keyframes_together():
    project = video_project(animated_clip())
    session = Session(project)
    report = session.apply([lambda p: remove_effect_from_clip(p, "c", "b")], label="Supprimer l'effet")
    assert report.ok
    clip = project.tracks[0].clips[0]
    assert [e.id for e in clip.effects] == ["v"] and "fx.b.intensity" not in names(clip)
    assert session.undo()
    clip = project.tracks[0].clips[0]
    assert [e.id for e in clip.effects] == ["b", "v"]
    assert [(k.property_name, k.value) for k in clip.animation if k.property_name == "fx.b.intensity"] == [
        ("fx.b.intensity", 0.0), ("fx.b.intensity", 40.0)]
    assert effective_effect_parameters(clip, clip.effects[0], 5.0)["intensity"] == pytest.approx(20.0)
    assert session.redo() and "fx.b.intensity" not in names(project.tracks[0].clips[0])


def test_disabling_or_adding_an_effect_keeps_the_keyframes():
    project = video_project(animated_clip())
    clip = project.tracks[0].clips[0]
    set_clip_effect_enabled(project, "c", "b", False)
    add_effect_to_clip(project, "c", "sharpen")
    assert sum(1 for n in names(clip) if n.startswith("fx.")) == 3


# ---------------------------------------------------------------------------
# Préréglage
# ---------------------------------------------------------------------------


def preset_of(*effects):
    return make_preset("p1", "Look", "", EffectCategory.LOOK, list(effects))


def test_a_preset_replaces_a_unique_effect_and_its_keyframes_but_not_the_others():
    clip = animated_clip()
    project = video_project(clip)
    chain = apply_preset_to_clip(project, "c", preset_of(create_effect(EffectType.VIGNETTE, effect_id="pv")))
    assert [e.id for e in chain][0] == "b" and chain[1].type == EffectType.VIGNETTE and chain[1].id != "v"
    assert sorted(names(clip)) == ["fx.b.intensity", "fx.b.intensity", "graphic.width"]    # l'ancienne vignette a perdu sa courbe


def test_a_preset_effect_is_born_without_animation_even_with_the_replaced_id():
    clip = animated_clip()
    project = video_project(clip)
    apply_preset_to_clip(project, "c", preset_of(create_effect(EffectType.VIGNETTE, effect_id="v", params={"intensity": 0.9})))
    assert clip.effects[1].type == EffectType.VIGNETTE and clip.effects[1].params["intensity"] == 0.9
    assert clip.effects[1].id != "v" and "fx.v.intensity" not in names(clip)
    assert not any(n.startswith(f"fx.{clip.effects[1].id}.") for n in names(clip))


def test_a_stackable_preset_effect_that_collides_gets_a_new_id():
    clip = animated_clip()
    project = video_project(clip)
    apply_preset_to_clip(project, "c", preset_of(create_effect(EffectType.BLUR, effect_id="b", params={"intensity": 9.0})))
    ids = [e.id for e in clip.effects]
    assert len(ids) == 3 == len(set(ids)) and ids[0] == "b" and ids[:2] == ["b", "v"]
    assert clip.effects[0].params["intensity"] == 2.0                      # l'effet d'origine garde sa courbe
    assert names(clip).count("fx.b.intensity") == 2
    assert not any(n.startswith(f"fx.{ids[2]}.") for n in names(clip))


def test_applying_a_stackable_preset_twice_gives_two_distinct_effects():
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    project = video_project(clip)
    preset = preset_of(create_effect(EffectType.BLUR, effect_id="pb", params={"intensity": 5.0}))
    apply_preset_to_clip(project, "c", preset)
    apply_preset_to_clip(project, "c", preset)
    first, second = clip.effects
    assert first is not second and first.id != second.id and "pb" not in {first.id, second.id}
    assert [e.type for e in clip.effects] == [EffectType.BLUR, EffectType.BLUR]


def test_reapplying_a_unique_preset_drops_the_old_id_and_its_keyframes():
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    project = video_project(clip)
    preset = preset_of(create_effect(EffectType.VIGNETTE, effect_id="pv"))
    apply_preset_to_clip(project, "c", preset)
    old_id = clip.effects[0].id
    clip.animation = [Keyframe(f"fx.{old_id}.intensity", 0.0, 0.1, id="k0"), Keyframe(f"fx.{old_id}.intensity", 5.0, 0.4, id="k1")]
    apply_preset_to_clip(project, "c", preset)
    assert len(clip.effects) == 1 and clip.effects[0].id != old_id
    assert not any(n.startswith("fx.") for n in names(clip))


def test_preset_effects_never_share_the_preset_ids_and_the_preset_is_not_mutated():
    clip = animated_clip()
    project = video_project(clip)
    original = create_effect(EffectType.BLUR, effect_id="pb", params={"intensity": 9.0})
    preset = preset_of(original)
    apply_preset_to_clip(project, "c", preset)
    added = clip.effects[-1]
    assert added.id != "pb" and added is not original
    added.params["intensity"] = 1.0
    assert preset.effects == (original,) and preset.effects[0].id == "pb" and original.params["intensity"] == 9.0


def test_an_empty_preset_changes_nothing():
    clip = animated_clip()
    before = list(clip.animation)
    apply_preset_to_clip(video_project(clip), "c", preset_of())
    assert clip.animation == before and [e.id for e in clip.effects] == ["b", "v"]


def test_effects_with_unique_ids_only_renames_collisions():
    effects = [create_effect(EffectType.BLUR, effect_id="x"), create_effect(EffectType.SEPIA, effect_id="y"),
               create_effect(EffectType.BLUR, effect_id="x")]
    renamed = effects_with_unique_ids(["y"], effects)
    assert renamed[0].id == "x" and renamed[1].id != "y" and renamed[2].id not in {"x", "y", renamed[1].id}
    assert [e.type for e in renamed] == [e.type for e in effects]


# ---------------------------------------------------------------------------
# Collage d'attributs
# ---------------------------------------------------------------------------


def paste_project():
    source, target = animated_clip("src"), Clip(id="dst", asset_id="a", track_id="V1", timeline_start=20.0, source_in=0.0,
                                                source_out=10.0)
    return video_project(source, target), source, target


def test_pasting_effects_and_keyframes_animates_the_new_effects():
    project, source, target = paste_project()
    paste_attributes(project, ["dst"], copy_attributes(project, "src"), ("effects", "keyframes"))
    assert {e.id for e in target.effects}.isdisjoint({"b", "v"})
    blur = next(e for e in target.effects if e.type is EffectType.BLUR)
    vignette = next(e for e in target.effects if e.type is EffectType.VIGNETTE)
    assert sorted(names(target)) == sorted([f"fx.{blur.id}.intensity"] * 2 + [f"fx.{vignette.id}.intensity", "graphic.width"])
    assert effective_effect_parameters(target, blur, 5.0)["intensity"] == pytest.approx(20.0)
    assert names(source).count("fx.b.intensity") == 2                          # la source est intacte


def test_pasting_effects_without_keyframes_leaves_the_new_effects_unanimated_and_drops_old_curves():
    project, _source, target = paste_project()
    target.effects = [create_effect(EffectType.BLUR, effect_id="old")]
    target.animation = [Keyframe("fx.old.intensity", 0.0, 3.0, id="o0"), Keyframe("graphic.width", 0.0, 1.0, id="g")]
    paste_attributes(project, ["dst"], copy_attributes(project, "src"), ("effects",))
    assert names(target) == ["graphic.width"]


def test_pasting_keyframes_alone_keeps_a_curve_only_for_an_effect_the_clip_already_has():
    project, _source, target = paste_project()
    target.effects = [create_effect(EffectType.BLUR, effect_id="b"), create_effect(EffectType.SEPIA, effect_id="v")]
    paste_attributes(project, ["dst"], copy_attributes(project, "src"), ("keyframes",))
    # « b » existe sur la cible avec le même type ; « v » existe mais c'est un sépia (la vignette source) : abandonnée.
    assert sorted(names(target)) == ["fx.b.intensity", "fx.b.intensity", "graphic.width"]


def test_pasting_keyframes_onto_a_clip_without_the_effects_drops_the_curves():
    project, _source, target = paste_project()
    paste_attributes(project, ["dst"], copy_attributes(project, "src"), ("keyframes",))
    assert names(target) == ["graphic.width"]


def test_pasting_onto_a_graphics_track_never_brings_fx_curves():
    source = animated_clip("src")
    graphic = Clip(id="g", asset_id="", track_id="G1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    project = video_project(source)
    project.tracks.append(Track(id="G1", name="G1", type="graphics", clips=[graphic]))
    paste_attributes(project, ["g"], copy_attributes(project, "src"), ("effects", "keyframes"))
    assert not any(n.startswith("fx.") for n in names(graphic))


# ---------------------------------------------------------------------------
# Coupe et duplication
# ---------------------------------------------------------------------------


def test_cutting_an_animated_clip_keeps_the_effect_ids_and_splits_the_curve():
    clip = animated_clip()
    project = video_project(clip)
    left, right = cut_clip(project, "c", 4.0)
    for half in (left, right):
        assert [e.id for e in half.effects] == ["b", "v"]
    assert effective_effect_parameters(left, left.effects[0], 4.0)["intensity"] == pytest.approx(16.0)
    assert effective_effect_parameters(right, right.effects[0], 0.0)["intensity"] == pytest.approx(16.0)
    assert effective_effect_parameters(right, right.effects[0], 6.0)["intensity"] == pytest.approx(40.0)
    assert names(left).count("fx.b.intensity") == 2 and names(right).count("fx.b.intensity") == 2
    assert names(left).count("fx.v.intensity") >= 1 and names(right).count("fx.v.intensity") >= 1
    assert left.effects[0] is not right.effects[0]                          # deux chaînes indépendantes


def test_cutting_bounds_the_fx_curve_with_the_effect_bounds():
    """Une courbe de Bézier qui dépasse la borne de l'effet est coupée avec cette borne (le clip fournit le type)."""
    from core.animation import InterpolationType

    clip = animated_clip()
    clip.animation = [Keyframe("fx.v.intensity", 0.0, 0.0, id="a", interpolation=InterpolationType.BEZIER, out_slope=40.0),
                      Keyframe("fx.v.intensity", 10.0, 1.0, id="b", interpolation=InterpolationType.BEZIER, in_slope=40.0)]
    left, right = cut_clip(video_project(clip), "c", 2.0)
    for half in (left, right):
        assert all(0.0 <= kf.value <= 1.0 for kf in half.animation)


def test_duplicating_an_animated_clip_keeps_the_curves_on_the_copy():
    clip = animated_clip()
    project = video_project(clip)
    copy = duplicate_clip(project, "c")
    assert [e.id for e in copy.effects] == ["b", "v"] and copy.effects is not clip.effects
    assert sorted(names(copy)) == sorted(names(clip))
    remove_effect_from_clip(project, "c", "b")                                # l'original change, pas la copie
    assert names(copy).count("fx.b.intensity") == 2 and "fx.b.intensity" not in names(clip)


# ---------------------------------------------------------------------------
# Aplatissement multicam : collision d'identifiants
# ---------------------------------------------------------------------------


def test_flattening_gives_a_new_id_to_a_segment_effect_that_collides_with_an_angle_effect():
    project, source, segment = _with_segment("angle-1")
    angle_clip = source.tracks[0].clips[0]
    angle_clip.effects = [create_effect(EffectType.BLUR, effect_id="shared", params={"intensity": 2.0})]
    angle_clip.animation = [Keyframe("fx.shared.intensity", 0.0, 1.0, id="k0"), Keyframe("fx.shared.intensity", 5.0, 9.0, id="k1")]
    segment.effects = [create_effect(EffectType.VIGNETTE, effect_id="shared")]
    result = flatten_multicam_clip(project, segment.id)
    new = result.video[0]
    ids = [e.id for e in new.effects]
    assert len(ids) == 2 == len(set(ids)) and ids[0] == "shared"                          # l'effet de l'angle garde son id
    assert [e.type for e in new.effects] == [EffectType.BLUR, EffectType.VIGNETTE]
    assert set(names(new)) == {"fx.shared.intensity"}
    from core.animation_targets import get_target

    assert get_target("fx.shared.intensity", new).spec.maximum == 50.0                    # c'est bien le flou, pas la vignette
    vignette = get_target(f"fx.{ids[1]}.intensity", new)                                  # l'effet du segment : sa propre cible
    assert vignette.spec.maximum == 1.0 and not vignette.get_keyframes(new)
