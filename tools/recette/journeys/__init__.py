"""Les parcours scriptés : un parcours = des médias (recette), un script déclaratif, des exports et des analyses.

Un parcours est une description pure (aucune E/S) : le même code sert à la génération des médias, à la construction du
projet par ``Kut-Studio apply``, aux exports et aux tests. Le script ne contient aucun chemin absolu : les médias sont
cités relativement à son dossier (ADR-0005 : ``import_media`` est confiné au dossier du script).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..media import MediaItem

SCALES = ("full", "quick")


@dataclass(frozen=True)
class Capabilities:
    """Ce que la machine sait faire ; le script s'en passe proprement (dégradation) quand ce n'est pas le cas."""

    libass: bool = True
    whisper: bool = False


@dataclass(frozen=True)
class ExportSpec:
    name: str                         # « 9x16 »
    output: str                       # nom du fichier de sortie
    preset: str
    sequence: str | None = None       # nom ou identifiant ; défaut : la séquence active
    expected_size: tuple[int, int] | None = None
    expect_audio: bool = True


@dataclass(frozen=True)
class AnalysisSpec:
    """Analyse hors export, faite par le moteur sur un média généré (tempo, synchronisation), avec sa vérité connue."""

    kind: str                         # « tempo » | « sync »
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Journey:
    name: str
    title: str
    base: tuple[int, int, float]      # largeur, hauteur, cadence du projet de départ
    tracks: tuple[tuple[str, str], ...]   # (identifiant, genre) des pistes du projet de départ
    media: Callable[[str], list[MediaItem]]
    script: Callable[[str, Capabilities], dict[str, Any]]
    exports: Callable[[str, Capabilities], list[ExportSpec]]
    analyses: Callable[[str], list[AnalysisSpec]] = lambda scale: []
    description: str = ""


def check_scale(scale: str) -> str:
    if scale not in SCALES:
        raise ValueError(f"Échelle inconnue : {scale!r} (attendu : {', '.join(SCALES)})")
    return scale


def snap(seconds: float, fps: float = 30.0) -> float:
    """Durée arrondie à une image (la timeline compte en images ; une durée non alignée serait ajustée par le modèle)."""
    return round(round(seconds * fps) / fps, 6)


def registry() -> dict[str, Journey]:
    from . import motion3d, smoke, tiktok, youtube

    return {journey.name: journey for journey in (tiktok.JOURNEY, youtube.JOURNEY, motion3d.JOURNEY, smoke.JOURNEY)}


MAIN_JOURNEYS = ("tiktok", "youtube", "motion3d")
