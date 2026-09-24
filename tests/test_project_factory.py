"""Tests pour ``core.project_factory.create_default_project``."""

from core.project_factory import create_default_project
from core.project_model import Clip, MediaAsset, Project, Track


def test_default_project_has_three_tracks_V1_V2_S1():
    """Le projet par défaut expose trois pistes : V1, V2 et S1, dans cet ordre."""
    project = create_default_project()

    track_ids = [track.id for track in project.tracks]
    assert track_ids == ["V1", "V2", "S1"]
    assert [track.type for track in project.tracks] == ["video", "video", "subtitle"]


def test_default_project_name_is_unset_title():
    """Le projet porte le nom affiché au démarrage de l'application."""
    project = create_default_project()
    assert project.name == "Projet sans titre"


def test_default_project_has_demo_clips():
    """Les clips de démonstration historiques (intro, plan_a, b_roll, subtitle) sont présents."""
    project = create_default_project()

    all_clips = {clip.id: clip for track in project.tracks for clip in track.clips}
    assert set(all_clips) == {"intro", "plan_a", "b_roll", "subtitle_01"}

    # Le sous-titre porte son texte initial.
    assert all_clips["subtitle_01"].text == "Bienvenue dans Kut-Studio"
    # Les autres clips ont un text vide.
    assert all_clips["intro"].text == ""
    assert all_clips["plan_a"].text == ""
    assert all_clips["b_roll"].text == ""

    # Les clips de la timeline occupent les pistes attendues.
    assert all_clips["intro"].track_id == "V1"
    assert all_clips["plan_a"].track_id == "V1"
    assert all_clips["b_roll"].track_id == "V2"
    assert all_clips["subtitle_01"].track_id == "S1"


def test_default_project_has_valid_media_assets():
    """Chaque clip est rattaché à un MediaAsset valide du projet."""
    project = create_default_project()

    asset_ids = {asset.id for asset in project.media_assets}
    for clip in (clip for track in project.tracks for clip in track.clips):
        assert clip.asset_id in asset_ids
        # Tous les MediaAsset de démo sont des objets valides (la validation
        # du dataclass a déjà rejeté les valeurs invalides à la création).
        assert isinstance(
            next(a for a in project.media_assets if a.id == clip.asset_id),
            MediaAsset,
        )


def test_default_project_returns_a_fresh_instance():
    """Deux appels consécutifs produisent deux projets indépendants (pas de fuite d'état)."""
    project_a = create_default_project()
    project_b = create_default_project()

    initial_v1_clips = list(project_b.tracks[0].clips)
    intrusion = Clip(
        id="intrusion",
        asset_id="asset-intro",
        track_id="V1",
        timeline_start=99.0,
        source_in=0.0,
        source_out=1.0,
    )
    project_a.tracks[0].clips.append(intrusion)

    # project_b doit être intact : pas de fuite entre les instances.
    assert project_b.tracks[0].clips == initial_v1_clips
    assert intrusion not in project_b.tracks[0].clips
