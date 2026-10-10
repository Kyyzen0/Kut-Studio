"""Fenêtres, flou et netteté d'un nœud d'étalonnage : modèle, filtres de l'export mesurés sur le vrai FFmpeg, export réel.

Comme pour les mélangeurs (``tests/test_color_render.py``), les attendus sont recalculés ici en numpy : la correction
rendue seule par FFmpeg, puis combinée à la main par la clé (``maskedmerge`` 8 bits), ou la formule de la netteté.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph, MixerKind, as_graph, graph_from_dict, graph_to_dict, simplify
from core.color_qualifier import Qualifier
from core.color_render import WindowSourceMissing, detail_filters, windowed_nodes
from core.compositing import Mask, MaskShape
from core.export_engine import _build_color_grade_filters
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

BRIGHT = ColorGrade(gain=Wheel(y=0.5))
LEFT = Mask(shape=MaskShape.RECTANGLE, position_x=0.25, position_y=0.5, width=0.5, height=1.0)
BLUES = Qualifier(hue_center=210.0, hue_width=80.0, hue_soft=30.0, sat_low=0.2, sat_soft=0.1)

_IMAGE = np.random.default_rng(11).integers(0, 256, (40, 48, 3), dtype=np.uint8)


def _render(chain: str, image: np.ndarray = _IMAGE) -> np.ndarray:
    height, width, _ = image.shape
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-i", "-",
         "-vf", f"format=gbrp,{chain},format=gbrp,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=image.tobytes(), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return np.frombuffer(done.stdout, np.uint8).reshape(height, width, 3).astype(float)


def _blend(base: np.ndarray, over: np.ndarray, key: np.ndarray | float) -> np.ndarray:
    mask = np.rint(np.asarray(key, dtype=float) * 255.0)
    mask = mask[..., None] if mask.ndim == 2 else mask
    return np.floor((base * (255.0 - mask) + over * mask + 127.0) / 255.0)


def _windowed(grade=BRIGHT, **node) -> ColorNodeGraph:
    return ColorNodeGraph.serial((ColorNode("n1", grade, **node),))


# --- modèle ----------------------------------------------------------------------------------------------------------


def test_windows_blur_and_sharpen_survive_the_file_and_keep_the_node_a_graph():
    graph = _windowed(windows=(LEFT,), blur=3.0, sharpen=1.5)
    assert simplify(graph) is graph, "un seul nœud fenêtré reste un graphe (un ColorGrade n'a pas de fenêtre)"
    back = graph_from_dict(graph_to_dict(graph, lambda grade: {}), lambda raw: ColorGrade(gain=Wheel(y=0.5)))
    node = back.corrector("n1")
    assert node.windows == (LEFT,) and node.windows[0].id == LEFT.id, "l'identifiant suit (images-clés, tracking)"
    assert (node.blur, node.sharpen) == (3.0, 1.5)
    raw = graph_to_dict(graph, lambda grade: {})
    raw["nodes"][0]["windows"].append("abîmée")
    raw["nodes"][0]["blur"] = "beaucoup"
    damaged = graph_from_dict(raw, lambda raw: ColorGrade()).corrector("n1")
    assert damaged.windows == (LEFT,) and damaged.blur == 0.0, "une valeur abîmée est ignorée, pas le nœud"


def test_blur_and_sharpen_are_bounded_like_the_effects_and_make_the_graph_spatial():
    node = ColorNode("n1", blur=500.0, sharpen=-2.0)
    assert (node.blur, node.sharpen) == (50.0, 0.0)
    assert not as_graph(ColorGrade()).is_spatial()
    assert _windowed(blur=2.0).is_spatial() and _windowed(windows=(LEFT,)).is_spatial()
    assert not _windowed(grade=replace(BRIGHT, enabled=False), blur=2.0).is_spatial(), "contourné : plus rien"
    assert _windowed(ColorGrade(), blur=2.0).corrector("n1").is_active(), "un flou seul change l'image"
    assert not _windowed(ColorGrade(), windows=(LEFT,)).corrector("n1").is_active(), "une fenêtre seule, non"


def test_only_nodes_whose_window_matters_ask_for_a_matte():
    graph = _windowed(windows=(LEFT,))
    assert windowed_nodes(graph) == ("n1",)
    assert windowed_nodes(_windowed(ColorGrade(), windows=(LEFT,))) == (), "nœud neutre : rien à limiter"
    with pytest.raises(WindowSourceMissing):
        _build_color_grade_filters(graph)


# --- filtres de l'export ---------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_window_limits_the_correction_by_its_coverage():
    """Couverture constante (la pipette) : ``I + c·(G − I)`` ; avec un qualifieur, la clé est leur produit."""
    graded = _render(_build_color_grade_filters(BRIGHT))
    half = _render(_build_color_grade_filters(_windowed(windows=(LEFT,)), windows={"n1": 0.5}))
    assert np.array_equal(half, _blend(_IMAGE.astype(float), graded, 0.5))
    both = _windowed(windows=(LEFT,), qualifier=BLUES)
    keyed = _render(_build_color_grade_filters(both, windows={"n1": 0.5}))
    key = np.floor(np.rint(BLUES.key(_IMAGE / 255.0) * 255.0) * 128 / 255.0) / 255.0   # blend multiply 8 bits
    assert np.abs(keyed - _blend(_IMAGE.astype(float), graded, key)).max() <= 1


@needs_ffmpeg
def test_a_node_blur_is_gblur_on_the_corrected_picture():
    blurred = _render(_build_color_grade_filters(_windowed(blur=2.0)))
    assert np.array_equal(blurred, _render(f"{_build_color_grade_filters(BRIGHT)},format=gbrp,gblur=sigma=2"))
    assert detail_filters(ColorNode("n1", blur=2.0), pixel_scale=0.5) == "gblur=sigma=1.0", "σ suit la résolution"


@needs_ffmpeg
def test_a_node_sharpen_follows_the_unsharp_formula_on_every_channel():
    """``I + a·(I − B∗I)`` (``B`` : binomial 5×5), sur R, V et B, arrondi une fois ; au bord, l'image se réfléchit
    (``convolution`` : le pixel du bord n'est pas répété ; la passe du moniteur fait de même)."""
    amount = 1.25
    sharpened = _render(_build_color_grade_filters(_windowed(ColorGrade(), sharpen=amount)))
    image = _IMAGE.astype(float)
    weights = np.array([1, 4, 6, 4, 1], dtype=float) / 16.0
    padded = np.pad(image, ((2, 2), (2, 2), (0, 0)), mode="reflect")
    rows = sum(weights[i] * padded[i:i + image.shape[0], :, :] for i in range(5))
    blurred = sum(weights[j] * rows[:, j:j + image.shape[1], :] for j in range(5))
    expected = np.clip(np.floor(image + amount * (image - blurred) + 0.5), 0, 255)
    assert np.array_equal(sharpened, expected), np.abs(sharpened - expected).max()


@needs_ffmpeg
def test_a_window_matte_that_ends_up_unused_is_still_consumed():
    """Une branche fenêtrée recouverte par un calque sans clé n'est pas calculée : son flux de matte finit dans
    ``nullsink`` (FFmpeg refuse une sortie que rien ne lit)."""
    graph, top = _windowed(windows=(LEFT,)).with_branch("n1", MixerKind.LAYER)
    graph = graph.with_grade(top, ColorGrade(saturation=0.0))
    text = _build_color_grade_filters(graph, tag="t", windows={"n1": "w1"})
    assert "[w1]nullsink" in text


# --- export réel -----------------------------------------------------------------------------------------------------

W, H = 64, 36


def _project(media, grade, **transform) -> Project:
    clip = Clip("c", "v", "V1", 0.0, 0.0, 1.0)
    clip.color_grade = grade
    clip.transform = replace(clip.transform, **transform)
    return Project("p", width=W, height=H, fps=25.0,
                   media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                   tracks=[Track("V1", "V1", "video", clips=[clip])])


@needs_ffmpeg
def test_a_window_follows_the_clip_picture_when_the_clip_is_scaled(tmp_path):
    """La fenêtre couvre la moitié gauche **de l'image du clip** : plein cadre, la moitié gauche du cadre ; réduit de
    moitié et centré, le quart du cadre de 25 % à 50 % de sa largeur."""
    media = lavfi_video(tmp_path / "m.mp4", "color=c=0x606060:d=1", size=(W, H), seconds=1.0)
    graph = _windowed(windows=(LEFT,))
    frame = render_frame(build_render_plan(_project(media, graph)), W, H, 0.4).astype(float)
    assert frame[:, 4:28].mean() > 130 and abs(frame[:, 36:60].mean() - 96) < 6, "à gauche seulement"
    scaled = render_frame(build_render_plan(_project(media, graph, scale=0.5)), W, H, 0.4).astype(float)
    middle = scaled[12:24]
    assert middle[:, 18:30].mean() > 130, "la moitié gauche de l'image réduite"
    assert abs(middle[:, 34:46].mean() - 96) < 8, "sa moitié droite, pas corrigée"


@needs_ffmpeg
def test_an_inverted_window_blurs_the_background_and_keeps_the_subject_sharp(tmp_path):
    media = lavfi_video(tmp_path / "m.mp4", "testsrc2=d=1", size=(W, H), seconds=1.0)
    subject = replace(LEFT, inverted=True, id=LEFT.id)
    graph = _windowed(ColorGrade(), windows=(subject,), blur=4.0)
    plain = render_frame(build_render_plan(_project(media, ColorGrade())), W, H, 0.4).astype(float)
    shown = render_frame(build_render_plan(_project(media, graph)), W, H, 0.4).astype(float)
    assert np.abs(shown[:, 4:28] - plain[:, 4:28]).mean() < 2, "dans la fenêtre (inversée) : net"
    assert np.abs(shown[:, 38:60] - plain[:, 38:60]).mean() > 8, "le reste : flou"


# --- hors export : moniteur, pipette ---------------------------------------------------------------------------------


def test_the_window_matte_and_coverage_come_from_the_export_rasterizer(qapp):
    """La matte du moniteur et la couverture sous la pipette : même rastériseur, même animation (images-clés
    ``mask.<id>.*`` du clip) que l'export."""
    from core.animation import Keyframe
    from core.color_windows import window_coverage, window_matte
    from core.compositing import mask_property_id

    project = _project("absent.mp4", _windowed(windows=(LEFT,)))
    clip = project.tracks[0].clips[0]
    key, matte = window_matte(project, "c", (LEFT,), 0.0, W, H)
    assert (matte.width(), matte.height()) == (W, H)
    assert matte.pixelColor(8, 18).alpha() == 255 and matte.pixelColor(56, 18).alpha() == 0
    assert window_coverage(project, "c", (LEFT,), 0.0, (8.0, 18.0)) == 1.0
    assert window_coverage(project, "c", (LEFT,), 0.0, (56.0, 18.0)) == 0.0
    clip.animation = [Keyframe(mask_property_id(LEFT.id, "position_x"), 0.0, 0.25),
                      Keyframe(mask_property_id(LEFT.id, "position_x"), 1.0, 0.75)]
    moved, later = window_matte(project, "c", (LEFT,), 1.0, W, H)
    assert moved != key and later.pixelColor(56, 18).alpha() == 255, "la fenêtre animée a glissé à droite"
    assert window_coverage(project, "c", (LEFT,), 1.0, (8.0, 18.0)) == 0.0


@needs_ffmpeg
def test_the_eyedropper_reads_through_an_upstream_window_by_its_coverage(tmp_path):
    from core.color_pick import sample_node_input, upstream_graph

    media = lavfi_video(tmp_path / "m.mp4", "color=c=0x3366CC:d=1", size=(W, H), seconds=1.0)
    graph, second = _windowed(ColorGrade(saturation=0.0), windows=(LEFT,), blur=3.0).with_node_after("n1")
    upstream = upstream_graph(graph, second)
    outside = sample_node_input(str(media), 0.2, 50, 18, upstream, windows={"n1": 0.0})
    inside = sample_node_input(str(media), 0.2, 10, 18, upstream, windows={"n1": 1.0})
    assert outside[2] > outside[0] + 0.3, "hors de la fenêtre : le bleu du clip"
    assert max(inside) - min(inside) < 0.03, "dans la fenêtre : le gris du nœud (son flou ignoré)"
