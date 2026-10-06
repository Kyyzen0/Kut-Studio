"""Opérations sur les calques : groupes, ordre, parentage, copier/coller, presets, undo."""

from __future__ import annotations

import pytest

from core.animation import Keyframe
from core.compositing import Compositing, Mask, mask_property_id
from core.edit_history import ProjectHistory
from core.effects_model import EffectType, create_effect
from core.graphics import GraphicType, add_graphic_clip, update_graphic
from core.mograph_layers import (
    LayerError,
    copy_attributes,
    copy_layers,
    delete_layers,
    duplicate_layers,
    group_layers,
    layer_clips,
    layer_tree,
    move_into_group,
    parent_candidates,
    paste_attributes,
    paste_layers,
    rename_layer,
    reorder_layer,
    scene_for_project,
    set_layer_locked,
    set_layer_visible,
    set_parent,
    siblings,
    ungroup,
)
from core.mograph_presets import apply_preset, builtin_presets, load_user_presets, save_preset
from core.project_model import Project
from core.sequences import create_sequence
from core.visual_effects import ClipTransform, TransformKeyframe


def _project() -> Project:
    return Project(name="Calques", width=1280, height=720, fps=25.0)


def _three(project):
    return [add_graphic_clip(project, kind, timeline_start=0, duration=4) for kind in ("text", "shape", "solid")]


def test_parenting_refuses_cycles_and_keeps_the_layer_in_place():
    project = _project()
    a, b, c = _three(project)
    c.transform = ClipTransform(position_x=0.2, rotation=30, scale=1.5)
    before = scene_for_project(project).world_matrix(a.id, 1.0)
    set_parent(project, a.id, b.id, at_time=1.0)
    set_parent(project, b.id, c.id, at_time=1.0)
    assert scene_for_project(project).world_matrix(a.id, 1.0) == pytest.approx(before, abs=1e-6)
    with pytest.raises(LayerError):
        set_parent(project, c.id, a.id)  # A → B → C → A
    with pytest.raises(LayerError):
        set_parent(project, a.id, a.id)
    assert a.id not in {cid for cid, _ in parent_candidates(project, c.id)}


def test_group_ungroup_and_enter_group_tree():
    project = _project()
    a, b, c = _three(project)
    group = group_layers(project, [a.id, b.id], name="Titre")
    tree = layer_tree(project)
    # Le groupe prend la place de son membre le plus haut : sous l'aplat.
    assert [(n.name, n.depth) for n in tree] == [("Aplat", 0), ("Titre", 0), ("Rectangle", 1), ("Titre", 1)]
    inside = layer_tree(project, root_group=group.id)
    assert {n.clip_id for n in inside} == {a.id, b.id}
    with pytest.raises(LayerError):
        move_into_group(project, group.id, group.id)
    ungroup(project, group.id)  # groupe neutre : il disparaît
    assert group.id not in {clip.id for _i, _t, clip in layer_clips(project)}
    assert {clip.graphic.group_id for _i, _t, clip in layer_clips(project)} == {""}


def test_ungroup_of_a_transformed_group_keeps_the_render_as_a_controller():
    project = _project()
    a, b, _c = _three(project)
    group = group_layers(project, [a.id, b.id])
    group.transform = ClipTransform(position_x=0.1, rotation=20)
    before = scene_for_project(project).world_matrix(a.id, 1.0)
    ungroup(project, group.id)
    assert group.graphic.type is GraphicType.NULL
    assert a.graphic.parent_id == group.id
    assert scene_for_project(project).world_matrix(a.id, 1.0) == pytest.approx(before)


def test_adjustment_layers_cannot_be_grouped():
    project = _project()
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0)
    text = add_graphic_clip(project, "text", timeline_start=0)
    with pytest.raises(LayerError):
        group_layers(project, [adjustment.id, text.id])


def test_reorder_changes_only_the_order_of_siblings():
    project = _project()
    a, b, c = _three(project)
    assert [x.id for x in siblings(project, a.id)] == [a.id, b.id, c.id]
    reorder_layer(project, c.id, 0)
    assert [x.id for x in siblings(project, a.id)] == [c.id, a.id, b.id]
    reorder_layer(project, c.id, 2)
    assert [x.id for x in siblings(project, a.id)] == [a.id, b.id, c.id]


def test_rename_visibility_and_lock():
    project = _project()
    a, _b, _c = _three(project)
    rename_layer(project, a.id, "Générique")
    set_layer_visible(project, a.id, False)
    set_layer_locked(project, a.id, True)
    assert a.label == "Générique" and not a.graphic.visible and a.graphic.locked
    with pytest.raises(LayerError):
        set_parent(project, a.id, _b.id)  # calque verrouillé
    with pytest.raises(LayerError):
        rename_layer(project, a.id, "   ")


def test_copy_paste_remaps_parent_and_group_and_never_breaks_references():
    project = _project()
    a, b, c = _three(project)
    set_parent(project, a.id, b.id, keep_visual=False)
    set_parent(project, b.id, c.id, keep_visual=False)
    group = group_layers(project, [a.id, b.id])
    clipboard = copy_layers(project, [group.id])  # un groupe emporte son contenu
    assert len(clipboard.clips) == 3
    pasted = paste_layers(project, clipboard, at=2.0)
    by_label = {clip.label: clip for clip in pasted}
    new_ids = {clip.id for clip in pasted}
    assert by_label["Titre"].graphic.parent_id in new_ids  # parent copié avec lui
    assert by_label["Rectangle"].graphic.parent_id == c.id  # parent resté dans la séquence
    # Collage dans une autre séquence : la référence vers C disparaît.
    other = create_sequence(project, "Autre")
    project.active_sequence_id = other.id
    elsewhere = paste_layers(project, clipboard, at=0.0)
    assert {clip.graphic.parent_id for clip in elsewhere if clip.label == "Rectangle"} == {""}


def test_paste_adapts_pixel_sizes_to_another_frame():
    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0)
    clipboard = copy_layers(project, [text.id])
    small = Project(name="Petit", width=640, height=360, fps=25.0)
    pasted = paste_layers(small, clipboard, at=0.0)[0]
    assert pasted.graphic.width == text.graphic.width // 2
    assert pasted.graphic.font_size == text.graphic.font_size // 2


def test_duplicate_and_delete_detach_children():
    project = _project()
    a, b, _c = _three(project)
    set_parent(project, a.id, b.id, keep_visual=False)
    copies = duplicate_layers(project, [a.id])
    assert copies[0].graphic.parent_id == b.id
    delete_layers(project, [b.id])
    assert a.graphic.parent_id == "" and copies[0].graphic.parent_id == ""


def test_attributes_copy_transform_effects_masks_and_keyframes():
    project = _project()
    source, target, _c = _three(project)
    source.transform = ClipTransform(rotation=12, anchor_x=0.1)
    source.transform_keyframes = [TransformKeyframe("opacity", 0, 0), TransformKeyframe("opacity", 1, 1)]
    source.effects = [create_effect(EffectType.BLUR)]
    mask = Mask(feather=0.2)
    source.compositing = Compositing(masks=(mask,))
    pid = mask_property_id(mask.id, "feather")
    source.animation = [Keyframe(pid, 0, 0.0), Keyframe(pid, 1, 0.3), Keyframe("graphic.tracking", 0, 5.0)]
    clipboard = copy_attributes(project, source.id)
    paste_attributes(project, [target.id], clipboard, ("transform", "effects", "masks", "keyframes"))
    assert target.transform == source.transform and len(target.transform_keyframes) == 2
    assert target.effects[0].type is EffectType.BLUR and target.effects[0].id != source.effects[0].id
    new_mask = target.compositing.masks[0]
    assert new_mask.id != mask.id and new_mask.feather == pytest.approx(0.2)
    names = {kf.property_name for kf in target.animation}
    assert mask_property_id(new_mask.id, "feather") in names and "graphic.tracking" in names


def test_presets_apply_and_user_presets_round_trip(tmp_path):
    project = _project()
    presets = builtin_presets()
    assert {"Titre simple", "Lower third", "Call-out"} <= {p.name for p in presets}
    lower_third = next(p for p in presets if p.name == "Lower third")
    created = apply_preset(project, lower_third, at=3.0)
    groups = [c for c in created if c.graphic.type is GraphicType.GROUP]
    assert len(groups) == 1 and all(c.graphic.group_id == groups[0].id for c in created if c is not groups[0])
    assert min(c.timeline_start for c in created) == pytest.approx(3.0)
    saved = save_preset(project, [groups[0].id], "Mon bandeau", settings_dir=tmp_path)
    loaded = load_user_presets(settings_dir=tmp_path)
    assert [p.name for p in loaded] == ["Mon bandeau"] and len(loaded[0].clipboard.clips) == len(created)
    assert saved.path.endswith("mon-bandeau.json")


def test_every_layer_operation_is_undoable():
    project = _project()
    history = ProjectHistory()
    history.reset(project)
    a, b, _c = _three(project)
    history.record(project, "Ajouter")
    group = group_layers(project, [a.id, b.id])
    history.record(project, "Grouper")
    update_graphic(a, "text", "Changé")
    history.record(project, "Texte")
    restored = history.undo()
    assert next(c for t in restored.tracks for c in t.clips if c.id == a.id).graphic.text != "Changé"
    restored = history.undo()
    assert group.id not in {c.id for t in restored.tracks for c in t.clips}
    restored = history.redo()
    assert group.id in {c.id for t in restored.tracks for c in t.clips}


def test_builtin_presets_are_rebuilt_on_a_vertical_canvas():
    from core.social_formats import create_social_project

    project = create_social_project("vertical", 30)
    presets = {preset.name: preset for preset in builtin_presets()}
    assert {"Titre TikTok", "Mot en couleur", "Sous-titre karaoké"} <= set(presets)
    created = apply_preset(project, presets["Lower third"], at=0.0)
    group = next(c for c in created if c.graphic.type is GraphicType.GROUP)
    bar = next(c for c in created if c.graphic.type is GraphicType.SHAPE)
    assert group.transform.position_x == 0.0 and bar.graphic.width <= project.width - 200
    (title,) = apply_preset(project, presets["Titre TikTok"], at=1.0)
    assert title.graphic.stroke_position == "outside" and title.graphic.font_family == "Anton"
    assert {kf.property_name for kf in title.transform_keyframes} >= {"scale", "opacity"}
    (caption,) = apply_preset(project, presets["Sous-titre karaoké"], at=2.0)
    assert caption.graphic.word_reveal == "karaoke"
