"""Opérations d'édition, registre de propriétés, undo/redo, sérialisation, rétrocompatibilité."""

from __future__ import annotations

import json

import pytest

from core.animation import AnimatableProperty, InterpolationType, Keyframe, TangentMode, ValueKind
from core.animation_targets import PropertyTarget, get_target, register_target, targets_for
from core.edit_history import ProjectHistory
from core.keyframe_editing import (
    KeyframeRef,
    add_keyframe,
    can_paste,
    copy_keyframes,
    is_animated,
    keyframe_times,
    move_keyframes,
    next_keyframe_time,
    paste_keyframes,
    previous_keyframe_time,
    remove_keyframe_at,
    remove_keyframes,
    set_animation_enabled,
    set_interpolation,
    set_keyframe_time,
    set_keyframe_values,
    set_tangents,
    set_value_at,
    value_at,
)
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, TransformKeyframe, evaluate_transform

I = InterpolationType  # noqa: E741


def _project(start: float = 0.0, duration: float = 4.0) -> Project:
    asset = MediaAsset(id="a", path="/tmp/v.mp4", name="v", duration=60.0, width=1920,
                       height=1080, fps=30.0, media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=start, source_in=0.0,
                source_out=duration, transform=ClipTransform(opacity=0.8))
    return Project(name="p", width=1920, height=1080, fps=30.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


def _clip(project):
    return project.tracks[0].clips[0]


def _ref(project, prop, time):
    keyframe = get_target(prop).curve(_clip(project)).keyframe_at(time)
    return KeyframeRef("c", prop, keyframe.id)


# --- Registre -----------------------------------------------------------------------------------


def test_transform_properties_are_registered_for_video_and_graphics_only():
    ids = [t.id for t in targets_for("video")]
    assert ids[:5] == ["position_x", "position_y", "scale", "rotation", "opacity"]
    assert [t.id for t in targets_for("graphics")][:5] == ids[:5]
    assert targets_for("audio") == () and targets_for("subtitle") == ()
    with pytest.raises(KeyError):
        get_target("inconnue")


def test_a_new_property_becomes_animatable_by_registering_it(monkeypatch):
    """Exemple de la documentation : une propriété hors transform, sans autre code."""
    store: dict[str, list] = {}
    target = PropertyTarget(
        spec=AnimatableProperty("demo.amount", "k", ValueKind.FLOAT, 0.0, 0.0, 10.0, group="effect"),
        get_static=lambda clip: 2.0,
        set_static=lambda clip, value: None,
        get_keyframes=lambda clip: store.get(clip.id, []),
        set_keyframes=lambda clip, frames: store.__setitem__(clip.id, frames),
        make_keyframe=Keyframe,
        applies_to=lambda track_type: track_type == "video",
    )
    from core import animation_targets

    monkeypatch.setitem(animation_targets._REGISTRY, target.id, target)
    project = _project()
    add_keyframe(project, "c", "demo.amount", 0.0, 0.0)
    add_keyframe(project, "c", "demo.amount", 2.0, 20.0)            # borné à 10
    assert value_at(_clip(project), "demo.amount", 1.0) == pytest.approx(5.0)
    assert "demo.amount" in [t.id for t in targets_for("video")]


# --- Ajout, valeur, suppression -------------------------------------------------------------------


def test_activating_animation_keeps_the_current_value_then_editing_creates_keyframes():
    project = _project()
    clip = _clip(project)
    # Statique : modifier la valeur ne crée aucun keyframe.
    assert set_value_at(project, "c", "opacity", 1.0, 0.6) is None
    assert clip.transform.opacity == 0.6 and not is_animated(clip, "opacity")
    # Clic sur le losange : animation activée à la tête de lecture, valeur conservée.
    set_animation_enabled(project, "c", "opacity", True, 1.0)
    assert keyframe_times(clip, ["opacity"]) == [1.0]
    # Débutant : déplacer la tête puis modifier la valeur crée un keyframe.
    set_value_at(project, "c", "opacity", 3.0, 0.2)
    assert keyframe_times(clip, ["opacity"]) == [1.0, 3.0]
    assert value_at(clip, "opacity", 2.0) == pytest.approx(0.4)
    # Modifier sur un keyframe existant le met à jour, sans en créer.
    set_value_at(project, "c", "opacity", 3.0, 0.0)
    assert len(keyframe_times(clip, ["opacity"])) == 2 and value_at(clip, "opacity", 3.0) == 0.0


def test_adding_a_keyframe_without_value_never_changes_the_animation():
    project = _project()
    add_keyframe(project, "c", "scale", 0.0, 1.0, interpolation=I.EASE_IN_OUT)
    add_keyframe(project, "c", "scale", 4.0, 2.0)
    before = [value_at(_clip(project), "scale", t / 10) for t in range(41)]
    added = add_keyframe(project, "c", "scale", 1.7)
    after = [value_at(_clip(project), "scale", t / 10) for t in range(41)]
    assert added.time_seconds == 1.7 and after == pytest.approx(before)


def test_values_are_clamped_and_times_must_be_inside_the_clip():
    project = _project()
    assert add_keyframe(project, "c", "opacity", 1.0, 5.0).value == 1.0
    with pytest.raises(ValueError):
        add_keyframe(project, "c", "opacity", 9.0, 0.5)
    with pytest.raises(ValueError):
        add_keyframe(project, "c", "opacity", -1.0, 0.5)


def test_removing_the_last_keyframe_keeps_the_visible_value():
    project = _project()
    add_keyframe(project, "c", "rotation", 0.0, 0.0)
    add_keyframe(project, "c", "rotation", 2.0, 90.0)
    assert remove_keyframe_at(project, "c", "rotation", 2.0)
    assert not remove_keyframe_at(project, "c", "rotation", 3.0)
    remove_keyframes(project, [_ref(project, "rotation", 0.0)], local_time=0.0)
    assert not is_animated(_clip(project), "rotation") and _clip(project).transform.rotation == 0.0
    add_keyframe(project, "c", "rotation", 0.0, 0.0)
    add_keyframe(project, "c", "rotation", 2.0, 90.0)
    set_animation_enabled(project, "c", "rotation", False, 1.0)     # désactiver : valeur figée au temps courant
    assert not is_animated(_clip(project), "rotation") and _clip(project).transform.rotation == pytest.approx(45.0)


# --- Navigation ------------------------------------------------------------------------------------


def test_previous_and_next_keyframe_across_properties():
    project = _project()
    for prop, t in (("opacity", 0.5), ("scale", 1.0), ("opacity", 2.0), ("rotation", 3.0)):
        add_keyframe(project, "c", prop, t, 1.0)
    clip = _clip(project)
    props = ["opacity", "scale", "rotation"]
    assert next_keyframe_time(clip, props, 0.0) == 0.5
    assert next_keyframe_time(clip, props, 1.0) == 2.0
    assert previous_keyframe_time(clip, props, 2.0) == 1.0
    assert previous_keyframe_time(clip, props, 0.5) is None
    assert next_keyframe_time(clip, ["opacity"], 2.0) is None


# --- Déplacement, multi-sélection, interpolation, tangentes ---------------------------------------


def test_moving_several_keyframes_together_snaps_and_stays_inside_the_clip():
    project = _project(start=0.01)                                  # clip hors grille d'images
    for prop, t in (("opacity", 1.0), ("scale", 1.0), ("scale", 2.0)):
        add_keyframe(project, "c", prop, t, 1.0)
    refs = [_ref(project, "opacity", 1.0), _ref(project, "scale", 2.0)]
    moved = move_keyframes(project, refs, 0.52, fps=30)
    times = sorted(moved.values())
    for t in times:
        frames = (0.01 + t) * 30
        assert abs(frames - round(frames)) < 1e-3                  # aligné sur les images de la timeline
    assert keyframe_times(_clip(project), ["scale"])[0] == 1.0      # non sélectionné : immobile
    # Le bloc ne sort pas du clip : le plus tardif s'arrête à la fin.
    clamped = move_keyframes(project, refs, 100.0)
    assert max(clamped.values()) == pytest.approx(4.0)


def test_moving_onto_another_keyframe_replaces_it():
    project = _project()
    add_keyframe(project, "c", "scale", 1.0, 1.0)
    add_keyframe(project, "c", "scale", 2.0, 3.0)
    ref = _ref(project, "scale", 2.0)
    set_keyframe_time(project, ref, 1.0)
    assert keyframe_times(_clip(project), ["scale"]) == [1.0]
    assert value_at(_clip(project), "scale", 1.0) == 3.0


def test_interpolation_values_and_tangents_by_reference():
    project = _project()
    add_keyframe(project, "c", "position_x", 0.0, 0.0)
    add_keyframe(project, "c", "position_x", 2.0, 1.0)
    first = _ref(project, "position_x", 0.0)
    assert set_interpolation(project, [first], "ease_out") == 1
    assert value_at(_clip(project), "position_x", 1.0) == pytest.approx(0.75)
    set_keyframe_values(project, {first: 0.5})
    assert value_at(_clip(project), "position_x", 0.0) == 0.5
    set_interpolation(project, [first], I.BEZIER)
    linked = set_tangents(project, first, out_slope=2.0)
    assert linked.in_slope == linked.out_slope == 2.0 and linked.tangent_mode is TangentMode.LINKED
    broken = set_tangents(project, first, in_slope=-1.0, mode="broken")
    assert (broken.in_slope, broken.out_slope) == (-1.0, 2.0)
    automatic = set_tangents(project, first, auto=True)
    assert automatic.in_slope is None and automatic.out_slope is None


# --- Copier / coller --------------------------------------------------------------------------------


def test_copy_paste_on_the_same_property_at_the_playhead():
    project = _project()
    add_keyframe(project, "c", "opacity", 1.0, 0.0, interpolation=I.EASE_IN)
    add_keyframe(project, "c", "opacity", 1.5, 1.0)
    clipboard = copy_keyframes(project, "c", ["opacity"])
    created = paste_keyframes(project, "c", clipboard, 3.0)
    clip = _clip(project)
    assert len(created) == 2 and keyframe_times(clip, ["opacity"]) == [1.0, 1.5, 3.0, 3.5]
    assert get_target("opacity").curve(clip).keyframe_at(3.0).interpolation is I.EASE_IN


def test_paste_on_a_compatible_property_clamps_to_its_bounds():
    project = _project()
    add_keyframe(project, "c", "rotation", 0.0, 0.0)
    add_keyframe(project, "c", "rotation", 1.0, 360.0)
    clipboard = copy_keyframes(project, "c", ["rotation"])
    assert can_paste(clipboard, "scale")
    paste_keyframes(project, "c", clipboard, 0.0, target_property="scale")
    assert value_at(_clip(project), "scale", 1.0) == 10.0            # borné (échelle max)
    assert value_at(_clip(project), "scale", 0.0) == 0.05


def test_paste_rejects_incompatible_types_and_drops_keyframes_after_the_clip():
    project = _project()
    vec = AnimatableProperty("demo.vec", "k", ValueKind.VEC2, (0.0, 0.0))
    store: dict = {}
    from core import animation_targets

    animation_targets._REGISTRY["demo.vec"] = PropertyTarget(
        vec, lambda c: (0.0, 0.0), lambda c, v: None, lambda c: store.get("k", []),
        lambda c, f: store.__setitem__("k", f), Keyframe, lambda tt: True,
    )
    try:
        add_keyframe(project, "c", "opacity", 0.0, 1.0)
        add_keyframe(project, "c", "opacity", 3.0, 0.0)
        clipboard = copy_keyframes(project, "c", ["opacity"])
        assert not can_paste(clipboard, "demo.vec")
        with pytest.raises(ValueError):
            paste_keyframes(project, "c", clipboard, 0.0, target_property="demo.vec")
        created = paste_keyframes(project, "c", clipboard, 2.0)        # 5.0 dépasse la fin (4 s)
        assert len(created) == 1
    finally:
        del animation_targets._REGISTRY["demo.vec"]


def test_copy_paste_a_whole_animation_on_another_clip():
    project = _project()
    second = Clip(id="d", asset_id="a", track_id="V1", timeline_start=10.0, source_in=0.0, source_out=4.0)
    project.tracks[0].clips.append(second)
    add_keyframe(project, "c", "scale", 0.0, 1.0)
    add_keyframe(project, "c", "scale", 1.0, 2.0)
    add_keyframe(project, "c", "rotation", 0.5, 45.0)
    clipboard = copy_keyframes(project, "c", ["position_x", "scale", "rotation"])
    assert clipboard.property_ids == ("scale", "rotation")
    paste_keyframes(project, "d", clipboard, 1.0)
    assert keyframe_times(second, ["scale"]) == [1.0, 2.0] and keyframe_times(second, ["rotation"]) == [1.5]


# --- Undo / redo ----------------------------------------------------------------------------------------


def test_every_keyframe_edit_is_undoable_through_project_snapshots():
    project = _project()
    history = ProjectHistory()
    history.reset(project)
    add_keyframe(project, "c", "opacity", 0.0, 1.0)
    history.record(project, "Ajouter")
    add_keyframe(project, "c", "opacity", 2.0, 0.0)
    history.record(project, "Ajouter")
    move_keyframes(project, [_ref(project, "opacity", 2.0)], 1.0)
    history.record(project, "Déplacer")
    set_interpolation(project, [_ref(project, "opacity", 0.0)], "hold")
    history.record(project, "Interpolation")
    restored = history.undo()
    assert get_target("opacity").curve(_clip(restored)).keyframe_at(0.0).interpolation is I.LINEAR
    restored = history.undo()
    assert keyframe_times(_clip(restored), ["opacity"]) == [0.0, 2.0]
    restored = history.redo()
    assert keyframe_times(_clip(restored), ["opacity"]) == [0.0, 3.0]


# --- Sérialisation .kut ----------------------------------------------------------------------------------


def test_animation_roundtrips_through_a_kut_file(tmp_path):
    project = _project()
    add_keyframe(project, "c", "opacity", 0.0, 1.0, interpolation=I.BEZIER)
    add_keyframe(project, "c", "opacity", 2.0, 0.0, interpolation=I.HOLD)
    set_tangents(project, _ref(project, "opacity", 0.0), in_slope=-0.1, out_slope=0.3, mode="broken")
    add_keyframe(project, "c", "scale", 1.0, 1.5)
    path = tmp_path / "anim.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    stored = raw["project"]["tracks"][0]["clips"][0]["transform_keyframes"]
    assert raw["version"] == 13
    assert {"interpolation", "tangent_mode", "id"} <= set(stored[0])
    reloaded = load_project(str(path))
    clip, original = _clip(reloaded), _clip(project)
    assert clip.transform_keyframes == original.transform_keyframes
    assert [k.id for k in clip.transform_keyframes] == [k.id for k in original.transform_keyframes]
    for t in (0.0, 0.7, 1.9, 2.0, 3.5):
        assert evaluate_transform(clip.transform, clip.transform_keyframes, t) == \
            evaluate_transform(original.transform, original.transform_keyframes, t)


def _legacy_file(tmp_path, keyframes, transform=None):
    project = _project()
    path = tmp_path / "old.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = 12
    clip = raw["project"]["tracks"][0]["clips"][0]
    clip["transform"] = transform or {"position_x": 0, "position_y": 0, "scale": 1.0, "rotation": 0, "opacity": 1.0}
    clip["transform_keyframes"] = keyframes
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _legacy_value(transform, keyframes, prop, t):
    """Ancien moteur : valeur de base avant le premier keyframe, puis linéaire."""
    frames = sorted((k for k in keyframes if k["property_name"] == prop), key=lambda k: k["time_seconds"])
    if not frames or t < frames[0]["time_seconds"]:
        return transform[prop]
    for a, b in zip(frames, frames[1:]):
        if a["time_seconds"] <= t <= b["time_seconds"]:
            u = (t - a["time_seconds"]) / (b["time_seconds"] - a["time_seconds"])
            return a["value"] + (b["value"] - a["value"]) * u
    return frames[-1]["value"]


def test_old_projects_with_keyframes_render_exactly_as_before(tmp_path):
    transform = {"position_x": 0.0, "position_y": 0.0, "scale": 1.0, "rotation": 0.0, "opacity": 0.5}
    keyframes = [
        {"property_name": "opacity", "time_seconds": 1.0, "value": 1.0},
        {"property_name": "opacity", "time_seconds": 3.0, "value": 0.2},
        {"property_name": "scale", "time_seconds": 0.0, "value": 2.0},
        {"property_name": "scale", "time_seconds": 2.0, "value": 1.0},
        {"property_name": "rotation", "time_seconds": 2.5, "value": 0.0},   # = base : pas de saut
    ]
    clip = _clip(load_project(str(_legacy_file(tmp_path, keyframes, transform))))
    for step in range(41):
        t = step / 10
        evaluated = evaluate_transform(clip.transform, clip.transform_keyframes, t, clip.duration)
        for prop in ("opacity", "scale", "rotation"):
            assert getattr(evaluated, prop) == pytest.approx(_legacy_value(transform, keyframes, prop, t))
    # Seule l'opacité (base ≠ premier keyframe, premier keyframe après 0) reçoit un « hold » à 0.
    added = [k for k in clip.transform_keyframes if k.interpolation is I.HOLD]
    assert [(k.property_name, k.time_seconds, k.value) for k in added] == [("opacity", 0.0, 0.5)]


def test_old_projects_without_animation_are_untouched(tmp_path):
    clip = _clip(load_project(str(_legacy_file(tmp_path, []))))
    assert clip.transform_keyframes == []
    plan = build_render_plan(Project(name="p", width=1920, height=1080, fps=30.0,
                                     media_assets=[], tracks=[]))
    assert plan.video_layers == ()


def test_a_new_file_is_never_migrated_twice(tmp_path):
    project = _project()
    add_keyframe(project, "c", "opacity", 2.0, 0.1)                   # un seul keyframe, après 0
    path = tmp_path / "new.kut"
    save_project(project, str(path))
    clip = _clip(load_project(str(path)))
    assert len(clip.transform_keyframes) == 1                         # pas de « hold » ajouté
    assert evaluate_transform(clip.transform, clip.transform_keyframes, 0.0).opacity == pytest.approx(0.1)


def test_corrupt_optional_animation_fields_fall_back_to_safe_defaults(tmp_path):
    path = _legacy_file(tmp_path, [
        {"property_name": "scale", "time_seconds": 0.0, "value": 1.0, "interpolation": "spline?",
         "in_slope": "abc", "out_slope": float("1e400") if False else None, "tangent_mode": 7, "id": None},
        {"property_name": "scale", "time_seconds": 1.0, "value": 2.0, "interpolation": "bezier"},
    ])
    clip = _clip(load_project(str(path)))
    first = clip.transform_keyframes[0]
    assert first.interpolation is I.LINEAR and first.in_slope is None and first.tangent_mode is TangentMode.LINKED
    assert first.id                                                   # identifiant régénéré


def test_split_and_trim_keep_interpolation_and_tangents():
    from core.timeline_operations import cut_clip, duplicate_clip

    project = _project()
    add_keyframe(project, "c", "scale", 0.0, 1.0, interpolation=I.EASE_IN_OUT)
    add_keyframe(project, "c", "scale", 4.0, 3.0)
    expected = [value_at(_clip(project), "scale", t / 10) for t in range(41)]
    duplicate = duplicate_clip(project, "c")
    assert [k.interpolation for k in duplicate.transform_keyframes] == [I.EASE_IN_OUT, I.LINEAR]
    left, right = cut_clip(project, "c", 1.5)
    for step in range(41):
        t = step / 10
        part = value_at(left, "scale", t) if t <= 1.5 else value_at(right, "scale", t - 1.5)
        assert part == pytest.approx(expected[step])


def test_render_plan_carries_the_same_keyframes():
    project = _project()
    add_keyframe(project, "c", "opacity", 0.0, 1.0, interpolation=I.EASE_OUT)
    add_keyframe(project, "c", "opacity", 2.0, 0.0)
    layer = build_render_plan(project).video_layers[0]
    assert tuple(layer.transform_keyframes) == tuple(_clip(project).transform_keyframes)


def test_transform_keyframe_is_a_generic_keyframe_with_validation():
    keyframe = TransformKeyframe("opacity", 1.0, 0.5, "ease_in")
    assert isinstance(keyframe, Keyframe) and keyframe.interpolation is I.EASE_IN
    assert TransformKeyframe("opacity", 1.0, 0.5) == TransformKeyframe("opacity", 1.0, 0.5)  # id non comparé
    with pytest.raises(ValueError):
        TransformKeyframe("opacity", 1.0, 2.0)
    with pytest.raises(ValueError):
        TransformKeyframe("inconnue", 1.0, 0.5)
    assert register_target is not None


def test_adding_a_keyframe_on_an_overshooting_bezier_does_not_fail():
    project = _project()
    add_keyframe(project, "c", "opacity", 0.0, 0.0, interpolation=I.BEZIER)
    add_keyframe(project, "c", "opacity", 1.0, 1.0)
    set_tangents(project, _ref(project, "opacity", 0.0), out_slope=10.0)
    set_tangents(project, _ref(project, "opacity", 1.0), in_slope=10.0, mode="broken")
    shown = value_at(_clip(project), "opacity", 0.8)
    raw = get_target("opacity").curve(_clip(project)).evaluate(0.8)
    assert not 0.0 <= raw <= 1.0                                       # la courbe brute déborde ici
    added = add_keyframe(project, "c", "opacity", 0.8)                # sans ValueError
    assert added.value == shown and value_at(_clip(project), "opacity", 0.8) == shown
