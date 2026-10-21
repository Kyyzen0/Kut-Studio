"""Ken Burns : un mouvement lent, déterministe, qui ne découvre jamais le bord du cadre."""

from __future__ import annotations

import numpy as np
import pytest

from core.graphics import add_graphic_clip
from core.ken_burns import KEN_BURNS_MOVES, apply_ken_burns, ken_burns_keyframes, move_for, photo_size
from core.project_model import Project
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, evaluate_transform
from render_probe import needs_ffmpeg, render_frame


def test_photo_size_fills_or_fits_the_frame():
    assert photo_size(4000, 3000, 1080, 1920, fill=True) == (2560, 1920)
    assert photo_size(4000, 3000, 1080, 1920, fill=False) == (1080, 810)
    assert photo_size(200, 100, 1080, 1920, fill=False) == (200, 100)      # jamais agrandie en « tenir »


def test_the_move_depends_only_on_the_clip_id():
    assert move_for("clip-1") == move_for("clip-1")
    assert len({move_for(f"photo-{n}") for n in range(40)}) == len(KEN_BURNS_MOVES)   # une suite varie


@pytest.mark.parametrize("move", KEN_BURNS_MOVES)
def test_every_move_keeps_the_edges_out_of_the_frame(move):
    frames = ken_burns_keyframes(4.0, move, strength=0.12)
    for t in np.linspace(0.0, 4.0, 17):
        value = evaluate_transform(ClipTransform(), frames, t, 4.0)
        margin = (value.scale - 1.0) / 2.0                     # excédent de chaque côté, en fraction du cadre
        assert abs(value.position_x) <= margin + 1e-9 and abs(value.position_y) <= margin + 1e-9
    assert {kf.time_seconds for kf in frames} == {0.0, 4.0}


def test_apply_keeps_unrelated_animation_and_skips_non_images():
    project = Project(name="kb", width=1080, height=1920, fps=30.0)
    text = add_graphic_clip(project, "text", timeline_start=0, duration=3)
    assert apply_ken_burns([text]) == 0 and not text.transform_keyframes


@needs_ffmpeg
def test_a_ken_burns_photo_covers_the_vertical_frame_at_every_moment(tmp_path):
    from PySide6.QtGui import QColor, QImage

    photo = tmp_path / "photo.png"
    image = QImage(400, 300, QImage.Format_RGB32)
    image.fill(QColor("#1673FF"))
    image.save(str(photo))
    project = Project(name="kb", width=90, height=160, fps=10.0)
    clip = add_graphic_clip(project, "image", timeline_start=0, duration=2, source_path=str(photo))
    width, height = photo_size(400, 300, 90, 160, fill=True)
    from core.graphics import update_graphic

    update_graphic(clip, "width", width)
    update_graphic(clip, "height", height)
    apply_ken_burns([clip])
    plan = build_render_plan(project)
    for t in (0.0, 1.0, 1.9):
        frame = render_frame(plan, 90, 160, t, fps=10)
        assert frame[..., 2].min() > 150, (t, move_for(clip.id))     # aucun bord noir découvert
