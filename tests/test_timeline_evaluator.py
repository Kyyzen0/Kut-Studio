"""Tests pour l'évaluateur pur de la timeline (``core.timeline_evaluator``)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_evaluator import (
    ActiveClip,
    evaluate_timeline,
    timeline_duration,
)


# ---------------------------------------------------------------------------
# Usines de fixtures
# ---------------------------------------------------------------------------


def _make_asset(
    *,
    asset_id: str = "asset-1",
    path: str = "/tmp/source.mp4",
    duration: float = 10.0,
    media_type: str = "video",
) -> MediaAsset:
    """Construit un ``MediaAsset`` valide aux valeurs sobres."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name=f"Media {asset_id}",
        duration=duration,
        width=0 if media_type == "audio" else 1920,
        height=0 if media_type == "audio" else 1080,
        fps=0.0 if media_type == "audio" else 30.0,
        media_type=media_type,
        has_audio=media_type == "audio",
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
    """Construit un ``Clip`` valide à partir de sa ``duration``."""
    return Clip(
        id=clip_id,
        asset_id=asset_id,
        track_id=track_id,
        timeline_start=timeline_start,
        source_in=source_in,
        source_out=source_in + duration,
        enabled=enabled,
    )


def _make_project(
    *,
    tracks: list[Track] | None = None,
    media_assets: list[MediaAsset] | None = None,
) -> Project:
    """Construit un projet minimal avec 2 pistes V1/V2 par défaut."""
    if media_assets is None:
        media_assets = [_make_asset()]
    if tracks is None:
        tracks = [
            Track(id="V1", name="V1", type="video"),
            Track(id="V2", name="V2", type="video"),
        ]
    return Project(
        name="Evaluator-Test",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=media_assets,
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# Timeline vide / trous
# ---------------------------------------------------------------------------


def test_empty_project_returns_no_active_clips():
    """Une timeline vide ne produit aucun clip actif."""
    project = _make_project()

    assert evaluate_timeline(project, 0.0) == []
    assert evaluate_timeline(project, 5.0) == []


def test_gap_between_two_clips_returns_no_active_clips():
    """Un trou entre deux clips ne produit aucun clip actif."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(clip_id="a", timeline_start=0.0, duration=2.0),
            _make_clip(clip_id="b", timeline_start=5.0, duration=2.0),
        ],
    )
    project = _make_project(tracks=[track])

    # 3.0 est strictement entre la fin de "a" et le début de "b".
    assert evaluate_timeline(project, 3.0) == []


# ---------------------------------------------------------------------------
# Bornes : clip actif à son début / inactif à sa fin
# ---------------------------------------------------------------------------


def test_clip_is_active_at_its_exact_start():
    """Un clip est actif à ``timeline_start`` (borne inférieure inclusive)."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[_make_clip(clip_id="c", timeline_start=2.0, duration=3.0)],
    )
    project = _make_project(tracks=[track])

    active = evaluate_timeline(project, 2.0)

    assert len(active) == 1
    assert active[0].clip_id == "c"


def test_clip_is_inactive_at_its_exact_end():
    """Un clip est inactif à ``timeline_start + duration`` (borne supérieure exclusive)."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[_make_clip(clip_id="c", timeline_start=2.0, duration=3.0)],
    )
    project = _make_project(tracks=[track])

    assert evaluate_timeline(project, 5.0) == []


# ---------------------------------------------------------------------------
# Calcul de ``source_time``
# ---------------------------------------------------------------------------


def test_source_time_reflects_move_clip():
    """``source_time`` suit correctement un déplacement de clip."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(
                clip_id="moved",
                timeline_start=4.0,
                source_in=2.0,
                duration=5.0,
            ),
        ],
    )
    project = _make_project(tracks=[track])

    active = evaluate_timeline(project, 5.0)

    assert len(active) == 1
    # source_time = source_in + (time - timeline_start) = 2.0 + (5.0 - 4.0)
    assert active[0].source_time == pytest.approx(3.0)


def test_source_time_reflects_trim_left():
    """``source_time`` suit correctement un trim gauche (source_in avance)."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(
                clip_id="trimmed",
                timeline_start=2.5,
                source_in=1.5,
                duration=2.0,
            ),
        ],
    )
    project = _make_project(tracks=[track])

    active = evaluate_timeline(project, 2.5)

    assert len(active) == 1
    # source_time = source_in + (time - timeline_start) = 1.5 + 0.0
    assert active[0].source_time == pytest.approx(1.5)


def test_source_time_reflects_trim_right():
    """``source_time`` reste correct après un trim droit (duration raccourcie)."""
    # Clip rogné à droite : timeline_start et source_in inchangés,
    # mais duration réduite de 5.0 → 3.0 (source_out abaissé).
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(
                clip_id="shortened",
                timeline_start=5.0,
                source_in=2.0,
                duration=3.0,
            ),
        ],
    )
    project = _make_project(tracks=[track])

    # À t=6.0, le clip est actif (timeline [5.0, 8.0]).
    active = evaluate_timeline(project, 6.0)

    assert len(active) == 1
    # source_time = source_in + (time - timeline_start) = 2.0 + 1.0
    assert active[0].source_time == pytest.approx(3.0)
    # La durée raccourcie est bien reportée sur timeline_end.
    assert active[0].timeline_end == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Chevauchements V1/V2
# ---------------------------------------------------------------------------


def test_simultaneous_clips_on_V1_and_V2_are_both_returned():
    """Les clips simultanés sur V1 et V2 sont tous deux actifs."""
    project = _make_project(
        tracks=[
            Track(
                id="V1",
                name="V1",
                type="video",
                clips=[
                    _make_clip(
                        clip_id="v1-only",
                        track_id="V1",
                        timeline_start=0.0,
                        duration=4.0,
                    ),
                ],
            ),
            Track(
                id="V2",
                name="V2",
                type="video",
                clips=[
                    _make_clip(
                        clip_id="v2-only",
                        track_id="V2",
                        timeline_start=2.0,
                        duration=4.0,
                    ),
                ],
            ),
        ],
    )

    active = evaluate_timeline(project, 3.0)

    assert [ac.clip_id for ac in active] == ["v1-only", "v2-only"]
    # V1 (index 0) précède V2 (index 1) dans la sortie.
    assert active[0].track_index == 0
    assert active[1].track_index == 1


def test_active_clips_carry_track_metadata():
    """Chaque ``ActiveClip`` expose le type et l'index de sa piste."""
    asset_audio = _make_asset(
        asset_id="asset-audio",
        path="/tmp/audio.wav",
        media_type="audio",
    )
    project = _make_project(
        media_assets=[_make_asset(), asset_audio],
        tracks=[
            Track(
                id="V1",
                name="V1",
                type="video",
                clips=[_make_clip(clip_id="vid", track_id="V1")],
            ),
            Track(
                id="A1",
                name="A1",
                type="audio",
                clips=[
                    _make_clip(
                        clip_id="aud",
                        track_id="A1",
                        asset_id="asset-audio",
                    )
                ],
            ),
        ],
    )

    active = evaluate_timeline(project, 0.0)
    by_id = {ac.clip_id: ac for ac in active}

    assert by_id["vid"].track_type == "video"
    assert by_id["vid"].track_index == 0
    assert by_id["vid"].source_path == "/tmp/source.mp4"
    assert by_id["aud"].track_type == "audio"
    assert by_id["aud"].track_index == 1
    assert by_id["aud"].source_path == "/tmp/audio.wav"


# ---------------------------------------------------------------------------
# Exclusion des clips désactivés
# ---------------------------------------------------------------------------


def test_disabled_clips_are_excluded_from_evaluation():
    """Un clip ``enabled=False`` ne doit jamais apparaître dans la sortie."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(clip_id="on", timeline_start=0.0, duration=4.0),
            _make_clip(
                clip_id="off",
                timeline_start=1.0,
                duration=2.0,
                enabled=False,
            ),
        ],
    )
    project = _make_project(tracks=[track])

    active = evaluate_timeline(project, 1.5)

    assert [ac.clip_id for ac in active] == ["on"]


def test_disabled_clips_do_not_contribute_to_duration():
    """La durée de timeline ignore les clips désactivés."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(clip_id="on", timeline_start=0.0, duration=4.0),
            _make_clip(
                clip_id="off-long",
                timeline_start=10.0,
                duration=20.0,
                enabled=False,
            ),
        ],
    )
    project = _make_project(tracks=[track])

    assert timeline_duration(project) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Durée de la timeline
# ---------------------------------------------------------------------------


def test_timeline_duration_is_max_of_enabled_clip_ends():
    """``timeline_duration`` retourne le maximum des fins de clips activés."""
    project = _make_project(
        tracks=[
            Track(
                id="V1",
                name="V1",
                type="video",
                clips=[
                    _make_clip(clip_id="short", timeline_start=0.0, duration=2.0),
                    _make_clip(
                        clip_id="long",
                        timeline_start=4.0,
                        duration=5.0,
                    ),
                ],
            ),
            Track(
                id="V2",
                name="V2",
                type="video",
                clips=[
                    _make_clip(
                        clip_id="mid",
                        track_id="V2",
                        timeline_start=1.0,
                        duration=3.0,
                    ),
                ],
            ),
        ],
    )

    # max(2.0, 9.0, 4.0) = 9.0
    assert timeline_duration(project) == pytest.approx(9.0)


def test_timeline_duration_is_zero_when_no_enabled_clip():
    """Aucun clip activé ⇒ durée ``0.0``."""
    project = _make_project(
        tracks=[
            Track(
                id="V1",
                name="V1",
                type="video",
                clips=[
                    _make_clip(
                        clip_id="off",
                        timeline_start=0.0,
                        duration=5.0,
                        enabled=False,
                    ),
                ],
            ),
        ],
    )

    assert timeline_duration(project) == 0.0


def test_timeline_duration_is_zero_for_empty_project():
    """Un projet totalement vide a une durée de ``0.0``."""
    project = _make_project()
    assert timeline_duration(project) == 0.0


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


def test_negative_time_raises_value_error():
    """Un temps négatif doit être refusé par ``ValueError``."""
    project = _make_project()

    with pytest.raises(ValueError, match="positif"):
        evaluate_timeline(project, -0.1)


def test_missing_media_for_active_clip_raises_key_error():
    """Un clip actif référençant un média absent lève une ``KeyError`` claire."""
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _make_clip(
                clip_id="orphan",
                asset_id="ghost-asset",
                timeline_start=0.0,
                duration=2.0,
            ),
        ],
    )
    # Le projet ne contient pas le média "ghost-asset".
    project = _make_project(tracks=[track])

    with pytest.raises(KeyError, match="ghost-asset"):
        evaluate_timeline(project, 0.5)


# ---------------------------------------------------------------------------
# Pureté du module
# ---------------------------------------------------------------------------


def test_timeline_evaluator_does_not_import_pyside6():
    """Le module ``core.timeline_evaluator`` ne doit pas dépendre de PySide6."""
    module_path = (
        Path(__file__).resolve().parent.parent
        / "core"
        / "timeline_evaluator.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    forbidden = {"PySide6", "PySide2", "PyQt5", "PyQt6"}
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if top in forbidden:
                    offenders.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".", 1)[0]
            if top in forbidden:
                offenders.append(f"from {node.module} import ...")

    assert not offenders, (
        "Le module timeline_evaluator ne doit pas importer de GUI Qt : "
        + ", ".join(offenders)
    )


def test_active_clip_is_immutable():
    """``ActiveClip`` est une dataclass figée : toute mutation lève une erreur."""
    asset = _make_asset()
    project = _make_project(
        tracks=[
            Track(
                id="V1",
                name="V1",
                type="video",
                clips=[_make_clip(clip_id="only")],
            ),
        ],
        media_assets=[asset],
    )

    active = evaluate_timeline(project, 0.0)
    assert isinstance(active[0], ActiveClip)

    with pytest.raises(Exception):
        active[0].clip_id = "mutated"  # type: ignore[misc]
