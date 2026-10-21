"""Exposition de l'étalonnage : des diaphragmes (EV) en lumière linéaire, rendus par le vrai FFmpeg.

Avant, ``eq=gamma=2^EV`` : un gamma n'est pas une exposition. Un gris moyen (128) à −2 EV sortait à 0 au lieu de 66, et
une couleur dérivait vers le rouge dans les ombres. L'interface annonce des EV (−2 à +2).
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade
from core.color_wheels import exposure_filter, exposure_transfer, srgb_decode, srgb_encode
from core.export_engine import _build_color_grade_filters
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

W, H = 256, 16


def test_one_stop_doubles_the_light_whatever_the_input():
    """+1 EV double la lumière, −1 EV la divise par deux : c'est la définition d'un diaphragme."""
    for value in (0.05, 0.2, 0.5):
        assert srgb_decode(exposure_transfer(value, 1.0)) == pytest.approx(2 * srgb_decode(value), rel=1e-9)
    for value in (0.1, 0.5, 0.9):
        assert srgb_decode(exposure_transfer(value, -1.0)) == pytest.approx(srgb_decode(value) / 2, rel=1e-9)
    assert exposure_transfer(0.0, 2.0) == 0.0 and exposure_transfer(1.0, 1.0) == 1.0, "noir et blanc écrêté"
    assert exposure_transfer(0.5, 0.0) == pytest.approx(0.5)
    assert srgb_encode(srgb_decode(0.3)) == pytest.approx(0.3)
    levels = [exposure_transfer(x / 255, 1.3) for x in range(256)]
    assert levels == sorted(levels), "monotone"


def test_zero_exposure_emits_nothing_and_no_gamma_is_used():
    assert exposure_filter(0.0) == "" and exposure_filter(1e-9) == ""
    assert _build_color_grade_filters(ColorGrade(exposure=0.0)) == ""
    chain = _build_color_grade_filters(ColorGrade(exposure=1.0, contrast=0.2, saturation=1.1))
    assert chain.startswith("lutrgb=") and "gamma=" not in chain
    assert chain.index("lutrgb=") < chain.index("eq="), "l'exposition précède le contraste"


def _ramp(tmp_path):
    ramp = tmp_path / "ramp.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=256x16:r=25:d=2,format=gbrp,geq=r='X':g='X':b='X'", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv444p", str(ramp)], check=True, timeout=60)
    return ramp


def _render(media, width, height, grade, t=0.5):
    clip = Clip("c", "v", "V1", 0.0, 0.0, 2.0)
    clip.color_grade = grade
    project = Project("p", width=width, height=height, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 2.0, width, height, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    return render_frame(build_render_plan(project), width, height, t)


@needs_ffmpeg
@pytest.mark.parametrize("stops", [-2.0, -1.0, -0.5, 0.5, 1.0, 2.0])
def test_the_rendered_ramp_follows_the_exposure_formula_at_every_level(tmp_path, stops):
    """Les 256 niveaux d'une rampe, rendus par FFmpeg, égalent la formule (à l'aller-retour YUV de la rampe près)."""
    frame = _render(_ramp(tmp_path), W, H, ColorGrade(exposure=stops))[8]
    expected = np.array([exposure_transfer(x / 255, stops) * 255 for x in range(256)])
    difference = np.abs(frame[:, 0].astype(float) - expected)
    assert difference.mean() < 0.6 and difference.max() < 4.0, f"{difference.mean():.2f} / {difference.max():.1f}"
    assert np.abs(frame[:, 0].astype(int) - frame[:, 1].astype(int)).max() == 0, "un gris reste neutre"


@needs_ffmpeg
@pytest.mark.parametrize("stops, grey", [(-2.0, 66), (-1.0, 92), (1.0, 176), (2.0, 239)])
def test_a_mid_grey_lands_where_the_stops_say(tmp_path, stops, grey):
    """Avant : −2 EV donnait 0, −1 EV 54, +1 EV 190, +2 EV 231."""
    frame = _render(_ramp(tmp_path), W, H, ColorGrade(exposure=stops))
    assert abs(int(frame[8, 128, 0]) - grey) <= 1


@needs_ffmpeg
def test_a_colour_keeps_its_hue_in_the_shadows(tmp_path):
    """La lumière de chaque canal est multipliée de même : (128, 110, 96) à −2 EV donne (66, 56, 48), et non (7, 0, 0)."""
    media = lavfi_video(tmp_path / "patch.mp4", "color=c=0x806E60", size=(64, 36), seconds=2.0)
    frame = _render(media, 64, 36, ColorGrade(exposure=-2.0)).reshape(-1, 3).mean(axis=0)
    expected = [exposure_transfer(v / 255, -2.0) * 255 for v in (128, 110, 96)]
    assert np.abs(frame - expected).max() < 3.0, f"{frame} au lieu de {expected}"
