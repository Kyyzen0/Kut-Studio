"""L'aperçu suit la cadence du projet : 29,97 reste 29,97 (``int()`` la ramenait à 29)."""

from __future__ import annotations

import pytest
from test_segment_fingerprint import _project

from core.filter_graph import build_preview_command, normalize_fps
from core.preview_segments import build_segment_job


@pytest.mark.parametrize("value, expected", [
    (30, 30), (30.0, 30), (25.0, 25), (60, 60),
    (29.97, 29.97), (23.976, 23.976), (59.94, 59.94),
    (0, 30), (-5, 30), (None, 30), ("n/a", 30), (float("nan"), 30),
])
def test_normalize_fps(value, expected):
    result = normalize_fps(value)
    assert result == expected and (isinstance(result, int) == isinstance(expected, int))


@pytest.mark.parametrize("fps", [23.976, 29.97, 59.94])
def test_a_fractional_project_rate_reaches_the_preview_job_and_the_graph(tmp_path, fps):
    project = _project(tmp_path)
    project.tracks[1].clips.clear()            # pas de sous-titre : il exigerait un fichier SRT temporaire
    project.fps = fps
    job = build_segment_job(project, 0, quality="standard")
    assert job.fps == fps
    command = build_preview_command(job.plan, width=job.width, height=job.height, fps=job.fps, quality="standard",
                                    start=0.0, duration=2.0, output_path=str(tmp_path / "o.mp4"))
    graph = command[command.index("-filter_complex") + 1] if "-filter_complex" in command else open(
        command[command.index("-filter_complex_script") + 1], encoding="utf-8").read()
    assert f"fps={fps}" in graph
    assert f"fps={int(fps)}," not in graph and f"fps={int(fps)}[" not in graph


def test_an_integer_rate_keeps_its_usual_form(tmp_path):
    project = _project(tmp_path)               # 25 i/s
    job = build_segment_job(project, 0, quality="standard")
    assert job.fps == 25 and isinstance(job.fps, int)
