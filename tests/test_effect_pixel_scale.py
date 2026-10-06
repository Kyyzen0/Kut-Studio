"""Un effet réglé en pixels (σ du flou) garde sa taille à l'écran quelle que soit la résolution de rendu.

Le réglage est donné en pixels **de la séquence** : un aperçu fidèle à ½ ou ¼, ou un export à une autre taille, le
ramène à sa propre taille, comme le moniteur GPU le faisait déjà. Avant la correction, l'aperçu standard montrait un
flou deux fois trop large.
"""

from __future__ import annotations

import numpy as np

from core.effects_model import EffectType, create_effect
from core.export_engine import _build_clip_effect_filters
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from render_probe import downscale, lavfi_video, needs_ffmpeg, render_frame

W, H = 320, 180


def _blur(sigma: float):
    return create_effect(EffectType.BLUR).with_parameters({"intensity": sigma})


def test_the_blur_sigma_follows_the_output_scale():
    assert _build_clip_effect_filters((_blur(8.0),)) == "gblur=sigma=8.0"     # inchangé à pleine taille
    assert _build_clip_effect_filters((_blur(8.0),), 0.5) == "gblur=sigma=4.0"


def _edge_width(row: np.ndarray) -> int:
    """Nombre de pixels de transition (entre 10 % et 90 %) du bord central d'un profil noir → blanc.

    Seule la moitié centrale compte : ``gblur`` assombrit aussi les bords de l'image."""
    row = row[len(row) // 4: 3 * len(row) // 4]
    low, high = row.min(), row.max()
    normalized = (row - low) / max(1e-6, high - low)
    return int(np.count_nonzero((normalized > 0.1) & (normalized < 0.9)))


def _video_project(tmp_path) -> Project:
    # Moitié gauche noire, moitié droite blanche : un bord net dont le flou s'élargit.
    media = lavfi_video(tmp_path / "edge.mp4", "color=c=black", size=(W, H))
    edge = tmp_path / "edge2.mp4"
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(media), "-vf",
                    f"drawbox=x={W // 2}:y=0:w={W // 2}:h={H}:color=white:t=fill", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv444p", str(edge)], check=True, timeout=60)
    asset = MediaAsset(id="a", path=str(edge), name="edge", duration=2.0, width=W, height=H, fps=25.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    clip.effects = [_blur(6.0)]
    return Project(name="flou", width=W, height=H, fps=25.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


@needs_ffmpeg
def test_a_half_size_preview_blurs_like_the_reduced_export(tmp_path):
    plan = build_render_plan(_video_project(tmp_path))
    full = downscale(render_frame(plan, W, H, 0.5), 2)[H // 4, :, 1]
    half = render_frame(plan, W // 2, H // 2, 0.5, quality="standard")[H // 4, :, 1].astype(np.float64)
    assert abs(_edge_width(full) - _edge_width(half)) <= 1, (_edge_width(full), _edge_width(half))
    # Loin des bords de l'image (que gblur assombrit). L'export réduit et l'aperçu ne tombent pas sur la même phase
    # d'échantillonnage (½ pixel) : l'écart moyen compte, pas le pire pixel de la pente. Sans la correction, le bord de
    # l'aperçu était deux fois plus large (14 pixels de transition contre 7).
    middle = slice(W // 8, 3 * W // 8)
    assert float(np.abs(full[middle] - half[middle]).mean()) <= 2.0, float(np.abs(full[middle] - half[middle]).mean())


@needs_ffmpeg
def test_a_blurred_graphic_layer_keeps_its_blur_size_at_half_resolution(tmp_path):
    project = Project(name="flou", width=W, height=H, fps=25.0)
    solid = add_graphic_clip(project, "solid", timeline_start=0, duration=2)
    for name, value in (("fill_color", "#FFFFFF"), ("width", W // 2), ("height", H)):
        update_graphic(solid, name, value)
    from core.visual_effects import ClipTransform

    solid.transform = ClipTransform(position_x=0.25)
    solid.effects = [_blur(6.0)]
    plan = build_render_plan(project)
    full = downscale(render_frame(plan, W, H, 0.5), 2)[H // 4, :, 1]
    half = render_frame(plan, W // 2, H // 2, 0.5, quality="standard")[H // 4, :, 1].astype(np.float64)
    assert abs(_edge_width(full) - _edge_width(half)) <= 1, (_edge_width(full), _edge_width(half))
