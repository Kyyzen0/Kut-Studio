"""Segment d'aperçu composé à partir de son début (``origin``) : coût indépendant de la position, images identiques.

Avant, un segment à ``t`` composait toute la timeline depuis 0 puis jetait le début (``-ss`` de sortie) : son coût
croissait avec ``t`` (mesuré : ×7,8 pour une image à 28 s). Le fond est maintenant coupé à ``origin`` et aucune image
de calque n'est fabriquée avant ; l'export (``origin = 0``) garde le graphe historique à l'octet près.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.blend_modes import BlendMode
from core.compositing import Compositing
from core.effects_model import EffectType, create_effect
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.text_animations import apply_text_animation

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H, FPS, LENGTH = 160, 90, 10, 6.0
START, SEGMENT = 4.0, 1.0


def _project(tmp_path: Path, *, with_video: bool = True) -> Project:
    """Vidéo, titre animé, forme en Produit, calque d'effets et grain : chaque branche du graphe."""
    project = Project(name="Origine", width=W, height=H, fps=float(FPS))
    if with_video:
        source = tmp_path / "src.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s={W}x{H}:r={FPS}:d={LENGTH}", "-pix_fmt", "yuv420p", str(source)], check=True)
        project.media_assets.append(MediaAsset("a", str(source), "src", LENGTH, W, H, FPS, "video"))
        project.tracks.append(Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, LENGTH)]))
    title = add_graphic_clip(project, "text", timeline_start=0.5, duration=LENGTH - 0.5)
    update_graphic(title, "text", "Kut")
    update_graphic(title, "font_size", 24)
    apply_text_animation(title, "bounce")                     # change à chaque image : rien n'est réutilisable
    shape = add_graphic_clip(project, "shape", timeline_start=0.0, duration=LENGTH)
    update_graphic(shape, "fill_color", "#E0A040")
    shape.compositing = Compositing(blend_mode=BlendMode.MULTIPLY)
    adjustment = add_graphic_clip(project, "adjustment", timeline_start=1.0, duration=LENGTH - 1.0)
    adjustment.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    grain = add_graphic_clip(project, "light", timeline_start=0.0, duration=LENGTH)
    update_graphic(grain, "light_kind", "grain")
    return project


def _segment_command(plan, output: Path, *, legacy: bool, monkeypatch) -> list[str]:
    import core.filter_graph as filter_graph

    if legacy:  # le graphe d'avant : tout depuis 0, puis ``-ss`` de sortie
        original = filter_graph.build_filter_complex
        monkeypatch.setattr(filter_graph, "build_filter_complex",
                            lambda *args, **kwargs: original(*args, **{**kwargs, "origin": 0.0}))
    command = filter_graph.build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=START,
                                                 duration=SEGMENT, output_path=str(output))
    monkeypatch.undo()
    return command


def _decoded_md5(path: Path) -> list[str]:
    completed = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"],
                               capture_output=True, text=True, check=True, timeout=60)
    return [line.rsplit(",", 1)[-1].strip() for line in completed.stdout.splitlines() if line and line[0] != "#"]


def test_export_graph_is_unchanged_without_origin(tmp_path):
    from core.export_engine import ExportEngine

    plan = build_render_plan(_project(tmp_path, with_video=False))
    default = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    explicit = ExportEngine._build_filter_complex(plan, W, H, FPS, None, origin=0.0)
    assert default == explicit
    assert "trim=start=" not in default[0]


def test_stream_input_filter_keeps_historic_chain_at_zero():
    from core.mograph_stream import stream_input_filter

    assert stream_input_filter(10, 6.0) == (
        "fps=10,format=rgba,tpad=stop=-1:stop_mode=clone,trim=duration=6.0,setpts=PTS-STARTPTS"
    )
    late = stream_input_filter(10, 6.0, 4.0)
    # Écartées avant la conversion RVBA, les images d'avant l'origine ne coûtent plus rien.
    assert late.index("trim=start=4.0") < late.index("format=rgba")


def test_segment_graph_starts_at_its_origin(tmp_path):
    from core.filter_graph import build_preview_command

    plan = build_render_plan(_project(tmp_path, with_video=False))
    command = build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=START,
                                    duration=SEGMENT, output_path=str(tmp_path / "s.mp4"))
    graph = command[command.index("-filter_complex") + 1]
    background = next(part for part in graph.split(";") if part.startswith("color=c=black:"))
    assert background.endswith("trim=start=4.0[bg]")
    assert command[command.index("-ss") + 1] == "4.000"     # la sortie se cale toujours sur le segment


def test_no_layer_frame_is_prepared_before_the_origin(tmp_path, monkeypatch):
    """Complexité : les images de calques préparées pour un segment tardif sont celles du segment, pas de 0 à t."""
    from core.filter_graph import build_preview_command
    from core.mograph_raster import MographRenderer

    asked: list[float] = []
    original = MographRenderer.any_active
    monkeypatch.setattr(MographRenderer, "any_active",
                        lambda self, ids, t: asked.append(t) or original(self, ids, t))
    monkeypatch.setattr(MographRenderer, "coverage_key",
                        lambda self, clip_id, t, _o=MographRenderer.coverage_key: asked.append(t) or _o(self, clip_id, t))
    plan = build_render_plan(_project(tmp_path, with_video=False))
    build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=START, duration=SEGMENT,
                          output_path=str(tmp_path / "s.mp4"))
    assert asked
    # Une image de marge au plus (arrondi de la grille de ``write_stream``).
    assert min(asked) >= START - 1.0 / FPS - 1e-9
    assert len(asked) <= 5 * (LENGTH - START + 1.0 / FPS) * FPS + 5  # 5 éléments, jamais toute la timeline


@needs_ffmpeg
def test_late_segment_frames_are_identical_to_full_composition(tmp_path, monkeypatch):
    plan = build_render_plan(_project(tmp_path))
    outputs = {}
    for legacy in (True, False):
        output = tmp_path / f"segment-{'avant' if legacy else 'apres'}.mp4"
        command = _segment_command(plan, output, legacy=legacy, monkeypatch=monkeypatch)
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stderr
        outputs[legacy] = _decoded_md5(output)
    assert len(outputs[False]) >= SEGMENT * FPS                # images + paquets audio du segment
    assert outputs[False] == outputs[True]                     # au bit près, image par image et son compris
