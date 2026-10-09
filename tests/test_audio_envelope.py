"""Forme d'onde des clips audio : l'enveloppe de crête du fichier, lue sur la portion de média que le clip fait entendre.

Avant : tout le fichier réduit à 64 à 384 colonnes étirées sur le clip (un morceau coupé à 30 s montrait son début),
d'un son rééchantillonné à 200 Hz (il ne restait que les basses), et une forme d'onde *inventée* tant que la vraie
n'était pas prête.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.audio_envelope import ENVELOPE_RATE, AudioEnvelope, column_peaks, extract_envelope
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_remapping import TimeRemapping

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent : pas de décodage réel")


def _beep(tmp_path: Path) -> Path:
    """1 s de silence stéréo, sauf un bip à mi-volume **sur le canal droit seul**, de 0,5 à 0,6 s."""
    path = tmp_path / "bip.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=exprs='0|if(between(t,0.5,0.6),0.5*sin(2*PI*440*t),0)':s=48000:d=1", str(path)],
                   check=True, timeout=60)
    return path


@needs_ffmpeg
def test_the_envelope_keeps_a_short_sound_of_one_channel_where_it_happens(tmp_path):
    envelope = extract_envelope(str(_beep(tmp_path)))
    assert envelope.rate == ENVELOPE_RATE
    assert envelope.duration == pytest.approx(1.0, abs=0.01)
    peaks = list(envelope.peaks)
    assert max(peaks[255:295]) == pytest.approx(128, abs=4)     # 0,5 de la pleine échelle, canal droit seul
    assert max(peaks[:245]) == 0 and max(peaks[305:]) == 0       # à 10 ms près du bip, rien
    # L'ancien rééchantillonnage à 200 Hz ne gardait que les basses : un la à 440 Hz y disparaissait.


@needs_ffmpeg
def test_a_file_without_sound_or_a_missing_one_gives_no_waveform(tmp_path):
    video = tmp_path / "muet.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x36:d=1", "-pix_fmt", "yuv420p",
                    str(video)], check=True, timeout=60)
    assert not extract_envelope(str(video))
    assert not extract_envelope(str(tmp_path / "absent.wav"))
    assert not extract_envelope(str(_beep(tmp_path)), cancelled=lambda: True)


def test_columns_take_the_peak_of_the_media_slice_they_cover():
    envelope = AudioEnvelope(500, bytes([0, 255, 10, 20, 128, 0, 0, 64]))   # 16 ms de crêtes
    assert column_peaks(envelope, [0.0, 0.004, 0.008, 0.016]) == pytest.approx([1.0, 20 / 255, 128 / 255])
    # Clip inversé : bornes décroissantes, colonnes dans l'ordre de la timeline.
    assert column_peaks(envelope, [0.016, 0.008, 0.004, 0.0]) == pytest.approx([128 / 255, 20 / 255, 1.0])
    # Colonnes plus fines qu'une crête (zoom maximal) : la crête qui les contient.
    assert column_peaks(envelope, [0.002, 0.0025, 0.003]) == pytest.approx([1.0, 1.0])
    # Au-delà de la fin du média : rien d'inventé.
    assert column_peaks(envelope, [0.014, 0.02, 0.03]) == pytest.approx([64 / 255, 0.0])
    assert column_peaks(envelope, [0.0, 0.016], gain=0.5) == pytest.approx([0.5])
    assert column_peaks(AudioEnvelope(500, b""), [0.0, 1.0]) == []


# --- dans la timeline ------------------------------------------------------------------------------------------------


@pytest.fixture
def timeline(qtbot, tmp_path):
    from core.media_previews import audio_envelope_cache_key
    from core.studio_runtime import StudioRuntime
    from ui.timeline_panel import TimelinePanel

    path = _beep(tmp_path) if shutil.which("ffmpeg") else tmp_path / "bip.wav"
    project = Project(name="Onde", media_assets=[MediaAsset("bip", str(path), "bip", 1.0, 0, 0, 0.0, "audio", True)],
                      tracks=[Track("A1", "A1", "audio", clips=[Clip("c", "bip", "A1", 0.0, 0.4, 0.8)])])
    panel = TimelinePanel(project)
    qtbot.addWidget(panel)
    panel.attach_runtime(StudioRuntime(profile="balanced"))
    panel.resize(1200, 400)
    panel.set_timeline_duration(2.0)
    panel.set_project(project)
    envelope = AudioEnvelope(ENVELOPE_RATE, bytes(250) + bytes([128] * 50) + bytes(200))   # le bip, comme décodé
    panel._runtime.cache.put(audio_envelope_cache_key(str(path)), envelope, size_bytes=len(envelope.peaks))
    return panel, project, envelope


def _drawn_peaks(panel, project, envelope) -> list[float]:
    """Hauteur relative de chaque colonne de la forme d'onde du clip (0 : rien de dessiné)."""
    from ui.timeline_widgets.waveform import waveform_shape

    widget = panel.clip_widgets["c"]
    region = 100.0
    shape = waveform_shape(widget, project.tracks[0].clips[0], envelope, 0, widget.width(), region)
    if shape is None:
        return []
    columns = shape.size() // 2                          # le haut de gauche à droite, puis le bas de droite à gauche
    return [(shape.at(2 * columns - 1 - index).y() - shape.at(index).y()) / (region * 0.9) for index in range(columns)]


def test_the_waveform_shows_the_part_of_the_media_the_clip_plays(timeline):
    panel, project, envelope = timeline
    drawn = _drawn_peaks(panel, project, envelope)       # clip : de 0,4 à 0,8 s du média, le bip est à 0,5–0,6 s
    width = len(drawn)
    loud = [index for index, value in enumerate(drawn) if value > 0.4]
    assert loud and loud[0] / width == pytest.approx(0.25, abs=0.03) and loud[-1] / width == pytest.approx(0.5, abs=0.03)
    project.tracks[0].clips[0].time_remapping = TimeRemapping(speed=2.0)
    project.tracks[0].clips[0].source_out = 1.0                 # à 2× : 0,3 s de clip pour 0,6 s de média
    loud = [index for index, value in enumerate(_drawn_peaks(panel, project, envelope)) if value > 0.4]
    assert loud[0] / len(drawn) == pytest.approx(0.05 / 0.3, abs=0.03)
    project.tracks[0].clips[0].time_remapping = TimeRemapping(speed=2.0, reverse=True)
    reversed_loud = [index for index, value in enumerate(_drawn_peaks(panel, project, envelope)) if value > 0.4]
    assert reversed_loud[-1] / len(drawn) == pytest.approx(1.0 - 0.05 / 0.3, abs=0.03)


def test_the_clip_gain_scales_the_waveform(timeline):
    panel, project, envelope = timeline
    full = max(_drawn_peaks(panel, project, envelope))
    project.tracks[0].clips[0].gain_db = -6.0
    assert max(_drawn_peaks(panel, project, envelope)) == pytest.approx(full * 10 ** (-6 / 20), rel=0.02)


def test_no_waveform_is_invented_before_the_envelope_is_ready(timeline):
    panel, project, _envelope = timeline
    panel._runtime.cache.clear()
    widget = panel.clip_widgets["c"]
    widget.repaint()
    assert getattr(widget, "_waveform_polygon", None) is None


def test_a_speed_curve_that_turns_back_reads_each_column_between_its_own_edges():
    """Une courbe de vitesse peut passer en négatif : le temps du média repart en arrière au milieu du clip."""
    envelope = AudioEnvelope(500, bytes([0, 10, 255, 20, 0, 0, 90, 0]))
    # Aller jusqu'à 14 ms, puis retour : chaque colonne lit sa propre tranche, pas une crête voisine.
    edges = [0.0, 0.004, 0.008, 0.014, 0.010, 0.006, 0.002]
    assert column_peaks(envelope, edges) == pytest.approx([10 / 255, 1.0, 90 / 255, 90 / 255, 20 / 255, 1.0])
