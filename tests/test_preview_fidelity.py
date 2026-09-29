"""Tests de l'aperçu fidele et du cache de rendu (tache 30)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def _project_with_clip():
    from core.project_factory import create_default_project
    from core.render_plan import build_render_plan

    project = create_default_project()
    plan = build_render_plan(project)
    return project, plan


def test_filter_graph_matches_export():
    """L'apercu utilise exactement le filter_complex de l'export."""
    import tempfile

    from core.export_engine import ExportEngine
    from core.filter_graph import build_filter_complex

    _project, plan = _project_with_clip()
    tmp = tempfile.NamedTemporaryFile(suffix=".srt", delete=False)
    tmp.write(b"1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    tmp.close()
    expected = ExportEngine._build_filter_complex(plan, 960, 540, 30, tmp.name)
    actual = build_filter_complex(plan, 960, 540, 30, tmp.name)
    assert actual == expected


def test_fingerprint_stable_and_sensitive():
    from core.filter_graph import fingerprint_plan

    _project, plan = _project_with_clip()
    base = {"width": 1920, "height": 1080, "fps": 30, "quality": "standard"}
    assert fingerprint_plan(plan, **base) == fingerprint_plan(plan, **base)
    other = dict(base, quality="draft")
    assert fingerprint_plan(plan, **base) != fingerprint_plan(plan, **other)


def test_effect_change_changes_fingerprint():
    from core.effects_model import EffectType, create_effect
    from core.filter_graph import fingerprint_plan
    from core.render_plan import RenderLayer

    _project, plan = _project_with_clip()
    base = {"width": 1920, "height": 1080, "fps": 30, "quality": "standard"}
    before = fingerprint_plan(plan, **base)
    layer = plan.video_layers[0]
    effect = create_effect(EffectType.BLUR, params={"intensity": 5.0})
    patched = RenderLayer(
        clip_id=layer.clip_id,
        asset_id=layer.asset_id,
        track_id=layer.track_id,
        track_index=layer.track_index,
        source_path=layer.source_path,
        source_in=layer.source_in,
        source_out=layer.source_out,
        timeline_start=layer.timeline_start,
        timeline_end=layer.timeline_end,
        source_fps=layer.source_fps,
        transform=layer.transform,
        transform_keyframes=layer.transform_keyframes,
        time_remapping=layer.time_remapping,
        effects=(effect,),
        color_grade=layer.color_grade,
    )
    from dataclasses import replace

    plan2 = replace(plan, video_layers=(patched,) + tuple(plan.video_layers[1:]))
    assert fingerprint_plan(plan2, **base) != before


# ---------------------------------------------------------------------------
# Sous-titres : l'apercu doit ecrire le meme fichier que l'export
# ---------------------------------------------------------------------------


def _plan_with_cues():
    from dataclasses import replace

    from core.subtitle_io import SubtitleCue
    from core.text_style import default_text_style

    _project, plan = _project_with_clip()
    return replace(
        plan,
        subtitle_cues=(
            SubtitleCue(start=0.0, end=1.0, text="Bonjour"),
            SubtitleCue(start=1.0, end=2.0, text="Au revoir"),
        ),
        subtitle_styles=(default_text_style(), default_text_style()),
    )


def test_preview_writes_same_subtitle_file_as_export(tmp_path):
    """Le fichier SRT/ASS de l'apercu est celui de l'export, a l'octet pres."""
    from core.export_engine import ExportEngine
    from core.filter_graph import build_filter_complex, write_subtitle_file
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine

    plan = _plan_with_cues()
    engine = PreviewEngine(
        cache=DiskPreviewCache(directory=tmp_path / "subs", ttl_seconds=0)
    )
    from dataclasses import replace

    _project, plain_plan = _project_with_clip()
    # Projet sans sous-titre : rien a preparer.
    silent = replace(plain_plan, subtitle_cues=(), subtitle_styles=())
    assert engine._write_subtitles(silent) is None

    prepared = engine._write_subtitles(plan)
    assert prepared is not None
    try:
        assert "Bonjour" in Path(prepared).read_text(encoding="utf-8")
    finally:
        os.remove(prepared)

    path = write_subtitle_file(plan)
    assert path is not None
    try:
        expected = ExportEngine._build_filter_complex(plan, 960, 540, 30, path)
        assert build_filter_complex(plan, 960, 540, 30, path) == expected
        assert "subtitles=" in expected[0]
    finally:
        os.remove(path)

    # Sans fichier prepare, le graphe partage refuse de rendre : c'est
    # exactement le cas que le moteur doit couvrir pour un projet
    # sous-titre (sinon l'apercu echoue alors que l'export fonctionne).
    with pytest.raises(RuntimeError):
        build_filter_complex(plan, 960, 540, 30, None)


# ---------------------------------------------------------------------------
# Rendu reel (FFmpeg) : segment en cache et aucun temporaire qui fuit
# ---------------------------------------------------------------------------


def _real_plan(tmp_path):
    from tests.test_export_integration import (
        _build_overlap_project,
        _generate_color_clip,
        _require_ffmpeg,
    )

    from core.render_plan import build_render_plan

    ffmpeg, _ffprobe = _require_ffmpeg()
    red = tmp_path / "red.mp4"
    blue = tmp_path / "blue.mp4"
    _generate_color_clip(ffmpeg, red, color="red", duration=2.0)
    _generate_color_clip(ffmpeg, blue, color="blue", duration=2.0)
    return build_render_plan(_build_overlap_project(red, blue))


def _preview_job(plan, params="real"):
    from core.preview_cache import PreviewSegmentKey
    from core.preview_engine import PreviewJob

    key = PreviewSegmentKey(
        clip_id="v1-red", start=0.0, end=2.0, quality="draft", params_hash=params
    )
    return PreviewJob(
        key=key,
        plan=plan,
        width=160,
        height=90,
        fps=15,
        quality="draft",
        start=0.0,
        duration=2.0,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")
def test_default_render_caches_segment_without_temp_leak(tmp_path):
    """Rendu reel : le segment est mis en cache, sans temporaire residuel."""
    import tempfile

    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine

    plan = _real_plan(tmp_path)
    job = _preview_job(plan)
    engine = PreviewEngine(
        cache=DiskPreviewCache(directory=tmp_path / "segments", ttl_seconds=0)
    )
    temp_root = Path(tempfile.gettempdir())
    before_segments = set(temp_root.glob("kut-preview-*.mp4"))
    before_subtitles = set(temp_root.glob("kut-studio-subtitles-*"))

    assert engine.request(job)["status"] == "pending"
    assert engine.pump(1) == 1
    assert engine.state().last_error == ""

    cached = engine.cache.lookup(job.key)
    assert cached is not None and cached.stat().st_size > 0
    assert set(temp_root.glob("kut-preview-*.mp4")) == before_segments
    assert set(temp_root.glob("kut-studio-subtitles-*")) == before_subtitles
    # Deuxieme demande : servie depuis le cache, aucun nouveau rendu.
    assert engine.request(job)["status"] == "cached"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")
def test_default_render_incruste_les_sous_titres(tmp_path):
    """Avec libass, un projet sous-titre se rend aussi en apercu."""
    from core.export_engine import _ffmpeg_supports_subtitles

    if not _ffmpeg_supports_subtitles():
        pytest.skip("FFmpeg sans libass : filtre 'subtitles' indisponible")

    import tempfile
    from dataclasses import replace

    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.subtitle_io import SubtitleCue
    from core.text_style import default_text_style

    # Le plan reel porte les medias ; on lui ajoute des sous-titres.
    plan = replace(
        _real_plan(tmp_path),
        subtitle_cues=(SubtitleCue(start=0.0, end=1.5, text="Bonjour"),),
        subtitle_styles=(default_text_style(),),
    )
    job = _preview_job(plan, params="subs")
    engine = PreviewEngine(
        cache=DiskPreviewCache(directory=tmp_path / "subs-segments", ttl_seconds=0)
    )
    temp_root = Path(tempfile.gettempdir())
    before_subtitles = set(temp_root.glob("kut-studio-subtitles-*"))

    engine.request(job)
    assert engine.pump(1) == 1
    assert engine.state().last_error == ""
    assert engine.cache.lookup(job.key) is not None
    assert set(temp_root.glob("kut-studio-subtitles-*")) == before_subtitles

