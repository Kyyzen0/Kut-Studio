"""Fabrique un projet Kut-Studio par défaut prêt à être affiché.

Ce module isole la création du projet initial dans une fonction pure,
réutilisable par les tests et par ``MainWindow``. Aucune dépendance
PySide6 : seul ``core.project_model`` est nécessaire.
"""

from __future__ import annotations

from .project_model import Clip, MediaAsset, Project, Track


_TRACK_TYPES_LABELS = {"video": "Vidéo", "subtitle": "Sous-titres"}


def create_default_project() -> Project:
    """Construit le projet de démonstration affiché au démarrage.

    Le projet se nomme « Projet sans titre » et contient trois pistes
    (V1, V2, S1) avec les clips et ``MediaAsset`` historiquement
    présents dans l'interface (``Intro``, ``Plan A``, ``B-roll``,
    sous-titre de bienvenue). Les chemins de médias restent vides :
    les ``MediaAsset`` sont des objets métier valides mais ne pointent
    vers aucun fichier sur disque tant que l'utilisateur n'importe pas
    de média réel.
    """
    asset_intro = _make_demo_asset("asset-intro", "Intro")
    asset_plan_a = _make_demo_asset("asset-plan-a", "Plan A")
    asset_b_roll = _make_demo_asset("asset-b-roll", "B-roll")
    asset_subtitle = _make_demo_asset("asset-subtitle", "Sous-titre 01")

    clip_intro = Clip(
        id="intro",
        asset_id="asset-intro",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
        label="Intro",
    )
    clip_plan_a = Clip(
        id="plan_a",
        asset_id="asset-plan-a",
        track_id="V1",
        timeline_start=6.5,
        source_in=0.0,
        source_out=5.5,
        label="Plan A",
    )
    clip_b_roll = Clip(
        id="b_roll",
        asset_id="asset-b-roll",
        track_id="V2",
        timeline_start=2.0,
        source_in=0.0,
        source_out=5.5,
        label="B-roll",
    )
    clip_subtitle = Clip(
        id="subtitle_01",
        asset_id="asset-subtitle",
        track_id="S1",
        timeline_start=1.0,
        source_in=0.0,
        source_out=4.0,
        label="Sous-titre 01",
        text="Bienvenue dans Kut-Studio",
    )

    track_v1 = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[clip_intro, clip_plan_a],
    )
    track_v2 = Track(
        id="V2",
        name="V2",
        type="video",
        clips=[clip_b_roll],
    )
    track_s1 = Track(
        id="S1",
        name="S1",
        type="subtitle",
        clips=[clip_subtitle],
    )

    return Project(
        name="Projet sans titre",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[
            asset_intro,
            asset_plan_a,
            asset_b_roll,
            asset_subtitle,
        ],
        tracks=[track_v1, track_v2, track_s1],
    )


def _make_demo_asset(asset_id: str, name: str) -> MediaAsset:
    """Construit un MediaAsset de démonstration au chemin vide."""
    return MediaAsset(
        id=asset_id,
        path="",
        name=name,
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
