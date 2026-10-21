"""Recadrage suivi : un plan 16:9 recadré en 9:16 dont la fenêtre « remplir » garde un point suivi au centre.

La géométrie est vérifiée sur le pan évalué (images-clés simplifiées comprises) et sur l'image rendue par le vrai FFmpeg
(graphe de l'export) : un carré blanc qui traverse le plan reste au milieu du cadre vertical.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from core.animation import InterpolationType
from core.follow_reframe import ReframeRefused, apply_follow_reframe, follow_pan_keyframes
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import TimeRemapping
from core.tracking_model import ClipTracking, Sample, SampleStatus, TrackData, Tracker
from core.tracking_motion import cover_size, pan_offset
from core.visual_effects import ClipTransform, TransformKeyframe, evaluate_transform
from render_probe import needs_ffmpeg, render_frame

FPS = 25
SRC_W, SRC_H = 320, 180          # média 16:9
SEQ_W, SEQ_H = 180, 320          # cadre vertical
COVER_W, _COVER_H = cover_size(SRC_W, SRC_H, SEQ_W, SEQ_H)
EXCESS = COVER_W - SEQ_W
SCALE = COVER_W / SRC_W


def _tracker(position, *, seconds: float = 2.0, size=(SRC_W, SRC_H)) -> Tracker:
    """Tracker dont l'échantillon de l'image ``i`` est ``position(i / FPS)`` (pixels du média)."""
    samples = {
        i: Sample(*position(i / FPS), 1.0, SampleStatus.TRACKED) for i in range(int(seconds * FPS) + 1)
    }
    return Tracker(id="sujet", name="Sujet", data=TrackData.from_samples(FPS, samples, source_size=size))


def _clip(**fields) -> Clip:
    values = {"id": "c", "asset_id": "a", "track_id": "V1", "timeline_start": 0.0, "source_in": 0.0, "source_out": 2.0}
    return Clip(**{**values, **fields})


def _pan(clip: Clip, local_time: float, name: str = "pan_x") -> float:
    return getattr(evaluate_transform(clip.transform, clip.transform_keyframes, local_time), name)


def _subject_in_frame(subject_x: float, pan: float) -> float:
    """Abscisse du sujet dans le cadre : sa place dans le média agrandi, moins le bord gauche de la fenêtre."""
    return subject_x * SCALE - pan_offset(EXCESS, pan)


def _crossing(t: float) -> tuple[float, float]:
    return (50.0 + 110.0 * t, 90.0)


# --- Géométrie --------------------------------------------------------------------------------------------------------


def test_the_subject_stays_in_the_centre_of_the_vertical_frame():
    clip = _clip()
    apply_follow_reframe(clip, [_tracker(_crossing)], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS,
                         sigma=0.0)
    assert clip.transform.fill is True
    for t in (0.3, 0.7, 1.0, 1.5, 1.9):
        assert _subject_in_frame(_crossing(t)[0], _pan(clip, t)) == pytest.approx(SEQ_W / 2, abs=1.0), t
    assert not any(frame.property_name == "pan_y" for frame in clip.transform_keyframes)   # pas de marge en hauteur
    assert all(frame.interpolation == InterpolationType.LINEAR for frame in clip.transform_keyframes)
    assert len(clip.transform_keyframes) <= 4                     # mouvement linéaire : quelques images-clés suffisent


def test_the_window_never_leaves_the_media():
    edge = _tracker(lambda t: (4.0 + 10.0 * t, 90.0))           # le sujet longe le bord gauche
    frames = follow_pan_keyframes(_clip(), [edge], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS)
    assert frames and all(frame.value == -1.0 for frame in frames)


def test_smoothing_calms_a_shaky_subject():
    shaky = _tracker(lambda t: (160.0 + (12.0 if round(t * FPS) % 2 else -12.0), 90.0))

    def steps(sigma: float) -> float:
        clip = _clip()
        apply_follow_reframe(clip, [shaky], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS,
                             sigma=sigma)
        pans = [_pan(clip, i / FPS) for i in range(2 * FPS)]
        return max(abs(b - a) for a, b in zip(pans, pans[1:]))

    assert steps(0.0) > 0.1
    assert steps(12.0) < steps(0.0) / 20


def test_the_source_time_shown_by_the_clip_is_followed():
    tracker = _tracker(_crossing, seconds=4.0)
    late = _clip(source_in=1.0, source_out=3.0)                   # le plan commence à 1 s du média
    apply_follow_reframe(late, [tracker], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS, sigma=0.0)
    assert _subject_in_frame(_crossing(1.5)[0], _pan(late, 0.5)) == pytest.approx(SEQ_W / 2, abs=1.0)
    backwards = _clip(source_in=0.0, source_out=2.0)
    backwards.time_remapping = TimeRemapping(reverse=True)       # lu à rebours : le sujet repart vers la gauche
    apply_follow_reframe(backwards, [tracker], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS,
                         sigma=0.0)
    assert _pan(backwards, 0.2) > _pan(backwards, 1.8)


def test_a_taller_media_follows_on_the_vertical_axis():
    rising = _tracker(lambda t: (90.0, 260.0 - 100.0 * t), size=(SEQ_W, SEQ_H))
    frames = follow_pan_keyframes(_clip(), [rising], media_size=(SEQ_W, SEQ_H), canvas_size=(SRC_W, SRC_H), fps=FPS,
                                  sigma=0.0)
    assert {frame.property_name for frame in frames} == {"pan_y"}
    assert frames[0].value > frames[-1].value                   # le sujet monte : la fenêtre monte avec lui


def test_positions_measured_on_another_resolution_follow_the_scale():
    half = _tracker(lambda t: (_crossing(t)[0] / 2, 45.0), size=(SRC_W // 2, SRC_H // 2))
    clip = _clip()
    apply_follow_reframe(clip, [half], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS, sigma=0.0)
    assert _subject_in_frame(_crossing(1.0)[0], _pan(clip, 1.0)) == pytest.approx(SEQ_W / 2, abs=1.0)


def test_a_clip_already_shaped_like_the_frame_has_no_margin():
    with pytest.raises(ReframeRefused) as refused:
        follow_pan_keyframes(_clip(), [_tracker(_crossing)], media_size=(SEQ_W, SEQ_H), canvas_size=(SEQ_W, SEQ_H),
                             fps=FPS)
    assert refused.value.reason == "no_margin"


def test_an_unanalysed_tracker_is_refused_and_nothing_changes():
    clip = _clip()
    clip.transform_keyframes = [TransformKeyframe("pan_x", 0.0, 0.5)]
    with pytest.raises(ReframeRefused) as refused:
        apply_follow_reframe(clip, [Tracker(id="vide")], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H),
                             fps=FPS)
    assert refused.value.reason == "no_positions"
    assert clip.transform.fill is False and [frame.value for frame in clip.transform_keyframes] == [0.5]


def test_only_the_pan_animation_is_replaced():
    clip = _clip()
    clip.transform = ClipTransform(scale=1.2)
    clip.transform_keyframes = [TransformKeyframe("pan_x", 0.0, 0.9), TransformKeyframe("opacity", 1.0, 0.5)]
    apply_follow_reframe(clip, [_tracker(_crossing)], media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H), fps=FPS)
    assert clip.transform.scale == pytest.approx(1.2) and clip.transform.fill is True
    assert [frame.value for frame in clip.transform_keyframes if frame.property_name == "opacity"] == [0.5]
    assert 0.9 not in [frame.value for frame in clip.transform_keyframes if frame.property_name == "pan_x"]
    keys = [(frame.property_name, frame.time_seconds) for frame in clip.transform_keyframes]
    assert keys == sorted(keys)


# --- Rendu réel -------------------------------------------------------------------------------------------------------


def _crossing_square(tmp_path):
    """Média 16:9 noir où un carré blanc de 20 px traverse le plan : centre en ``_crossing(t)``."""
    path = tmp_path / "carre.mp4"
    graph = (f"color=c=black:s={SRC_W}x{SRC_H}:r={FPS}:d=2[fond];color=c=white:s=20x20:r={FPS}:d=2[carre];"
             "[fond][carre]overlay=x='40+110*t':y=80")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_complex", graph, "-c:v", "libx264",
                    "-qp", "0", "-pix_fmt", "yuv444p", str(path)], check=True, timeout=60)
    return path


@needs_ffmpeg
def test_the_export_keeps_the_tracked_square_in_the_middle_of_the_vertical_frame(tmp_path):
    asset = MediaAsset(id="a", path=str(_crossing_square(tmp_path)), name="carré", duration=2.0, width=SRC_W,
                       height=SRC_H, fps=float(FPS), media_type="video")
    clip = _clip()
    clip.tracking = ClipTracking(trackers=(_tracker(_crossing),))
    apply_follow_reframe(clip, clip.tracking.trackers, media_size=(SRC_W, SRC_H), canvas_size=(SEQ_W, SEQ_H),
                         fps=FPS, sigma=0.0)
    project = Project(name="9:16", width=SEQ_W, height=SEQ_H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    plan = build_render_plan(project)
    for t in (0.4, 1.0, 1.6):
        image = render_frame(plan, SEQ_W, SEQ_H, t)
        columns = np.nonzero(image.mean(axis=2).max(axis=0) > 128)[0]
        assert columns.size, f"carré absent du cadre à {t} s"
        assert (columns[0] + columns[-1]) / 2 == pytest.approx(SEQ_W / 2, abs=3.0), t
