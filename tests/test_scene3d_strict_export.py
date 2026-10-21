"""Un clip « scène 3D » qui ne se rend pas : tolérant à l'écran, STRICT à l'export (lot B2b, revue P1).

Lire une scène ne prouve pas qu'elle se rende (texture PNG tronquée, bornes de ``kut3d``). Deux défenses : la vérification
préalable de ``check_export_request`` (:mod:`core.scene3d_check`) et la propagation des échecs de rendu pendant l'export.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

import kut3d
from core import cli, mograph_stream, scene3d
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, check_export_request
from core.export_runner import ExportStatus, run_export
from core.mograph_ffmpeg import prepare_graphics_streams
from core.project_io import save_project
from core.render_plan import build_render_plan
from core.scene3d_check import export_problems
from tests.scene3d_render_samples import FPS, SIZE, project_with_background, write_animated_scene, write_static_scene

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent"
)

W, H = SIZE
BAD_PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"


@pytest.fixture(autouse=True)
def _isolated(qapp, tmp_path, monkeypatch):
    scene3d.clear_signature_cache()
    cache = tmp_path / "mograph-cache"
    cache.mkdir()
    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: cache)
    yield
    scene3d.clear_signature_cache()


def _cache(tmp_path) -> Path:
    return tmp_path / "mograph-cache"


def _request(plan, path: Path) -> ExportRequest:
    return ExportRequest(render_plan=plan, output_path=str(path), format=ExportFormat.MP4_H264,
                         preset=ExportPreset(name="t", resolution=SIZE, crf=30, audio_bitrate="64k"), fps=int(FPS))


def _failing_render(monkeypatch, *, on_call: int = 3):
    """``kut3d.render_frame`` lève ``RenderError`` à partir de la ``on_call``-ième image (hors rendu d'essai 16x16)."""
    real = kut3d.render_frame
    calls: list[tuple] = []

    def render_frame(scene, t=0.0, size=(640, 360), **kwargs):
        if tuple(size) != (16, 16):
            calls.append((t, size))
            if len(calls) >= on_call:
                raise kut3d.RenderError("mémoire insuffisante (simulée)")
        return real(scene, t=t, size=size, **kwargs)

    monkeypatch.setattr(kut3d, "render_frame", render_frame)
    return calls


# (a) texture illisible ⇒ export refusé avant tout rendu long -----------------------------------------------------------------


def test_a_corrupt_texture_refuses_the_export_before_any_rendering(tmp_path, monkeypatch):
    scene = write_static_scene(tmp_path, textured=True)
    (tmp_path / "tex.png").write_bytes(BAD_PNG)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    assert plan.missing_media == () and plan.warnings == ()                       # la lecture ne décode pas : le plan l'accepte
    calls = _failing_render(monkeypatch, on_call=10_000)
    with pytest.raises(ValueError, match=r"Scène 3D non rendable.*static\.kut3d.*texture"):
        check_export_request(_request(plan, tmp_path / "out.mp4"))
    assert calls == []                                                            # aucun rendu à taille réelle
    result = run_export(_request(plan, tmp_path / "out.mp4"))
    assert result.status is ExportStatus.FAILED and "static.kut3d" in result.message
    assert not (tmp_path / "out.mp4").exists() and not list(tmp_path.glob("out.mp4*"))
    assert list(_cache(tmp_path).glob("*.ffconcat")) == []                        # rien n'a été rastérisé


def test_the_refusal_message_is_bounded_and_names_the_clip(tmp_path):
    scene = write_static_scene(tmp_path, textured=True)
    (tmp_path / "tex.png").write_bytes(BAD_PNG)
    project, clip = project_with_background(tmp_path, scene, clip_id="mon_clip")
    clip.label = "L" * 5000
    problems = export_problems(build_render_plan(project))
    assert len(problems) == 1 and "static.kut3d" in problems[0] and len(problems[0]) < 400


# (b) rendu d'essai en erreur ⇒ export refusé ----------------------------------------------------------------------------------


def test_a_scene_beyond_the_engine_limits_refuses_the_export(tmp_path, monkeypatch):
    import kut3d.render.assemble as assemble

    scene = write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    monkeypatch.setattr(assemble, "MAX_TRIANGLES", 1)                             # le cube (12 triangles) dépasse la borne
    with pytest.raises(ValueError, match=r"static\.kut3d.*trop lourde"):
        check_export_request(_request(plan, tmp_path / "out.mp4"))
    assert run_export(_request(plan, tmp_path / "out.mp4")).status is ExportStatus.FAILED


def test_a_valid_scene_passes_the_check_and_probes_once_per_scene(tmp_path, monkeypatch):
    scene = write_static_scene(tmp_path, textured=True)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    seen: list = []
    real = kut3d.render_frame
    monkeypatch.setattr(kut3d, "render_frame", lambda *a, **k: seen.append(k.get("size")) or real(*a, **k))
    check_export_request(_request(plan, tmp_path / "out.mp4"))
    assert export_problems(plan) == []
    assert seen == [(16, 16), (16, 16)]                                           # un rendu d'essai minuscule par appel


# (c) échec pendant l'export ⇒ l'export échoue ---------------------------------------------------------------------------------


@pytest.mark.parametrize("animated", [True, False])
def test_a_render_failure_during_the_export_fails_it_without_an_output_file(tmp_path, monkeypatch, animated):
    scene = write_animated_scene(tmp_path) if animated else write_static_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    calls = _failing_render(monkeypatch, on_call=3 if animated else 1)
    output = tmp_path / "film.mp4"
    result = run_export(_request(plan, output))
    assert result.status is ExportStatus.FAILED
    assert "Scène 3D" in result.message and "mémoire insuffisante" in result.message
    assert len(calls) >= (3 if animated else 1)
    assert not output.exists() and not list(tmp_path.glob("film.mp4*"))           # ni sortie, ni .partial
    assert list(_cache(tmp_path).glob("*.ffconcat")) == []                        # aucune liste orpheline


def test_the_graphics_preparation_does_not_cache_an_empty_image_for_the_export(tmp_path, monkeypatch):
    scene = write_animated_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    _failing_render(monkeypatch, on_call=1)
    assert prepare_graphics_streams(plan, W, H, FPS) is False                     # non fatal pour l'appelant, mais rien d'écrit
    assert list(_cache(tmp_path).glob("*.ffconcat")) == []


# (d) le même échec à l'écran reste toléré --------------------------------------------------------------------------------------


def test_the_same_failure_on_screen_draws_nothing_and_logs_without_raising(tmp_path, monkeypatch, caplog):
    from PySide6.QtGui import QImage, QPainter

    scene = write_static_scene(tmp_path)
    _failing_render(monkeypatch, on_call=1)
    from core.graphics import GraphicOverlay, GraphicType

    graphic = GraphicOverlay(type=GraphicType.SCENE3D, source_path=str(scene), width=W, height=H)
    canvas = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    with caplog.at_level(logging.WARNING, logger="kut_studio.scene3d"):
        scene3d.draw_scene(painter, graphic, W, H)                                # ne lève jamais
    painter.end()
    assert max(canvas.pixelColor(x, y).alpha() for x in range(W) for y in range(H)) == 0
    assert "non rendue" in caplog.text


def test_a_preview_segment_graph_tolerates_a_failing_scene(tmp_path, monkeypatch):
    scene = write_animated_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    _failing_render(monkeypatch, on_call=1)
    graph, video, _audio, _inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None, quality="draft")   # ne lève pas
    assert graph and video


def test_an_empty_image_cached_by_the_screen_never_serves_the_export(tmp_path, monkeypatch):
    scene = write_animated_scene(tmp_path)
    project, _clip = project_with_background(tmp_path, scene)
    plan = build_render_plan(project)
    monkeypatch.setattr(kut3d, "render_frame", _raise_always(kut3d.render_frame))
    # L'extraction d'une image (scopes) construit le graphe avec la qualité « export » : mêmes noms d'images que l'export.
    ExportEngine._build_filter_complex(plan, W, H, FPS, None, horizon=100.0)     # écran : tolérant, images vides au cache
    assert any(_cache(tmp_path).glob("f-*.png"))
    result = run_export(_request(plan, tmp_path / "film.mp4"))                  # l'export refait le rendu : il échoue franchement
    assert result.status is ExportStatus.FAILED and "Scène 3D" in result.message
    assert not (tmp_path / "film.mp4").exists()


def _raise_always(real):
    def render_frame(scene, t=0.0, size=(640, 360), **kwargs):
        if tuple(size) == (16, 16):
            return real(scene, t=t, size=size, **kwargs)
        raise kut3d.RenderError("mémoire insuffisante (simulée)")

    return render_frame


# (e) tous les chemins d'entrée de l'export refusent ---------------------------------------------------------------------------


def test_the_application_engine_and_the_command_line_refuse_too(tmp_path, capsys):
    scene = write_static_scene(tmp_path, textured=True)
    (tmp_path / "tex.png").write_bytes(BAD_PNG)
    project, _clip = project_with_background(tmp_path, scene)
    engine = ExportEngine()
    errors: list[str] = []
    engine.failed.connect(errors.append)
    engine.start(_request(build_render_plan(project), tmp_path / "app.mp4"))
    assert len(errors) == 1 and "static.kut3d" in errors[0] and not (tmp_path / "app.mp4").exists()

    kut = tmp_path / "p.kut"
    save_project(project, str(kut))
    code = cli.run(["export", str(kut), "-o", str(tmp_path / "cli.mp4"), "--json"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_FAILED and '"ok": false' in out and "static.kut3d" in out
    assert not (tmp_path / "cli.mp4").exists()


# (f) une scène valide exporte toujours ------------------------------------------------------------------------------------------


def test_a_valid_textured_scene_still_exports(tmp_path):
    scene = write_static_scene(tmp_path, textured=True)
    project, _clip = project_with_background(tmp_path, scene)
    result = run_export(_request(build_render_plan(project), tmp_path / "film.mp4"))
    assert result.status is ExportStatus.OK, result.message
    assert (tmp_path / "film.mp4").stat().st_size > 0


def test_a_plan_without_a_scene_is_not_probed(tmp_path, monkeypatch):
    project, _clip = project_with_background(tmp_path, write_static_scene(tmp_path))
    project.tracks = [track for track in project.tracks if track.type != "graphics"]
    plan = build_render_plan(project)
    monkeypatch.setattr(kut3d, "render_frame", lambda *a, **k: pytest.fail("aucun rendu pour un plan sans 3D"))
    assert export_problems(plan) == []
