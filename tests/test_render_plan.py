"""Tests pour le plan de rendu pur (``core.render_plan``)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import (
    RenderLayer,
    RenderPlan,
    build_render_plan,
)
from core.timeline_evaluator import timeline_duration


# ---------------------------------------------------------------------------
# Usines
# ---------------------------------------------------------------------------


def _make_asset(
    *, asset_id: str = "asset-1", path: str = "/tmp/source.mp4", duration: float = 10.0
) -> MediaAsset:
    """Construit un ``MediaAsset`` valide et sobre."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name=f"Media {asset_id}",
        duration=duration,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )


def _make_clip(
    *,
    clip_id: str,
    asset_id: str = "asset-1",
    track_id: str = "V1",
    timeline_start: float = 0.0,
    source_in: float = 0.0,
    duration: float = 2.0,
    enabled: bool = True,
) -> Clip:
    """Construit un ``Clip`` valide avec une durée explicite."""
    return Clip(
        id=clip_id,
        asset_id=asset_id,
        track_id=track_id,
        timeline_start=timeline_start,
        source_in=source_in,
        source_out=source_in + duration,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Plan vide
# ---------------------------------------------------------------------------


def test_empty_project_produces_zero_duration_plan():
    """Un projet sans clip vidéo activé donne un plan de durée 0."""
    project = Project(
        name="Vide",
        tracks=[
            Track(id="V1", name="V1", type="video"),
            Track(id="V2", name="V2", type="video"),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    assert isinstance(plan, RenderPlan)
    assert plan.video_layers == ()
    assert plan.duration == 0.0
    # Les métadonnées du projet remontent dans le plan.
    assert plan.width == 1920
    assert plan.height == 1080
    assert plan.fps == pytest.approx(30.0)


def test_project_with_only_subtitle_track_is_also_empty_for_video():
    """Les pistes non vidéo sont ignorées pour le plan vidéo.

    Le plan ne contient aucune couche vidéo, mais sa durée reflète
    toujours ``timeline_duration(project)`` : c'est la durée totale
    de la timeline qui sert à dimensionner le fond noir lors d'un
    export.
    """
    project = Project(
        name="Sous-titres",
        tracks=[
            Track(
                id="S1", name="S1", type="subtitle",
                clips=[
                    _make_clip(clip_id="sub-1", track_id="S1", duration=4.0),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    assert plan.video_layers == ()
    assert plan.duration == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Durée du plan
# ---------------------------------------------------------------------------


def test_plan_duration_matches_timeline_duration():
    """``RenderPlan.duration`` doit valoir ``timeline_duration(project)``."""
    project = Project(
        name="D",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(clip_id="a", timeline_start=0.0, duration=2.0),
                    _make_clip(clip_id="b", timeline_start=4.0, duration=5.0),
                ],
            ),
            Track(
                id="V2", name="V2", type="video",
                clips=[
                    _make_clip(
                        clip_id="c", track_id="V2",
                        timeline_start=1.0, duration=3.0,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    assert plan.duration == timeline_duration(project)
    # max(2, 9, 4) = 9.0
    assert plan.duration == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Clips désactivés
# ---------------------------------------------------------------------------


def test_disabled_clips_are_excluded_from_plan():
    """Un clip ``enabled=False`` ne doit jamais apparaître dans le plan."""
    project = Project(
        name="Disabled",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(clip_id="on", timeline_start=0.0, duration=2.0),
                    _make_clip(
                        clip_id="off", timeline_start=0.0, duration=10.0,
                        enabled=False,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    layer_ids = [layer.clip_id for layer in plan.video_layers]
    assert layer_ids == ["on"]
    # La durée ignore le clip désactivé.
    assert plan.duration == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Trims conservés
# ---------------------------------------------------------------------------


def test_trims_are_preserved_in_layers():
    """``source_in`` et ``source_out`` doivent être conservés à leur valeur."""
    project = Project(
        name="Trim",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(
                        clip_id="trimmed",
                        timeline_start=5.0, source_in=2.0, duration=3.0,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    assert len(plan.video_layers) == 1
    layer = plan.video_layers[0]
    assert isinstance(layer, RenderLayer)
    assert layer.source_in == pytest.approx(2.0)
    assert layer.source_out == pytest.approx(5.0)
    assert layer.timeline_start == pytest.approx(5.0)
    assert layer.timeline_end == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Ordre V1 puis V2
# ---------------------------------------------------------------------------


def test_layers_are_emitted_in_track_order_v1_then_v2():
    """Les couches sont émises dans l'ordre des pistes du projet."""
    project = Project(
        name="Order",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(clip_id="v1-first", timeline_start=0.0, duration=2.0),
                    _make_clip(
                        clip_id="v1-second",
                        timeline_start=4.0, duration=2.0,
                    ),
                ],
            ),
            Track(
                id="V2", name="V2", type="video",
                clips=[
                    _make_clip(
                        clip_id="v2-only",
                        track_id="V2",
                        timeline_start=1.0, duration=2.0,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    track_ids = [layer.track_id for layer in plan.video_layers]
    assert track_ids == ["V1", "V1", "V2"]
    assert [layer.clip_id for layer in plan.video_layers] == [
        "v1-first", "v1-second", "v2-only",
    ]
    # Les indices de piste reflètent l'ordre dans project.tracks.
    assert [layer.track_index for layer in plan.video_layers] == [0, 0, 1]


# ---------------------------------------------------------------------------
# Média manquant
# ---------------------------------------------------------------------------


def test_active_clip_with_missing_asset_raises_key_error():
    """Un clip actif dont l'asset est absent lève une ``KeyError`` claire."""
    project = Project(
        name="Orphan",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(
                        clip_id="orphan",
                        asset_id="ghost-asset",
                        timeline_start=0.0, duration=2.0,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],  # ghost-asset absent
    )
    with pytest.raises(KeyError, match="ghost-asset"):
        build_render_plan(project)


# ---------------------------------------------------------------------------
# Pureté du module
# ---------------------------------------------------------------------------


def test_render_plan_module_does_not_import_pyside6_or_ffmpeg():
    """``core.render_plan`` ne doit importer ni PySide6 ni FFmpeg."""
    module_path = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "render_plan.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    forbidden_top_level = {"PySide6", "PySide2", "PyQt5", "PyQt6", "ffmpeg"}
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in forbidden_top_level:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".", 1)[0]
            if top in forbidden_top_level:
                offenders.append(f"from {node.module} import ...")

    assert not offenders, (
        "Le module render_plan ne doit pas importer PySide6 ni ffmpeg : "
        + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Immutabilité
# ---------------------------------------------------------------------------


def test_render_plan_and_layer_are_immutable():
    """``RenderPlan`` et ``RenderLayer`` sont des dataclasses figées."""
    layer = RenderLayer(
        clip_id="c", asset_id="a", track_id="V1", track_index=0,
        source_path="/tmp/x.mp4", source_in=0.0, source_out=2.0,
        timeline_start=0.0, timeline_end=2.0,
    )
    with pytest.raises(Exception):
        layer.clip_id = "mutated"  # type: ignore[misc]

    plan = RenderPlan(
        width=1920, height=1080, fps=30.0, duration=2.0,
        video_layers=(layer,),
    )
    with pytest.raises(Exception):
        plan.duration = 99.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cohérence avec timeline_evaluator
# ---------------------------------------------------------------------------


def test_layers_cover_all_active_clips_of_the_project():
    """Chaque clip activé doit produire exactement une ``RenderLayer``."""
    project = Project(
        name="Cover",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    _make_clip(clip_id="a", timeline_start=0.0, duration=2.0),
                    _make_clip(
                        clip_id="b",
                        timeline_start=2.0, duration=3.0,
                        enabled=False,
                    ),
                    _make_clip(
                        clip_id="c",
                        timeline_start=5.0, duration=2.0,
                    ),
                ],
            ),
            Track(
                id="V2", name="V2", type="video",
                clips=[
                    _make_clip(
                        clip_id="d", track_id="V2",
                        timeline_start=1.0, duration=2.0,
                    ),
                ],
            ),
        ],
        media_assets=[_make_asset()],
    )
    plan = build_render_plan(project)

    expected_ids = {"a", "c", "d"}
    assert {layer.clip_id for layer in plan.video_layers} == expected_ids
    # Et chaque ``RenderLayer`` hérite de l'asset résolu.
    for layer in plan.video_layers:
        assert layer.source_path == "/tmp/source.mp4"
        assert layer.asset_id == "asset-1"


# ---------------------------------------------------------------------------
# audio_layers (tâche 10)
# ---------------------------------------------------------------------------


def _make_audio_asset(asset_id: str = "asset-audio", path: str = "/tmp/song.mp3", duration: float = 30.0):
    """Construit un ``MediaAsset`` audio conforme à la validation."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name="Audio",
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )


def test_audio_layers_include_audio_tracks_and_video_with_sound():
    """``audio_layers`` contient les pistes audio + les vidéos avec son."""
    audio_asset = _make_audio_asset()
    silent_video_asset = MediaAsset(
        id="silent",
        path="/tmp/silent.mp4",
        name="Silent",
        duration=4.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=False,
    )
    video_with_audio_asset = MediaAsset(
        id="vid-with-audio",
        path="/tmp/v.mp4",
        name="V",
        duration=4.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=True,
    )

    project = Project(
        name="AudioMix",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="silent-v1", asset_id="silent", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
                Clip(id="vid-with-audio", asset_id="vid-with-audio", track_id="V1",
                     timeline_start=5.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="A1", name="A1", type="audio", clips=[
                Clip(id="music", asset_id="asset-audio", track_id="A1",
                     timeline_start=0.0, source_in=0.0, source_out=10.0),
            ]),
        ],
        media_assets=[audio_asset, silent_video_asset, video_with_audio_asset],
    )

    plan = build_render_plan(project)
    audio_ids = {layer.clip_id for layer in plan.audio_layers}
    # Le clip audio pur est présent.
    assert "music" in audio_ids
    # Le clip vidéo avec son est également mixé (piste son embarquée).
    assert "vid-with-audio" in audio_ids
    # Le clip vidéo silencieux ne produit PAS de couche audio.
    assert "silent-v1" not in audio_ids

    # La durée totale du plan reste alignée sur la timeline
    # (``max(end of music, end of vid-with-audio) = 10``).
    assert plan.duration == pytest.approx(10.0)


def test_audio_layers_exclude_disabled_clips():
    """Un clip audio désactivé ne doit pas apparaître dans ``audio_layers``."""
    audio_asset = _make_audio_asset(duration=10.0)
    project = Project(
        name="Disabled",
        tracks=[
            Track(id="A1", name="A1", type="audio", clips=[
                Clip(id="on", asset_id="asset-audio", track_id="A1",
                     timeline_start=0.0, source_in=0.0, source_out=5.0),
                Clip(id="off", asset_id="asset-audio", track_id="A1",
                     timeline_start=5.0, source_in=0.0, source_out=5.0,
                     enabled=False),
            ]),
        ],
        media_assets=[audio_asset],
    )
    plan = build_render_plan(project)
    assert {layer.clip_id for layer in plan.audio_layers} == {"on"}


def test_audio_layers_track_order_matches_project_tracks():
    """Les ``AudioLayer`` apparaissent dans l'ordre des pistes du projet."""
    audio_asset = _make_audio_asset()
    project = Project(
        name="Order",
        tracks=[
            Track(id="A1", name="A1", type="audio", clips=[
                Clip(id="first", asset_id="asset-audio", track_id="A1",
                     timeline_start=0.0, source_in=0.0, source_out=3.0),
            ]),
            Track(id="A2", name="A2", type="audio", clips=[
                Clip(id="second", asset_id="asset-audio", track_id="A2",
                     timeline_start=0.0, source_in=0.0, source_out=2.0),
            ]),
        ],
        media_assets=[audio_asset],
    )
    plan = build_render_plan(project)
    assert [layer.clip_id for layer in plan.audio_layers] == ["first", "second"]


def test_audio_layers_raise_for_missing_asset():
    """Un clip audio actif sans asset lève une ``KeyError``."""
    project = Project(
        name="Orphan",
        tracks=[
            Track(id="A1", name="A1", type="audio", clips=[
                Clip(id="orphan", asset_id="ghost-audio", track_id="A1",
                     timeline_start=0.0, source_in=0.0, source_out=3.0),
            ]),
        ],
        media_assets=[],
    )
    with pytest.raises(KeyError, match="ghost-audio"):
        build_render_plan(project)