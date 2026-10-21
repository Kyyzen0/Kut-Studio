"""Détection des changements de plan, et découpage d'un clip à ces changements.

Les tests réels utilisent FFmpeg sur un clip synthétique dont la coupe est connue (rouge 1 s, puis bleu 2 s, coupe à
1,0 s du média). Sans FFmpeg, ils sont sautés.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_export_integration import _require_ffmpeg

from core.project_model import Clip, MediaAsset, Project, Track
from core.scene_detection import (
    SceneDetectionJob,
    cut_clip_at_scenes,
    parse_scene_times,
    scene_command,
    scene_cut_refusal,
    shot_boundaries,
)


def _two_colour_clip(ffmpeg: str, path: Path) -> None:
    """Rouge pendant 1 s, puis bleu pendant 2 s : une seule coupe, à 1,0 s du média."""
    subprocess.run(
        [ffmpeg, "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=red:s=160x90:d=1:r=25",
         "-f", "lavfi", "-i", "color=blue:s=160x90:d=2:r=25",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p", "-t", "3", str(path)],
        check=True,
    )


def _one_colour_clip(ffmpeg: str, path: Path) -> None:
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=160x90:d=2:r=25",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


# --- Fonctions pures ----------------------------------------------------------------------------------------------


def test_the_showinfo_times_are_moved_back_onto_the_media_clock():
    text = "[Parsed_showinfo_1 @ 0x1] n: 0 pts_time:0.5 duration:0.04\n[Parsed_showinfo_1 @ 0x1] pts_time:1.25"
    assert parse_scene_times(text, start=10.0) == [10.5, 11.25]


def test_the_command_seeks_before_the_input_and_applies_the_threshold():
    command = scene_command("ffmpeg", "/films/a.mp4", start=2.5, duration=4.0, threshold=0.4)
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "2.500" and command[command.index("-t") + 1] == "4.000"
    assert "select='gt(scene,0.400)',showinfo" in command


@pytest.mark.parametrize("threshold", [0.0, 0.01, 0.99, 1.0])
def test_a_threshold_outside_the_supported_range_is_refused(threshold):
    with pytest.raises(ValueError):
        scene_command("ffmpeg", "a.mp4", start=0.0, duration=1.0, threshold=threshold)


def test_cuts_too_close_to_an_edge_or_to_each_other_are_dropped():
    kept = shot_boundaries([0.1, 1.0, 1.1, 2.5, 3.95], low=0.0, high=4.0)
    assert kept == [1.0, 2.5]


# --- FFmpeg réel ---------------------------------------------------------------------------------------------------


def test_real_detection_finds_the_cut_at_its_media_time(tmp_path):
    ffmpeg, _ffprobe = _require_ffmpeg()
    media = tmp_path / "cut.mp4"
    _two_colour_clip(ffmpeg, media)
    job = SceneDetectionJob(ffmpeg, str(media), start=0.0, duration=3.0)
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "finished" and snapshot.progress == 1.0
    assert len(snapshot.result) == 1 and abs(snapshot.result[0] - 1.0) < 0.1


def test_real_detection_from_a_later_start_keeps_the_media_clock(tmp_path):
    # Le seek commence à 0,5 s : la coupe à 1,0 s du média doit rester à 1,0 s, pas à 0,5 s.
    ffmpeg, _ffprobe = _require_ffmpeg()
    media = tmp_path / "cut.mp4"
    _two_colour_clip(ffmpeg, media)
    job = SceneDetectionJob(ffmpeg, str(media), start=0.5, duration=2.0)
    job.run()
    result = job.snapshot().result
    assert result is not None and len(result) == 1 and abs(result[0] - 1.0) < 0.1


def test_a_single_colour_clip_has_no_cut(tmp_path):
    ffmpeg, _ffprobe = _require_ffmpeg()
    media = tmp_path / "flat.mp4"
    _one_colour_clip(ffmpeg, media)
    job = SceneDetectionJob(ffmpeg, str(media), start=0.0, duration=2.0)
    job.run()
    assert job.snapshot().state == "finished" and job.snapshot().result == ()


def test_an_unreadable_media_fails_with_ffmpeg_output(tmp_path):
    ffmpeg, _ffprobe = _require_ffmpeg()
    job = SceneDetectionJob(ffmpeg, str(tmp_path / "absent.mp4"), start=0.0, duration=1.0)
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "failed" and snapshot.message


def test_a_job_cancelled_before_it_runs_does_not_run(tmp_path):
    job = SceneDetectionJob("ffmpeg", str(tmp_path / "a.mp4"), start=0.0, duration=1.0)
    job.cancel()
    job.run()
    assert job.snapshot().state == "cancelled"


# --- Découpage du clip ------------------------------------------------------------------------------------------


def _project() -> Project:
    asset = MediaAsset(id="a", path="/films/a.mp4", name="A", duration=10.0, width=160, height=90, fps=25.0,
                       media_type="video")
    track = Track(id="V1", name="V1", type="video", clips=[
        Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=4.0),
    ])
    return Project(name="Plans", width=160, height=90, fps=25.0, media_assets=[asset], tracks=[track])


def test_the_clip_is_cut_at_each_scene_change_on_the_timeline():
    project = _project()
    created = cut_clip_at_scenes(project, "c1", [1.0, 2.5, 3.9, 0.1])
    clips = sorted(project.tracks[0].clips, key=lambda clip: clip.timeline_start)
    assert len(created) == 2
    assert [round(clip.timeline_start, 3) for clip in clips] == [0.0, 1.0, 2.5]
    assert [round(clip.duration, 3) for clip in clips] == [1.0, 1.5, 1.5]


def test_a_retimed_clip_is_refused_without_changing_the_project(monkeypatch):
    project = _project()
    monkeypatch.setattr(Clip, "speed", property(lambda self: 2.0))
    with pytest.raises(ValueError):
        cut_clip_at_scenes(project, "c1", [1.0])
    assert len(project.tracks[0].clips) == 1


def test_the_refusal_names_a_non_linear_clip_and_accepts_a_plain_one(monkeypatch):
    plain = _project().tracks[0].clips[0]
    assert scene_cut_refusal(plain) == ""
    monkeypatch.setattr(Clip, "speed", property(lambda self: 0.5))
    assert "vitesse différente de 1" in scene_cut_refusal(plain)


def test_an_unknown_clip_is_reported_as_missing():
    with pytest.raises(KeyError):
        cut_clip_at_scenes(_project(), "absent", [1.0])
