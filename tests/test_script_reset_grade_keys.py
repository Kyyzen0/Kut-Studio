"""Scripts : `reset_color_grade` retire aussi les images clés `color.*`, comme l'interface (ADR-0008, étapes 8 et 9).

Parité avec « Tout réinitialiser » de l'inspecteur (vraie fenêtre hors écran) : mêmes clés restantes dans le modèle, même étape d'annulation ;
verrous, graphe de nœuds, preset. `set_color_grade` / `set_color_wheel` refusent d'écrire la base d'un réglage animé (l'interface écrit alors la clé
de la tête de lecture ; un script n'en a pas).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_operations import find_clip
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes

L = InterpolationType.LINEAR


def _keys() -> list[Keyframe]:
    return [Keyframe("color.exposure", 0.0, 0.0, L), Keyframe("color.exposure", 2.0, 1.0, L), Keyframe("color.lift.r", 1.0, 0.2, L),
            Keyframe("fx.e1.intensity", 1.0, 0.5, L), Keyframe("position_x", 1.0, 0.1, L)]


SCENARIOS = {
    "simple": lambda: ColorGrade(saturation=1.3),
    "neutre": lambda: None,
    "graphe": lambda: ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.2)), ColorNode("n2", ColorGrade()))),
    "un seul nœud, non simplifié": lambda: ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.2)),)),
}


def _script(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_verb_leaves_the_same_keys_as_the_inspector_reset(window, scenario):  # noqa: F811
    _track, clip = _add_track_clip(window, "video", "v")
    window.set_usage_level("advanced")
    clip.color_grade = SCENARIOS[scenario]()
    clip.animation = _keys()
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    scripted = copy.deepcopy(window.project)
    session, report = _script(scripted, {"op": "reset_color_grade", "clip": clip.id})
    assert report.ok, report.error
    window.properties_panel.color_reset_button.click()
    ui_clip = window.project.tracks[-1].clips[0]
    verb_clip = find_clip(session.project, clip.id)
    assert [(k.property_name, k.time_seconds, k.value) for k in verb_clip.animation] == [(k.property_name, k.time_seconds, k.value) for k in ui_clip.animation]
    assert verb_clip.color_grade == ui_clip.color_grade
    expected = {"simple": 2, "neutre": 2, "graphe": 5, "un seul nœud, non simplifié": 2}[scenario]
    assert len(verb_clip.animation) == expected, "seules les clés `color.*` partent, et seulement si le clip n'est plus un graphe"
    assert session.undo() and len(find_clip(session.project, clip.id).animation) == 5            # une seule étape d'annulation
    assert session.redo() and len(find_clip(session.project, clip.id).animation) == expected


def _project(*, locked: bool = False) -> Project:
    project = Project("p", width=320, height=180, fps=30.0, media_assets=[MediaAsset("m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 10.0)])])
    clip = find_clip(project, "a")
    clip.color_grade = ColorGrade(saturation=1.3)
    clip.animation = _keys()
    project.tracks[0].locked = locked
    return project


def test_a_locked_track_or_layer_refuses_before_writing_anything():
    locked = _project(locked=True)
    before = copy.deepcopy(locked.tracks)
    session, report = _script(locked, {"op": "reset_color_grade", "clip": "a"})
    assert not report.ok and "verrouill" in report.error and copy.deepcopy(session.project.tracks) == before
    layered = _project()
    donor = Project("g", width=320, height=180, fps=30.0, tracks=[Track("G1", "G1", "graphics")])
    _s, made = _script(donor, {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 3, "id": "t1", "text": "A", "track": "G1"})
    assert made.ok
    find_clip(layered, "a").graphic = replace(find_clip(_s.project, "t1").graphic, locked=True)
    before = copy.deepcopy(layered.tracks)
    session, report = _script(layered, {"op": "reset_color_grade", "clip": "a"})
    assert not report.ok and "verrouill" in report.error and copy.deepcopy(session.project.tracks) == before


def test_a_preset_keeps_the_keys_like_the_interface():
    from core.color_grading import builtin_color_preset_ids

    project = _project()
    preset = sorted(builtin_color_preset_ids())[0]
    session, report = _script(project, {"op": "apply_color_preset", "clip": "a", "preset": preset})
    assert report.ok, report.error
    assert len(find_clip(session.project, "a").animation) == 5


def test_a_mutation_after_commit_does_not_reach_the_undo_state():
    project = _project()
    session, report = _script(project, {"op": "reset_color_grade", "clip": "a"})
    assert report.ok, report.error
    find_clip(session.project, "a").animation.append(Keyframe("color.hue", 1.0, 5.0, L))        # mutation APRÈS le commit
    assert session.undo()
    assert len(find_clip(session.project, "a").animation) == 5


def test_the_base_of_an_animated_setting_is_not_written_by_a_script():
    project = _project()
    before = copy.deepcopy(project.tracks)
    for op in ({"op": "set_color_grade", "clip": "a", "exposure": 0.4}, {"op": "set_color_wheel", "clip": "a", "wheel": "lift", "r": 0.1}):
        session, report = _script(project, op)
        assert not report.ok and "animé" in report.error and copy.deepcopy(session.project.tracks) == before
    ok = _script(project, {"op": "set_color_grade", "clip": "a", "hue": 5.0}, {"op": "set_color_wheel", "clip": "a", "wheel": "lift", "g": 0.1})[1]
    assert ok.ok, ok.error                                                                        # un réglage non animé s'écrit comme avant
