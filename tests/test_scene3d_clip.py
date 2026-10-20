"""Clip « scène 3D » dans le modèle (lot B2a) : création, refus à chaque entrée, plan de rendu provisoire.

Un clip 3D est un calque graphique (``GraphicType.SCENE3D``) : il se comporte comme les autres calques (coupe, duplication,
groupe, verrous), sauf ce que ce lot refuse explicitement : vitesse et remappage, preset, composition, changement de fichier.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from core import time_commands
from core.composition_ops import CompositionError, convert_to_composition
from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip, graphic_defaults, update_graphic
from core.mograph_layers import LayerError, group_layers, layer_tree, scene_for_project, set_layer_locked
from core.mograph_presets import save_preset
from core.project_io import load_project, save_project
from core.render_plan import build_render_plan
from core.scene3d import Scene3DError, ensure_fixed_speed, is_scene3d_clip
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.sequences import create_sequence_from_selection
from core.time_ops import (
    add_speed_point, clear_speed_curve, insert_hold, set_clip_interpolation, set_clip_preserve_pitch, set_clip_remap_audio,
)
from core.time_presets import TimePreset, apply_preset
from core.time_remapping import TimeRemapping
from core.timeline_operations import (
    find_clip, remove_clip_freeze_frame, reset_clip_time_remapping, set_clip_freeze_duration, set_clip_freeze_frame, set_clip_reverse,
    set_clip_speed,
)
from core.tracking_ops import TrackingError, set_stabilization
from core.transitions import add_transition
from tests.scene3d_samples import base_project, project_with_scene, write_scene


def _snapshot(project):
    return copy.deepcopy([track.clips for track in project.tracks])


# --- Création ---------------------------------------------------------------------------------------------------------


def test_adding_a_scene_makes_a_graphic_clip_with_a_technical_media(tmp_path):
    project, clip, scene = project_with_scene(tmp_path, duration=4.0)
    track = next(t for t in project.tracks if t.id == clip.track_id)
    assert track.type == "graphics" and clip in track.clips
    assert (clip.label, clip.duration, clip.source_in, clip.source_out) == ("scene", 4.0, 0.0, 4.0)
    graphic = clip.graphic
    assert graphic.type is GraphicType.SCENE3D and graphic.source_path == str(scene.resolve())
    assert (graphic.width, graphic.height) == (project.width, project.height)            # taille de rendu = taille du projet
    asset = next(a for a in project.media_assets if a.id == clip.asset_id)
    assert asset.media_type == "graphic" and asset.path == str(scene.resolve())
    assert graphic.fill_color and not graphic.background_enabled


def test_a_prepared_graphic_is_accepted_and_its_path_is_normalised(tmp_path):
    project = base_project()
    scene = write_scene(tmp_path)
    prepared = graphic_defaults(GraphicType.SCENE3D, source_path=str(scene))
    clip = add_graphic_clip(project, "scene3d", timeline_start=0.0, graphic=prepared, clip_id="x")
    assert clip.graphic.source_path == str(scene.resolve())


@pytest.mark.parametrize("name", ["missing.kut3d", "notes.txt"])
def test_a_missing_or_wrong_file_is_refused_and_leaves_nothing_behind(tmp_path, name):
    project = base_project()
    before_tracks, before_assets = len(project.tracks), len(project.media_assets)
    with pytest.raises(Scene3DError):
        add_graphic_clip(project, GraphicType.SCENE3D, timeline_start=0.0, source_path=str(tmp_path / name))
    assert (len(project.tracks), len(project.media_assets)) == (before_tracks, before_assets)


def test_an_unreadable_scene_is_refused(tmp_path):
    bad = tmp_path / "bad.kut3d"
    bad.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(Scene3DError):
        add_graphic_clip(base_project(), GraphicType.SCENE3D, timeline_start=0.0, source_path=str(bad))


def test_a_scene_without_a_file_cannot_exist():
    with pytest.raises(ValueError):
        GraphicOverlay(type=GraphicType.SCENE3D)
    with pytest.raises(ValueError):
        GraphicOverlay(type=GraphicType.SCENE3D, source_path="/x/photo.png")
    with pytest.raises(Scene3DError):
        add_graphic_clip(base_project(), GraphicType.SCENE3D, timeline_start=0.0)


def test_the_file_of_a_scene_cannot_be_swapped(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    (tmp_path / "other").mkdir()
    other = write_scene(tmp_path / "other")
    with pytest.raises(ValueError, match="ne se change pas"):
        update_graphic(clip, "source_path", str(other))
    assert update_graphic(clip, "source_path", clip.graphic.source_path) is clip.graphic        # inchangé : pas un refus
    update_graphic(clip, "visible", False)
    assert clip.graphic.visible is False


def test_a_script_cannot_add_a_scene_graphic_without_a_file():
    assert "scene3d" not in VERBS["add_graphic"].params[0].choices
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [{"op": "add_graphic", "graphic": "scene3d", "at": 0, "duration": 1}]})


def test_a_scene_is_not_animatable_in_size(tmp_path):
    from core.animation_targets import targets_for_clip

    _project, clip, _scene = project_with_scene(tmp_path)
    ids = {target.spec.id for target in targets_for_clip(clip, "graphics")}
    assert "opacity" in ids
    assert "graphic.width" not in ids and "graphic.height" not in ids


# --- Les calques graphiques : même comportement -----------------------------------------------------------------------


def test_the_layer_tree_and_the_scene_graph_accept_a_3d_layer(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    assert [node.clip_id for node in layer_tree(project)] == [clip.id]
    assert clip.id in scene_for_project(project).layers


def test_a_locked_layer_and_a_locked_track_refuse_grouping(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    set_layer_locked(project, clip.id, True)
    with pytest.raises(LayerError, match="verrouillé"):
        group_layers(project, [clip.id])
    set_layer_locked(project, clip.id, False)
    next(t for t in project.tracks if t.id == clip.track_id).locked = True
    with pytest.raises(LayerError, match="verrouillée"):
        group_layers(project, [clip.id])


def test_a_nested_sequence_keeps_the_scene(tmp_path):
    project, clip, scene = project_with_scene(tmp_path, start=0.0, duration=3.0)
    result = create_sequence_from_selection(project, [clip.id], "Imbriquée")
    nested = next(s for s in project.sequences if s.id == result.clip.sequence_id)
    moved = next(c for t in nested.tracks for c in t.clips if c.id == clip.id)
    assert moved.graphic.source_path == str(scene.resolve()) and moved.graphic.type is GraphicType.SCENE3D


# --- Refus : vitesse et remappage du temps -----------------------------------------------------------------------------


def _speed_edits(project, clip_id):
    clip = find_clip(project, clip_id)
    return {
        "speed": lambda: set_clip_speed(project, clip_id, 2.0),
        "reverse": lambda: set_clip_reverse(project, clip_id, True),
        "freeze": lambda: set_clip_freeze_frame(project, clip_id, 0.5, 1.0),
        "freeze_duration": lambda: set_clip_freeze_duration(project, clip_id, 2.0),
        "add_point": lambda: add_speed_point(project, clip_id, 1.0, 2.0),
        "hold": lambda: insert_hold(project, clip_id, 1.0),
        "clear": lambda: clear_speed_curve(project, clip_id),
        "interpolation": lambda: set_clip_interpolation(project, clip_id, "blending"),
        "pitch": lambda: set_clip_preserve_pitch(project, clip_id, False),
        "audio": lambda: set_clip_remap_audio(project, clip_id, False),
        "preset": lambda: apply_preset(project, clip_id, TimePreset.SLOW_50, local_time=1.0, fps=25.0),
        "command_speed": lambda: time_commands.apply_time_command(project, clip_id, "speed", 2.0),
        "command_reverse": lambda: time_commands.apply_time_command(project, clip_id, "reverse", True),
        "command_hold": lambda: time_commands.apply_time_command(project, clip_id, "hold", local_time=1.0, fps=25.0),
        "command_add_point": lambda: time_commands.apply_time_command(project, clip_id, "add_point", local_time=1.0, fps=25.0),
        "command_pitch": lambda: time_commands.apply_time_command(project, clip_id, "preserve_pitch", False),
        "_clip": clip,
    }


def test_every_speed_entry_of_the_model_refuses_a_scene_and_changes_nothing(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    before = _snapshot(project)
    edits = _speed_edits(project, clip.id)
    edits.pop("_clip")
    for name, edit in edits.items():
        with pytest.raises(ValueError, match="propre vitesse"):
            edit()
        assert _snapshot(project) == before, name
    assert clip.time_remapping == TimeRemapping.default() and not clip.has_speed_curve


def test_resetting_the_speed_of_a_scene_is_harmless(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    reset_clip_time_remapping(project, clip.id)
    remove_clip_freeze_frame(project, clip.id)
    assert clip.time_remapping == TimeRemapping.default()


def test_the_speed_of_a_text_layer_is_not_affected_by_the_new_guard():
    project = base_project()
    title = add_graphic_clip(project, GraphicType.TEXT, timeline_start=0.0, duration=3.0)
    ensure_fixed_speed(title)
    assert not is_scene3d_clip(title) and not is_scene3d_clip(SimpleNamespace(graphic=None))
    set_clip_speed(project, title.id, 2.0)
    assert title.time_remapping.speed == 2.0


def test_every_time_script_verb_refuses_a_scene(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    samples = [
        {"op": "set_clip_speed", "clip": clip.id, "speed": 2.0},
        {"op": "set_clip_reverse", "clip": clip.id, "reverse": True},
        {"op": "set_clip_freeze_frame", "clip": clip.id, "source_time": 0.5, "duration": 1.0},
        {"op": "reset_clip_time_remapping", "clip": clip.id},
        {"op": "add_speed_point", "clip": clip.id, "at": 1.0, "value": 2.0},
        {"op": "insert_hold", "clip": clip.id, "at": 1.0},
        {"op": "clear_speed_curve", "clip": clip.id},
        {"op": "set_clip_interpolation", "clip": clip.id, "interpolation": "blending"},
        {"op": "set_clip_preserve_pitch", "clip": clip.id, "enabled": False},
        {"op": "set_clip_remap_audio", "clip": clip.id, "enabled": False},
    ]
    before = _snapshot(project)
    for operation in samples:
        session = Session(project)
        report = apply_script(session, parse_script({"kut_script": 1, "ops": [operation]}))
        assert not report.ok, operation
        assert _snapshot(project) == before, operation


# --- Refus : preset, composition, stabilisation, transitions ---------------------------------------------------------------


def test_a_scene_cannot_be_saved_as_a_preset(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    title = add_graphic_clip(project, GraphicType.TEXT, timeline_start=0.0, duration=3.0)
    settings = tmp_path / "settings"
    for ids in ([clip.id], [title.id, clip.id]):
        with pytest.raises(LayerError, match="preset"):
            save_preset(project, ids, "Ma scène", settings_dir=settings)
    assert not (settings / "mograph_presets").exists() or not list((settings / "mograph_presets").iterdir())
    group = group_layers(project, [clip.id])                                   # un groupe emporte son contenu
    with pytest.raises(LayerError, match="preset"):
        save_preset(project, [group.id], "Groupe", settings_dir=settings)
    assert save_preset(project, [title.id], "Titre", settings_dir=settings).name == "Titre"      # les autres calques : inchangé


def test_a_scene_cannot_enter_a_composition(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path, start=0.0, duration=3.0)
    before = _snapshot(project)
    with pytest.raises(CompositionError, match="scène 3D"):
        convert_to_composition(project, ["a", clip.id])
    assert _snapshot(project) == before


def test_a_scene_has_no_stabilization_nor_transition(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    with pytest.raises(TrackingError):
        set_stabilization(project, clip.id)
    other = add_graphic_clip(project, GraphicType.TEXT, timeline_start=6.0, duration=2.0)
    with pytest.raises(ValueError, match="vidéo"):
        add_transition(project, clip.id, other.id)
    assert clip.tracking is None and project.transitions == []


# --- Plan de rendu (B2b : le calque est rendu, fichier manquant = carte à l'écran + refus à l'export) ----------------------


def test_a_project_with_a_scene_builds_a_render_plan_that_draws_it(tmp_path):
    project, clip, scene = project_with_scene(tmp_path)
    title = add_graphic_clip(project, GraphicType.TEXT, timeline_start=0.0, duration=3.0, clip_id="title")
    plan = build_render_plan(project)
    assert sorted(layer.clip_id for layer in plan.graphics_layers) == sorted([title.id, clip.id])
    layer = next(item for item in plan.graphics_layers if item.clip_id == clip.id)
    assert layer.graphic.type is GraphicType.SCENE3D and layer.graphic.source_path == str(scene.resolve())
    assert plan.warnings == () and plan.missing_media == ()


def test_a_loaded_version_22_project_builds_its_plan(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    path = tmp_path / "p.kut"
    save_project(project, path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 22
    plan = build_render_plan(load_project(path))
    assert [layer.clip_id for layer in plan.graphics_layers] == [clip.id] and plan.warnings == ()


def test_a_hidden_track_or_a_disabled_clip_gives_no_warning(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    clip.enabled = False
    assert build_render_plan(project).warnings == ()
    clip.enabled = True
    next(t for t in project.tracks if t.id == clip.track_id).visible = False
    assert build_render_plan(project).warnings == ()


def test_a_scene_inside_a_nested_sequence_does_not_break_the_plan(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path, start=0.0, duration=3.0)
    create_sequence_from_selection(project, [clip.id], "Imbriquée")
    plan = build_render_plan(project)
    assert plan.warnings == () and plan.missing_media == ()
    assert plan.nested_sequences and any(sub.plan.graphics_layers for sub in plan.nested_sequences)

