"""Un clip audio qui ne commence pas à 0 doit être entendu **à sa place** dans le fichier exporté.

Régression trouvée en bâtissant le Multicam : ``amix`` ignore les horodatages de ses entrées. Le graphe retardait un clip
par ``asetpts=PTS+début/TB`` ; avec FFmpeg 9 le second de deux clips bout à bout jouait donc depuis le début de la
timeline (mélangé au premier) puis se taisait : tout export de plus d'un clip audio perdait son son après le premier.
Ces tests rendent de vrais fichiers et relisent le **ton** entendu à chaque instant.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from core.export_engine import ExportEngine, _ffmpeg_command_prefix
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

W, H, FPS = 160, 90, 25


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tones")
    assets = []
    for name, hertz in (("a", 300), ("b", 500), ("c", 700)):
        path = folder / f"{name}.mp4"
        _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d=8", "-f", "lavfi",
              "-i", f"sine=frequency={hertz}:sample_rate=48000:duration=8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-shortest", str(path)])
        assets.append(MediaAsset(name, str(path), name, 8.0, W, H, float(FPS), "video", True))
    return assets


def _export(plan, path):
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    command = [*_ffmpeg_command_prefix(), "-y", "-v", "error"]
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    _run(command)
    return path


def _heard(path, start: float, length: float = 1.5) -> float | None:
    """Fréquence dominante entendue (``None`` : silence)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{length}", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True, timeout=60).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    if samples.size < 4000 or float(np.abs(samples).max()) < 1e-3:
        return None
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    return float(np.fft.rfftfreq(samples.size, 1 / 16000)[int(np.argmax(spectrum))])


def test_the_second_of_two_clips_end_to_end_is_heard_after_the_first(tones, tmp_path):
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=list(tones), tracks=[
        Track("V1", "V1", "video", clips=[Clip("a", "a", "V1", 0.0, 0.0, 4.0), Clip("b", "b", "V1", 4.0, 0.0, 4.0)]),
    ])
    out = _export(build_render_plan(project), tmp_path / "two.mp4")
    assert _heard(out, 0.5) == pytest.approx(300, abs=15)
    assert _heard(out, 5.0) == pytest.approx(500, abs=15)      # avant le correctif : silence, puis le ton 500 s'entendait de 0 à 4 s
    assert _heard(out, 2.0) == pytest.approx(300, abs=15)      # et jamais mélangé au clip précédent


def test_a_clip_starting_later_with_a_gap_is_silent_in_the_gap_and_heard_after_it(tones, tmp_path):
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=list(tones), tracks=[
        Track("V1", "V1", "video", clips=[Clip("a", "a", "V1", 0.0, 0.0, 2.0)]),
        Track("A1", "A1", "audio", clips=[Clip("c", "c", "A1", 5.0, 0.0, 3.0)]),
    ])
    out = _export(build_render_plan(project), tmp_path / "gap.mp4")
    assert _heard(out, 0.2, 1.5) == pytest.approx(300, abs=15)
    assert _heard(out, 3.0, 1.5) is None                         # le trou entre les deux : silence
    assert _heard(out, 6.0, 1.5) == pytest.approx(700, abs=15)


def test_a_nested_sequence_placed_late_is_heard_at_its_position(tones, tmp_path):
    inner = Sequence("inner", "Inner", W, H, float(FPS), tracks=[
        Track("V1", "V1", "video", clips=[Clip("b", "b", "V1", 0.0, 0.0, 3.0)]),
    ])
    main = Sequence("main", "Main", W, H, float(FPS), tracks=[Track("V1", "V1", "video", clips=[Clip("a", "a", "V1", 0.0, 0.0, 3.0)]),
                                                             Track("V2", "V2", "video")])
    project = Project("p", media_assets=list(tones), sequences=[main, inner], active_sequence_id="main")
    insert_sequence_clip(project, "inner", "V2", 5.0)
    out = _export(build_render_plan(project), tmp_path / "nested.mp4")
    assert _heard(out, 0.5) == pytest.approx(300, abs=15)
    assert _heard(out, 3.5, 1.2) is None
    assert _heard(out, 6.0) == pytest.approx(500, abs=15)


def test_the_delay_is_emitted_only_for_a_clip_that_starts_after_zero(tones):
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=list(tones), tracks=[
        Track("V1", "V1", "video", clips=[Clip("a", "a", "V1", 0.0, 0.0, 2.0), Clip("b", "b", "V1", 2.5, 0.0, 2.0)]),
    ])
    graph = ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)[0]
    assert graph.count("adelay=") == 1 and "adelay=2500.000|2500.000" in graph
    assert "asetpts=PTS+2.5/TB" not in graph
