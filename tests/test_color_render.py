"""Nœuds parallèles, calques et qualifieur : le graphe de filtres de l'export, mesuré sur des pixels du vrai FFmpeg.

Les attendus sont calculés ici, en numpy : chaque branche est rendue **seule** par FFmpeg (sa chaîne exacte, déjà
couverte par ses propres tests), puis combinée à la main (somme des corrections, superposition par la clé, formule de
la clé écrite par :class:`~core.color_qualifier.Qualifier`). Le graphe complet doit retrouver cette combinaison.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNodeGraph, MixerKind, as_graph
from core.color_qualifier import KEY_LUT_SIZE, Qualifier, key_cube_path
from core.color_render import Highlight
from core.export_engine import _build_color_grade_filters
from core.gpu_grade import DOMAIN_RGB, atlas_array, bake_grade_lut, grade_is_active, lut_key
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

WARM = ColorGrade(gain=Wheel(0.2, 0.0, -0.2, 0.1))
BRIGHT = ColorGrade(gamma=Wheel(y=0.4))
GREY = ColorGrade(saturation=0.0)
BLUES = Qualifier(hue_center=210.0, hue_width=80.0, hue_soft=30.0, sat_low=0.2, sat_soft=0.1)

_IMAGE = np.random.default_rng(7).integers(0, 256, (48, 48, 3), dtype=np.uint8)


def _render(chain: str, image: np.ndarray = _IMAGE) -> np.ndarray:
    """``image`` (RVB) passée dans ``chain`` par FFmpeg, entrée et sortie en ``gbrp`` comme dans le graphe."""
    height, width, _ = image.shape
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-i", "-",
         "-vf", f"format=gbrp,{chain},format=gbrp,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=image.tobytes(), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return np.frombuffer(done.stdout, np.uint8).reshape(height, width, 3).astype(float)


def _alone(grade: ColorGrade) -> np.ndarray:
    return _render(_build_color_grade_filters(grade))


def _blend(base: np.ndarray, over: np.ndarray, key: np.ndarray) -> np.ndarray:
    """``maskedmerge`` 8 bits : la clé quantifiée en niveaux, arrondi au plus proche."""
    mask = np.rint(key * 255.0)[..., None]
    return np.floor((base * (255.0 - mask) + over * mask + 127.0) / 255.0)


# --- mélangeurs ------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_parallel_nodes_add_their_corrections_exactly():
    graph, second = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    graph, third = graph.with_grade(second, BRIGHT).with_branch(second, MixerKind.PARALLEL)
    graph = graph.with_grade(third, GREY)
    assert len(graph.mixers) == 1, "la troisième branche rejoint le même mélangeur"
    expected = np.clip(_alone(WARM) + _alone(BRIGHT) + _alone(GREY) - 2 * _IMAGE.astype(float), 0, 255)
    assert np.array_equal(_render(_build_color_grade_filters(graph)), expected)


@needs_ffmpeg
def test_parallel_branches_correct_the_image_of_their_split_point():
    """Un nœud avant la séparation : chaque branche corrige **son** image, pas celle du clip."""
    graph, before = as_graph(ColorGrade()).with_node_after("n1", BRIGHT)
    graph = graph.without("n1")
    graph, branch = graph.with_node_after(before, WARM)
    graph, other = graph.with_branch(branch, MixerKind.PARALLEL)
    graph = graph.with_grade(other, GREY)
    assert graph.split_point(graph.mixers[0].id) == before
    lifted = _alone(BRIGHT)
    expected = np.clip(_render(_build_color_grade_filters(WARM), lifted.astype(np.uint8))
                       + _render(_build_color_grade_filters(GREY), lifted.astype(np.uint8)) - lifted, 0, 255)
    assert np.array_equal(_render(_build_color_grade_filters(graph)), expected)


@needs_ffmpeg
def test_a_qualified_node_corrects_only_what_its_key_selects():
    graph = as_graph(GREY).with_qualifier("n1", BLUES)
    key = BLUES.key(_IMAGE / 255.0)
    assert 0.1 < (key > 0.5).mean() < 0.6, "l'image de test a du bleu et autre chose"
    rendered = _render(_build_color_grade_filters(graph))
    error = np.abs(rendered - _blend(_IMAGE.astype(float), _alone(GREY), key))
    # La clé passe par une LUT 3D 65³ (tétraédrique) : à moins d'un niveau près de la formule, en moyenne bien moins.
    assert error.mean() < 0.15 and error.max() <= 2, (error.mean(), error.max())
    # Loin de la sélection (au-delà d'une maille du réseau de la clé), rien ne change, au niveau près.
    far = Qualifier(hue_center=210.0, hue_width=80.0, hue_soft=60.0, sat_low=0.1, sat_soft=0.2).key(_IMAGE / 255.0) == 0
    assert far.mean() > 0.3 and np.array_equal(rendered[far], _IMAGE[far]), "hors sélection, rien ne change"


@needs_ffmpeg
def test_a_layer_goes_over_the_ones_below_where_its_key_selects_it():
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.LAYER)
    graph = graph.with_grade(top, GREY).with_qualifier(top, BLUES)
    key = BLUES.key(_IMAGE / 255.0)
    error = np.abs(_render(_build_color_grade_filters(graph)) - _blend(_alone(WARM), _alone(GREY), key))
    assert error.mean() < 0.15 and error.max() <= 2, (error.mean(), error.max())


@needs_ffmpeg
def test_a_layer_without_key_covers_everything_below():
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.LAYER)
    graph = graph.with_grade(top, GREY)
    text = _build_color_grade_filters(graph)
    assert "gain" not in text and np.array_equal(_render(text), _alone(GREY)), "le calque recouvert n'est pas calculé"


def test_a_graph_that_changes_nothing_has_no_filters_and_nothing_to_show():
    graph, other = as_graph(ColorGrade()).with_branch("n1", MixerKind.PARALLEL)
    graph = graph.with_qualifier(other, BLUES)
    assert _build_color_grade_filters(graph) == "" and not grade_is_active(graph)


def test_labels_carry_the_callers_prefix_so_two_clips_never_collide():
    graph, _ = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    first, second = _build_color_grade_filters(graph, tag="v0cg"), _build_color_grade_filters(graph, tag="v1cg")
    labels = lambda text: {part.split("]")[0] for part in text.split("[")[1:]}  # noqa: E731
    assert labels(first) and not labels(first) & labels(second)
    # Insérable dans une chaîne à virgules : commence et finit par un filtre (aucun label libre aux deux bouts).
    assert not first.startswith("[") and not first.endswith("]") and first.split(";")[-1].endswith("alphamerge")


# --- qualifieur ------------------------------------------------------------------------------------------------------


def test_the_key_formula_ranges_and_softness():
    q = Qualifier(hue_center=0.0, hue_width=40.0, hue_soft=20.0, use_sat=False, use_lum=False)
    hues = {0: [1.0, 0.0, 0.0], 30: [1.0, 0.5, 0.0], 60: [1.0, 1.0, 0.0], 345: [1.0, 0.0, 0.25]}
    keys = {hue: float(q.key(np.array(rgb))) for hue, rgb in hues.items()}
    assert keys[0] == 1.0 and keys[345] == 1.0 and keys[30] == pytest.approx(0.5) and keys[60] == 0.0
    assert float(q.key(np.array([0.5, 0.5, 0.5]))) == 0.0, "un gris n'a pas de teinte"
    lum = Qualifier(use_hue=False, use_sat=False, lum_low=0.4, lum_high=0.6, lum_soft=0.1)
    assert [float(lum.key(np.full(3, value))) for value in (0.25, 0.35, 0.5, 0.65, 0.75)] == \
        pytest.approx([0.0, 0.5, 1.0, 0.5, 0.0])
    assert float(Qualifier(invert=True, use_hue=False, use_sat=False, lum_high=0.5).key(np.full(3, 0.8))) == 1.0


def test_a_qualifier_that_selects_everything_does_not_restrict():
    assert not Qualifier().restricts() and not Qualifier(enabled=False, hue_width=10).restricts()
    assert Qualifier(lum_low=0.2).restricts() and Qualifier(invert=True, use_hue=False).restricts()
    graph = as_graph(GREY).with_qualifier("n1", Qualifier())
    assert _build_color_grade_filters(graph) == _build_color_grade_filters(GREY)


def test_the_key_lut_is_written_once_and_named_by_its_content():
    path = key_cube_path(BLUES)
    assert path.read_text(encoding="utf-8").count("\n") == KEY_LUT_SIZE ** 3 + 2
    stamp = path.stat().st_mtime_ns
    assert key_cube_path(Qualifier(**{**BLUES.to_dict()})) == path and path.stat().st_mtime_ns == stamp
    assert key_cube_path(Qualifier(hue_center=100.0)) != path


def test_a_qualifier_survives_the_file_and_a_broken_one_is_dropped():
    from core.color_nodes import graph_from_dict, graph_to_dict
    from core.project_io import _color_grade_to_dict, _deserialize_color_grade

    graph, top = as_graph(WARM).with_qualifier("n1", BLUES).with_branch("n1", MixerKind.LAYER)
    raw = graph_to_dict(graph, _color_grade_to_dict)
    assert graph_from_dict(raw, _deserialize_color_grade) == graph
    raw["nodes"][0]["qualifier"] = {"hue_width": 999}
    assert graph_from_dict(raw, _deserialize_color_grade).corrector("n1").qualifier is None


# --- export réel, calque d'effets, moniteur --------------------------------------------------------------------------

W, H = 64, 36


def _project(media) -> Project:
    return Project("p", width=W, height=H, fps=25.0,
                   media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                   tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0),
                                                              Clip("d", "v", "V1", 0.0, 0.0, 1.0)])])


@needs_ffmpeg
def test_two_clips_with_branched_nodes_export_together(tmp_path):
    """Deux clips aux mêmes nœuds dans le même graphe d'export : labels distincts, rendu réel."""
    media = lavfi_video(tmp_path / "m.mp4", "testsrc2=d=1", size=(W, H), seconds=1.0)
    project = _project(media)
    project.tracks[0].clips[1].timeline_start = 1.0
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    graph = graph.with_grade(top, GREY).with_qualifier(top, BLUES)
    for clip in project.tracks[0].clips:
        clip.color_grade = graph
    plain = _project(media)
    plain.tracks[0].clips[1].timeline_start = 1.0
    graded = render_frame(build_render_plan(project), W, H, 1.3).astype(float)
    reference = render_frame(build_render_plan(plain), W, H, 1.3).astype(float)
    assert np.abs(graded - reference).mean() > 3, "le second clip est bien étalonné"


@needs_ffmpeg
def test_a_masked_clip_keeps_its_transparency_through_branched_nodes(tmp_path):
    """Un masque (alpha posé avant l'étalonnage) : hors du masque, le fond noir reste visible ; le sous-graphe à
    branches (en ``gbrp``, sans alpha) faisait reparaître le clip, opaque, sur tout le cadre."""
    from core.compositing import Compositing, Mask, MaskShape

    media = lavfi_video(tmp_path / "m.mp4", "color=c=0xB4643C:d=1", size=(W, H), seconds=1.0)
    project = _project(media)
    project.tracks[0].clips = project.tracks[0].clips[:1]
    clip = project.tracks[0].clips[0]
    clip.compositing = Compositing(masks=(Mask(shape=MaskShape.RECTANGLE, width=0.4, height=0.5),))
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    clip.color_grade = graph.with_grade(top, GREY).with_qualifier(top, BLUES)
    frame = render_frame(build_render_plan(project), W, H, 0.4).astype(float)
    assert frame[2:6, 2:6].max() < 8, "hors du masque : le fond, pas le clip"
    assert frame[H // 2 - 2:H // 2 + 2, W // 2 - 2:W // 2 + 2].mean() > 40, "dans le masque : le clip étalonné"


@needs_ffmpeg
def test_an_effects_layer_with_branched_nodes_keeps_the_picture_under_it(tmp_path):
    from core.graphics import add_graphic_clip

    media = lavfi_video(tmp_path / "m.mp4", "testsrc2=d=1", size=(W, H), seconds=1.0)
    project = _project(media)
    plain = render_frame(build_render_plan(project), W, H, 0.4).astype(float)
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0.0, duration=1.0)
    graph, top = as_graph(BRIGHT).with_branch("n1", MixerKind.PARALLEL)
    adjustment.color_grade = graph.with_grade(top, WARM)
    graded = render_frame(build_render_plan(project), W, H, 0.4).astype(float)
    assert 3 < np.abs(graded - plain).mean() < 80, "étalonné, et l'image reste là (l'alpha n'a pas tout effacé)"


@needs_ffmpeg
def test_the_monitor_bakes_a_branched_graph_and_shows_the_selection(tmp_path):
    graph, top = as_graph(WARM).with_branch("n1", MixerKind.PARALLEL)
    graph = graph.with_grade(top, GREY).with_qualifier(top, BLUES)
    plain = atlas_array(bake_grade_lut(ColorGrade(), domain=DOMAIN_RGB))
    baked = atlas_array(bake_grade_lut(graph, domain=DOMAIN_RGB))
    shown = atlas_array(bake_grade_lut(Highlight(graph, top), domain=DOMAIN_RGB))
    assert np.abs(baked - plain).mean() * 255 > 5
    assert np.abs(shown - baked).mean() * 255 > 5, "hors sélection, le moniteur grise"
    assert grade_is_active(Highlight(graph, top))
    assert lut_key(Highlight(graph, top), domain=DOMAIN_RGB) != lut_key(graph, domain=DOMAIN_RGB)


def test_a_graph_can_still_be_built_from_serial_nodes_only():
    """Les fichiers de l'étape 1 (série) restent des graphes valides, rendus comme avant : la chaîne à virgules."""
    serial = ColorNodeGraph.serial(as_graph(WARM).nodes)
    assert ";" not in _build_color_grade_filters(serial)
