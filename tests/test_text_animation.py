"""Texte style TikTok : contour extérieur, emojis couleur, apparition mot par mot / lettre par lettre, karaoké."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from core.graphics import GraphicOverlay, GraphicType
from core.mograph_raster import draw_content, measure_text
from core.text_runs import reveal_from_times, reveal_state, split_emoji, word_count

W, H = 640, 200


def _image(graphic: GraphicOverlay, size=(W, H)):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    image = QImage(size[0], size[1], QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    draw_content(painter, graphic, (float(size[0]), float(size[1])))
    painter.end()
    image = image.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(size[1], image.bytesPerLine())[:, : size[0] * 4] \
        .reshape(size[1], size[0], 4).astype(np.int32)


def _text(text="SPRINT RACE", **values) -> GraphicOverlay:
    base = GraphicOverlay(type=GraphicType.TEXT, text=text, width=W, height=H, font_family="Anton", font_size=72,
                          fill_color="#FFFFFF", stroke_color="#000000", shadow_color="#00000000", shadow_offset_x=0,
                          shadow_offset_y=0)
    return replace(base, **values)


def _white(image) -> int:
    return int(((image[..., 0] > 230) & (image[..., 1] > 230) & (image[..., 2] > 230) & (image[..., 3] > 230)).sum())


def _ink(image) -> np.ndarray:
    return image[..., 3] > 40


# --- Contour extérieur ----------------------------------------------------------------------------------------------


def test_an_outside_stroke_leaves_the_letters_whole(qapp):
    plain = _white(_image(_text()))
    centre = _white(_image(_text(stroke_width=8)))
    outside = _white(_image(_text(stroke_width=8, stroke_position="outside")))
    assert centre < 0.75 * plain                     # centré, le contour mange l'intérieur des lettres condensées
    assert outside >= 0.97 * plain                   # extérieur : le remplissage reste entier
    assert _ink(_image(_text(stroke_width=8, stroke_position="outside"))).sum() > _ink(_image(_text())).sum()


# --- Mot par mot, lettre par lettre, karaoké ----------------------------------------------------------------------


def test_revealing_every_word_draws_exactly_the_plain_text(qapp):
    """Le placement par mot reproduit celui du texte entier : mêmes pixels (à l'arrondi près)."""
    for values in ({}, {"stroke_width": 6}, {"stroke_width": 6, "stroke_position": "outside"}):
        plain = _image(_text("FULL SEND ON THE STRAIGHT", **values))
        words = _image(_text("FULL SEND ON THE STRAIGHT", word_reveal="word", reveal=1.0, **values))
        assert np.abs(plain - words).max() <= 2, values


def test_word_by_word_shows_the_first_words_only(qapp):
    image = _image(_text("AAA BBB CCC DDD", word_reveal="word", reveal=0.5))
    columns = np.nonzero(_ink(image).any(axis=0))[0]
    full = np.nonzero(_ink(_image(_text("AAA BBB CCC DDD"))).any(axis=0))[0]
    assert columns.max() < (full.min() + full.max()) / 2 + 20    # seule la moitié gauche (deux mots sur quatre)


def test_typewriter_reveals_letters_and_ignores_spaces(qapp):
    state = reveal_state("AB CD", "typewriter", 0.5)
    assert state.letters == 4 and [state.letter_alpha(i) for i in range(4)] == [1.0, 1.0, 0.0, 0.0]
    image = _image(_text("AB CD", word_reveal="typewriter", reveal=0.5))
    assert 0 < _ink(image).sum() < _ink(_image(_text("AB CD"))).sum() * 0.7


def test_karaoke_and_highlighted_words_take_the_highlight_colour(qapp):
    karaoke = _image(_text("ONE TWO THREE", word_reveal="karaoke", reveal=0.5, highlight_color="#FF2433"))
    red = np.nonzero((karaoke[..., 0] > 215) & (karaoke[..., 1] < 80) & (karaoke[..., 3] > 200))
    assert red[1].size > 0
    middle = (red[1].min() + red[1].max()) / 2
    assert W * 0.35 < middle < W * 0.65                          # le mot courant est le deuxième (au centre)
    assert _white(karaoke) > 0                                   # les autres restent dans la couleur du texte
    marked = _image(_text("ONE TWO THREE", highlight_words=(2,), highlight_color="#22B8FF"))
    blue = np.nonzero((marked[..., 2] > 200) & (marked[..., 0] < 80) & (marked[..., 3] > 200))
    assert blue[1].size > 0 and blue[1].min() > W * 0.55        # « THREE », à droite


def test_word_times_drive_karaoke_and_word_reveal():
    times = (0.0, 0.5, 1.2)
    assert reveal_from_times(times, 0.6, 3, "karaoke") == pytest.approx(1.5 / 3)
    assert reveal_from_times(times, -1.0, 3, "karaoke") == pytest.approx(0.5 / 3)
    assert reveal_from_times(times, 0.5, 3, "word") == pytest.approx(1.0 / 3)
    assert reveal_from_times(times, 2.0, 3, "word") == pytest.approx(1.0)
    assert reveal_from_times(times, 1.0, 3, "none") is None and reveal_from_times((), 1.0, 3, "word") is None


def test_the_scene_turns_word_times_into_the_reveal_of_the_frame(qapp):
    from core.graphics import add_graphic_clip
    from core.mograph_raster import scene_for_plan
    from core.project_model import Project
    from core.render_plan import build_render_plan

    project = Project(name="k", width=W, height=H, fps=25.0)
    clip = add_graphic_clip(project, "text", timeline_start=1.0, duration=3.0)
    clip.graphic = _text("A B C", word_reveal="karaoke", word_times=(0.0, 1.0, 2.0))
    scene = scene_for_plan(build_render_plan(project))
    reveals = [scene.evaluate(clip.id, 1.0 + t).graphic.reveal for t in (0.2, 1.2, 2.2)]
    assert [reveal_state("A B C", "karaoke", r).current_word() for r in reveals] == [0, 1, 2]


# --- Emojis -------------------------------------------------------------------------------------------------------


def _colour_emoji_font_available(qapp) -> bool:
    image = _image(_text("🔥", font_size=96), size=(200, 200))
    colours = image[image[..., 3] > 200][:, :3]
    return colours.size > 0 and int(np.ptp(colours, axis=0).max()) > 60


def test_emoji_are_split_from_the_text():
    assert split_emoji("LIVE 👇") == [("LIVE ", False), ("👇", True)]
    assert [is_emoji for _s, is_emoji in split_emoji("👍🏽🇫🇷👨‍👩‍👧")] == [True, True, True]
    assert word_count("GO 👇 GO") == 3


def test_colour_emoji_are_drawn_in_colour_beside_the_text(qapp):
    if not _colour_emoji_font_available(qapp):
        pytest.skip("Aucune police emoji couleur sur cette machine (CI Linux sans Noto Color Emoji)")
    image = _image(_text("GO 🔥", stroke_width=4, stroke_position="outside"))
    vivid = image[(image[..., 3] > 200)][:, :3]
    saturation = vivid.max(axis=1) - vivid.min(axis=1)
    assert int((saturation > 120).sum()) > 200                   # orange / jaune de la flamme
    width, _height = measure_text(_text("GO 🔥", autosize=True))
    assert width > measure_text(_text("GO", autosize=True))[0] + 40


def test_an_emoji_only_title_is_no_longer_empty(qapp):
    if not _colour_emoji_font_available(qapp):
        pytest.skip("Aucune police emoji couleur sur cette machine (CI Linux sans Noto Color Emoji)")
    assert _ink(_image(_text("👇"))).sum() > 500
