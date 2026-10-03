"""Tests pour ``core.timeline_view_model`` (projection immuable)."""

import pytest

from core.project_factory import create_default_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import cut_clip, move_clip
from core.timeline_view_model import (
    TimelineClipView,
    build_clip_views,
    color_key_for_clip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_with_paths() -> Project:
    """Projet minimal avec deux médias aux chemins distincts."""
    asset_a = MediaAsset(
        id="asset-a",
        path="/tmp/a.mp4",
        name="A",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    asset_b = MediaAsset(
        id="asset-b",
        path="/tmp/b.mp4",
        name="B",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    track_v1 = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="c-video",
                asset_id="asset-a",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=4.0,
                label="Mon intro",
                text="",
            ),
        ],
    )
    track_s1 = Track(
        id="S1",
        name="S1",
        type="subtitle",
        clips=[
            Clip(
                id="c-sub",
                asset_id="asset-b",
                track_id="S1",
                timeline_start=1.0,
                source_in=0.0,
                source_out=4.0,
                label="Sous-titre",
                text="Bienvenue",
            ),
        ],
    )
    return Project(
        name="Demo",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset_a, asset_b],
        tracks=[track_v1, track_s1],
    )


# ---------------------------------------------------------------------------
# build_clip_views
# ---------------------------------------------------------------------------


def test_clip_view_carries_position_duration_label_text_and_path() -> None:
    """Une vue restitue fidèlement les champs principaux du clip projeté."""
    project = _project_with_paths()
    views = build_clip_views(project)

    video = next(v for v in views if v.id == "c-video")
    assert video.start == pytest.approx(0.0)
    assert video.end == pytest.approx(4.0)
    assert video.label == "Mon intro"
    assert video.text == ""
    assert video.source_path == "/tmp/a.mp4"
    assert video.track_id == "V1"
    assert video.track_index == 0

    subtitle = next(v for v in views if v.id == "c-sub")
    assert subtitle.track_id == "S1"
    assert subtitle.track_index == 1  # la piste S1 est en 2e position dans le projet
    assert subtitle.label == "Sous-titre"
    assert subtitle.text == "Bienvenue"
    assert subtitle.source_path == "/tmp/b.mp4"


def test_clip_views_have_no_pyside6_dependency() -> None:
    """Le module de projection n'importe pas PySide6 (vérifié sur le code source)."""
    import pathlib
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "core"
        / "timeline_view_model.py"
    ).read_text(encoding="utf-8")
    # On retire la docstring du module avant de chercher de vraies
    # instructions d'import : la docstring peut mentionner PySide6 sans
    # que cela constitue une dépendance.
    stripped = re.sub(r'^\s*""".*?"""\s*', "", source, count=1, flags=re.DOTALL)
    assert not re.search(r"^\s*(?:from|import)\s+PySide", stripped, flags=re.MULTILINE)


def test_clip_views_are_rebuilt_after_business_operation() -> None:
    """La projection reflète l'état du Project après une opération métier."""
    project = create_default_project()
    views_before = build_clip_views(project)
    intro_before = next(v for v in views_before if v.id == "intro")
    assert intro_before.start == pytest.approx(0.0)

    # L'opération métier mute le Project (timeline_start du clip intro).
    move_clip(project, "intro", 2.5)

    # Une nouvelle projection reflète la nouvelle position.
    views_after = build_clip_views(project)
    intro_after = next(v for v in views_after if v.id == "intro")
    assert intro_after.start == pytest.approx(2.5)


def test_clip_views_reflect_cut_operation() -> None:
    """Une coupe métier se traduit par une vue supplémentaire dans la projection."""
    project = create_default_project()
    assert len(build_clip_views(project)) == 4

    cut_clip(project, "intro", 2.0)

    views = build_clip_views(project)
    assert len(views) == 5
    ids = {v.id for v in views}
    assert "intro" in ids
    assert "intro-split-2" in ids


def test_color_key_is_deterministic_for_same_id() -> None:
    """Deux clips avec le même identifiant reçoivent la même couleur."""
    project = create_default_project()
    views_first = build_clip_views(project)
    views_second = build_clip_views(project)

    for a, b in zip(views_first, views_second):
        assert a.color_key == b.color_key


def test_clip_view_is_immutable() -> None:
    """TimelineClipView est frozen : on ne peut pas muter ses champs."""
    project = create_default_project()
    view = build_clip_views(project)[0]
    with pytest.raises((AttributeError, Exception)):
        view.start = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# color_key_for_clip
# ---------------------------------------------------------------------------


def test_color_key_returns_a_hex_string() -> None:
    project = create_default_project()
    for clip in (c for t in project.tracks for c in t.clips):
        key = color_key_for_clip(clip)
        assert key.startswith("#")
        assert len(key) == 7
