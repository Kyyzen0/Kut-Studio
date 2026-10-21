"""Effets d'impact : zoom d'impact, secousse, flash blanc, sur chaque cut."""

from __future__ import annotations

import pytest

from core.impact_fx import add_flash, apply_camera_shake, apply_impact_on_cuts, apply_impact_zoom, cut_times
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import evaluate_transform
from render_probe import lavfi_video, needs_ffmpeg, render_frame


def _project(tmp_path=None, path="/nonexistent/m.mp4", *starts):
    asset = MediaAsset(id="a", path=path, name="m", duration=10.0, width=64, height=64, fps=25.0, media_type="video")
    clips = [Clip(id=f"c{i}", asset_id="a", track_id="V1", timeline_start=s, source_in=0.0, source_out=1.0)
             for i, s in enumerate(starts or (0.0, 1.0, 2.0, 3.5))]
    return Project(name="i", width=200, height=200, fps=25.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=clips)])


def _at(clip, t):
    return evaluate_transform(clip.transform, clip.transform_keyframes, t, clip.duration)


def test_cuts_are_the_starts_of_clips_that_follow_another():
    assert cut_times(_project(), "V1") == [1.0, 2.0]                 # 3,5 s suit un trou : pas un cut


def test_an_impact_zoom_settles_on_the_clip_scale():
    clip = _project().tracks[0].clips[1]
    apply_impact_zoom(clip, strength=0.14)
    assert _at(clip, 0.0).scale == pytest.approx(1.14) and _at(clip, 0.3).scale == pytest.approx(1.0)
    assert _at(clip, 0.1).scale > _at(clip, 0.2).scale


def test_a_shake_is_reproducible_and_returns_to_rest():
    first, second = _project().tracks[0].clips[:2]
    second.id = first.id
    apply_camera_shake(first)
    apply_camera_shake(second)
    def values(clip):
        return [(k.property_name, k.time_seconds, k.value) for k in clip.transform_keyframes]

    assert values(first) == values(second)                          # même identifiant : même secousse
    rest = _at(first, 0.45)
    assert (rest.position_x, rest.position_y, rest.rotation) == pytest.approx((0.0, 0.0, 0.0))
    assert max(abs(_at(first, t).position_x) for t in (0.05, 0.1, 0.15)) > 0.0


def test_impact_on_every_cut_zooms_the_incoming_shots_and_adds_flashes():
    project = _project()
    assert apply_impact_on_cuts(project, "V1", flash=True) == 2
    clips = {clip.id: clip for clip in project.tracks[0].clips}
    assert not clips["c0"].transform_keyframes and clips["c1"].transform_keyframes and clips["c2"].transform_keyframes
    flashes = [c for t in project.tracks if t.type == "graphics" for c in t.clips]
    assert sorted(c.timeline_start for c in flashes) == [1.0, 2.0]
    assert all(c.graphic.light_kind == "flash" for c in flashes)


@needs_ffmpeg
def test_an_exported_flash_fades_from_white(tmp_path):
    media = lavfi_video(tmp_path / "b.mp4", "color=c=0x101820", size=(64, 64), seconds=2.0)
    project = _project(tmp_path, str(media), 0.0)
    project.tracks[0].clips[0].source_out = 2.0
    add_flash(project, 0.5, length=0.4)
    plan = build_render_plan(project)
    levels = [float(render_frame(plan, 200, 200, t).mean()) for t in (0.4, 0.5, 0.7, 1.0)]
    assert levels[1] > 200 and levels[0] < 40 and levels[1] > levels[2] > levels[3]   # 1re image : 90 % (ease-out)
