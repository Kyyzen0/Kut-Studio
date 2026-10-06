"""Effets lumineux d'un clip, exportés pour de vrai : le moniteur GPU (référence numpy) montre la même image.

Les formules sont vérifiées effet par effet dans ``test_gpu_pipeline.py`` (filtre seul) ; ici, la chaîne complète
d'un clip de l'export (mise au cadre, échelle, rotation, effets, temps du clip) contre le programme GPU au même
instant — en particulier l'onde du heat haze, qui dépend du temps.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from core.effects_model import EffectType, create_effect
from core.gpu_effects import VIGNETTE_EXPORT, program_for, reference_layer
from core.gpu_frames import yuv_to_rgb_matrix
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from render_probe import needs_ffmpeg, render_frame

W, H = 192, 108


def _media(tmp_path):
    path = tmp_path / "src.mkv"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r=25:d=2",
                    "-c:v", "ffv1", "-pix_fmt", "yuv444p", str(path)], check=True, timeout=60)
    return path


def _project(tmp_path, effects):
    asset = MediaAsset(id="a", path=str(_media(tmp_path)), name="s", duration=2.0, width=W, height=H, fps=25.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    clip.effects = list(effects)
    return Project(name="fx", width=W, height=H, fps=25.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


def _source_codes(tmp_path, t):
    """Codes YUV (4:4:4, normalisés) de l'image source à ``t`` : l'entrée du programme GPU."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(tmp_path / "src.mkv"), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"], capture_output=True, check=True).stdout
    planes = np.frombuffer(raw, np.uint8).reshape(3, H, W)
    return np.stack(planes, axis=-1) / 255.0


@needs_ffmpeg
@pytest.mark.parametrize("t", [0.4, 1.2])
def test_an_animated_heat_haze_matches_the_monitor_at_any_time(tmp_path, t):
    haze = create_effect(EffectType.HEAT_HAZE).with_parameters(
        {"amplitude": 5.0, "frequency": 0.09, "speed": 7.0, "top": 0.1, "span": 0.5})
    exported = render_frame(build_render_plan(_project(tmp_path, [haze])), W, H, t) / 255.0
    still = render_frame(build_render_plan(_project(tmp_path, [])), W, H, t) / 255.0
    monitor = reference_layer(_source_codes(tmp_path, t), program_for([haze], vignette_extent=VIGNETTE_EXPORT, time=t),
                              yuv_to_rgb=yuv_to_rgb_matrix())
    error = np.abs(monitor - exported)[:, 8:-8].mean() * 255
    baseline = np.abs(_source_rgb(tmp_path, t) - still)[:, 8:-8].mean() * 255
    assert error <= baseline + 1.5, (error, baseline)
    assert np.abs(exported - still).mean() * 255 > 3.0                    # l'onde bouge vraiment l'image


def _source_rgb(tmp_path, t):
    return reference_layer(_source_codes(tmp_path, t), program_for(()), yuv_to_rgb=yuv_to_rgb_matrix())


@needs_ffmpeg
def test_glow_and_aberration_survive_the_clip_chain_of_the_export(tmp_path):
    effects = [create_effect(EffectType.GLOW).with_parameters({"threshold": 0.5, "radius": 5.0, "intensity": 1.0}),
               create_effect(EffectType.CHROMATIC_ABERRATION).with_parameters({"intensity": 3.0})]
    exported = render_frame(build_render_plan(_project(tmp_path, effects)), W, H, 0.5) / 255.0
    still = render_frame(build_render_plan(_project(tmp_path, [])), W, H, 0.5) / 255.0
    monitor = reference_layer(_source_codes(tmp_path, 0.5), program_for(effects, vignette_extent=VIGNETTE_EXPORT),
                              yuv_to_rgb=yuv_to_rgb_matrix())
    baseline = np.abs(_source_rgb(tmp_path, 0.5) - still)[:, 8:-8].mean() * 255
    error = np.abs(monitor - exported)[:, 8:-8].mean() * 255
    assert error <= baseline + 2.0, (error, baseline)
    assert exported.mean() > still.mean() + 0.01                           # le bloom éclaire
