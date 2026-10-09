"""Pipette du qualificateur : la couleur qui arrive au nœud courant sous le clic, et le qualifieur qui l'entoure."""

from __future__ import annotations

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import MixerKind, as_graph
from core.color_pick import canvas_to_media, qualifier_around, sample_node_input, upstream_graph
from core.color_qualifier import Qualifier
from core.tracking_motion import fit_box

BLUE = (0.2, 0.4, 0.8)


def test_the_upstream_of_a_node_is_what_feeds_it():
    graph, second = as_graph(ColorGrade(exposure=0.2)).with_node_after("n1")
    graph, third = graph.with_node_after(second)
    assert upstream_graph(graph, "n1") is None, "le premier nœud lit l'image du clip"
    upstream = upstream_graph(graph, third)
    assert {node.id for node in upstream.nodes} == {"n1", second} and upstream.sink.id == second
    branched, branch = graph.with_branch(third, MixerKind.PARALLEL)
    assert {node.id for node in upstream_graph(branched, branch).nodes} == {"n1", second}, \
        "une branche reçoit l'image du point de séparation"


def test_a_picked_colour_becomes_ranges_around_it():
    qualifier = qualifier_around(BLUE)
    assert qualifier.enabled and qualifier.use_hue and qualifier.hue_center == pytest.approx(220.0)
    assert qualifier.key(np.array(BLUE)) == pytest.approx(1.0)
    assert qualifier.key(np.array((0.8, 0.4, 0.2))) == 0.0, "l'orange n'est pas pris"
    grey = qualifier_around((0.5, 0.5, 0.5))
    assert not grey.use_hue and grey.lum_low < 0.5 < grey.lum_high, "un gris : pas de teinte, sa luminance"


def test_shift_widens_the_selection_to_the_new_colour():
    base = qualifier_around(BLUE)
    teal = (0.2, 0.7, 0.7)
    widened = qualifier_around(teal, base, extend=True)
    assert widened.key(np.array(BLUE)) == pytest.approx(1.0) and widened.key(np.array(teal)) == pytest.approx(1.0)
    assert widened.hue_width > base.hue_width
    fresh = qualifier_around(teal, base, extend=False)
    assert fresh.key(np.array(BLUE)) == 0.0, "sans Maj, la nouvelle couleur remplace l'ancienne"
    assert qualifier_around(teal, Qualifier(invert=True), extend=False).invert, "l'inversion choisie reste"


def test_a_viewer_point_maps_to_the_media_pixel_under_it():
    fit = fit_box(1280, 720, 1920, 1080)
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    assert canvas_to_media((960.0, 540.0), identity, fit) == pytest.approx((640.0, 360.0))
    half = (0.5, 0.0, 0.0, 0.5, 480.0, 270.0)                      # réduit de moitié, centré
    assert canvas_to_media((960.0, 540.0), half, fit) == pytest.approx((640.0, 360.0))
    assert canvas_to_media((100.0, 100.0), half, fit) is None, "hors de l'image du clip"
    portrait = fit_box(1080, 1920, 1920, 1080)                       # bandes noires de part et d'autre
    assert canvas_to_media((100.0, 540.0), identity, portrait) is None


@needs_ffmpeg
def test_the_sample_is_the_colour_reaching_the_node(tmp_path):
    """Un plan bleu uni : la pipette du premier nœud lit le bleu ; celle d'un nœud placé après une désaturation lit
    un gris (ce qui arrive à ce nœud)."""
    media = lavfi_video(tmp_path / "blue.mp4", "color=c=0x3366CC:d=1", size=(64, 36), seconds=1.0)
    r, g, b = sample_node_input(str(media), 0.2, 32, 18, None)
    assert b > r + 0.3 and abs(r - 0.2) < 0.05 and abs(b - 0.8) < 0.05, (r, g, b)
    graph, second = as_graph(ColorGrade(saturation=0.0)).with_node_after("n1", ColorGrade(gain=Wheel(y=0.1)))
    grey = sample_node_input(str(media), 0.2, 63, 35, upstream_graph(graph, second))
    assert max(grey) - min(grey) < 0.03, grey
