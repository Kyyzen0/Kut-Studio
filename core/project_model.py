"""Modèles de données fondamentaux de Kut-Studio.

Ce module définit les dataclasses partagées (MediaAsset, Clip, Track, Project)
qui serviront de base à la refactorisation progressive de l'application.

Aucune dépendance à PySide6 : ces modèles sont purement métier et peuvent être
manipulés hors d'un contexte Qt (tests, scripts, futurs services).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MediaAsset:
    """Décrit un média source importé dans le projet (vidéo, audio, image...).

    Attributes:
        id: Identifiant unique du média dans le projet.
        path: Chemin vers le fichier source sur le disque.
        name: Nom humain du média (affiché dans l'UI).
        duration: Durée totale du média en secondes (>= 0).
        width: Largeur intrinsèque du média en pixels (> 0).
        height: Hauteur intrinsèque du média en pixels (> 0).
        fps: Fréquence d'images du média (> 0).
        media_type: Type de média ("video", "audio", "image", "subtitle"...).
    """

    id: str
    path: str
    name: str
    duration: float
    width: int
    height: int
    fps: float
    media_type: str

    def __post_init__(self) -> None:
        """Rejette les valeurs physiquement impossibles pour un média."""
        if self.duration < 0.0:
            raise ValueError("La durée d'un MediaAsset doit être positive ou nulle.")
        if self.width <= 0:
            raise ValueError("La largeur d'un MediaAsset doit être strictement positive.")
        if self.height <= 0:
            raise ValueError("La hauteur d'un MediaAsset doit être strictement positive.")
        if self.fps <= 0.0:
            raise ValueError("Le fps d'un MediaAsset doit être strictement positif.")


@dataclass
class Clip:
    """Une portion d'un MediaAsset placée sur une Track à un instant donné.

    Attributes:
        id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du MediaAsset source.
        track_id: Identifiant de la Track sur laquelle le clip est placé.
        timeline_start: Position de début sur la timeline (en secondes, >= 0).
        source_in: Point d'entrée dans le média source (en secondes, >= 0).
        source_out: Point de sortie du média source (en secondes, > source_in).
        enabled: Indique si le clip est actif (False = clip désactivé / muet).
        label: Nom affiché du clip dans la timeline (par défaut "").
        text: Contenu textuel éventuel du clip, notamment pour les
            sous-titres (par défaut "" ; reste vide pour les clips vidéo).
    """

    id: str
    asset_id: str
    track_id: str
    timeline_start: float
    source_in: float
    source_out: float
    enabled: bool = True
    label: str = ""
    text: str = ""

    def __post_init__(self) -> None:
        """Empêche les configurations qui produiraient une durée nulle ou négative."""
        if self.source_in < 0.0:
            raise ValueError("source_in doit être positif ou nul.")
        if self.source_out <= self.source_in:
            raise ValueError(
                "source_out doit être strictement supérieur à source_in "
                "pour garantir une durée de clip positive."
            )
        if self.timeline_start < 0.0:
            raise ValueError("timeline_start doit être positif ou nul.")

    @property
    def duration(self) -> float:
        """Durée du clip sur la timeline (égale à ``source_out - source_in``)."""
        return self.source_out - self.source_in


@dataclass
class Track:
    """Une piste de la timeline (vidéo, audio, sous-titres...).

    Attributes:
        id: Identifiant unique de la piste dans le projet.
        name: Nom humain de la piste (ex. "V1", "S1").
        type: Type logique de la piste ("video", "audio", "subtitle"...).
        clips: Liste des clips présents sur cette piste.
    """

    id: str
    name: str
    type: str
    clips: list[Clip] = field(default_factory=list)


@dataclass
class Project:
    """Le projet complet : métadonnées de rendu + médias importés + pistes.

    Attributes:
        name: Nom humain du projet.
        width: Largeur de la timeline en pixels (> 0).
        height: Hauteur de la timeline en pixels (> 0).
        fps: Fréquence d'images cible du projet (> 0).
        media_assets: Liste des médias importés dans le projet.
        tracks: Liste des pistes composant la timeline.
    """

    name: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    media_assets: list[MediaAsset] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Vérifie que les paramètres de rendu du projet sont cohérents."""
        if self.width <= 0:
            raise ValueError("La largeur du projet doit être strictement positive.")
        if self.height <= 0:
            raise ValueError("La hauteur du projet doit être strictement positive.")
        if self.fps <= 0.0:
            raise ValueError("Le fps du projet doit être strictement positif.")
