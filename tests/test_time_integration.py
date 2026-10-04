"""Le temps d'un clip avec le reste du montage, **avec une courbe de vitesse** : séquences imbriquées, Multicam, suivi, sous-titres.

Chaque consommateur du temps lit le ``TimeMap`` du clip (une seule source de vérité) ; ces tests le prouvent sur une rampe, là
où une vitesse constante ne distinguerait pas « lit le mapping » de « multiplie par la vitesse ». Aucun rendu : tout se
vérifie sur le modèle, donc exactement.
"""

from __future__ import annotations

import math

import pytest
from test_multicam_core import _with_segment
from test_sequences import _two_level_project
from test_tracking_split import FPS as TRACK_FPS
from test_tracking_split import _assert_same_motion, _clip, _follower, _frames, _point, _positions
from test_tracking_split import _project as tracked_project

from core.animation import InterpolationType
from core.multicam_ops import switch_angle
from core.project_model import Clip, Track
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip
from core.time_ops import add_speed_point
from core.timeline_evaluator import evaluate_timeline
from core.timeline_operations import cut_clip, find_clip


def ramp(project, clip_id, *points):
    """Pose des points de vitesse ``(temps local, valeur)`` sur un clip."""
    for moment, value in points:
        add_speed_point(project, clip_id, moment, value, interpolation=InterpolationType.LINEAR)


# ---------------------------------------------------------------------------
# Séquence imbriquée : sa sortie est une source temporelle, l'enfant n'est jamais modifié
# ---------------------------------------------------------------------------


def test_a_nested_clip_with_a_ramp_reads_its_child_through_the_time_map():
    project = _two_level_project()
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    ramp(project, nested.id, (0.0, 1.0), (1.0, 1.0), (2.0, 0.25))
    child = project.get_sequence("intro")
    before = [(c.timeline_start, c.source_in, c.source_out) for track in child.tracks for c in track.clips]
    for moment in (0.25, 0.9, 1.5, 1.9, 2.4):
        red = next(a for a in evaluate_timeline(project, moment) if a.clip_id == "r1")
        assert red.source_time == pytest.approx(nested.time_map.source_time(moment), abs=1e-6), moment
    assert [(c.timeline_start, c.source_in, c.source_out) for track in child.tracks for c in track.clips] == before


def test_the_child_of_a_ramped_nested_clip_is_still_rendered_once_with_an_exact_duration():
    project = _two_level_project()
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    ramp(project, nested.id, (0.0, 1.0), (2.0, 0.5))
    plan = build_render_plan(project)
    layer = next(layer for layer in plan.video_layers if layer.nested_key)
    assert layer.time_map is not None and layer.timeline_end - layer.timeline_start == pytest.approx(nested.duration)
    assert len(plan.nested_sequences) == 1


# ---------------------------------------------------------------------------
# Multicam : les bascules suivent le remappage du segment
# ---------------------------------------------------------------------------


def test_an_angle_switch_in_a_ramped_segment_keeps_the_time_of_the_source_continuous():
    project, _source, segment = _with_segment("angle-1")
    ramp(project, segment.id, (0.0, 1.0), (3.0, 0.5), (6.0, 1.5))
    reference = segment.time_map
    cut = segment.timeline_start + 2.5
    result = switch_angle(project, cut, "angle-2")
    assert result is not None and result.cut
    left, right = result.clip, None
    clips = sorted(project.tracks[0].clips, key=lambda clip: clip.timeline_start)
    left, right = clips[0], clips[1]
    assert (left.angle_id, right.angle_id) == ("angle-1", "angle-2")
    for local in (0.0, 0.7, 1.4, 2.0, 2.45):
        assert left.time_map.source_time(local) == pytest.approx(reference.source_time(local), abs=1e-6)
    for local in (0.0, 0.5, 1.0, 2.0, 3.0):
        assert right.time_map.source_time(local) == pytest.approx(reference.source_time(2.5 + local), abs=1e-6)
    assert left.duration + right.duration == pytest.approx(reference.duration, abs=1e-6)


# ---------------------------------------------------------------------------
# Suivi et stabilisation : les données sont en temps source, le clip lié lit le mapping
# ---------------------------------------------------------------------------


def _follower_motion(project, follower_id):
    clip = _clip(project, follower_id)
    return _positions(project, follower_id, range(int(round(clip.duration * TRACK_FPS))))


def test_a_follower_moves_with_the_source_time_of_a_ramped_clip():
    project = tracked_project()
    follower = _follower(project)
    ramp(project, "v", (0.0, 1.0), (0.4, 1.0), (0.8, 0.25))
    positions = _positions(project, follower.id, _frames(follower))
    source = _clip(project, "v")
    for index in (0, 5, 10, 20, 30):
        position = source.time_map.source_time(index / TRACK_FPS) * TRACK_FPS             # l'image source montrée à cet instant
        expected = (_point(position)[0] - _point(0)[0], _point(position)[1] - _point(0)[1])
        got = (positions[index][0] - positions[0][0], positions[index][1] - positions[0][1])
        assert math.dist(got, expected) < 0.4, (index, got, expected)


@pytest.mark.parametrize("cut", [0.2, 0.55, 1.0])
def test_cutting_a_ramped_source_never_changes_what_its_follower_shows(cut):
    project = tracked_project()
    follower = _follower(project)
    ramp(project, "v", (0.0, 1.0), (0.4, 1.0), (0.8, 0.25))
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)
    assert max(abs(position[0] - before[0][0]) for position in before) > 20.0
    cut_clip(project, "v", cut)
    _assert_same_motion(before, _positions(project, follower.id, frames), tolerance=0.3)


# ---------------------------------------------------------------------------
# Sous-titres d'une séquence imbriquée remappée
# ---------------------------------------------------------------------------


def test_the_subtitles_of_a_ramped_nested_sequence_are_lifted_through_the_time_map():
    project = _two_level_project()
    inner = project.get_sequence("intro")
    inner.tracks.append(Track("S1", "S1", "subtitle", clips=[Clip("s", "red", "S1", 1.0, 0.0, 2.0, text="Bonjour")]))
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    ramp(project, nested.id, (0.0, 1.0), (1.0, 0.5), (3.0, 2.0))
    cues = build_render_plan(project).subtitle_cues
    assert [cue.text for cue in cues] == ["Bonjour"]
    # La phrase occupe l'intervalle de source [1 s ; 3 s] de l'enfant : à l'écran, celui que le mapping y fait correspondre.
    (start, end), = nested.time_map.timeline_intervals_of(1.0, 3.0)[:1]
    assert cues[0].start == pytest.approx(nested.timeline_start + start, abs=1e-3)
    assert cues[0].end == pytest.approx(nested.timeline_start + end, abs=1e-3)
    # Ce n'est pas l'identité : la rampe ralentit puis accélère, la phrase ne tombe plus sur [1 s ; 3 s] ni ne dure 2 s.
    assert abs(cues[0].start - (nested.timeline_start + 1.0)) > 0.2 and abs((cues[0].end - cues[0].start) - 2.0) > 0.2


def test_the_find_helper_sees_the_ramped_clip():
    project = _two_level_project()
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    ramp(project, nested.id, (0.0, 1.0), (1.0, 0.5))
    assert find_clip(project, nested.id).has_speed_curve


# ---------------------------------------------------------------------------
# Coller le temps : les mêmes refus que le réglage direct
# ---------------------------------------------------------------------------


def test_pasting_an_interpolating_time_onto_a_nested_clip_is_refused_like_setting_it_directly():
    from core.time_presets import copy_time, paste_time
    from core.time_remapping import TimeInterpolation, TimeRemapping

    project = _two_level_project()
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    plain = project.get_sequence("intro").tracks[0].clips[0]
    plain.time_remapping = TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING)
    snapshot = copy_time(plain)
    before = nested.time_remapping
    with pytest.raises(ValueError, match="séquence imbriquée"):
        paste_time(project, nested.id, snapshot)
    assert nested.time_remapping == before and not nested.animation                      # rien n'a été touché
    plain.time_remapping = TimeRemapping(speed=0.5)                                      # l'échantillonnage se colle partout
    paste_time(project, nested.id, copy_time(plain))
    assert nested.time_remapping.speed == 0.5
