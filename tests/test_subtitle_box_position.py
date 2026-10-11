"""Fond, position et ombre des sous-titres : ce que l'éditeur de style règle est ce que l'export dessine.

Avant, le fond (couleur et opacité) était écrit dans ``BackColour``, la couleur de l'**ombre** en ASS, et ne s'affichait
jamais ; ``position_x`` / ``position_y`` n'étaient lus nulle part ; la couleur d'ombre du style n'était jamais utilisée.
Les cas libass lisent les pixels d'une image rendue par le graphe de l'application (voir ``test_libass_subtitles.py``) ;
les autres vérifient le fichier ASS, sans FFmpeg.
"""

from __future__ import annotations

import numpy as np
import pytest
import test_libass_subtitles as libass
from PySide6.QtGui import QImage

from core.subtitle_io import PLAY_RES_X, PLAY_RES_Y, SubtitleCue, ass_position, format_ass, style_has_box
from core.text_style import TextAlignment, TextStyle

W, H = libass.W, libass.H
SCALE = W / PLAY_RES_X
"""Unités du script ASS (1920 × 1080) vers pixels de l'image (640 × 360)."""
QUIET = dict(outline_width=0.0, shadow_offset=0.0, font_size=96.0)
"""Ni contour ni ombre, et un corps de 96 unités (32 pixels sur 640 × 360) : le texte blanc se mesure sans ambiguïté."""


# --- le fichier ASS (sans FFmpeg) ------------------------------------------------------------------------------------------


def test_the_background_is_a_box_layer_under_the_text_not_the_shadow_colour():
    style = TextStyle(background_color="#0f766e", background_opacity=0.9, padding_x=20, padding_y=8, **QUIET)
    assert style_has_box(style)
    output = format_ass([SubtitleCue(0.0, 1.0, "A")], style)
    styles = [line for line in output.splitlines() if line.startswith("Style: ")]
    assert [line.split(",")[0] for line in styles] == ["Style: Default", "Style: DefaultBox"]
    text, box = (line.split(",") for line in styles)
    assert box[15:18] == ["3", "20.0", "0.0"], "boîte opaque, marge interne, pas d'ombre"
    assert box[5] == "&H196E760F&", "OutlineColour = la couleur du fond et son opacité (ASS : BGR + alpha)"
    assert box[3] == "&HFFFFFFFF&", "le texte du calque de fond est invisible"
    assert text[15] == "0", "le texte garde son propre style (ni contour ni ombre ici)"
    dialogues = [line for line in output.splitlines() if line.startswith("Dialogue: ")]
    assert dialogues[0].startswith("Dialogue: 0,") and ",DefaultBox," in dialogues[0] and "{\\xbord20\\ybord8\\pos(" in dialogues[0]
    assert dialogues[1].startswith("Dialogue: 1,") and ",Default," in dialogues[1] and dialogues[1].endswith("}A")


@pytest.mark.parametrize("style", [
    TextStyle(), TextStyle(background_color="#00ff00", background_opacity=0.0),
    TextStyle(background_color=None, background_opacity=1.0)], ids=["défaut", "opacité nulle", "sans couleur"])
def test_without_a_visible_background_nothing_changes(style):
    assert not style_has_box(style)
    output = format_ass([SubtitleCue(0.0, 1.0, "A")], style)
    assert output.count("Style: ") == 1 and output.count("Dialogue: ") == 1
    assert "Box" not in output and "\\pos" not in output and "{" not in output


def test_the_shadow_colour_of_the_style_is_the_ass_back_colour():
    output = format_ass([SubtitleCue(0.0, 1.0, "A")], TextStyle(shadow_color="#ff0000", shadow_offset=3.0))
    assert output.splitlines()[next(i for i, line in enumerate(output.splitlines()) if line.startswith("Style: "))] \
        .split(",")[6] == "&H000000FF&"


@pytest.mark.parametrize("alignment, expected", [
    (TextAlignment.BOTTOM_CENTER, (960, 1080 - 48)), (TextAlignment.TOP_LEFT, (32, 48)),
    (TextAlignment.MIDDLE_CENTER, (960, 540)), (TextAlignment.BOTTOM_RIGHT, (1920 - 32, 1080 - 48)),
    (TextAlignment.MIDDLE_LEFT, (32, 540)), (TextAlignment.TOP_RIGHT, (1920 - 32, 48))])
def test_the_anchor_is_the_alignment_point_shifted_by_the_offset(alignment, expected):
    assert ass_position(TextStyle(alignment=alignment)) is None, "sans décalage : libass place le texte avec les marges"
    style = TextStyle(alignment=alignment, position_x=10.0, position_y=-5.0)
    assert ass_position(style) == pytest.approx((expected[0] + 0.10 * PLAY_RES_X, expected[1] - 0.05 * PLAY_RES_Y))


def test_an_offset_is_written_as_pos_on_the_text_line():
    output = format_ass([SubtitleCue(0.0, 1.0, "A")], TextStyle(position_x=10.0, position_y=-5.0))
    assert "{\\pos(1152,978)}A" in output
    assert output.count("Dialogue: ") == 1


# --- le rendu réel par libass -----------------------------------------------------------------------------------------------


def _pixels(image: QImage) -> np.ndarray:
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    stride = rgb.bytesPerLine()
    data = np.frombuffer(bytes(rgb.constBits()), dtype=np.uint8).reshape(H, stride)[:, : W * 3]
    return data.reshape(H, W, 3).astype(int)


def _render(tmp_path, style: TextStyle) -> np.ndarray:
    plan = libass._plan(libass._generate_source(tmp_path / "fond.mp4"), style=style)
    image, _command, subtitle_file = libass._render_image(plan, tmp_path, libass.CUE_START + libass.CUE_DURATION / 2)
    assert subtitle_file.endswith(".ass"), "un style personnalisé passe par un fichier ASS"
    return _pixels(image)


def _bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    assert len(xs), "aucun pixel"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _white(pixels: np.ndarray) -> np.ndarray:
    return (pixels > 190).all(axis=2)


def _green(pixels: np.ndarray, *, strict: bool = True) -> np.ndarray:
    return (pixels[:, :, 1] > 200) & (pixels[:, :, 0] < 80) & (pixels[:, :, 2] < 80) if strict else \
        (pixels[:, :, 1] - pixels[:, :, 0] > 25)


@pytest.mark.libass
def test_an_opaque_background_is_drawn_around_the_text_with_the_padding_of_the_style(tmp_path):
    """Avant : « 0 pixel vert » avec un fond vert opaque. Le fond déborde du texte de ``padding`` (unités ASS)."""
    style = TextStyle(background_color="#00FF00", background_opacity=1.0, padding_x=60.0, padding_y=30.0, **QUIET)
    pixels = _render(tmp_path, style)
    text_left, text_top, text_right, text_bottom = _bounds(_white(pixels))
    box_left, box_top, box_right, box_bottom = _bounds(_green(pixels))
    assert (text_right - text_left) > 100, "le texte est bien rendu"
    assert box_left <= text_left - 60 * SCALE + 3 and box_right >= text_right + 60 * SCALE - 3, "marge latérale"
    assert box_top <= text_top - 30 * SCALE + 3 and box_bottom >= text_bottom + 30 * SCALE - 3, "marge verticale"
    assert box_left >= text_left - 60 * SCALE - 12 and box_top >= text_top - 30 * SCALE - 12, "pas bien plus grand"
    assert _green(pixels).sum() > 3000, "une vraie boîte, pas un contour"


@pytest.mark.libass
def test_the_background_opacity_blends_the_box_with_the_picture(tmp_path):
    base = dict(background_color="#00FF00", padding_x=40.0, padding_y=20.0, **QUIET)
    opaque = _render(tmp_path, TextStyle(background_opacity=1.0, **base))
    half = _render(tmp_path, TextStyle(background_opacity=0.5, **base))
    none = _render(tmp_path, TextStyle(background_opacity=0.0, background_color=None, **QUIET))
    box = _green(opaque)
    assert box.sum() > 2000
    inside = half[box & ~_white(half) & ~_white(opaque)]
    assert inside.size and 100 < inside[:, 1].mean() < 170, "à 50 % le vert se mêle au fond sombre (≈ 150)"
    assert not _green(none, strict=False).any(), "sans fond, aucune boîte"


@pytest.mark.libass
@pytest.mark.parametrize("alignment, toward_x, toward_y", [
    (TextAlignment.BOTTOM_CENTER, -1, -1), (TextAlignment.TOP_LEFT, 1, 1), (TextAlignment.MIDDLE_CENTER, -1, 1),
    (TextAlignment.BOTTOM_RIGHT, -1, -1), (TextAlignment.TOP_RIGHT, -1, 1)], ids=lambda value: getattr(value, "value", value))
def test_the_offset_moves_the_text_by_that_share_of_the_frame(tmp_path, alignment, toward_x, toward_y):
    """15 % en x, 10 % en y : le texte se décale de 96 × 36 pixels sur un cadre de 640 × 360, quel que soit l'alignement
    (le sens est choisi pour que le texte reste dans l'image : un texte coupé ne se mesure plus)."""
    still = _bounds(_white(_render(tmp_path, TextStyle(alignment=alignment, **QUIET))))
    moved = _bounds(_white(_render(tmp_path, TextStyle(alignment=alignment, position_x=15.0 * toward_x,
                                                       position_y=10.0 * toward_y, **QUIET))))
    dx, dy = moved[0] - still[0], moved[1] - still[1]
    assert abs(dx - 0.15 * W * toward_x) <= 4 and abs(dy - 0.10 * H * toward_y) <= 4, f"décalage mesuré ({dx}, {dy})"
    assert moved[2] - moved[0] == pytest.approx(still[2] - still[0], abs=2), "la taille du texte ne change pas"


@pytest.mark.libass
def test_the_box_follows_the_text_when_the_offset_moves_it(tmp_path):
    style = TextStyle(background_color="#00FF00", background_opacity=1.0, padding_x=40.0, padding_y=20.0,
                      position_x=10.0, position_y=-20.0, **QUIET)
    pixels = _render(tmp_path, style)
    text, box = _bounds(_white(pixels)), _bounds(_green(pixels))
    assert box[0] < text[0] and box[1] < text[1] and box[2] > text[2] and box[3] > text[3], "le fond enveloppe le texte"
    still = _bounds(_white(_render(tmp_path, TextStyle(background_color="#00FF00", background_opacity=1.0,
                                                      padding_x=40.0, padding_y=20.0, **QUIET))))
    assert abs((still[1] - text[1]) - 0.20 * H) <= 4, "remonté de 20 % du cadre"


@pytest.mark.libass
def test_the_shadow_has_the_colour_of_the_style(tmp_path):
    """Avant : l'ombre était toujours transparente (``BackColour`` portait le fond) et ``shadow_color`` ignorée."""
    style = TextStyle(shadow_color="#FF0000", shadow_offset=14.0, outline_width=0.0, font_size=96.0)
    pixels = _render(tmp_path, style)
    red = (pixels[:, :, 0] > 200) & (pixels[:, :, 1] < 80) & (pixels[:, :, 2] < 80)
    assert red.sum() > 150, "une ombre rouge se dessine derrière le texte"


# --- l'aperçu du moniteur ----------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _frame(panel):
    """Le cadre de la séquence dans le repère de la couche des sous-titres."""
    from PySide6.QtCore import QPoint

    layer, canvas = panel._subtitle_layer, panel.canvas_rect()
    origin = layer.mapFromGlobal(panel.graphics_view.viewport().mapToGlobal(QPoint(0, 0)))
    return canvas.x() + origin.x(), canvas.y() + origin.y(), canvas.width(), canvas.height()


def _label_box(panel):
    label = panel.preview_subtitle_overlay
    return label.x(), label.y(), label.x() + label.width(), label.y() + label.height()


@pytest.mark.parametrize("alignment, column, row", [
    (TextAlignment.BOTTOM_CENTER, 1, 0), (TextAlignment.TOP_LEFT, 0, 2), (TextAlignment.MIDDLE_CENTER, 1, 1),
    (TextAlignment.BOTTOM_RIGHT, 2, 0), (TextAlignment.TOP_RIGHT, 2, 2), (TextAlignment.MIDDLE_LEFT, 0, 1)],
    ids=lambda value: getattr(value, "value", value))
def test_the_preview_places_the_subtitle_where_the_alignment_and_margins_say(window, alignment, column, row):
    panel = window.preview_panel
    panel.set_subtitle("Bonjour", TextStyle(alignment=alignment, margin_x=64.0, margin_y=72.0))
    fx, fy, fw, fh = _frame(panel)
    left, top, right, bottom = _label_box(panel)
    mx, my = 64.0 * fw / PLAY_RES_X, 72.0 * fh / PLAY_RES_Y
    # Gauche / droite : le bord à la marge ; centre : centré. Bas / haut : le bord à la marge ; milieu : centré.
    assert (left - fx, (left + right) / 2 - fx - fw / 2, right - fx - fw + mx)[column] == pytest.approx(
        (mx, 0.0, 0.0)[column], abs=2.0)
    assert (bottom - fy - fh + my, (top + bottom) / 2 - fy - fh / 2, top - fy - my)[row] == pytest.approx(0.0, abs=2.0)


def test_the_preview_applies_the_offset_as_a_share_of_the_frame(window):
    panel = window.preview_panel
    panel.set_subtitle("Bonjour", TextStyle())
    still = _label_box(panel)
    panel.set_subtitle("Bonjour", TextStyle(position_x=-20.0, position_y=-30.0))
    moved = _label_box(panel)
    _fx, _fy, fw, fh = _frame(panel)
    assert moved[0] - still[0] == pytest.approx(-0.20 * fw, abs=2.0)
    assert moved[1] - still[1] == pytest.approx(-0.30 * fh, abs=2.0)
    assert moved[2] - moved[0] == still[2] - still[0], "la taille ne change pas"


def test_the_preview_subtitle_follows_the_frame_when_the_window_is_resized(window, qtbot):
    panel = window.preview_panel
    panel.set_subtitle("Bonjour", TextStyle(alignment=TextAlignment.BOTTOM_CENTER))
    window.resize(1100, 760)
    qtbot.wait(50)
    fx, fy, fw, fh = _frame(panel)
    left, top, right, bottom = _label_box(panel)
    assert (left + right) / 2 == pytest.approx(fx + fw / 2, abs=2.0) and bottom < fy + fh
    assert bottom == pytest.approx(fy + fh - 48.0 * fh / PLAY_RES_Y, abs=2.0)
    panel.clear_subtitle()
    assert panel.preview_subtitle_overlay.isHidden()


def test_the_preview_wraps_a_long_subtitle_inside_the_video_frame_and_its_margins(window):
    """Avant : la largeur maximale venait de la couche entière (≥ 280 px), pas du cadre : un texte long dépassait la
    vidéo, surtout aligné à gauche ou à droite, et ne revenait pas à la ligne comme à l'export."""
    panel = window.preview_panel
    long_text = "Un sous-titre bien trop long pour tenir sur une seule ligne dans le cadre de la vidéo, " * 2
    for alignment in (TextAlignment.BOTTOM_LEFT, TextAlignment.BOTTOM_CENTER, TextAlignment.BOTTOM_RIGHT):
        panel.set_subtitle(long_text, TextStyle(alignment=alignment, margin_x=80.0))
        fx, _fy, fw, _fh = _frame(panel)
        left, _top, right, _bottom = _label_box(panel)
        margin = 80.0 * fw / PLAY_RES_X
        assert right - left <= fw - 2 * margin + 1, f"{alignment.value} : {right - left} px dans un cadre de {fw:.0f}"
        assert left >= fx - 1 and right <= fx + fw + 1, "le texte reste dans le cadre"
    window.resize(900, 700)
    window.repaint()
    panel.set_subtitle(long_text, TextStyle(alignment=TextAlignment.BOTTOM_LEFT, margin_x=80.0))
    fx, _fy, fw, _fh = _frame(panel)
    left, _top, right, _bottom = _label_box(panel)
    assert left >= fx - 1 and right <= fx + fw + 1, "un moniteur étroit : toujours dans le cadre"


# --- cues qui se recouvrent ----------------------------------------------------------------------------------------------------


def test_a_boxed_cue_always_carries_a_pos_so_libass_cannot_separate_its_box_from_its_text():
    from core.subtitle_io import format_ass_with_styles

    boxed = TextStyle(background_color="#00FF00", background_opacity=1.0, **QUIET)
    assert ass_position(boxed) is None and ass_position(boxed, force=True) == pytest.approx((960.0, 1032.0))
    entries = [(SubtitleCue(0.0, 1.0, "A"), TextStyle(**QUIET)), (SubtitleCue(0.0, 1.0, "B"), boxed)]
    lines = [line for line in format_ass_with_styles(entries, TextStyle(**QUIET)).splitlines()
             if line.startswith("Dialogue: ")]
    assert "\\pos" not in lines[0], "un cue sans fond ni décalage : comme avant"
    assert all("\\pos(960,1032)" in line for line in lines[1:]), "la boîte et le texte du cue à fond"


@pytest.mark.libass
@pytest.mark.parametrize("boxed_first", [True, False], ids=["fond d'abord", "fond ensuite"])
def test_a_boxed_cue_keeps_its_box_around_its_text_when_another_cue_overlaps(tmp_path, boxed_first):
    """Avant : un cue simple déclaré avant un cue à fond décalait sa boîte de ~39 pixels sans son texte."""
    import subprocess

    from core.subtitle_io import format_ass_with_styles

    plain = TextStyle(color="#FF3030", **QUIET)
    boxed = TextStyle(color="#FFFFFF", background_color="#00FF00", background_opacity=1.0, padding_x=30.0,
                      padding_y=20.0, **QUIET)
    entries = [(SubtitleCue(0.0, 2.5, "AAAA"), plain), (SubtitleCue(0.0, 2.5, "BBBB"), boxed)]
    if boxed_first:
        entries.reverse()
    script = tmp_path / "t.ass"
    script.write_text(format_ass_with_styles(entries, TextStyle(**QUIET)), encoding="utf-8")
    source = libass._generate_source(tmp_path / "fond.mp4")
    picture = tmp_path / "o.png"
    subprocess.run([*libass._ffmpeg_command_prefix(), "-y", "-v", "error", "-i", str(source), "-vf",
                    f"subtitles=filename={script}", "-frames:v", "1", str(picture)], check=True, timeout=240)
    image = QImage(str(picture))
    assert not image.isNull()
    pixels = _pixels(image)
    box, text = _bounds(_green(pixels)), _bounds(_white(pixels))
    assert box[0] <= text[0] and box[1] <= text[1] and box[2] >= text[2] and box[3] >= text[3], \
        f"le texte {text} sort de sa boîte {box}"
    assert abs(((box[1] + box[3]) - (text[1] + text[3])) / 2) <= 6, "la boîte est centrée sur son texte"
