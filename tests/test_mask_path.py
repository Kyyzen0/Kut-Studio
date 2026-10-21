"""ADR-0006 lot 3b : masque en courbe de Bézier fermée (`MaskShape.PATH`) : modèle, fichier, rendu Qt et FFmpeg."""

from __future__ import annotations

import copy
import json
import math
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from core import mask_path
from core.compositing import (
    Compositing,
    Mask,
    MaskMode,
    MaskShape,
    build_ffmpeg_filters,
    compositing_from_dict,
    compositing_to_dict,
    mask_from_dict,
    mask_to_dict,
)
from core.graphics import add_graphic_clip, update_graphic
from core.mograph_raster import (
    MographRenderer,
    mask_path as raster_mask_path,
    scene_for_plan,
)
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H = 160, 90
K = mask_path.KAPPA
CIRCLE_NODES = ((0.0, -0.5), (0.5, 0.0), (0.0, 0.5), (-0.5, 0.0))
CIRCLE_HANDLES = (
    (-K / 2, 0.0, K / 2, 0.0),
    (0.0, -K / 2, 0.0, K / 2),
    (K / 2, 0.0, -K / 2, 0.0),
    (0.0, K / 2, 0.0, -K / 2),
)
NOTCHED = (
    (-0.5, -0.5),
    (0.0, 0.0),
    (0.5, -0.5),
    (0.5, 0.5),
    (-0.5, 0.5),
)  # un « M » : encoche en haut au centre


# --- Modèle ------------------------------------------------------------------------------------------------------------


def test_a_path_without_nodes_falls_back_to_the_documented_default():
    mask = Mask(shape=MaskShape.PATH)
    assert (
        mask.points == mask_path.DEFAULT_NODES
        and mask.handles == mask_path.DEFAULT_TANGENTS
    )
    assert len(mask.points) == len(mask.handles) == 4
    short = Mask(
        shape=MaskShape.PATH, points=((0.0, 0.0), (1.0, 0.0)), handles=((0.0,) * 4,) * 2
    )
    assert (
        short.points == mask_path.DEFAULT_NODES
    )  # moins de 3 nœuds : comme un polygone sans sommets


def test_nodes_and_handles_are_bounded_by_the_model_not_silently_dropped():
    mask = Mask(
        shape=MaskShape.PATH,
        points=((5.0, -9.0), (0.0, 0.0), (1.0, 1.0)),
        handles=((9.0, 0.0, 0.0, -9.0), (0.0,) * 4, (0.0,) * 4),
    )
    assert mask.points[0] == (mask_path.POINT_LIMIT, -mask_path.POINT_LIMIT)
    assert mask.handles[0] == (
        mask_path.HANDLE_LIMIT,
        0.0,
        0.0,
        -mask_path.HANDLE_LIMIT,
    )
    assert len(mask.points) == len(mask.handles) == 3


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_value_is_refused(bad):
    with pytest.raises(ValueError):
        Mask(shape=MaskShape.PATH, points=((bad, 0.0), (0.0, 0.0), (1.0, 1.0)))
    with pytest.raises(ValueError):
        Mask(
            shape=MaskShape.PATH,
            points=CIRCLE_NODES,
            handles=((bad, 0.0, 0.0, 0.0),) + CIRCLE_HANDLES[1:],
        )


def test_malformed_nodes_or_mismatched_lists_are_refused_and_the_count_is_capped():
    with pytest.raises(ValueError):
        Mask(shape=MaskShape.PATH, points=((0.0, 0.0), (1.0,), (1.0, 1.0)))
    with pytest.raises(ValueError):
        Mask(shape=MaskShape.PATH, points=CIRCLE_NODES, handles=CIRCLE_HANDLES[:3])
    nodes = tuple((math.cos(i), math.sin(i)) for i in range(mask_path.MAX_NODES))
    assert len(Mask(shape=MaskShape.PATH, points=nodes).points) == mask_path.MAX_NODES
    with pytest.raises(ValueError, match=str(mask_path.MAX_NODES)):
        Mask(
            shape=MaskShape.PATH, points=nodes + ((0.0, 0.0),)
        )  # le message ne recopie pas l'entrée
    with pytest.raises(ValueError) as error:
        Mask(shape=MaskShape.PATH, points=((0.0, 0.0),) * 100_000)
    assert len(str(error.value)) < 200


def test_missing_handles_mean_angular_nodes_and_other_shapes_carry_none():
    angular = Mask(shape=MaskShape.PATH, points=NOTCHED)
    assert angular.handles == ((0.0, 0.0, 0.0, 0.0),) * 5
    polygon = Mask(shape=MaskShape.POLYGON, handles=CIRCLE_HANDLES)
    assert (
        polygon.handles == () and len(polygon.points) == 5
    )  # le polygone par défaut, sans tangentes
    assert Mask(shape=MaskShape.ELLIPSE, handles=CIRCLE_HANDLES).handles == ()
    assert (
        replace(
            Mask(shape=MaskShape.PATH, points=CIRCLE_NODES, handles=CIRCLE_HANDLES),
            feather=0.1,
        ).handles
        == CIRCLE_HANDLES
    )


# --- Fichier du projet -------------------------------------------------------------------------------------------------

# Écrit par la version AVANT le lot 3b, pour trois masques sans courbe : le champ ``handles`` n'y apparaît nulle part.
GOLDEN = (
    '{"blend_mode": "normal", "chroma_key": {"enabled": false, "color": "#00FF00", "tolerance": 0.1, "softness": 0.05, '
    '"spill_suppression": 0.0}, "masks": [{"shape": "rectangle", "position_x": 0.5, "position_y": 0.5, "width": 0.5, '
    '"height": 0.5, "rotation": 0.0, "feather": 0.0, "expansion": 0.0, "opacity": 1.0, "inverted": false, "mode": "add", '
    '"name": "", "id": "aaaaaaaa", "keyframes": []}, {"shape": "ellipse", "position_x": 0.5, "position_y": 0.5, "width": '
    '0.5, "height": 0.5, "rotation": 0.0, "feather": 0.1, "expansion": 0.0, "opacity": 1.0, "inverted": true, "mode": '
    '"subtract", "name": "E", "id": "bbbbbbbb", "keyframes": [{"property_name": "width", "time_seconds": 1, "value": 0.8}]}, '
    '{"shape": "polygon", "position_x": 0.5, "position_y": 0.5, "width": 0.5, "height": 0.5, "rotation": 0.0, "feather": '
    '0.0, "expansion": 0.0, "opacity": 1.0, "inverted": false, "mode": "add", "name": "", "id": "cccccccc", "keyframes": [], '
    '"points": [[0.0, -0.5], [0.5, -0.15], [0.31, 0.5], [-0.31, 0.5], [-0.5, -0.15]]}]}'
)


def _legacy_compositing() -> Compositing:
    from core.compositing import MaskKeyframe

    return Compositing(
        masks=(
            Mask(id="aaaaaaaa"),
            Mask(
                shape=MaskShape.ELLIPSE,
                feather=0.1,
                mode=MaskMode.SUBTRACT,
                inverted=True,
                id="bbbbbbbb",
                name="E",
                keyframes=(MaskKeyframe("width", 1, 0.8),),
            ),
            Mask(shape=MaskShape.POLYGON, id="cccccccc"),
        )
    )


def test_masks_without_a_curve_serialize_exactly_as_before():
    assert json.dumps(compositing_to_dict(_legacy_compositing())) == GOLDEN


def test_a_project_without_a_curve_is_saved_byte_for_byte_as_before(tmp_path):
    project = Project(name="Legacy")
    project.media_assets.append(
        MediaAsset("a", "/tmp/a.mp4", "a", 2, 16, 16, 25, "video")
    )
    project.tracks.append(
        Track(
            "V1",
            "V1",
            "video",
            clips=[Clip("c", "a", "V1", 0, 0, 1, compositing=_legacy_compositing())],
        )
    )
    path = tmp_path / "legacy.kut"
    save_project(project, str(path))
    text = path.read_text(encoding="utf-8")
    compositing = json.dumps(
        json.loads(text)["project"]["sequences"][0]["tracks"][0]["clips"][0][
            "compositing"
        ]
    )
    assert "handles" not in compositing and '"shape": "path"' not in text
    assert GOLDEN[GOLDEN.index('"masks"') : -1] in compositing
    assert (
        CURRENT_VERSION == 17
    )  # additif : le champ est optionnel, la version ne bouge pas
    assert json.loads(text)["version"] == 17


def test_a_curve_round_trips_through_the_file_and_the_render_plan(tmp_path):
    mask = Mask(
        shape=MaskShape.PATH,
        points=NOTCHED,
        handles=((0.0,) * 4, (-0.1, 0.0, 0.1, 0.0)) + ((0.0,) * 4,) * 3,
        id="curve001",
    )
    project = Project(name="Curve")
    project.media_assets.append(
        MediaAsset("a", "/tmp/a.mp4", "a", 2, 16, 16, 25, "video")
    )
    project.tracks.append(
        Track(
            "V1",
            "V1",
            "video",
            clips=[
                Clip("c", "a", "V1", 0, 0, 1, compositing=Compositing(masks=(mask,)))
            ],
        )
    )
    path = tmp_path / "curve.kut"
    save_project(project, str(path))
    restored = load_project(str(path))
    got = restored.tracks[0].clips[0].compositing.masks[0]
    assert got == mask and got.handles[1] == (-0.1, 0.0, 0.1, 0.0)
    assert build_render_plan(restored).video_layers[0].compositing.masks[0] == mask
    assert mask_from_dict(mask_to_dict(mask)) == mask


def test_old_files_still_open_and_a_curve_without_handles_loads_angular():
    old = {
        "shape": "polygon",
        "id": "p0p0p0p0",
        "points": [[0, -0.5], [0.5, 0.5], [-0.5, 0.5]],
        "keyframes": [],
    }
    assert mask_from_dict(old).handles == () and len(mask_from_dict(old).points) == 3
    curve = mask_from_dict(
        {
            "shape": "path",
            "id": "c0c0c0c0",
            "points": [[0, -0.5], [0.5, 0.5], [-0.5, 0.5]],
        }
    )
    assert curve.handles == ((0.0,) * 4,) * 3
    empty = mask_from_dict({"shape": "path", "id": "e0e0e0e0"})
    assert empty.points == mask_path.DEFAULT_NODES


def test_a_corrupt_curve_is_dropped_alone_and_the_rest_of_the_file_survives():
    good = mask_to_dict(Mask(id="good0001"))
    corrupt = [
        {"shape": "path", "id": "bad00001", "points": CIRCLE_NODES, "handles": "oops"},
        {
            "shape": "path",
            "id": "bad00002",
            "points": CIRCLE_NODES,
            "handles": [[0, 0, 0, 0]] * 2,
        },
        {
            "shape": "path",
            "id": "bad00003",
            "points": CIRCLE_NODES,
            "handles": [[float("nan"), 0, 0, 0]] + [[0, 0, 0, 0]] * 3,
        },
        {
            "shape": "path",
            "id": "bad00004",
            "points": [[0, 0]] * (mask_path.MAX_NODES + 1),
        },
    ]
    value = compositing_from_dict({"masks": [*corrupt, good]})
    assert [m.id for m in value.masks] == ["good0001"]


# --- Rendu Qt ----------------------------------------------------------------------------------------------------------


def _project() -> Project:
    return Project(name="Path", width=W, height=H, fps=10.0)


def _white_layer(project: Project, masks):
    clip = add_graphic_clip(project, "solid", timeline_start=0, duration=2)
    update_graphic(clip, "fill_color", "#FFFFFF")
    update_graphic(clip, "width", W)
    update_graphic(clip, "height", H)
    clip.compositing = Compositing(masks=tuple(masks))
    return clip


def _render(project: Project, t: float = 0.0) -> QImage:
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    return MographRenderer(
        scene, W, H, fps=10.0, quality="export", motion_blur=plan.motion_blur
    ).render(scene.top_level(), t, blend_modes=True)


def _alpha_of(masks) -> QImage:
    project = _project()
    _white_layer(project, masks)
    return _render(project)


def _alpha(image: QImage, x: int, y: int) -> int:
    return image.pixelColor(x, y).alpha()


def _curve(**kwargs) -> Mask:
    kwargs.setdefault("points", CIRCLE_NODES)
    kwargs.setdefault("handles", CIRCLE_HANDLES)
    return Mask(shape=MaskShape.PATH, **kwargs)


@pytest.mark.usefixtures("qapp")
def test_a_four_node_circle_gives_the_ellipse_pixels_within_a_pixel():
    box = {"width": 0.5, "height": 0.8}
    circle = _alpha_of([_curve(**box)])
    ellipse = _alpha_of([Mask(shape=MaskShape.ELLIPSE, **box)])
    worst = max(
        abs(_alpha(circle, x, y) - _alpha(ellipse, x, y))
        for x in range(W)
        for y in range(H)
    )
    assert worst <= 12  # écart de repliement du bord, jamais un pixel entier
    assert _alpha(circle, 80, 45) == 255 and _alpha(circle, 5, 5) == 0

    # Les bords tombent au même pixel (à 1 près).
    def edge(image, y):
        return next(x for x in range(W) if _alpha(image, x, y) > 128)

    assert (
        abs(edge(circle, 45) - edge(ellipse, 45)) <= 1
        and abs(edge(circle, 45) - 40) <= 1
    )


@pytest.mark.usefixtures("qapp")
def test_a_concave_curve_leaves_its_notch_empty():
    image = _alpha_of(
        [Mask(shape=MaskShape.PATH, points=NOTCHED, width=1.0, height=1.0)]
    )
    assert _alpha(image, 80, 10) == 0  # au-dessus de l'encoche, dans la boîte
    assert (
        _alpha(image, 20, 20) == 255
        and _alpha(image, 140, 20) == 255
        and _alpha(image, 80, 80) == 255
    )
    polygon = _alpha_of(
        [Mask(shape=MaskShape.POLYGON, points=NOTCHED, width=1.0, height=1.0)]
    )
    assert all(
        _alpha(image, x, y) == _alpha(polygon, x, y)
        for x in range(W)
        for y in range(0, H, 3)
    )  # angulaire = polygone


@pytest.mark.usefixtures("qapp")
def test_smooth_and_angular_nodes_draw_differently():
    angular = _alpha_of(
        [Mask(shape=MaskShape.PATH, points=CIRCLE_NODES, width=0.8, height=0.8)]
    )  # un losange
    smooth = _alpha_of([_curve(width=0.8, height=0.8)])
    x, y = (
        80 + 38,
        45 + 22,
    )  # (0,59 ; 0,61) des demi-axes : dans l'ellipse, hors du losange
    assert _alpha(smooth, x, y) == 255 and _alpha(angular, x, y) == 0


@pytest.mark.usefixtures("qapp")
def test_feather_inversion_opacity_operations_and_rotation_apply_to_a_curve():
    soft = _alpha_of([_curve(width=0.5, height=0.8, feather=0.1)])
    assert any(0 < _alpha(soft, x, 45) < 255 for x in range(W))
    inverted = _alpha_of([_curve(width=0.5, height=0.8, inverted=True)])
    assert _alpha(inverted, 80, 45) == 0 and _alpha(inverted, 5, 5) == 255
    assert _alpha(
        _alpha_of([_curve(width=0.5, height=0.8, opacity=0.5)]), 80, 45
    ) in range(126, 130)
    hole = _alpha_of(
        [
            Mask(width=1.0, height=1.0),
            _curve(width=0.3, height=0.5, mode=MaskMode.SUBTRACT),
        ]
    )
    assert _alpha(hole, 80, 45) == 0 and _alpha(hole, 10, 10) == 255
    cut = _alpha_of(
        [
            Mask(width=1.0, height=1.0),
            _curve(width=0.3, height=0.5, mode=MaskMode.INTERSECT),
        ]
    )
    assert _alpha(cut, 80, 45) == 255 and _alpha(cut, 10, 10) == 0
    wide = _alpha_of([_curve(width=0.9, height=0.2)])
    turned = _alpha_of([_curve(width=0.9, height=0.2, rotation=90.0)])
    assert _alpha(wide, 80 + 50, 45) == 255 and _alpha(turned, 80 + 50, 45) == 0
    grown = _alpha_of([_curve(width=0.3, height=0.5, expansion=0.3)])
    assert (
        _alpha(grown, 80 + 40, 45) == 255
        and _alpha(_alpha_of([_curve(width=0.3, height=0.5)]), 80 + 40, 45) == 0
    )


@pytest.mark.usefixtures("qapp")
def test_the_raster_path_closes_through_the_last_node_and_follows_the_box():
    path = raster_mask_path(_curve(width=0.5, height=0.8, position_x=0.25), 160.0, 90.0)
    rect = path.boundingRect()
    assert rect.center().x() == pytest.approx(
        40.0, abs=0.01
    ) and rect.width() == pytest.approx(80.0, abs=0.5)
    assert rect.height() == pytest.approx(72.0, abs=0.5)


@pytest.mark.usefixtures("qapp")
def test_an_animated_property_still_moves_the_curve_nodes_do_not_animate():
    from core.keyframe_editing import add_keyframe

    project = _project()
    clip = _white_layer(project, [_curve(id="curve001", width=0.3, height=0.5)])
    add_keyframe(project, clip.id, "mask.curve001.position_x", 0.0, 0.25)
    add_keyframe(project, clip.id, "mask.curve001.position_x", 1.0, 0.75)
    assert (
        _alpha(_render(project, 0.0), 40, 45) == 255
        and _alpha(_render(project, 1.0), 40, 45) == 0
    )
    assert _alpha(_render(project, 1.0), 120, 45) == 255


# --- Repli historique `geq` --------------------------------------------------------------------------------------------


def test_the_geq_fallback_approximates_a_curve_by_its_box_and_production_never_reaches_it():
    value = Compositing(masks=(_curve(width=0.5, height=0.5),))
    filters = ",".join(build_ffmpeg_filters(value, W, H))
    assert (
        "between(X," in filters and "pow(" not in filters
    )  # la boîte : documenté, pas d'erreur
    from core.export_engine import ExportEngine

    project = _project()
    video = Clip("v", "a", "V1", 0, 0, 2, compositing=value)
    project.media_assets.append(
        MediaAsset("a", "/tmp/a.mp4", "a", 2, W, H, 10, "video")
    )
    project.tracks.append(Track("V1", "V1", "video", clips=[video]))
    graph = ExportEngine._build_filter_complex(
        build_render_plan(project), W, H, 10, None
    )[0]
    assert (
        "geq=" not in graph and "alphamerge" in graph
    )  # l'export passe par la matte Qt, pas par le repli


# --- Duplication, coupe, séquence imbriquée ---------------------------------------------------------------------------


def test_duplicating_and_cutting_a_clip_keep_the_curve_and_its_ids():
    from core.timeline_operations import cut_clip, duplicate_clip

    mask = _curve(id="curve001")
    project = Project(name="Dup", width=W, height=H, fps=10.0)
    project.media_assets.append(
        MediaAsset("a", "/tmp/a.mp4", "a", 4, W, H, 10, "video")
    )
    project.tracks.append(
        Track(
            "V1",
            "V1",
            "video",
            clips=[
                Clip("c", "a", "V1", 0, 0, 2, compositing=Compositing(masks=(mask,)))
            ],
        )
    )
    twin = duplicate_clip(project, "c")
    left, right = cut_clip(project, "c", 1.0)
    for clip in (twin, left, right):
        assert (
            clip.compositing.masks == (mask,)
            and clip.compositing.masks[0].id == "curve001"
        )
    assert copy.deepcopy(twin).compositing.masks[0].handles == CIRCLE_HANDLES


# --- FFmpeg réel : la même matte à l'export ----------------------------------------------------------------------------


def _ffmpeg_frame(project: Project, t: float, tmp_path: Path) -> QImage:
    from core.export_engine import ExportEngine, input_arguments

    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(
        plan, W, H, 10, None
    )
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=rgb24[probe];[{audio}]anullsink"
    out = tmp_path / f"frame-{t}.png"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return QImage(str(out))


@needs_ffmpeg
@pytest.mark.usefixtures("qapp")
def test_real_export_of_a_full_frame_video_masked_by_a_curve_matches_the_ellipse_export(
    tmp_path,
):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=white:s={W}x{H}:r=10:d=2",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )

    def export(mask: Mask) -> QImage:
        project = _project()
        project.media_assets.append(
            MediaAsset("a", str(source), "src", 2, W, H, 10, "video")
        )
        project.tracks.append(
            Track(
                "V1",
                "V1",
                "video",
                clips=[
                    Clip(
                        "v", "a", "V1", 0, 0, 2, compositing=Compositing(masks=(mask,))
                    )
                ],
            )
        )
        return _ffmpeg_frame(project, 0.5, tmp_path)

    box = {"width": 0.5, "height": 0.8}
    circle, ellipse = (
        export(_curve(**box)),
        export(Mask(shape=MaskShape.ELLIPSE, **box)),
    )
    assert circle.pixelColor(80, 45).red() > 235 and circle.pixelColor(5, 5).red() < 20
    worst = max(
        abs(circle.pixelColor(x, y).red() - ellipse.pixelColor(x, y).red())
        for x in range(W)
        for y in range(H)
    )
    assert worst <= 16
    notched = export(Mask(shape=MaskShape.PATH, points=NOTCHED, width=1.0, height=1.0))
    assert (
        notched.pixelColor(80, 10).red() < 20 and notched.pixelColor(20, 20).red() > 235
    )  # la concavité survit à l'export
