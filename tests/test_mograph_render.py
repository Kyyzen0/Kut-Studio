"""Rendu motion graphics : rastériseur Qt, graphe FFmpeg, parité aperçu / export, cache."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from core.blend_modes import BLEND_MODES, BlendMode
from core.compositing import Compositing, Mask, MaskMode, MaskShape
from core.effects_model import EffectType, create_effect
from core.graphics import add_graphic_clip, update_graphic
from core.mograph_program import graphics_program
from core.mograph_raster import MographRenderer, scene_for_plan
from core.motion_blur import MotionBlurSettings
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, TransformKeyframe

pytestmark = pytest.mark.usefixtures("qapp")

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H = 160, 90


def _project() -> Project:
    return Project(name="Rendu", width=W, height=H, fps=10.0)


def _render(project: Project, t: float = 0.0, *, quality: str = "export", blend: bool = True):
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    renderer = MographRenderer(scene, W, H, fps=10.0, quality=quality, motion_blur=plan.motion_blur)
    return renderer.render(scene.top_level(), t, blend_modes=blend)


def _rgba(image, x: int, y: int) -> tuple[int, int, int, int]:
    color = image.pixelColor(x, y)
    return (color.red(), color.green(), color.blue(), color.alpha())


def _transparent_pockets(image, threshold: int = 128) -> int:
    """Pixels transparents que le vide extérieur n'atteint pas : des trous dans l'encre.

    Le remplissage part des bords en 4-connexité : les coins du carré où une barre recouvre une
    hampe touchent le fond en diagonale, une 8-connexité s'y engouffrerait et ne verrait rien.
    """
    width, height = image.width(), image.height()
    empty = {(x, y) for y in range(height) for x in range(width) if image.pixelColor(x, y).alpha() < threshold}
    outside = {(x, y) for x, y in empty if x in (0, width - 1) or y in (0, height - 1)}
    frontier = list(outside)
    while frontier:
        x, y = frontier.pop()
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbour in empty and neighbour not in outside:
                outside.add(neighbour)
                frontier.append(neighbour)
    return len(empty - outside)


def _shape(project, kind="rectangle", *, size=(60, 40), color="#FF0000", **transform):
    clip = add_graphic_clip(project, "shape", timeline_start=0, duration=2, shape=kind)
    update_graphic(clip, "width", size[0])
    update_graphic(clip, "height", size[1])
    update_graphic(clip, "fill_color", color)
    clip.transform = ClipTransform(**transform)
    return clip


# --- Rastériseur ------------------------------------------------------------------------------


def test_shapes_fill_their_box_with_anchor_and_rotation():
    project = _project()
    _shape(project, "ellipse", size=(40, 40))
    image = _render(project)
    assert _rgba(image, 80, 45)[3] == 255  # centre de l'ellipse
    assert _rgba(image, 61, 26)[3] == 0  # coin de la boîte, hors ellipse
    project = _project()
    _shape(project, size=(40, 20), anchor_x=0.0, anchor_y=0.0, rotation=90)
    image = _render(project)
    # Ancrage haut-gauche au centre du cadre, rotation de 90° : le calque part vers le bas-gauche.
    assert _rgba(image, 75, 60)[3] == 255 and _rgba(image, 85, 40)[3] == 0


def test_line_and_polygon_render():
    project = _project()
    line = _shape(project, "line", size=(100, 10))
    update_graphic(line, "stroke_width", 4)
    update_graphic(line, "stroke_color", "#00FF00")
    image = _render(project)
    assert _rgba(image, 80, 45)[1] > 200 and _rgba(image, 80, 30)[3] == 0
    project = _project()
    polygon = _shape(project, "polygon", size=(60, 60))
    update_graphic(polygon, "polygon_sides", 3)
    image = _render(project)
    assert _rgba(image, 80, 50)[3] == 255 and _rgba(image, 52, 18)[3] == 0


def test_text_renders_with_background_and_tracking_changes_width():
    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0, duration=2)
    for name, value in (("text", "HI"), ("font_size", 20), ("width", 150), ("height", 60),
                        ("background_enabled", True), ("background_color", "#0000FFFF")):
        update_graphic(text, name, value)
    image = _render(project)
    blue = sum(1 for x in range(W) for y in range(H) if _rgba(image, x, y)[2] > 200 and _rgba(image, x, y)[0] < 60)
    white = sum(1 for x in range(W) for y in range(H) if min(_rgba(image, x, y)[:3]) > 200)
    assert blue > 50 and white > 10
    from core.mograph_raster import measure_text

    narrow = measure_text(text.graphic)[0]
    update_graphic(text, "tracking", 20.0)
    assert measure_text(text.graphic)[0] == pytest.approx(narrow + 40.0, abs=2)


def test_text_path_fills_with_the_winding_rule():
    """Indépendant de la police installée : le contrat qui garde le test de pixels ci-dessous."""
    from core.mograph_raster import text_path

    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0, duration=2)
    update_graphic(text, "text", "Kut")
    path, _block = text_path(text.graphic, 150, 60)
    assert path.fillRule() == Qt.WindingFill
    # L'ombre est un ``translated()`` du tracé : elle doit hériter de la même règle.
    assert path.translated(4, 4).fillRule() == Qt.WindingFill


@pytest.mark.parametrize(
    "style",
    [
        {},
        # Remplissage invisible : seul l'ombre décalée, tracé par ``translated()``, reste à l'image.
        {"fill_color": "#00000000", "shadow_color": "#000000FF", "shadow_offset_x": 6, "shadow_offset_y": 6},
        # Contour fin : le pinceau de ``drawPath`` doit suivre la même règle de remplissage.
        {"stroke_width": 1, "stroke_color": "#FF0000"},
    ],
    ids=["fill", "shadow", "stroke"],
)
def test_overlapping_glyph_contours_leave_no_hole(style):
    from PySide6.QtGui import QFontDatabase

    if "Inter" not in QFontDatabase.families():
        pytest.skip("police « Inter » absente : sans police variable aux contours superposés, le défaut ne se reproduit pas")
    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0, duration=2)
    # « f » et « t » n'ont pas de contre-poinçon : tout vide fermé dans leur encre est un défaut.
    fields = {"text": "f t", "font_family": "Inter", "bold": True, "font_size": 40, "width": 150, "height": 60,
              "fill_color": "#000000", "shadow_offset_x": 0, "shadow_offset_y": 0, **style}
    for name, value in fields.items():
        update_graphic(text, name, value)
    image = _render(project)
    ink = sum(1 for x in range(W) for y in range(H) if _rgba(image, x, y)[3] > 200)
    assert ink > 100  # le texte est bien tracé (sinon l'absence de trou ne prouverait rien)
    assert _transparent_pockets(image) == 0


@pytest.mark.parametrize(
    ("modes", "inside", "outside"),
    [
        ((MaskMode.ADD,), 255, 0),
        ((MaskMode.SUBTRACT,), 0, 255),
        ((MaskMode.ADD, MaskMode.INTERSECT), 255, 0),
    ],
)
def test_mask_operations(modes, inside, outside):
    project = _project()
    clip = _shape(project, size=(160, 90))
    masks = [Mask(width=0.5, height=0.5, mode=mode) for mode in modes]
    clip.compositing = Compositing(masks=tuple(masks))
    image = _render(project)
    assert _rgba(image, 80, 45)[3] == inside
    assert _rgba(image, 5, 5)[3] == outside


def test_intersect_of_disjoint_masks_is_empty_and_invert_flips():
    project = _project()
    clip = _shape(project, size=(160, 90))
    clip.compositing = Compositing(masks=(
        Mask(position_x=0.25, width=0.3), Mask(position_x=0.75, width=0.3, mode=MaskMode.INTERSECT),
    ))
    image = _render(project)
    assert _rgba(image, 40, 45)[3] == 0 and _rgba(image, 120, 45)[3] == 0
    clip.compositing = Compositing(masks=(Mask(width=0.5, inverted=True),))
    image = _render(project)
    assert _rgba(image, 80, 45)[3] == 0 and _rgba(image, 5, 45)[3] == 255


def test_feather_softens_and_expansion_grows_the_mask():
    project = _project()
    clip = _shape(project, size=(160, 90))
    clip.compositing = Compositing(masks=(Mask(shape=MaskShape.ELLIPSE, width=0.5, height=0.8, feather=0.15),))
    image = _render(project)
    edge = [_rgba(image, x, 45)[3] for x in range(30, 60)]
    assert any(0 < value < 255 for value in edge)
    clip.compositing = Compositing(masks=(Mask(width=0.5, expansion=0.2),))
    image = _render(project)
    assert _rgba(image, 30, 45)[3] == 255  # 0,7 × 160 = 112 px de large


def test_motion_blur_smears_moving_layers_only_when_enabled():
    project = _project()
    clip = _shape(project, size=(20, 20))
    clip.transform_keyframes = [TransformKeyframe("position_x", 0.0, -0.4), TransformKeyframe("position_x", 1.0, 0.4)]

    def partial(image):
        return sum(1 for x in range(W) if 0 < _rgba(image, x, 45)[3] < 250)

    sharp = partial(_render(project, 0.5))
    update_graphic(clip, "motion_blur", True)
    blurred = partial(_render(project, 0.5))
    assert blurred > sharp + 4
    assert partial(_render(project, 0.5, quality="draft")) == sharp  # aperçu brouillon : pas de flou
    project.active_sequence.motion_blur = MotionBlurSettings(enabled=False)
    assert partial(_render(project, 0.5)) == sharp  # interrupteur global


def test_groups_are_isolated_and_their_opacity_applies_once():
    project = _project()
    a = _shape(project, size=(80, 60), color="#FF0000")
    b = _shape(project, size=(80, 60), color="#0000FF")
    from core.mograph_layers import group_layers

    group = group_layers(project, [a.id, b.id])
    group.transform = ClipTransform(opacity=0.5)
    image = _render(project)
    red, green, blue, alpha = _rgba(image, 80, 45)
    # Une seule application de l'opacité au groupe aplati (bleu par-dessus rouge).
    assert alpha == pytest.approx(128, abs=2) and blue > 240 and red < 15


def test_adjustment_coverage_follows_opacity_and_masks():
    project = _project()
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0, duration=2)
    adjustment.transform = ClipTransform(opacity=0.5)
    adjustment.compositing = Compositing(masks=(Mask(position_x=0.25, width=0.5, height=1.0),))
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    renderer = MographRenderer(scene, W, H)
    coverage = renderer.render_coverage(adjustment.id, 0.0)
    assert _rgba(coverage, 20, 45)[3] == pytest.approx(128, abs=2)
    assert _rgba(coverage, 140, 45)[3] == 0


# --- Découpage en éléments ----------------------------------------------------------------------


def test_program_splits_only_where_ffmpeg_must_see_below():
    project = _project()
    a = _shape(project)
    b = _shape(project)
    c = _shape(project)
    c.compositing = Compositing(blend_mode=BlendMode.MULTIPLY)
    d = _shape(project)
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0, duration=2)
    adjustment.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    e = _shape(project)
    e.effects = [create_effect(EffectType.BLUR)]
    add_graphic_clip(project, "null", timeline_start=0)  # ne coupe pas la bande
    plan = build_render_plan(project)
    program = graphics_program(scene_for_plan(plan))
    assert [(el.kind, el.layer_ids) for el in program] == [
        ("band", (a.id, b.id)), ("layer", (c.id,)), ("band", (d.id,)),
        ("adjustment", (adjustment.id,)), ("layer", (e.id,)),
    ]
    assert program[1].blend is BlendMode.MULTIPLY


# --- FFmpeg : parité et export --------------------------------------------------------------------


def _ffmpeg_frame(project: Project, t: float, tmp_path: Path):
    from PySide6.QtGui import QImage

    from core.export_engine import ExportEngine

    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, 10, None)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=rgb24[probe];[{audio}]anullsink"
    out = tmp_path / f"frame-{t}.png"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += ["-i", path]
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return QImage(str(out))


def _solid_background(project, color):
    background = add_graphic_clip(project, "solid", timeline_start=0, duration=2)
    update_graphic(background, "fill_color", color)
    update_graphic(background, "width", W)
    update_graphic(background, "height", H)
    return background


# Écart toléré (niveaux 8 bits) entre le rastériseur Qt et l'export FFmpeg.
# ``overlay`` passe par le ``hardlight`` de FFmpeg, dont la formule entière tronque avant de doubler
# (``2 * (a * b / 255)`` : jusqu'à −2 face à la formule W3C de Qt), auquel s'ajoutent les aller-retours
# yuv420p → gbrp du fond. Mesuré : 5 avec FFmpeg 6.1 (Ubuntu), 6 avec FFmpeg 7.1 (Windows), moins de 4 avec
# le FFmpeg récent de macOS. Les autres modes n'ont pas ce terme : ils gardent la borne stricte de 4.
BLEND_TOLERANCE = {BlendMode.OVERLAY: 8}


@needs_ffmpeg
@pytest.mark.parametrize("mode", list(BLEND_MODES))
def test_blend_modes_match_between_qt_and_ffmpeg(mode, tmp_path):
    project = _project()
    _solid_background(project, "#3366CC")
    top = _shape(project, size=(W, H), color="#E0A040")
    top.compositing = Compositing(blend_mode=mode)
    # Référence Qt : la pile complète composée par le rastériseur.
    expected = _rgba(_render(project), 80, 45)[:3]
    actual = _rgba(_ffmpeg_frame(project, 0.5, tmp_path), 80, 45)[:3]
    assert actual == pytest.approx(expected, abs=BLEND_TOLERANCE.get(mode, 4)), mode


@needs_ffmpeg
def test_preview_segment_and_export_compose_graphics_identically(tmp_path):
    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0, duration=2)
    update_graphic(text, "text", "Kut")
    update_graphic(text, "font_size", 20)
    reference = _render(project, 0.5)
    exported = _ffmpeg_frame(project, 0.5, tmp_path)
    differences = [
        max(abs(a - b) for a, b in zip(_rgba(reference, x, y)[:3], _rgba(exported, x, y)[:3]))
        for x in range(0, W, 3) for y in range(0, H, 3)
        if _rgba(reference, x, y)[3] in (0, 255)
    ]
    assert max(differences) <= 40  # sous-échantillonnage 4:2:0 sur les bords de lettres
    assert sum(differences) / len(differences) < 3
    from core.export_engine import ExportEngine, with_output_color_stage
    from core.filter_graph import build_preview_command

    plan = build_render_plan(project)
    command = build_preview_command(plan, width=W, height=H, fps=10, quality="high",
                                    start=0.0, duration=1.0, output_path=str(tmp_path / "s.mp4"))
    common, video_label, *_rest = ExportEngine._build_filter_complex(plan, W, H, 10, None, quality="high")
    # Même graphe que l'export, dernière étape comprise (conversion de couleur BT.709).
    assert command[command.index("-filter_complex") + 1] == with_output_color_stage(common, video_label)[0]


@needs_ffmpeg
def test_real_export_with_video_mask_adjustment_and_blend(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=0x20C040:s={W}x{H}:r=10:d=2", "-pix_fmt", "yuv420p", str(source)], check=True)
    project = _project()
    project.media_assets.append(MediaAsset("a", str(source), "src", 2, W, H, 10, "video"))
    video = Clip("v", "a", "V1", 0, 0, 2, transform=ClipTransform(anchor_x=0.0, anchor_y=0.0, flip_h=True),
                 compositing=Compositing(masks=(Mask(shape=MaskShape.ELLIPSE, width=0.9, height=0.9, feather=0.05),)))
    project.tracks.append(Track("V1", "V1", "video", clips=[video]))
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=0, duration=2)
    adjustment.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    adjustment.compositing = Compositing(masks=(Mask(position_x=0.25, width=0.5, height=1.0),))
    frame = _ffmpeg_frame(project, 0.5, tmp_path)
    # Ancrage haut-gauche au centre + miroir : la vidéo occupe le quart bas-gauche.
    left = _rgba(frame, 40, 70)
    right_top = _rgba(frame, 120, 20)
    assert abs(left[0] - left[1]) < 12 and left[1] > 40  # gris (noir et blanc) sous l'adjustment
    assert max(right_top[:3]) < 20  # hors de la vidéo : fond noir


@needs_ffmpeg
def test_nested_sequence_with_graphics_exports(tmp_path):
    from core.sequences import create_sequence, insert_sequence_clip

    project = _project()
    inner = create_sequence(project, "Titre", width=W, height=H, fps=10.0)
    main_id = project.active_sequence_id
    project.active_sequence_id = inner.id
    _shape(project, size=(W, H), color="#FFFFFF")
    project.active_sequence_id = main_id
    project.tracks.append(Track("V1", "V1", "video"))
    nested = insert_sequence_clip(project, inner.id, "V1", 0.0)
    nested.transform = ClipTransform(scale=0.5)
    frame = _ffmpeg_frame(project, 0.5, tmp_path)
    assert min(_rgba(frame, 80, 45)[:3]) > 200  # le calque de la séquence imbriquée, au centre
    assert max(_rgba(frame, 5, 5)[:3]) < 20  # échelle 0,5 : le coin reste noir


# --- Cache -------------------------------------------------------------------------------------------


def _stream_files(project, quality="export"):
    from core.export_engine import ExportEngine

    plan = build_render_plan(project)
    _graph, _video, _audio, inputs = ExportEngine._build_filter_complex(plan, W, H, 10, None, quality=quality)
    return [Path(p) for p in inputs if p.endswith(".ffconcat")]


def test_static_layers_are_rendered_once_and_animation_per_frame():
    project = _project()
    _shape(project)
    (playlist,) = _stream_files(project)
    assert playlist.read_text().count("file 'f-") == 2  # une image (+ répétition finale)
    project = _project()
    clip = _shape(project)
    clip.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 2.0, 90.0)]
    (playlist,) = _stream_files(project)
    assert playlist.read_text().count("file 'f-") >= 20


def test_editing_one_element_keeps_the_other_streams_cached():
    project = _project()
    a = _shape(project)
    b = _shape(project)
    b.compositing = Compositing(blend_mode=BlendMode.SCREEN)
    first = _stream_files(project)
    update_graphic(b, "fill_color", "#00FF00")
    second = _stream_files(project)
    assert first[0] == second[0]  # bande de A inchangée
    assert first[1] != second[1]
    a.transform = ClipTransform(position_x=0.1)
    assert _stream_files(project)[0] != first[0]


def test_segment_fingerprint_follows_parents_masks_and_motion_blur():
    from core.filter_graph import fingerprint_plan
    from core.mograph_layers import set_parent

    project = _project()
    parent = add_graphic_clip(project, "null", timeline_start=0, duration=2)
    child = _shape(project)
    set_parent(project, child.id, parent.id, keep_visual=False)

    def fingerprint():
        return fingerprint_plan(build_render_plan(project), width=W, height=H, fps=10, quality="standard")

    base = fingerprint()
    parent.transform = ClipTransform(rotation=10)
    after_parent = fingerprint()
    child.compositing = Compositing(masks=(Mask(),))
    after_mask = fingerprint()
    project.active_sequence.motion_blur = MotionBlurSettings(samples=16)
    assert len({base, after_parent, after_mask, fingerprint()}) == 4


def test_cache_manager_accounts_and_evicts_layer_frames(tmp_path):
    from core.cache_manager import KIND_MOGRAPH, CacheManager
    from core.mograph_stream import MographFrameCache

    project = _project()
    clip = _shape(project)
    clip.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 2.0, 90.0)]
    _stream_files(project)
    cache = MographFrameCache()
    manager = CacheManager(mograph=cache, max_bytes=1)
    usage = {item.kind: item for item in manager.usage()}
    assert usage[KIND_MOGRAPH].entries > 10
    manager.enforce()
    assert cache.stats()["bytes"] <= 1


@needs_ffmpeg
def test_scopes_frame_sees_layers_at_the_playhead(tmp_path):
    from PySide6.QtGui import QImage

    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest

    project = _project()
    late = _shape(project, size=(W, H), color="#FFFFFF")
    late.timeline_start = 1.0  # n'existe qu'à partir de 1 s
    plan = build_render_plan(project)
    request = ExportRequest(
        render_plan=plan, output_path=str(tmp_path / "x.mp4"), format=ExportFormat.MP4_H264,
        preset=ExportPreset("T", (W, H), 28, "64k"), fps=10,
    )
    engine = ExportEngine()
    frames = {}
    for playhead in (0.5, 1.5):
        command = engine.build_frame_command(request, playhead)
        completed = subprocess.run(command, capture_output=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        image = QImage()
        image.loadFromData(completed.stdout)
        frames[playhead] = _rgba(image, 80, 45)
    assert max(frames[0.5][:3]) < 30 and min(frames[1.5][:3]) > 220
