"""Un clip « scène 3D » avec le vrai FFmpeg (lot B2b) : export MP4, animation et coupe, images clés, cache, déterminisme, parité.

Le projet fait 64x36 à 10 i/s, avec un fond vidéo uni (0x2060A0) ; le cube est rendu par le moteur numpy, lu à l'écran du film.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import kut3d
from core import mograph_stream, scene3d
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, input_arguments
from core.export_runner import ExportStatus, run_export
from core.render_plan import build_render_plan
from core.timeline_operations import cut_clip
from core.visual_effects import TransformKeyframe
from tests.scene3d_render_samples import (
    BACKGROUND, FPS, SIZE, project_with_background, write_animated_scene, write_static_scene,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent"
)

W, H = SIZE
CENTER = (H // 2, W // 2)
CORNER = (1, 1)


@pytest.fixture(autouse=True)
def _isolated(qapp, tmp_path, monkeypatch):
    scene3d.clear_signature_cache()
    cache = tmp_path / "mograph-cache"
    cache.mkdir()
    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: cache)
    yield
    scene3d.clear_signature_cache()


def graph_frames(plan, *, origin: float = 0.0, count: int | None = None) -> np.ndarray:
    """Les images (N, H, W, 3) que le graphe d'export compose, sans encodage (aucune perte)."""
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None, origin=origin)
    command = ["ffmpeg", "-v", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", f"{graph};[{video}]format=rgb24[probe]", "-map", "[probe]"]
    if count is not None:
        command += ["-frames:v", str(count)]
    command += ["-f", "rawvideo", "-", "-map", f"[{audio}]", "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=180)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-600:]
    return np.frombuffer(completed.stdout, np.uint8).reshape(-1, H, W, 3)


def export_mp4(plan, path: Path):
    request = ExportRequest(render_plan=plan, output_path=str(path), format=ExportFormat.MP4_H264,
                            preset=ExportPreset(name="t", resolution=SIZE, crf=14, audio_bitrate="64k"), fps=int(FPS))
    return run_export(request)


def decode(path: Path) -> np.ndarray:
    completed = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               capture_output=True, timeout=120, check=True)
    return np.frombuffer(completed.stdout, np.uint8).reshape(-1, H, W, 3)


def reference_pixel(scene: Path, t: float = 0.0, at=CENTER) -> np.ndarray:
    """Le pixel que le moteur 3D donne au centre (alpha 255 attendu : le cube couvre le centre)."""
    frame = kut3d.render_frame(kut3d.load_scene(scene, allow_outside=False), t=t, size=SIZE, backend="numpy", samples=1)
    assert frame.color[at][3] == 255
    return frame.color[at][:3].astype(int)


# (a) -----------------------------------------------------------------------------------------------------------------------


def test_a_cube_clip_is_exported_over_the_background(tmp_path):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, duration=2.0)
    plan = build_render_plan(project)
    assert plan.missing_media == () and plan.warnings == ()
    result = export_mp4(plan, tmp_path / "film.mp4")
    assert result.status is ExportStatus.OK, result.message
    frames = decode(tmp_path / "film.mp4")
    assert len(frames) >= 20
    first = frames[0].astype(int)
    assert np.abs(first[CENTER] - reference_pixel(scene)).max() <= 12             # le cube (yuv420p : tolérance de codage)
    assert np.abs(first[CORNER] - np.array(BACKGROUND)).max() <= 8               # hors cube : le fond
    assert np.abs(frames[15].astype(int)[CENTER] - reference_pixel(scene)).max() <= 12   # une scène fixe reste identique
    assert np.abs(first[CENTER] - np.array(BACKGROUND)).max() > 40               # et le cube se distingue bien du fond


def test_the_clip_is_visible_only_during_its_span(tmp_path):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, start=1.0, duration=1.0)
    frames = graph_frames(build_render_plan(project), count=30).astype(int)
    assert np.abs(frames[5][CENTER] - np.array(BACKGROUND)).max() <= 3             # avant : fond seul
    assert np.abs(frames[15][CENTER] - reference_pixel(scene)).max() <= 3          # pendant : cube (sans perte de codage)
    assert np.abs(frames[25][CENTER] - np.array(BACKGROUND)).max() <= 3           # après : fond seul


# (b) -----------------------------------------------------------------------------------------------------------------------


def test_an_animated_scene_changes_over_time_and_follows_the_clip_time_after_a_cut(tmp_path):
    scene = write_animated_scene(tmp_path)
    project, clip = project_with_background(tmp_path, scene, start=0.0, duration=2.0)
    whole = graph_frames(build_render_plan(project), count=20).astype(int)
    assert not np.array_equal(whole[0], whole[5]) and not np.array_equal(whole[0], whole[9])    # deux instants, deux images
    _left, right = cut_clip(project, clip.id, 0.5)
    cut = graph_frames(build_render_plan(project), count=20).astype(int)
    # Couper ne change rien à l'image : la seconde moitié reprend où la première s'arrête (même image à chaque instant).
    assert np.array_equal(cut, whole)
    assert right.source_in == pytest.approx(0.5)


def test_a_clip_trimmed_on_the_left_starts_in_the_middle_of_the_animation(tmp_path):
    from core.timeline_operations import trim_clip_left

    scene = write_animated_scene(tmp_path)
    project, clip = project_with_background(tmp_path, scene, start=0.0, duration=2.0)
    whole = graph_frames(build_render_plan(project), count=20).astype(int)
    trim_clip_left(project, clip.id, 0.5)
    trimmed = graph_frames(build_render_plan(project), count=20).astype(int)
    assert np.array_equal(trimmed[5:], whole[5:])                                       # la suite est inchangée
    assert np.abs(trimmed[:5][:, CENTER[0], CENTER[1]] - np.array(BACKGROUND)).max() <= 3     # avant le clip : fond seul
    assert not np.array_equal(trimmed[0], whole[0])


# (c) -----------------------------------------------------------------------------------------------------------------------


def test_animated_opacity_and_position_apply_to_the_scene_layer(tmp_path):
    scene = write_static_scene(tmp_path)
    project, clip = project_with_background(tmp_path, scene, duration=2.0)
    clip.transform_keyframes = [
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="opacity", time_seconds=2.0, value=0.0),
    ]
    frames = graph_frames(build_render_plan(project), count=20).astype(int)
    cube = reference_pixel(scene)
    background = np.array(BACKGROUND)
    assert np.abs(frames[0][CENTER] - cube).max() <= 3
    expected_middle = (cube + background) / 2.0                                         # 50 % d'opacité à 1 s
    assert np.abs(frames[10][CENTER] - expected_middle).max() <= 8
    assert np.abs(frames[19][CENTER] - background).max() <= 16                          # presque transparent à la fin
    clip.transform_keyframes = [TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.0),
                                TransformKeyframe(property_name="position_x", time_seconds=2.0, value=0.25)]
    moved = graph_frames(build_render_plan(project), count=20).astype(int)
    assert np.abs(moved[0][CENTER] - cube).max() <= 3
    assert np.abs(moved[19][CENTER] - background).max() <= 8                            # le cube a quitté le centre
    assert np.abs(moved[19][CENTER[0], CENTER[1] + int(0.23 * W)] - cube).max() <= 12   # et se trouve plus à droite


def test_effects_blend_modes_and_masks_apply_to_the_scene_layer(tmp_path):
    from core.blend_modes import BlendMode
    from core.compositing import Compositing, Mask
    from core.effects_model import EffectType, create_effect

    scene = write_static_scene(tmp_path)
    project, clip = project_with_background(tmp_path, scene, duration=1.0)
    cube = reference_pixel(scene)
    background = np.array(BACKGROUND)
    edge = (CENTER[0], CENTER[1] + 6)                                                  # dans le cube, hors du masque étroit
    clip.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    gray = graph_frames(build_render_plan(project), count=2).astype(int)[0]
    assert np.ptp(gray[CENTER]) <= 6 and np.ptp(cube) > 20                              # effet : le cube devient gris
    clip.effects = []
    clip.compositing = Compositing(blend_mode=BlendMode.MULTIPLY)
    multiplied = graph_frames(build_render_plan(project), count=2).astype(int)[0]
    assert np.abs(multiplied[CENTER] - cube * background / 255.0).max() <= 8             # fusion : produit avec le fond
    clip.compositing = Compositing(masks=(Mask(width=0.1, height=0.4),))
    masked = graph_frames(build_render_plan(project), count=2).astype(int)[0]
    assert np.abs(masked[CENTER] - cube).max() <= 8                                      # masque : le centre reste
    assert np.abs(masked[edge] - background).max() <= 8                                  # et le reste du cube disparaît
    clip.compositing = Compositing()
    unmasked = graph_frames(build_render_plan(project), count=2).astype(int)[0]
    assert np.abs(unmasked[edge] - cube).max() <= 12                                     # (sans masque, le cube y est)


# (d) -----------------------------------------------------------------------------------------------------------------------


def test_a_deleted_scene_file_refuses_the_export_cleanly(tmp_path):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    scene.unlink()
    plan = build_render_plan(project)
    result = export_mp4(plan, tmp_path / "refused.mp4")
    assert result.status is ExportStatus.FAILED and "Média introuvable" in result.message and "scène 3D" in result.message
    assert not (tmp_path / "refused.mp4").exists() and not list(tmp_path.glob(".refused*"))


def test_an_unreadable_scene_file_refuses_the_export_too(tmp_path):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    scene.write_text("pas du json", encoding="utf-8")
    result = export_mp4(build_render_plan(project), tmp_path / "refused.mp4")
    assert result.status is ExportStatus.FAILED and "Média introuvable" in result.message


def test_a_scene_clip_inside_a_nested_sequence_renders_at_the_same_scene_time(tmp_path):
    from core.sequences import create_sequence_from_selection

    scene = write_animated_scene(tmp_path)
    project, clip = project_with_background(tmp_path, scene, start=1.0, duration=2.0)
    flat = graph_frames(build_render_plan(project), count=40).astype(int)
    create_sequence_from_selection(project, [clip.id], "Imbriquée")
    plan = build_render_plan(project)
    assert plan.nested_sequences and plan.missing_media == () and plan.warnings == ()
    nested = graph_frames(plan, count=40).astype(int)
    assert np.abs(nested[:10] - flat[:10]).max() <= 3                                    # avant le clip : le fond
    assert not np.array_equal(flat[12], flat[16])                                        # la scène bouge bien
    assert np.abs(nested[10:29] - flat[10:29]).max() <= 6                                # même scène, mêmes instants
    # Dernière image du clip (indice 29) : sous FFmpeg 6.1 (Ubuntu 24.04), une séquence imbriquée perd sa dernière image et
    # laisse voir le fond, pour TOUT calque (mesuré avec un aplat « solid » ou « rectangle » : même écart que la scène 3D) ;
    # sous FFmpeg 7.x elle est conservée. On accepte donc « l'image à plat » ou « le fond », jamais un autre instant de la scène.
    last = np.abs(nested[29] - flat[29]).max(), np.abs(nested[29] - flat[0]).max()
    assert min(last) <= 6


# (e) -----------------------------------------------------------------------------------------------------------------------


def test_editing_a_model_changes_the_next_export(tmp_path):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, duration=1.0)
    before = graph_frames(build_render_plan(project), count=5).astype(int)
    obj = tmp_path / "cube.obj"
    obj.write_text(obj.read_text(encoding="utf-8").replace("1 1 1", "2 2 2").replace("-1 -1 -1", "-2 -2 -2"), encoding="utf-8")
    after = graph_frames(build_render_plan(project), count=5).astype(int)
    assert not np.array_equal(before, after)                                             # le cache a été invalidé

    def covered(frame):
        return int((np.abs(frame - np.array(BACKGROUND)).max(axis=2) > 12).sum())

    assert covered(after[0]) > covered(before[0])                                         # le cube modifié est plus grand


def test_editing_a_texture_changes_the_next_export(tmp_path):
    scene = write_static_scene(tmp_path, textured=True)
    project, _clip = project_with_background(tmp_path, scene, duration=1.0)
    before = graph_frames(build_render_plan(project), count=3).astype(int)
    from tests.scene3d_render_samples import png_bytes

    (tmp_path / "tex.png").write_bytes(png_bytes((255, 0, 255, 255)))
    after = graph_frames(build_render_plan(project), count=3).astype(int)
    assert not np.array_equal(before[0][CENTER], after[0][CENTER])


# (f) -----------------------------------------------------------------------------------------------------------------------


def _png_digests(directory: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob("f-*.png"))}


def test_two_exports_make_the_same_intermediate_images_to_the_bit(tmp_path, monkeypatch):
    scene = write_animated_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, duration=1.0)
    plan = build_render_plan(project)
    runs = []
    for index in range(2):
        folder = tmp_path / f"run{index}"
        folder.mkdir()
        monkeypatch.setattr(mograph_stream, "cache_directory", lambda folder=folder: folder)
        scene3d.clear_signature_cache()
        frames = graph_frames(plan, count=10)
        runs.append((_png_digests(folder), hashlib.sha256(frames.tobytes()).hexdigest()))
    assert runs[0][0] and runs[0] == runs[1]                  # mêmes noms, mêmes octets de PNG, mêmes images composées


# (g) -----------------------------------------------------------------------------------------------------------------------


def test_a_static_scene_is_rendered_once_for_the_whole_export(tmp_path, monkeypatch):
    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, duration=2.0)
    calls: list[float] = []
    original = kut3d.render_frame
    monkeypatch.setattr(kut3d, "render_frame", lambda *a, **k: calls.append(1) or original(*a, **k))
    graph_frames(build_render_plan(project), count=20)
    assert len(calls) == 1


# Parité aperçu fidèle / export ------------------------------------------------------------------------------------------------------------


def test_a_preview_segment_composes_the_same_frames_as_the_export(tmp_path):
    scene = write_animated_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, start=0.0, duration=2.0)
    plan = build_render_plan(project)
    export = graph_frames(plan, count=20)
    segment = graph_frames(plan, origin=1.0, count=10)
    assert len(segment) == 10 and np.array_equal(segment, export[10:20])                  # même graphe, mêmes images au bit près


def test_the_preview_command_runs_and_shows_the_cube(tmp_path):
    from core.filter_graph import build_preview_command

    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene, duration=2.0)
    plan = build_render_plan(project)
    output = tmp_path / "segment.mp4"
    command = build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=0.5, duration=1.0,
                                    output_path=str(output))
    completed = subprocess.run(command, capture_output=True, timeout=180)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-500:]
    frames = decode(output).astype(int)
    assert np.abs(frames[2][CENTER] - reference_pixel(scene)).max() <= 12
