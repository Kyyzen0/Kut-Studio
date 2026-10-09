"""Étalonnage à l'export, mesuré sur des pixels rendus par le vrai FFmpeg : température, teinte, ombres, hautes lumières.

Jusqu'au 2026-10-09, ``colorbalance`` portait ``pl=1`` (« conserver la luminosité ») : FFmpeg y met la saturation à
zéro dès qu'un canal vaut 0 ou 255 après réglage — un rouge saturé réchauffé sortait **gris** — et y annule les
ombres et hautes lumières, qui décalent les trois canaux d'autant.
"""

from __future__ import annotations

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade
from core.export_engine import _build_color_grade_filters
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

W, H = 64, 36


def _rendered(tmp_path, color: str, grade: ColorGrade | None) -> np.ndarray:
    """Couleur moyenne (RVB 0..255) du centre d'un plan uni ``color``, étalonné par ``grade``, rendu par l'export."""
    media = lavfi_video(tmp_path / f"{color}.mp4", f"color=c={color}", size=(W, H), seconds=1.0)
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0)])])
    project.tracks[0].clips[0].color_grade = grade
    frame = render_frame(build_render_plan(project), W, H, 0.3).astype(float)
    return frame[H // 2 - 4:H // 2 + 4, W // 2 - 4:W // 2 + 4].reshape(-1, 3).mean(axis=0)


def test_colorbalance_no_longer_asks_to_preserve_lightness():
    chain = _build_color_grade_filters(ColorGrade(temperature=25.0, shadows=0.2))
    balance = next(part for part in chain.split(",") if part.startswith("colorbalance="))
    assert "pl=" not in balance


@needs_ffmpeg
@pytest.mark.parametrize("color", ["0xE62814", "0x285AE6"], ids=["rouge", "bleu"])
def test_a_saturated_colour_warmed_up_stays_a_colour_not_grey(tmp_path, color):
    """Rouge (230, 40, 20) ou bleu saturé, saturation 1,3 et température : avec ``pl=1``, (128, 128, 128)."""
    graded = _rendered(tmp_path, color, ColorGrade(saturation=1.3, temperature=25.0))
    assert np.ptp(graded) > 150, graded
    source = _rendered(tmp_path, color, None)
    assert np.argmax(graded) == np.argmax(source), "la couleur dominante reste la même"


@needs_ffmpeg
def test_the_shadows_and_highlights_sliders_change_the_picture(tmp_path):
    """Avec ``pl=1``, un décalage égal des trois canaux était aussitôt annulé : les deux curseurs n'avaient aucun
    effet sur les pixels (sauf à griser ceux qui touchaient 0 ou 255)."""
    dark = _rendered(tmp_path, "0x28323C", None)
    lifted = _rendered(tmp_path, "0x28323C", ColorGrade(shadows=0.5))
    assert (lifted - dark).min() > 10, (dark, lifted)
    bright = _rendered(tmp_path, "0xC8BEAA", None)
    lowered = _rendered(tmp_path, "0xC8BEAA", ColorGrade(highlights=-0.5))
    assert (bright - lowered).min() > 10, (bright, lowered)
    assert np.ptp(lowered) > 20, "les hautes lumières baissent sans griser"


@needs_ffmpeg
@pytest.mark.parametrize("color", ["0x5A4632", "0x808080", "0xC8C0B8"], ids=["sombre", "gris-moyen", "clair"])
def test_temperature_warms_every_tone_without_changing_its_luminance(tmp_path, color):
    """Température : une balance des blancs sur toute l'image (rouge plus, bleu moins), la luminance Rec. 709 garde sa
    valeur. Jusqu'au 2026-10-10 elle passait par les tons moyens de ``colorbalance``, nuls dès que max + min dépasse
    ≈ 202 : un gris moyen (128) ou un ton clair ne se réchauffaient pas du tout."""
    source = _rendered(tmp_path, color, None)
    warm = _rendered(tmp_path, color, ColorGrade(temperature=40.0))
    assert warm[0] > source[0] + 5 and warm[2] < source[2] - 5, (source, warm)
    luma = np.array([0.2126, 0.7152, 0.0722])
    assert abs(warm @ luma - source @ luma) < 2.0, (source @ luma, warm @ luma)


@needs_ffmpeg
def test_hue_rotates_the_colours():
    """Teinte : une rotation (un rouge passe vers le jaune, un bleu vers le magenta), pas un second réglage de
    température (jusqu'au 2026-10-10, la teinte décalait le rouge et le bleu comme elle)."""
    import subprocess

    pixels = np.array([[[220, 40, 40], [40, 40, 220]]], dtype=np.uint8)
    chain = _build_color_grade_filters(ColorGrade(hue=40.0))
    done = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "2x1", "-i", "-",
                           "-vf", chain, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], input=pixels.tobytes(),
                          capture_output=True, check=True)
    red, blue = np.frombuffer(done.stdout, np.uint8).reshape(2, 3).astype(int)
    assert red[1] > 60 and red[2] < 60, f"le rouge tourne vers le jaune : {red}"
    assert blue[0] > 60 and blue[1] < 60, f"le bleu tourne vers le magenta : {blue}"
