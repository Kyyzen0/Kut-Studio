"""Plafonds de ressources des verbes de script, au-delà de l'inventaire générique de ``test_script_resource_guards.py``.

- **une seule mesure d'images clés par clip** : un verbe ne contourne pas le plafond d'un autre (transform, effets, masques, vitesse) ;
- **un projet qui dépasse déjà un plafond** n'est ni corrigé ni corrompu : le verbe refuse d'AJOUTER, il ne supprime rien et ce qui ne
  fait que remplacer passe ;
- **les verbes classés « bornés »** : la preuve de leur borne par exécution (transitions, liaisons de suivi, préréglage d'étalonnage) ;
- **les verbes d'images clés que la découverte ne voit pas** (``apply_ken_burns``).
"""

from __future__ import annotations

import importlib
from copy import deepcopy

import pytest

from core import tracking_ops
from core.animation import Keyframe
from core.project_model import Clip, Project, Track
from core.scripting import resource_limits as limits
from core.scripting.verbs import VERBS
from tests.script_resource_guards import CAP, REFUSALS, _effect, _find, _keyframes_of, _mask, _segment, base_project


def _run(verb: str, args: dict, project: Project) -> Exception | None:
    """Applique le verbe DIRECTEMENT (sans session) ; rend le refus, ou ``None``."""
    try:
        VERBS[verb].build(dict(args))(project)
    except REFUSALS as error:
        return error
    return None


def _lower(monkeypatch, name: str, value: int = CAP) -> None:
    monkeypatch.setattr(importlib.import_module("core.scripting.resource_limits"), name, value)


def _animate(clip: Clip, count: int, prop: str = "fx.fx1.brightness") -> None:
    start = len(clip.animation)
    clip.animation = [*clip.animation, *(Keyframe(prop, 0.0005 * (start + index), 0.1) for index in range(count))]


# --- Une seule mesure d'images clés par clip ----------------------------------------------------------------------------------

KEYFRAME_WRITERS = {
    "add_keyframe": ({"clip": "c1", "property": "scale", "at": 3.9, "value": 1.2}, None),
    "add_effect_keyframe": ({"clip": "c1", "effect": "fx1", "parameter": "brightness", "at": 3.9, "value": 0.3}, _effect),
    "add_mask_keyframe": ({"clip": "c1", "mask": "m1", "property": "feather", "at": 3.9, "value": 0.1}, _mask),
    "add_speed_point": ({"clip": "c1", "at": 2.0, "value": 2.0}, None),
    "insert_hold": ({"clip": "c1", "at": 2.0}, None),
    "apply_impact_zoom": ({"clip": "c1", "start": 2.0}, None),
    "apply_camera_shake": ({"clip": "c1", "length": 1.0}, None),
    "apply_ken_burns": ({"clip": "c1"}, None),
    "apply_time_preset": ({"clip": "c1", "preset": "ramp_in"}, None),
}


@pytest.mark.parametrize("writer", sorted(KEYFRAME_WRITERS))
@pytest.mark.parametrize("filled_by", ["transform", "animation"])
def test_one_keyframe_measure_for_every_writer(writer, filled_by, monkeypatch):
    """Un clip plein d'images clés d'une famille (transform, ou animation : effets, masques, vitesse) refuse les verbes de TOUTES les familles."""
    args, prepare = KEYFRAME_WRITERS[writer]
    project = base_project()
    if prepare is not None:
        prepare(project)
    clip = _find(project, "c1")
    _lower(monkeypatch, "MAX_KEYFRAMES_PER_CLIP", 100)
    if filled_by == "transform":
        from core.visual_effects import TransformKeyframe

        clip.transform_keyframes = [TransformKeyframe("rotation", 0.0005 * index, 0.0) for index in range(100)]
    else:
        _animate(clip, 100, "graphic.opacity")
    before = deepcopy(project)
    error = _run(writer, args, project)
    assert error is not None and "images clés" in str(error), f"{writer} ne voit pas les {filled_by} du clip : {error}"
    assert project == before, f"{writer} a modifié le projet en refusant"


def test_a_replacement_adds_nothing_and_is_not_refused_at_the_ceiling(monkeypatch):
    """Poser une image clé à l'instant d'une image clé existante la remplace : au plafond, elle passe."""
    project = base_project()
    clip = _find(project, "c1")
    _lower(monkeypatch, "MAX_KEYFRAMES_PER_CLIP", 3)
    for at in (0.5, 1.0, 1.5):
        assert _run("add_keyframe", {"clip": "c1", "property": "scale", "at": at, "value": 1.1}, project) is None
    assert _keyframes_of(clip) == 3
    assert _run("add_keyframe", {"clip": "c1", "property": "scale", "at": 1.0, "value": 1.3}, project) is None
    assert _keyframes_of(clip) == 3
    assert _run("add_keyframe", {"clip": "c1", "property": "scale", "at": 2.0, "value": 1.3}, project) is not None


def test_a_multicam_switch_that_cuts_nothing_adds_nothing(monkeypatch):
    """Le même angle, ou le début du segment : aucune coupe (``switch_angle``), donc aucun clip de plus -- le projet plein l'accepte ; une vraie coupe est refusée."""
    project = base_project()
    _segment(project)
    _lower(monkeypatch, "MAX_CLIPS_IN_PROJECT", limits.total_clips(project))
    base = {"track": "V1"}
    assert _run("switch_multicam_angle", {**base, "at": 6.0, "angle": "angle-1"}, project) is None             # déjà cet angle
    assert _run("switch_multicam_angle", {**base, "at": 5.0, "angle": "angle-2"}, project) is None             # début du segment : l'angle est remplacé
    before = deepcopy(project)
    assert _run("switch_multicam_angle", {**base, "at": 7.0, "angle": "angle-1"}, project) is not None          # le segment montre angle-2, la coupe crée un clip
    assert project == before


# --- Un projet qui dépasse déjà un plafond ----------------------------------------------------------------------------------


def test_a_project_already_over_a_ceiling_is_not_corrupted(monkeypatch):
    """Le fichier a été ouvert dans l'application avec plus d'objets que le plafond : un verbe refuse d'ajouter, rien n'est supprimé ni ramené."""
    project = base_project()
    for index in range(10):
        project.tracks.append(Track(f"x{index}", f"x{index}", "video"))
    clip = _find(project, "c1")
    clip.transform_keyframes = []
    _animate(clip, 10)
    _lower(monkeypatch, "MAX_TRACKS_PER_SEQUENCE", 5)
    _lower(monkeypatch, "MAX_KEYFRAMES_PER_CLIP", 5)
    before = deepcopy(project)
    assert _run("add_track", {"type": "video"}, project) is not None
    assert _run("add_keyframe", {"clip": "c1", "property": "scale", "at": 1.0, "value": 1.1}, project) is not None
    assert project == before and len(project.tracks) >= 13 and len(clip.animation) == 10
    # ... et ce qui n'ajoute rien passe (remplacer, retirer) : le verbe ne confond pas « déjà plein » et « refusé ».
    assert _run("remove_track", {"track": "x0"}, project) is None
    limits.require_room_in(10, 0, 5, "objets", "Le projet")
    limits.require_room_in(10, -1, 5, "objets", "Le projet")


def test_an_operation_that_shrinks_an_over_full_clip_is_not_refused(monkeypatch):
    """Un clip au-delà du plafond : ce qui lui retire des images clés (une vitesse constante remplace la courbe) passe ; ce qui en ajoute est refusé."""
    project = base_project()
    clip = _find(project, "c1")
    _animate(clip, 10, "time.speed")
    _lower(monkeypatch, "MAX_KEYFRAMES_PER_CLIP", 5)
    assert _run("apply_time_preset", {"clip": "c1", "preset": "ramp_in"}, deepcopy(project)) is None             # 10 points -> 2 : moins qu'avant
    assert _run("add_speed_point", {"clip": "c1", "at": 2.0, "value": 2.0}, deepcopy(project)) is not None       # un de plus : refusé
    with pytest.raises(ValueError):
        limits.require_keyframes_total(clip, 11, "test")
    limits.require_keyframes_total(clip, 10, "test")
    limits.require_keyframes_total(clip, 3, "test")


def test_the_generic_guard_refuses_only_what_it_adds():
    limits.require_room_in(5, 1, 6, "objets", "Le projet")
    with pytest.raises(ValueError, match="dépasseraient la limite de 6"):
        limits.require_room_in(5, 2, 6, "objets", "Le projet")
    with pytest.raises(ValueError, match="Le projet compte déjà 7 objets"):
        limits.require_room_in(7, 1, 6, "objets", "Le projet")


# --- Les verbes classés « bornés » : la preuve ----------------------------------------------------------------------------------


def test_apply_color_preset_creates_nothing():
    project = base_project()
    from core.effects_library import builtin_presets  # noqa: F401  (le registre des presets d'étalonnage est à part)
    from core.color_grading import builtin_color_presets

    preset = builtin_color_presets()[0].id
    clip = _find(project, "c1")
    for _ in range(3):
        assert _run("apply_color_preset", {"clip": "c1", "preset": preset}, project) is None
    grade = clip.color_grade
    assert not hasattr(grade, "nodes") or len(grade.nodes) == 1
    assert not hasattr(grade, "correctors") or sum(len(node.windows) for node in grade.correctors) == 0


def test_transitions_are_bounded_by_the_clips():
    """Une transition par jonction, et un clip n'est que dans une transition : au plus la moitié des clips de la séquence."""
    project = base_project()
    project.tracks[0].clips = [Clip(f"v{i}", "m", "V1", 4.0 * i, 0.0, 4.0, label=f"v{i}") for i in range(4)]
    first = {"from_clip": "v0", "to_clip": "v1", "transition": "crossfade", "duration": 1.0}
    assert _run("add_transition", first, project) is None
    assert _run("add_transition", first, project) is not None                                                        # la même jonction
    assert _run("add_transition", {**first, "from_clip": "v1", "to_clip": "v2"}, project) is not None              # v1 est déjà prise
    assert _run("apply_transition_preset", {"preset": "crossfade", "from_clip": "v1", "to_clip": "v2"}, project) is not None
    assert _run("add_transition", {**first, "from_clip": "v2", "to_clip": "v3"}, project) is None
    assert len(project.transitions) == 2 <= len(project.tracks[0].clips) // 2


def test_links_are_bounded_by_masks_and_windows():
    """Une liaison par propriété cible, et un masque ciblé doit exister : au plus 2 + masques + fenêtres."""
    project = base_project()
    project.tracks[0].clips.append(Clip("c2", "m", "V1", 4.0, 0.0, 4.0, label="c2"))
    tracking_ops.add_tracker(project, "c1", timeline_time=0.0, x=100.0, y=50.0, tracker_id="t1")
    tracking_ops.set_tracker_position(project, "c1", "t1", 3.0, 130.0, 60.0)
    link = {"clip": "c2", "source": "c1", "trackers": ["t1"], "at": 1.0}
    assert _run("add_link", link, project) is None
    assert _run("add_link", link, project) is not None                                                              # la même propriété
    assert _run("add_link", {**link, "target": "mask", "mask": "inconnu"}, project) is not None                    # un masque qui n'existe pas
    assert len(tracking_ops.tracking_of(_find(project, "c2")).links) == 1


# --- Les verbes d'images clés que la découverte automatique ne voit pas -------------------------------------------------------------


def test_apply_ken_burns_is_capped(monkeypatch):
    project = base_project()
    assert _run("apply_ken_burns", {"clip": "c1"}, project) is None
    count = _keyframes_of(_find(project, "c1"))
    assert count > 0
    fresh = base_project()
    _lower(monkeypatch, "MAX_KEYFRAMES_PER_CLIP", count - 1)
    before = deepcopy(fresh)
    assert _run("apply_ken_burns", {"clip": "c1"}, fresh) is not None and fresh == before


# --- Une garde ne réserve que ce que l'opération AJOUTE ---------------------------------------------------------------------------


def test_branch_color_node_reserves_one_node_when_the_mixer_is_reused_and_two_otherwise(monkeypatch):
    project = base_project()
    assert _run("branch_color_node", {"clip": "c1", "node": "n1", "kind": "parallel"}, project) is None      # n1, la branche, le mélangeur : 3 nœuds
    assert limits.MAX_COLOR_NODES_PER_CLIP >= 4
    _lower(monkeypatch, "MAX_COLOR_NODES_PER_CLIP", 4)
    other_kind = deepcopy(project)
    before = deepcopy(other_kind)
    # Un autre genre de mélangeur : il en faudrait un nouveau, 3 -> 5 nœuds : refusé, projet inchangé.
    assert _run("branch_color_node", {"clip": "c1", "node": "n1", "kind": "layer"}, other_kind) is not None and other_kind == before
    # Le même genre : la branche rejoint le mélangeur existant, 3 -> 4 nœuds : accepté.
    assert _run("branch_color_node", {"clip": "c1", "node": "n1", "kind": "parallel"}, project) is None
    assert _run("branch_color_node", {"clip": "c1", "node": "n1", "kind": "parallel"}, project) is not None     # 4 -> 5 : refusé


def test_a_photo_already_in_the_project_adds_no_media(monkeypatch, tmp_path):
    """Poser une photo dont le média existe déjà (même chemin) n'ajoute rien : le projet plein l'accepte ; une photo neuve est refusée."""
    from tests.script_resource_guards import _slot, make_context

    context = make_context(tmp_path)
    project = base_project()
    _slot(project)
    assert VERBS["fill_slot_with_photo"].build({"clip": "s1", "path": "p.png"}, context)(project) is not None     # le média de la photo est créé
    project.tracks[0].clips.append(Clip("s2", "", "V1", 8.0, 0.0, 2.0, label="s2", template_slot="slot-02"))
    _lower(monkeypatch, "MAX_MEDIA_IN_PROJECT", len(project.media_assets))
    assert VERBS["fill_slot_with_photo"].build({"clip": "s2", "path": "p.png"}, context)(project) is not None     # même photo : aucun média neuf
    project.tracks[0].clips.append(Clip("s3", "", "V1", 11.0, 0.0, 2.0, label="s3", template_slot="slot-03"))
    other = tmp_path / "q.png"
    other.write_bytes((tmp_path / "p.png").read_bytes())
    before = deepcopy(project)
    try:
        VERBS["fill_slot_with_photo"].build({"clip": "s3", "path": "q.png"}, context)(project)
    except ValueError as error:
        assert "médias" in str(error)
    else:
        raise AssertionError("une photo neuve au plafond de médias devait être refusée")
    assert project == before


@pytest.mark.parametrize("constant", ["MAX_CLIPS_IN_PROJECT", "MAX_MEDIA_IN_PROJECT"])
def test_update_leaderboard_reserves_the_clips_and_media_of_the_rows_it_adds(constant, monkeypatch):
    """Un classement réécrit avec une ligne de plus crée cinq calques : cinq clips et cinq médias internes (les anciens calques ne comptent pas)."""
    rows = [{"rank": "1", "name": "A", "value": "1"}]
    project = base_project()
    assert _run("add_leaderboard", {"rows": rows, "start": 0.0, "duration": 3.0, "id": "lb1"}, project) is None
    two = [*rows, {"rank": "2", "name": "B", "value": "2"}]
    module = limits
    have = limits.total_clips(project) if constant == "MAX_CLIPS_IN_PROJECT" else len(project.media_assets)
    monkeypatch.setattr(module, constant, have + 4)
    before = deepcopy(project)
    assert _run("update_leaderboard", {"leaderboard": "lb1", "rows": two}, project) is not None and project == before
    monkeypatch.setattr(module, constant, have + 5)
    assert _run("update_leaderboard", {"leaderboard": "lb1", "rows": two}, project) is None
