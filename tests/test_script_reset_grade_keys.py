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


# --- Deux gestes de réinitialisation dans l'interface, deux portées (parité verbe / page Couleur) ---------------------------------------


def _detailed_graph() -> ColorNodeGraph:
    return ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.2), blur=3.0, sharpen=1.0), ColorNode("n2", ColorGrade(hue=5.0), blur=2.0)))


def _snapshot(clip):
    return clip.color_grade, [(k.property_name, k.time_seconds, k.value) for k in clip.animation]


@pytest.mark.parametrize("graph", [True, False], ids=["graphe", "un seul nœud"])
def test_the_page_reset_is_the_verb_with_detail(window, graph):  # noqa: F811
    """« Tout réinitialiser » de la page Couleur remet aussi flou et netteté : c'est `reset_color_grade` avec `detail: true`."""
    from core.workspace_state import PAGE_COLOR

    _track, clip = _add_track_clip(window, "video", "v")
    window.set_usage_level("advanced")
    clip.color_grade = _detailed_graph() if graph else ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.2), blur=3.0, sharpen=1.0),))
    clip.animation = _keys()
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.switch_page(PAGE_COLOR)
    window.on_color_node_selected("n1")
    scripted = copy.deepcopy(window.project)
    session, report = _script(scripted, {"op": "reset_color_grade", "clip": clip.id, "node": "n1", "detail": True})
    assert report.ok, report.error
    window.on_color_node_reset("n1")
    ui_clip = find_clip(window.project, clip.id)
    verb_clip = find_clip(session.project, clip.id)
    assert _snapshot(verb_clip) == _snapshot(ui_clip)
    result = verb_clip.color_grade                                      # un graphe d'un nœud neutre sans détail se simplifie en réglage simple
    assert isinstance(result, ColorGrade) or all(node.blur == 0.0 and node.sharpen == 0.0 for node in result.correctors if node.id == "n1")
    assert session.undo() and _snapshot(find_clip(session.project, clip.id)) == _snapshot(find_clip(scripted, clip.id)) \
        and len(find_clip(session.project, clip.id).animation) == 5                                           # une seule étape d'annulation
    assert session.redo() and _snapshot(find_clip(session.project, clip.id)) == _snapshot(ui_clip)


def test_the_default_keeps_blur_and_sharpen_like_the_inspector_reset():
    """Sans `detail`, le verbe garde sa portée d'avant (réglage du nœud seul, comme l'inspecteur) : flou et netteté (`set_color_detail`) restent."""
    project = _project()
    find_clip(project, "a").color_grade = _detailed_graph()
    default = _script(copy.deepcopy(project), {"op": "reset_color_grade", "clip": "a", "node": "n1"})[0]
    explicit = _script(copy.deepcopy(project), {"op": "reset_color_grade", "clip": "a", "node": "n1", "detail": False})[0]
    node = find_clip(default.project, "a").color_grade.correctors[0]
    assert (node.blur, node.sharpen, node.grade) == (3.0, 1.0, ColorGrade())
    assert _snapshot(find_clip(default.project, "a")) == _snapshot(find_clip(explicit.project, "a"))
    with_detail = _script(copy.deepcopy(project), {"op": "reset_color_grade", "clip": "a", "node": "n1", "detail": True})[0]
    assert find_clip(with_detail.project, "a").color_grade.correctors[0].blur == 0.0


def test_detail_refuses_before_writing_on_a_locked_track_and_on_a_bad_node():
    locked = _project(locked=True)
    find_clip(locked, "a").color_grade = _detailed_graph()
    before = copy.deepcopy(locked.tracks)
    session, report = _script(locked, {"op": "reset_color_grade", "clip": "a", "node": "n1", "detail": True})
    assert not report.ok and "verrouill" in report.error and copy.deepcopy(session.project.tracks) == before
    project = _project()
    find_clip(project, "a").color_grade = _detailed_graph()
    before = copy.deepcopy(project.tracks)
    session, report = _script(project, {"op": "reset_color_grade", "clip": "a", "node": "inconnu", "detail": True})
    assert not report.ok and copy.deepcopy(session.project.tracks) == before
