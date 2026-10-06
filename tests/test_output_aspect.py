"""Une sortie d'un autre format que la séquence montre le cadre de la séquence, sans déformation.

Exporter une séquence 16:9 avec le preset vertical (1080×1920) étirait les calques graphiques (le rastériseur mettait x
et y à l'échelle séparément) alors que chaque clip vidéo était cadré seul dans la sortie : les deux ne tombaient plus au
même endroit. Le cadre de la séquence est désormais composé à sa taille réduite, puis centré entre des bandes.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.export_engine import composition_size
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform
from render_probe import lavfi_video, needs_ffmpeg, render_frame


@pytest.mark.parametrize(
    ("frame", "output", "expected"),
    [
        ((1920, 1080), (1920, 1080), (1920, 1080)),
        ((1920, 1080), (960, 540), (960, 540)),
        ((1920, 1080), (1080, 1920), (1080, 608)),
        ((1080, 1920), (1920, 1080), (608, 1080)),
        ((1080, 1350), (270, 337), (270, 337)),       # aperçu brouillon : arrondi, même format
    ],
)
def test_composition_size_fits_the_sequence_frame_in_the_output(frame, output, expected):
    assert composition_size(*frame, *output) == expected


@needs_ffmpeg
def test_a_16_9_sequence_exported_vertically_keeps_its_layers_square_and_aligned(tmp_path):
    media = lavfi_video(tmp_path / "blue.mp4", "color=c=blue", size=(320, 180))
    asset = MediaAsset(id="a", path=str(media), name="blue", duration=2.0, width=320, height=180, fps=25.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    project = Project(name="16:9", width=320, height=180, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    square = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    for name, value in (("fill_color", "#FF0000"), ("width", 40), ("height", 40)):
        update_graphic(square, name, value)
    square.transform = ClipTransform(position_y=0.25)        # sous le centre, en fraction du cadre de la séquence
    image = render_frame(build_render_plan(project), 180, 320, 0.5)
    red = (image[..., 0] > 200) & (image[..., 1] < 60) & (image[..., 2] < 60)
    blue = (image[..., 2] > 150) & (image[..., 0] < 80)
    rows, cols = np.nonzero(red)
    assert abs((rows.max() - rows.min()) - (cols.max() - cols.min())) <= 3      # carré (avant : étiré en hauteur)
    blue_rows = np.nonzero(blue.any(axis=1))[0]
    top, bottom = blue_rows.min(), blue_rows.max()                              # cadre 180×102 centré
    assert abs((bottom - top + 1) - 102) <= 2 and abs(top - (320 - 102) // 2) <= 2
    centre = (rows.min() + rows.max()) / 2.0
    expected = top + (bottom - top + 1) * 0.75                                  # 0,25 sous le centre du cadre
    assert abs(centre - expected) <= 2, (centre, expected)
