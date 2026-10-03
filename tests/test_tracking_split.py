"""Couper la source d'un tracking : les clips liés continuent de la suivre.

Principe testé : **une coupe ne change rien à ce que montrent les clips liés**, ni tout de suite, ni après une
modification ultérieure. Les données de tracking sont en temps source et partagées par les deux moitiés ; la liaison
suit, à chaque instant de la timeline, la moitié qui couvre cet instant (``TrackLink.continuation_ids``).

Les données sont synthétiques (pas de FFmpeg) : le mouvement est connu exactement. Les vérifications de rendu réel
(aperçu et export) sont en fin de fichier.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from core import tracking_ops as ops
from core.compositing import Compositing, Mask, evaluate_mask_at
from core.edit_history import ProjectHistory
from core.graphics import add_graphic_clip
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import SequenceError, create_sequence_from_selection, duplicate_sequence
from core.time_remapping import FreezeFrameMode, TimeRemapping
from core.timeline_operations import cut_clip, delete_clip
from core.tracking_bindings import TrackingContext, effective_clip_state, link_issues
from core.tracking_model import (
    BorderMode,
    ClipTracking,
    Sample,
    SampleStatus,
    Smoothing,
    TrackData,
    Tracker,
    TrackLink,
    TrackTarget,
)
from core.visual_effects import evaluate_transform

W, H, FPS = 320, 180, 30.0
FRAMES = 40
SOURCE_DURATION = FRAMES / FPS


def _point(i: int) -> tuple[float, float]:
    """Mouvement du point suivi : +2 px / image en x, +1 px / image en y."""
    return 80.0 + 2.0 * i, 60.0 + 1.0 * i


def _curved_point(i: int) -> tuple[float, float]:
    """Mouvement **dissymétrique** (accélère) : les deux moitiés d'un plan n'ont pas les mêmes écarts de lissage."""
    return 80.0 + 0.12 * i * i, 60.0 + 0.5 * i


def _tracker(first: int = 0, last: int = FRAMES - 1, motion=_point) -> Tracker:
    samples = {i: Sample(*motion(i), 1.0, SampleStatus.TRACKED) for i in range(first, last + 1)}
    return Tracker(id="t1", name="Tracker 1", data=TrackData.from_samples(FPS, samples, source_size=(W, H)))


def _asset(asset_id: str = "m") -> MediaAsset:
    return MediaAsset(asset_id, f"/absent/{asset_id}.mp4", asset_id, SOURCE_DURATION, W, H, FPS, "video")


def _project(*, first: int = 0, last: int = FRAMES - 1, remap: TimeRemapping | None = None, motion=_point) -> Project:
    """Un clip vidéo « v » (sur V1) qui porte un tracker, sans fichier média (la logique n'en a pas besoin)."""
    project = Project(name="Découpe", width=W, height=H, fps=FPS)
    project.media_assets.append(_asset())
    source = Clip("v", "m", "V1", 0.0, 0.0, SOURCE_DURATION)
    if remap is not None:
        source.time_remapping = remap
    source.tracking = ClipTracking(trackers=(_tracker(first, last, motion),))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[source]))
    return project


def _clip(project: Project, clip_id: str) -> Clip:
    return ops.find_clip_and_track(project, clip_id)[0]


def _follower(project: Project, *, start: float = 0.0, duration: float | None = None, **link_options) -> Clip:
    """Un calque de forme qui suit le tracker de « v » (mouvement ajouté à sa position)."""
    source = _clip(project, "v")
    shape = add_graphic_clip(
        project, "shape", timeline_start=start, duration=duration if duration is not None else source.duration,
    )
    ops.add_link(project, shape.id, "v", ["t1"], timeline_time=0.0, **link_options)
    return shape


def _positions(project: Project, clip_id: str, frames) -> list[tuple[float, float]]:
    """Position (pixels) du clip à chaque image de ``frames``, **telle que rendue** (liaisons appliquées)."""
    clip = _clip(project, clip_id)
    state = effective_clip_state(clip, TrackingContext(project))
    result = []
    for frame in frames:
        values = evaluate_transform(clip.transform, list(state.transform_keyframes), frame / FPS, clip.duration)
        result.append((values.position_x * W, values.position_y * H))
    return result


def _assert_same_motion(before, after, *, tolerance: float = 0.05) -> None:
    worst = max(math.dist(a, b) for a, b in zip(before, after, strict=True))
    assert worst <= tolerance, f"le clip lié ne montre plus la même chose après la coupe (écart {worst:.3f} px)"


def _frames(clip: Clip) -> range:
    return range(int(round(clip.duration * FPS)))


# --- Le principe : une coupe ne change rien à ce que suit un clip lié --------------------------------------


@pytest.mark.parametrize(
    "cut_frame, first, last",
    [
        (3, 10, 30),     # avant le premier point suivi (le suivi commence à l'image 10)
        (20, 10, 30),    # au milieu des points suivis
        (36, 10, 30),    # après le dernier point suivi (le suivi s'arrête à l'image 30)
        (20, 0, FRAMES - 1),
    ],
    ids=["avant-le-premier-point", "au-milieu", "apres-le-dernier-point", "suivi-complet"],
)
def test_a_cut_never_changes_what_a_follower_shows(cut_frame, first, last):
    project = _project(first=first, last=last)
    follower = _follower(project)
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)
    assert before[-1] != before[0] or last < 5                       # le test suit bien un mouvement

    cut_clip(project, "v", cut_frame / FPS)

    _assert_same_motion(before, _positions(project, follower.id, frames))


@pytest.mark.parametrize(
    "remap",
    [TimeRemapping(speed=2.0), TimeRemapping(speed=0.5), TimeRemapping(reverse=True), TimeRemapping(speed=2.0, reverse=True)],
    ids=["x2", "x0.5", "inverse", "x2-inverse"],
)
def test_the_follower_stays_continuous_when_the_source_is_remapped(remap):
    project = _project(remap=remap)
    follower = _follower(project)
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)
    assert max(abs(a[0] - before[0][0]) for a in before) > 20.0       # le mouvement est bien suivi

    cut_clip(project, "v", _clip(project, "v").duration / 2.0)

    _assert_same_motion(before, _positions(project, follower.id, frames))


def test_the_follower_of_a_frozen_source_stays_put_on_both_sides_of_the_cut():
    freeze = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=0.5, freeze_duration=1.0)
    project = _project(remap=freeze)
    follower = _follower(project)
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)
    assert max(abs(a[0] - before[0][0]) for a in before) < 0.05      # image figée : le suiveur ne bouge pas

    cut_clip(project, "v", 0.4)

    _assert_same_motion(before, _positions(project, follower.id, frames))


def test_several_cuts_keep_the_order_and_the_motion():
    project = _project()
    follower = _follower(project)
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)

    _left, right = cut_clip(project, "v", 10 / FPS)
    cut_clip(project, right.id, 25 / FPS)           # couper la moitié droite
    cut_clip(project, "v", 5 / FPS)                 # puis la moitié gauche

    link = _clip(project, follower.id).tracking.links[0]
    starts = {c.id: c.timeline_start for t in project.tracks for c in t.clips}
    assert list(link.source_ids) == sorted(link.source_ids, key=starts.__getitem__)    # ordre chronologique
    assert len(link.source_ids) == 4
    _assert_same_motion(before, _positions(project, follower.id, frames))


def test_a_follower_inside_one_half_still_follows_if_it_is_extended_later():
    """La coupe est neutre aussi pour les modifications suivantes : étendre le clip lié après la coupe suit toujours."""
    cut = 20 / FPS
    reference = _project()
    reference_follower = _follower(reference, duration=10 / FPS)       # entièrement dans la future moitié gauche
    _clip(reference, reference_follower.id).source_out = 30 / FPS
    wanted = _positions(reference, reference_follower.id, range(30))

    project = _project()
    follower = _follower(project, duration=10 / FPS)
    cut_clip(project, "v", cut)
    _clip(project, follower.id).source_out = 30 / FPS                  # l'étend au-delà de la coupe

    _assert_same_motion(wanted, _positions(project, follower.id, range(30)))


def test_the_cut_is_one_operation_for_history_and_undo_restores_the_old_link():
    project = _project()
    follower = _follower(project)
    history = ProjectHistory()
    history.reset(project)

    cut_clip(project, "v", 20 / FPS)
    history.record(project, "Couper le clip")
    assert _clip(project, follower.id).tracking.links[0].continuation_ids

    restored = history.undo()

    assert _clip(restored, follower.id).tracking.links[0].continuation_ids == ()
    assert len(restored.tracks[0].clips) == 1


# --- Les relations : référence ajustée, jamais figée en silence ---------------------------------------------


def test_the_link_follows_both_halves_after_the_cut():
    project = _project()
    follower = _follower(project)
    left, right = cut_clip(project, "v", 20 / FPS)

    link = _clip(project, follower.id).tracking.links[0]

    assert link.source_clip_id == left.id and link.continuation_ids == (right.id,)
    assert ops.tracking_dependents(project, left.id) == [follower.id]
    assert ops.tracking_dependents(project, right.id) == [follower.id]
    assert link_issues(TrackingContext(project), _clip(project, follower.id), link) == ()


def test_both_halves_share_the_tracker_data_without_copying_it():
    project = _project()
    left, right = cut_clip(project, "v", 20 / FPS)
    assert left.tracking.tracker("t1").data is right.tracking.tracker("t1").data


def test_a_self_link_is_untouched_by_the_cut():
    project = _project()
    ops.add_link(project, "v", "v", ["t1"], target=TrackTarget.ANCHOR, timeline_time=0.0)
    left, right = cut_clip(project, "v", 20 / FPS)
    for half in (left, right):
        link = half.tracking.links[0]
        assert link.source_clip_id == "" and link.continuation_ids == ()


def test_deleting_the_right_half_reports_the_gap_instead_of_freezing_silently():
    project = _project()
    follower = _follower(project)
    _left, right = cut_clip(project, "v", 20 / FPS)

    delete_clip(project, right.id)

    clip = _clip(project, follower.id)
    link = clip.tracking.links[0]
    assert link.continuation_ids == ()                                # plus de référence vers un clip disparu
    assert link_issues(TrackingContext(project), clip, link) == ("source_gap",)
    assert any("tracking.link.source_gap" in warning for warning in build_render_plan(project).warnings)


def test_deleting_the_left_half_promotes_the_remaining_part_and_reports_the_gap():
    project = _project()
    follower = _follower(project)
    left, right = cut_clip(project, "v", 20 / FPS)

    delete_clip(project, left.id)

    clip = _clip(project, follower.id)
    link = clip.tracking.links[0]
    assert link.source_clip_id == right.id and link.continuation_ids == ()
    assert link_issues(TrackingContext(project), clip, link) == ("source_gap",)
    # La moitié qui reste est toujours suivie : sur sa plage le mouvement est celui d'avant la coupe.
    wanted = [_point(i) for i in range(20, FRAMES)]
    moved = _positions(project, follower.id, range(20, FRAMES))
    assert [round(b[0] - a[0], 1) for a, b in zip(moved, moved[1:])] == [pytest.approx(2.0, abs=0.1)] * (FRAMES - 21)
    assert wanted[-1][0] - wanted[0][0] == pytest.approx(moved[-1][0] - moved[0][0], abs=0.1)


def test_deleting_the_only_source_is_announced_and_never_silently_erased():
    project = _project()
    follower = _follower(project)

    assert ops.release_source(project, "v") == [follower.id]           # ce que l'interface annonce
    delete_clip(project, "v")

    clip = _clip(project, follower.id)
    link = clip.tracking.links[0]
    assert link.source_clip_id == "v"                                 # la liaison reste, signalée
    assert link_issues(TrackingContext(project), clip, link) == ("missing_source",)
    assert any("tracking.link.missing_source" in warning for warning in build_render_plan(project).warnings)


def test_overlapping_halves_are_reported_as_ambiguous_and_the_first_one_wins():
    project = _project()
    follower = _follower(project)
    left, right = cut_clip(project, "v", 20 / FPS)
    right.timeline_start = 10 / FPS                                    # la moitié droite recouvre la gauche

    clip = _clip(project, follower.id)
    issues = link_issues(TrackingContext(project), clip, clip.tracking.links[0])

    assert "ambiguous_source" in issues
    during_overlap = _positions(project, follower.id, [15])[0]         # image 15 : les deux moitiés couvrent l'instant
    assert during_overlap[0] - _positions(project, follower.id, [0])[0][0] == pytest.approx(30.0, abs=0.1)  # la gauche


def test_removing_a_tracker_from_one_half_keeps_the_link_to_the_other():
    project = _project()
    follower = _follower(project)
    left, right = cut_clip(project, "v", 20 / FPS)

    ops.remove_tracker(project, left.id, "t1")

    link = _clip(project, follower.id).tracking.links[0]
    assert link.tracker_ids == ("t1",)                                 # la moitié droite porte encore ce tracker


# --- Bake ---------------------------------------------------------------------------------------------------


def test_baking_after_a_cut_freezes_the_motion_of_both_halves():
    project = _project()
    follower = _follower(project)
    frames = _frames(follower)
    before = _positions(project, follower.id, frames)
    cut_clip(project, "v", 20 / FPS)
    link = _clip(project, follower.id).tracking.links[0]

    count = ops.bake_link(project, follower.id, link.id)

    assert count >= 2 and _clip(project, follower.id).tracking is None
    _assert_same_motion(before, _positions(project, follower.id, frames))
    # Figé : changer le tracker de la moitié droite n'a plus d'effet.
    right = _clip(project, "v-split-2")
    right.tracking = right.tracking.with_tracker(replace(right.tracking.tracker("t1"), data=_tracker(motion=_curved_point).data))
    _assert_same_motion(before, _positions(project, follower.id, frames))


# --- Masque -------------------------------------------------------------------------------------------------


def _mask_positions(project: Project, clip_id: str, mask: Mask, frames) -> list[tuple[float, float]]:
    from core.animation_targets import animation_curves

    clip = _clip(project, clip_id)
    state = effective_clip_state(clip, TrackingContext(project))
    curves = animation_curves(type("Vue", (), {"animation": state.animation})())
    values = [evaluate_mask_at(mask, curves, frame / FPS) for frame in frames]
    return [(v.position_x * W, v.position_y * H) for v in values]


def test_a_mask_of_another_clip_keeps_following_across_the_cut():
    project = _project()
    project.media_assets.append(_asset("m2"))
    mask = Mask(position_x=0.5, position_y=0.5, width=0.1, height=0.1)
    other = Clip("f", "m2", "V2", 0.0, 0.0, SOURCE_DURATION, compositing=Compositing(masks=(mask,)))
    project.tracks.insert(0, Track("V2", "V2", "video", clips=[other]))
    ops.add_link(project, "f", "v", ["t1"], target=TrackTarget.MASK, mask_id=mask.id, timeline_time=0.0)
    frames = range(FRAMES)
    before = _mask_positions(project, "f", mask, frames)
    assert before[-1][0] - before[0][0] > 50.0

    cut_clip(project, "v", 20 / FPS)

    _assert_same_motion(before, _mask_positions(project, "f", mask, frames))


def test_a_mask_following_its_own_clip_stays_continuous_across_the_cut():
    project = _project()
    mask = Mask(position_x=80 / W, position_y=60 / H, width=0.1, height=0.1)
    source = _clip(project, "v")
    source.compositing = Compositing(masks=(mask,))
    ops.add_link(project, "v", "v", ["t1"], target=TrackTarget.MASK, mask_id=mask.id, timeline_time=0.0)
    before = _mask_positions(project, "v", mask, range(FRAMES))

    left, right = cut_clip(project, "v", 20 / FPS)

    after = _mask_positions(project, left.id, mask, range(20)) + _mask_positions(project, right.id, mask, range(20))
    _assert_same_motion(before, after)


# --- Stabilisation ------------------------------------------------------------------------------------------


def _stabilized_project() -> Project:
    project = _project(motion=_curved_point)
    ops.set_stabilization(
        project, "v", tracker_ids=("t1",), smoothing=Smoothing.HIGH, borders=BorderMode.ZOOM, reference_index=0,
    )
    return project


def test_both_halves_of_a_stabilized_shot_keep_the_same_zoom_and_the_same_image():
    project = _stabilized_project()
    source = _clip(project, "v")
    state = effective_clip_state(source, TrackingContext(project))
    assert state.zoom > 1.05                                           # le plan est bien agrandi
    before = [
        evaluate_transform(source.transform, list(state.transform_keyframes), f / FPS, source.duration)
        for f in range(FRAMES)
    ]

    left, right = cut_clip(project, "v", 20 / FPS)

    after = []
    for half in (left, right):
        half_state = effective_clip_state(half, TrackingContext(project))
        assert half_state.zoom == pytest.approx(state.zoom)            # même agrandissement : pas de saut à la coupe
        after += [
            evaluate_transform(half.transform, list(half_state.transform_keyframes), f / FPS, half.duration)
            for f in range(20)
        ]
    for a, b in zip(before, after, strict=True):
        assert (b.position_x - a.position_x) * W == pytest.approx(0.0, abs=0.1)
        assert (b.position_y - a.position_y) * H == pytest.approx(0.0, abs=0.1)
        assert b.scale == pytest.approx(a.scale, rel=1e-4)


def test_a_black_border_stabilization_does_not_get_a_shared_range():
    project = _project()
    ops.set_stabilization(project, "v", tracker_ids=("t1",), smoothing=Smoothing.HIGH, borders=BorderMode.BLACK)
    left, right = cut_clip(project, "v", 20 / FPS)
    assert left.tracking.stabilization.shared_range is None and right.tracking.stabilization.shared_range is None


def test_the_shared_range_never_shrinks_when_a_half_is_cut_again():
    project = _stabilized_project()
    left, right = cut_clip(project, "v", 20 / FPS)
    first = left.tracking.stabilization.shared_range
    cut_clip(project, right.id, 30 / FPS)
    assert _clip(project, right.id).tracking.stabilization.shared_range == first == (0, FRAMES - 1)


# --- Séquences imbriquées -----------------------------------------------------------------------------------


def _nested_project():
    """Source « v » et suiveur dans une séquence enfant, imbriquée dans la séquence parente."""
    project = _project()
    follower = _follower(project)
    result = create_sequence_from_selection(project, ["v", follower.id], name="Enfant")
    return project, follower, result


def test_cutting_the_source_inside_a_nested_sequence_keeps_the_follower_moving():
    project, follower, nested = _nested_project()
    frames = _frames(follower)
    before = _positions_in(project, nested.sequence, follower.id, frames)

    parent_id = project.active_sequence_id
    project.active_sequence_id = nested.sequence.id                    # on édite l'intérieur de la séquence
    cut_clip(project, "v", 20 / FPS)
    project.active_sequence_id = parent_id

    _assert_same_motion(before, _positions_in(project, nested.sequence, follower.id, frames))
    # Dans le plan de la séquence parente, le sous-plan lit les mêmes images-clés.
    plan = build_render_plan(project)
    layer = next(
        item for entry in plan.nested_sequences for item in entry.plan.graphics_layers if item.clip_id == follower.id
    )
    values = evaluate_transform(layer.transform, list(layer.transform_keyframes), 25 / FPS, follower.duration)
    assert values.position_x * W - before[0][0] == pytest.approx(_point(25)[0] - _point(0)[0], abs=0.3)


def _positions_in(project: Project, sequence, clip_id: str, frames) -> list[tuple[float, float]]:
    clip = next(c for t in sequence.tracks for c in t.clips if c.id == clip_id)
    state = effective_clip_state(clip, TrackingContext(project, sequence))
    result = []
    for frame in frames:
        values = evaluate_transform(clip.transform, list(state.transform_keyframes), frame / FPS, clip.duration)
        result.append((values.position_x * W, values.position_y * H))
    return result


def test_nesting_only_the_half_a_follower_sees_is_allowed_and_prunes_the_other_half():
    project = _project()
    follower = _follower(project, duration=10 / FPS)                   # ne voit que la moitié gauche
    left, right = cut_clip(project, "v", 20 / FPS)
    assert _clip(project, follower.id).tracking.links[0].continuation_ids == (right.id,)

    result = create_sequence_from_selection(project, [follower.id, left.id], name="Gauche")

    inner = next(c for t in result.sequence.tracks for c in t.clips if c.id == follower.id)
    link = inner.tracking.links[0]
    assert link.source_clip_id == left.id and link.continuation_ids == ()   # plus de référence hors de la séquence
    assert link_issues(TrackingContext(project, result.sequence), inner, link) == ()


def test_nesting_that_would_cut_a_follower_from_a_half_it_sees_is_refused():
    project = _project()
    follower = _follower(project)                                      # voit les deux moitiés
    left, right = cut_clip(project, "v", 20 / FPS)

    with pytest.raises(SequenceError, match="sépare des clips liés"):
        create_sequence_from_selection(project, [follower.id, left.id])

    assert _clip(project, follower.id).tracking.links[0].continuation_ids == (right.id,)   # rien n'a été modifié


def test_duplicating_a_sequence_remaps_every_part_of_the_source():
    project, follower, nested = _nested_project()
    project.active_sequence_id = nested.sequence.id
    left, right = cut_clip(project, "v", 20 / FPS)

    copy = duplicate_sequence(project, nested.sequence.id)

    clips = {c.id for t in copy.tracks for c in t.clips}
    inner_follower = next(c for t in copy.tracks for c in t.clips if c.tracking is not None and c.tracking.links)
    link = inner_follower.tracking.links[0]
    assert set(link.source_ids) <= clips                               # tout pointe dans la copie
    assert not set(link.source_ids) & {left.id, right.id}              # et plus vers l'original
    assert len(link.source_ids) == 2


# --- Sérialisation ------------------------------------------------------------------------------------------


def test_a_project_without_a_cut_keeps_its_exact_link_format():
    link = TrackLink(source_clip_id="v", tracker_ids=("t1",))
    assert "continuation_ids" not in link.to_dict()
    assert TrackLink.from_dict(link.to_dict()) == link


def test_a_link_written_before_the_cut_support_still_loads():
    old = {"id": "l1", "source_clip_id": "v", "tracker_ids": ["t1"], "target": "transform", "mask_id": "",
           "position": True, "rotation": False, "scale": False, "reference_index": 0, "enabled": True}
    link = TrackLink.from_dict(old)
    assert link is not None and link.source_ids == ("v",)


def test_the_chain_and_the_shared_range_survive_a_save_and_a_load(tmp_path: Path):
    project = _stabilized_project()
    follower = _follower(project)
    cut_clip(project, "v", 20 / FPS)
    path = tmp_path / "coupe.kut"

    save_project(project, str(path))
    restored = load_project(str(path))

    link = _clip(restored, follower.id).tracking.links[0]
    assert link == _clip(project, follower.id).tracking.links[0] and len(link.continuation_ids) == 1
    assert _clip(restored, "v").tracking.stabilization.shared_range == (0, FRAMES - 1)


def test_a_malformed_chain_is_cleaned_at_load():
    link = TrackLink.from_dict({"source_clip_id": "v", "tracker_ids": ["t1"],
                                "continuation_ids": ["v", "b", "b", "", 3]})
    assert link is not None and link.continuation_ids == ("b", "3")
    own = TrackLink(source_clip_id="", tracker_ids=("t1",), continuation_ids=("x",))
    assert own.continuation_ids == () and own.source_ids == ()


# --- Aperçu et export : le rendu réel -----------------------------------------------------------------------

np = pytest.importorskip("numpy")


def _real_media(tmp_path_factory) -> Path:
    from tests.tracking_media import moving_points_video

    root = tmp_path_factory.mktemp("tracking-split-media")
    return moving_points_video(
        root / "translation.mkv", width=W, height=H, fps=FPS, count=FRAMES,
        points_at=lambda i: [_point(i)],
    )


@pytest.fixture(scope="module")
def real_media(tmp_path_factory):
    from tests.tracking_media import HAS_FFMPEG

    if not HAS_FFMPEG:
        pytest.skip("FFmpeg absent : le rendu réel de la coupe n'est pas vérifié")
    return _real_media(tmp_path_factory)


def _export_blob(project: Project, t: float, tmp_path: Path):
    from core.export_engine import ExportEngine

    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, int(FPS), None)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=gray[probe];[{audio}]anullsink"
    out = tmp_path / f"frame-{t}.raw"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += ["-i", path]
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", "-f", "rawvideo", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    frame = np.frombuffer(out.read_bytes(), dtype=np.uint8).reshape(H, W)
    weights = np.where(frame > 100, frame.astype(np.float64), 0.0)
    ys, xs = np.mgrid[0:H, 0:W]
    return float((xs * weights).sum() / weights.sum()), float((ys * weights).sum() / weights.sum())


@pytest.mark.usefixtures("qapp")
def test_preview_and_export_follow_the_source_after_the_cut(real_media, tmp_path):
    """Le calque lié est à la même place après la coupe, dans l'aperçu (Qt) comme à l'export (FFmpeg)."""
    from core.mograph_raster import MographRenderer, scene_for_plan
    from core.graphics import update_graphic

    project = _project()
    project.media_assets[0] = replace(project.media_assets[0], path=str(real_media))
    project.tracks[0].visible = False                                  # seul le calque lié est visible
    shape = add_graphic_clip(project, "shape", timeline_start=0.0, duration=SOURCE_DURATION, shape="rectangle")
    update_graphic(shape, "width", 20)
    update_graphic(shape, "height", 20)
    update_graphic(shape, "fill_color", "#FFFFFF")
    ops.add_link(project, shape.id, "v", ["t1"], timeline_time=0.0)
    cut_clip(project, "v", 20 / FPS)

    t = 25 / FPS                                                       # dans la moitié droite
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    image = MographRenderer(scene, W, H, fps=FPS, quality="export").render(scene.top_level(), t)
    alpha = np.array([[image.pixelColor(x, y).alpha() for x in range(W)] for y in range(H)], dtype=np.uint8)
    weights = np.where(alpha > 100, alpha.astype(np.float64), 0.0)
    ys, xs = np.mgrid[0:H, 0:W]
    preview = (float((xs * weights).sum() / weights.sum()), float((ys * weights).sum() / weights.sum()))
    exported = _export_blob(project, t, tmp_path)

    assert math.dist(preview, exported) < 0.6
    assert preview[0] - W / 2 == pytest.approx(2.0 * 25, abs=0.6)      # +2 px par image, suivi après la coupe
