"""« Coller les attributs » : un collage qui remplace l'étalonnage retire les images clés `color.*` de la destination (réglage qu'elles
animaient), dans la même étape d'annulation ; « images clés » ne colle les `color.*` que si elles s'appliqueraient (source ET destination
en réglage simple). Vraie fenêtre : le geste du menu du panneau Calques (`_paste_layer_attributes`)."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.mograph_layers import ATTRIBUTE_KINDS, copy_attributes, paste_attributes
from core.timeline_operations import find_clip
from tests.test_inspector_lock_guards import _add_track_clip, window  # noqa: F401 - fixture et aide communes

L = InterpolationType.LINEAR


def _color(value: float) -> list[Keyframe]:
    return [Keyframe("color.exposure", 0.0, 0.0, L), Keyframe("color.exposure", 2.0, value, L), Keyframe("position_x", 1.0, 0.1, L)]


def _names(clip) -> list[str]:
    return sorted(k.property_name for k in clip.animation)


def _setup(window, *, source_grade=None, target_grade=None, locked=False):  # noqa: F811
    _t, source = _add_track_clip(window, "video", "src")
    _t, target = _add_track_clip(window, "video", "dst", locked=locked)
    source.color_grade = source_grade if source_grade is not None else ColorGrade(saturation=1.4)
    source.animation = _color(1.0)
    target.color_grade = target_grade if target_grade is not None else ColorGrade(exposure=0.1)
    target.animation = _color(0.5)
    window.history.reset(window.project)
    window._attribute_clipboard = copy_attributes(window.project, source.id)
    return source, target


def _paste(window, monkeypatch, choice_key, ids):  # noqa: F811
    from ui import i18n

    labels = {"all": "dialog.layers.attr_all", "effects": "dialog.layers.attr_effects", "keyframes": "dialog.layers.attr_keyframes"}
    monkeypatch.setattr("ui.main_window_mixins.motion_graphics.QInputDialog.getItem",
                        lambda *_a, **_k: (i18n.translate(labels[choice_key]), True))
    window._paste_layer_attributes(ids)


def test_effects_paste_removes_the_destination_color_keys_in_one_undo_step(window, monkeypatch):  # noqa: F811
    _source, target = _setup(window)
    _paste(window, monkeypatch, "effects", ["dst"])
    clip = find_clip(window.project, "dst")
    assert clip.color_grade == ColorGrade(saturation=1.4) and _names(clip) == ["position_x"], "color.* partent, le reste de l'animation reste"
    window.undo_last()
    assert _names(find_clip(window.project, "dst")) == ["color.exposure", "color.exposure", "position_x"] \
        and find_clip(window.project, "dst").color_grade == ColorGrade(exposure=0.1), "une seule étape d'annulation"
    window.redo_last()
    assert _names(find_clip(window.project, "dst")) == ["position_x"]


@pytest.mark.parametrize("target_grade", [None, ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.1)),
                                                                        ColorNode("n2", ColorGrade(hue=3.0))))], ids=["neutre", "graphe"])
def test_effects_paste_clears_keys_whatever_the_destination_was(window, monkeypatch, target_grade):  # noqa: F811
    _setup(window, target_grade=target_grade)
    _paste(window, monkeypatch, "effects", ["dst"])
    assert _names(find_clip(window.project, "dst")) == ["position_x"]


def test_all_pastes_the_source_color_keys_when_both_are_simple(window, monkeypatch):  # noqa: F811
    _setup(window)
    _paste(window, monkeypatch, "all", ["dst"])
    clip = find_clip(window.project, "dst")
    assert [k.value for k in clip.animation if k.property_name == "color.exposure"] == [0.0, 1.0], "les clés de la source, pas celles de la destination"


def test_keyframes_alone_replaces_the_color_keys_with_the_source_ones_when_both_are_simple(window, monkeypatch):  # noqa: F811
    _setup(window)
    _paste(window, monkeypatch, "keyframes", ["dst"])
    clip = find_clip(window.project, "dst")
    assert clip.color_grade == ColorGrade(exposure=0.1)
    assert [k.value for k in clip.animation if k.property_name == "color.exposure"] == [0.0, 1.0]


def test_keyframes_do_not_carry_color_keys_into_a_node_graph_or_from_one(window, monkeypatch):  # noqa: F811
    graph = ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.1)), ColorNode("n2", ColorGrade(hue=3.0))))
    _setup(window, target_grade=graph)                                  # destination en graphe : clés ignorées au rendu, donc non copiées
    _paste(window, monkeypatch, "keyframes", ["dst"])
    assert _names(find_clip(window.project, "dst")) == ["position_x"]
    window.history.reset(window.project)
    window.project.tracks[-1].clips[0].animation = _color(0.5)
    window.project.tracks[-1].clips[0].color_grade = ColorGrade()
    window._attribute_clipboard = replace(window._attribute_clipboard, color_grade=graph)      # source en graphe : ses clés étaient inertes
    _paste(window, monkeypatch, "keyframes", ["dst"])
    assert _names(find_clip(window.project, "dst")) == ["position_x"], "elles animeraient un clip que la source n'animait pas"


def test_a_locked_track_or_layer_is_skipped_untouched(window, monkeypatch):  # noqa: F811
    _setup(window, locked=True)
    before = copy.deepcopy(window.project.tracks[-1])
    _paste(window, monkeypatch, "all", ["dst"])
    assert window.project.tracks[-1] == before
    window.project.tracks[-1].locked = False
    from core.graphics import GraphicOverlay
    find_clip(window.project, "dst").graphic = GraphicOverlay(locked=True)
    before = copy.deepcopy(window.project.tracks[-1])
    _paste(window, monkeypatch, "all", ["dst"])
    assert window.project.tracks[-1] == before


def test_core_paste_skips_a_non_editable_clip_before_writing_and_keeps_other_kinds_untouched():
    from core.project_model import Clip, MediaAsset, Project, Track

    def make():
        project = Project("p", width=320, height=180, fps=30.0, media_assets=[MediaAsset("m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video")],
                          tracks=[Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 5.0), Clip("b", "m", "V1", 6.0, 0.0, 5.0)])])
        a, b = find_clip(project, "a"), find_clip(project, "b")
        a.color_grade, a.animation = ColorGrade(saturation=1.4), _color(1.0)
        b.color_grade, b.animation = ColorGrade(exposure=0.1), _color(0.5)
        return project

    project = make()
    clipboard = copy_attributes(project, "a")
    paste_attributes(project, ["b"], clipboard, ("masks",))                       # coller des masques ne touche pas aux clés d'étalonnage
    assert _names(find_clip(project, "b")) == ["color.exposure", "color.exposure", "position_x"]
    paste_attributes(project, ["b"], clipboard, ("transform",))
    assert _names(find_clip(project, "b")) == ["color.exposure", "color.exposure", "position_x"]
    assert paste_attributes(project, ["b"], clipboard, ATTRIBUTE_KINDS) == 1
