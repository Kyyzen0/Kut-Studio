"""SFX synthétisés : déterministes, posés par leur ancre, répartis sur les pistes SFX, baissés près d'une voix."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from core.beat_detection import estimate_tempo
from core.project_model import Clip, MediaAsset, Project, Track
from core.sfx_placement import place_sfx, place_sfx_on_cuts
from core.sfx_synth import CATALOG, SAMPLE_RATE, SFX_SPECS, ensure_sfx_file, render_sfx, sfx_path


def _read(path):
    with wave.open(str(path), "rb") as handle:
        assert (handle.getnchannels(), handle.getframerate(), handle.getsampwidth()) == (2, SAMPLE_RATE, 2)
        return np.frombuffer(handle.readframes(handle.getnframes()), "<i2").reshape(-1, 2) / 32768.0


@pytest.mark.parametrize("sfx_id", [spec.id for spec, _render in CATALOG if spec.id != "beat_bed_120"])
def test_every_sound_is_a_deterministic_normalised_stereo_file(sfx_id):
    path = ensure_sfx_file(sfx_id)
    first = path.read_bytes()
    path.unlink()
    assert ensure_sfx_file(sfx_id).read_bytes() == first                # régénéré au bit près
    samples = _read(path)
    assert 0.85 < np.abs(samples).max() <= 0.9                          # crête à −1 dBFS
    assert 0.0 <= SFX_SPECS[sfx_id].anchor <= len(samples) / SAMPLE_RATE


def test_the_anchor_of_a_passby_is_its_loudest_moment():
    signal = render_sfx("passby_1")
    envelope = np.convolve(np.abs(signal).sum(axis=0), np.ones(2400) / 2400, mode="same")
    assert abs(np.argmax(envelope) / SAMPLE_RATE - SFX_SPECS["passby_1"].anchor) < 0.08


def test_the_beat_bed_is_on_its_grid():
    stereo = render_sfx("beat_bed_120")
    mono = stereo.mean(axis=0)
    decimated = mono[: len(mono) // 6 * 6].reshape(-1, 6).mean(axis=1)    # 48 kHz → 8 kHz
    estimate = estimate_tempo(decimated)
    assert estimate.bpm == pytest.approx(120.0, abs=0.5)
    assert min(estimate.downbeat % 2.0, 2.0 - estimate.downbeat % 2.0) <= 0.03


def _project():
    asset = MediaAsset(id="v", path="/nonexistent/v.mp4", name="v", duration=10.0, width=64, height=64, fps=25.0,
                       media_type="video")
    clips = [Clip(id=f"c{i}", asset_id="v", track_id="V1", timeline_start=i * 0.5, source_in=0.0, source_out=0.5)
             for i in range(4)]
    voice = Clip(id="vo", asset_id="v", track_id="A2", timeline_start=1.4, source_in=0.0, source_out=0.4)
    return Project(name="s", width=64, height=64, fps=25.0, media_assets=[asset], tracks=[
        Track(id="V1", name="V1", type="video", clips=clips),
        Track(id="A2", name="A2", type="audio", audio_role="voice", clips=[voice]),
        Track(id="A3", name="A3", type="audio", audio_role="sfx"),
    ])


def test_sfx_land_on_their_anchor_and_spread_over_free_sfx_tracks():
    project = _project()
    clips = place_sfx_on_cuts(project, "V1", ["whoosh_long"])
    assert len(clips) == 3
    for clip, cut in zip(clips, (0.5, 1.0, 1.5)):
        assert clip.timeline_start + SFX_SPECS["whoosh_long"].anchor == pytest.approx(cut)
    sfx_tracks = [t for t in project.tracks if t.audio_role == "sfx"]
    assert len(sfx_tracks) == 2                                     # des whooshes de 0,7 s tous les 0,5 s
    assert len({a.id for a in project.media_assets if a.path == str(sfx_path("whoosh_long"))}) == 1
    near = next(c for c in clips if abs(c.timeline_start + 0.35 - 1.5) < 1e-6)
    far = next(c for c in clips if abs(c.timeline_start + 0.35 - 0.5) < 1e-6)
    assert near.gain_db == pytest.approx(far.gain_db - 2.0)         # baissé près de la voix


def test_a_sound_that_would_start_before_zero_is_trimmed():
    project = _project()
    clip = place_sfx(project, "riser_2s", 0.5)
    assert clip.timeline_start == 0.0 and clip.source_in == pytest.approx(1.5)
