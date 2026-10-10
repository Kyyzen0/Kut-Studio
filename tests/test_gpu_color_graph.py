"""Graphe d'étalonnage spatial dans le moniteur GPU : opérations compilées, passes, référence contre l'export réel.

Le vrai GPU est comparé à la référence numpy par ``tools/gpu/selfcheck.py`` (cas ``grade_graph``) ; ici, la référence
(:func:`core.gpu_composite.reference_frame`), nourrie des LUT cuites par FFmpeg et de la matte rastérisée par le code
de l'export, doit retrouver l'image de l'export.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from render_probe import needs_ffmpeg

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph, MixerKind, as_graph
from core.color_qualifier import Qualifier
from core.color_render import Highlight
from core.compositing import Mask, MaskShape
from core.export_engine import _build_color_grade_filters
from core.gpu_color_graph import INPUT, GpuColorPass, compile_program
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, plan_frame

WARM = ColorGrade(gain=Wheel(0.2, 0.0, -0.2, 0.1))
GREY = ColorGrade(saturation=0.0)
LEFT = Mask(shape=MaskShape.ELLIPSE, position_x=0.35, position_y=0.5, width=0.5, height=0.7, feather=0.05)
BLUES = Qualifier(hue_center=210.0, hue_width=80.0, hue_soft=30.0, sat_low=0.2, sat_soft=0.1)


def _compile(value):
    return compile_program(value, _build_color_grade_filters)


def _ops(program):
    return [(step.op, step.inputs) for step in program.steps]


def test_a_graph_without_window_blur_or_sharpen_stays_one_lut():
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    assert _compile(graph.with_qualifier(top, BLUES)) is None
    assert _compile(WARM) is None


def test_a_windowed_node_corrects_its_input_through_its_key():
    graph = ColorNodeGraph.serial((ColorNode("n1", WARM, windows=(LEFT,), qualifier=BLUES),))
    program = _compile(graph)
    assert _ops(program) == [("lut", (INPUT,)), ("key", (INPUT,)), ("mix", (INPUT, 1, 2))]
    key = program.steps[1]
    assert key.window == "n1" and "lut3d" in key.bake.text, "clé = qualifieur × fenêtres"
    assert program.steps[0].bake.text.startswith("format=gbrp,") and program.result == 3
    assert program.windows() == ("n1",)


def test_blur_and_sharpen_act_on_the_correction_before_the_key():
    node = ColorNode("n1", ColorGrade(), windows=(LEFT,), blur=3.0, sharpen=1.0)
    program = _compile(ColorNodeGraph.serial((node,)))
    assert _ops(program) == [("blur", (INPUT,)), ("sharpen", (1,)), ("key", (INPUT,)), ("mix", (INPUT, 2, 3))]
    assert program.steps[2].bake is None, "fenêtre seule : pas de LUT de clé"


def test_mixers_become_sums_and_keyed_layers():
    graph, second = ColorNodeGraph.serial((ColorNode("n1", WARM, blur=2.0),)).with_branch("n1", MixerKind.PARALLEL)
    graph, third = graph.with_grade(second, GREY).with_branch(second, MixerKind.PARALLEL)
    program = _compile(graph.with_grade(third, ColorGrade(exposure=0.3)))
    adds = [step for step in program.steps if step.op == "add"]
    assert len(adds) == 2 and [step.clamp for step in adds] == [False, True], "écrêté une fois, à la fin"
    assert all(step.inputs[2] == INPUT for step in adds), "les branches corrigent l'image de leur séparation"
    layered, top = ColorNodeGraph.serial((ColorNode("n1", WARM, blur=2.0),)).with_branch("n1", MixerKind.LAYER)
    program = _compile(layered.with_grade(top, GREY).with_qualifier(top, BLUES))
    assert program.steps[-1].op == "mix" and program.steps[-2].op == "mix", "le calque qualifié pose sa correction"


def test_show_selection_greys_what_the_window_leaves_out():
    graph = ColorNodeGraph.serial((ColorNode("n1", WARM, windows=(LEFT,), blur=1.0),))
    program = _compile(Highlight(graph, "n1"))
    grey = [step for step in program.steps if step.op == "lut" and "colorchannelmixer=.3" in step.bake.text]
    assert grey and program.steps[-1].inputs[0] == grey[0].target


def test_the_plan_recycles_working_textures():
    nodes = tuple(ColorNode(f"n{i}", WARM, windows=(LEFT,)) for i in range(1, 11))
    program = _compile(ColorNodeGraph.serial(nodes))
    passes = tuple(GpuColorPass(step.op, step.target, step.inputs, "lut:x" if step.bake else "",
                                "win" if step.window else "") for step in program.steps)
    layer = CompositeLayer("v", (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (0, 0, 64, 36), grade_passes=passes,
                           grade_result=program.result, grade_split=0.3)
    plan = plan_frame(CompositeFrame(64, 36, 1.0, (layer,), (VideoSource("v", "yuv420p", 64, 36),)))
    shaders = [step.shader for step in plan.passes]
    assert shaders.count("grade") == 10 and shaders.count("colorkey") == 10 and shaders.count("colormix") == 11
    working = [name for name in plan.textures if name.startswith("layer0g")]
    assert len(working) <= 4, f"dix nœuds, {len(working)} textures de travail"
    for step in plan.passes:
        assert step.target not in step.inputs, "une passe ne lit jamais la texture qu'elle écrit"


# --- référence contre export -----------------------------------------------------------------------------------------

WIDTH, HEIGHT = 160, 90


def _spatial_graph() -> ColorNodeGraph:
    """Un nœud fenêtré (réchauffe l'ellipse), puis une branche parallèle grise et floutée à côté d'un contraste
    affûté."""
    from dataclasses import replace

    graph = ColorNodeGraph.serial((ColorNode("n1", WARM, windows=(LEFT,)),))
    graph, branch = graph.with_node_after("n1", ColorGrade(contrast=0.2))
    graph = graph.with_node(replace(graph.corrector(branch), sharpen=0.8))
    graph, other = graph.with_branch(branch, MixerKind.PARALLEL)
    return graph.with_node(replace(graph.corrector(other), grade=GREY, blur=1.5))


def _export_and_reference(tmp_path, codes, graph, scale):
    """``(image de l'export, image de la référence GPU)`` du clip ``codes`` étalonné par ``graph``, réduit à
    ``scale`` ; ``graph`` à ``None`` : sans étalonnage."""
    from PySide6.QtGui import QImage

    from core.color_windows import window_matte
    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.gpu_composite import reference_frame
    from core.gpu_grade import DOMAIN_RGB, DOMAIN_YUV, atlas_array, bake_grade_lut, lut_key
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan
    from core.tracking_motion import fit_box, video_layer_matrix
    from core.visual_effects import ClipTransform
    from tests.gpu_harness import reference_codes

    width, height = WIDTH, HEIGHT
    source = tmp_path / "src.mkv"
    raw = codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{width}x{height}", "-r", "10", "-i", "-", "-c:v", "ffv1", str(source)],
                   input=raw * 10, check=True)
    project = Project(name="g", width=width, height=height, fps=10)
    project.media_assets.append(MediaAsset("a", str(source), "src", 1.0, width, height, 10, "video"))
    clip = Clip("v", "a", "V1", 0.0, 0.0, 1.0)
    clip.transform = ClipTransform(scale=scale)
    clip.color_grade = graph
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[clip]))
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "x.mp4"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (width, height), 18, "64k"), fps=10)
    done = subprocess.run(ExportEngine().build_frame_command(request, 0.25), capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    image = QImage()
    image.loadFromData(done.stdout)
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    exported = np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())
    exported = exported[:, : width * 3].reshape(height, width, 3) / 255.0

    luts, mattes, passes, result = {}, {}, [], 0
    program = _compile(graph) if graph is not None else None
    if program is not None:
        window_key, matte = window_matte(project, "v", (LEFT,), 0.25, width, height)
        rgba = matte.convertToFormat(QImage.Format.Format_RGBA8888)          # gardée : ses octets sont lus ensuite
        mattes[window_key] = np.frombuffer(rgba.constBits(), np.uint8).reshape(height, width, 4)[..., 3] / 255.0
        for step in program.steps:
            lut = ""
            if step.bake is not None:
                domain = DOMAIN_YUV if step.inputs[0] == INPUT else DOMAIN_RGB
                options = {"domain": domain, "color_range": "video"} if domain == DOMAIN_YUV else {"domain": domain}
                lut = f"lut:{lut_key(step.bake, **options)}"
                luts[lut] = atlas_array(bake_grade_lut(step.bake, **options))
            passes.append(GpuColorPass(step.op, step.target, step.inputs, lut, window_key if step.window else "",
                                       step.amount, step.clamp))
        result = program.result
    values = clip.transform
    layer = CompositeLayer("v", tuple(video_layer_matrix(values, width, height)),
                           fit_box(width, height, width, height).rect, effect_scale=(values.scale, values.scale),
                           grade_passes=tuple(passes), grade_result=result)
    frame = CompositeFrame(width, height, 1.0, (layer,), (VideoSource("v", "yuv420p", width, height),))
    return exported, reference_frame(frame, {"v": reference_codes(codes)}, mattes, luts)


def _interior(scale):
    return (slice(12, -12), slice(20, -20)) if scale == 1.0 else (slice(25, -25), slice(42, -42))


@needs_ffmpeg
@pytest.mark.parametrize("scale, mean_limit, p99_limit", [(1.0, 2.5, 6.5), (0.6, 3.0, 8.0)],
                         ids=["plein-cadre", "reduit"])
def test_the_gpu_reference_of_a_spatial_graph_matches_the_export(tmp_path, scale, mean_limit, p99_limit, qapp):
    """Chroma uniforme (une couleur franche, des détails en luminance) : rien ne vient de la chroma 4:2:0, que l'export
    et le moniteur reconstruisent chacun à leur manière.

    Mesuré (FFmpeg 9, arm64) : sans étalonnage 0,41 niveau ; chaque opération seule 0,34 (gris flouté) à 0,83 (contraste
    affûté) ; la somme parallèle additionne l'écart de ses branches (0,97) ; le graphe entier 1,1 (99ᵉ centile 3,9),
    1,8 (5,3) une fois réduit (``scale`` bicubique de l'export contre bilinéaire du GPU, sur une image affûtée). Sur x86
    (CI : Ubuntu FFmpeg 6.1, Debian FFmpeg 7.1, Windows), les chemins SIMD de FFmpeg arrondissent autrement : 1,76
    (99ᵉ centile 4,9) plein cadre, la même valeur sur les trois. Les seuils sont ceux des autres comparaisons de
    l'aperçu à l'export (``tests/test_gpu_pipeline.py``)."""
    from tests.gpu_harness import smooth_pattern

    codes = smooth_pattern(WIDTH, HEIGHT)
    codes[..., 1], codes[..., 2] = 150, 110
    exported, shown = _export_and_reference(tmp_path, codes, _spatial_graph(), scale)
    error = np.abs(shown - exported)[_interior(scale)] * 255
    assert error.mean() < mean_limit and np.percentile(error, 99) < p99_limit, \
        (error.mean(), np.percentile(error, 99))
    plain, _ = _export_and_reference(tmp_path, codes, None, scale)
    assert np.abs(plain - exported)[_interior(scale)].mean() * 255 > 8, "l'étalonnage change vraiment l'image"


@needs_ffmpeg
def test_on_a_colourful_picture_the_graph_adds_nothing_to_the_4_2_0_gap(tmp_path, qapp):
    """Mire colorée : l'export et la référence reconstruisent la chroma 4:2:0 différemment (≈ 1 à 2 niveaux, déjà
    sans étalonnage) ; le graphe (gains, netteté) amplifie cet écart sans en créer d'autre."""
    from tests.gpu_harness import smooth_pattern

    codes = smooth_pattern(WIDTH, HEIGHT)
    exported, shown = _export_and_reference(tmp_path, codes, _spatial_graph(), 1.0)
    plain_export, plain_shown = _export_and_reference(tmp_path, codes, None, 1.0)
    error = np.abs(shown - exported)[_interior(1.0)].mean() * 255
    baseline = np.abs(plain_shown - plain_export)[_interior(1.0)].mean() * 255
    assert error < 2.0 * baseline + 0.5, (error, baseline)
