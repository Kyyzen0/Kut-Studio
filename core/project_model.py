"""Modèles de données fondamentaux de Kut-Studio.

Ce module définit les dataclasses partagées (MediaAsset, Clip, Track, Project)
qui serviront de base à la refactorisation progressive de l'application.

Aucune dépendance à PySide6 : ces modèles sont purement métier et peuvent être
manipulés hors d'un contexte Qt (tests, scripts, futurs services).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .time_remapping import TimeRemapping
    from .visual_effects import ClipTransform, TransformKeyframe


# ---------------------------------------------------------------------------
# Bornes audio
# ---------------------------------------------------------------------------

MIN_GAIN_DB: float = -60.0
"""Gain minimal (dB). -60 dB est perçu comme le silence."""

MAX_GAIN_DB: float = 12.0
"""Gain maximal (dB). Au-delà, le risque de saturation augmente fort."""

MIN_PAN: float = -1.0
"""Panoramique fully gauche."""

MAX_PAN: float = 1.0
"""Panoramique fully droite."""


def clamp_gain_db(value: object) -> float:
    """Ramène un gain (dB) dans la plage autorisée.

    Une valeur non numérique (``None``, ``"x"``) retombe sur 0 dB plutôt
    que de lever : un projet chargé ne doit jamais casser sur un champ
    audio absent ou douteux.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, number))


def clamp_pan(value: object) -> float:
    """Ramène un panoramique dans ``[-1, 1]``. Non numérique → centré."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(MIN_PAN, min(MAX_PAN, number))


def clamp_fade(value: object) -> float:
    """Ramène une durée de fondu à une valeur non négative."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, number)


@dataclass
class MediaAsset:
    """Décrit un média source importé dans le projet (vidéo, audio, image...).

    Attributes:
        id: Identifiant unique du média dans le projet.
        path: Chemin vers le fichier source sur le disque.
        name: Nom humain du média (affiché dans l'UI).
        duration: Durée totale du média en secondes. Pour un média
            audio seul, doit être strictement positive ; pour les
            autres types, ``>= 0``.
        width: Largeur intrinsèque du média en pixels. Pour un média
            audio seul, peut valoir ``0``.
        height: Hauteur intrinsèque du média en pixels. Pour un média
            audio seul, peut valoir ``0``.
        fps: Fréquence d'images du média. Pour un média audio seul,
            peut valoir ``0.0``. Pour les médias vidéo, doit être
            strictement positive.
        media_type: Type de média ("video", "audio", "image", "subtitle"...).
        has_audio: Indique si le média porte une piste audio exploitable.
            Vrai pour les médias audio seuls ; pour les vidéos, dépend
            du contenu source. Permet à l'export de mixer l'audio même
            depuis une piste vidéo.
    """

    id: str
    path: str
    name: str
    duration: float
    width: int
    height: int
    fps: float
    media_type: str
    has_audio: bool = False

    def __post_init__(self) -> None:
        """Rejette les valeurs physiquement impossibles pour un média."""
        if self.media_type == "audio":
            if self.duration <= 0.0:
                raise ValueError(
                    "La durée d'un MediaAsset audio doit être strictement positive."
                )
            # Pour un média audio seul, les dimensions vidéo sont à 0.
            if self.width != 0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir une largeur de 0."
                )
            if self.height != 0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir une hauteur de 0."
                )
            if self.fps != 0.0:
                raise ValueError(
                    "Un MediaAsset audio doit avoir un fps de 0.0."
                )
            if not self.has_audio:
                raise ValueError(
                    "Un MediaAsset audio doit avoir has_audio=True."
                )
        elif self.media_type == "subtitle":
            # Les sous-titres sont des métadonnées non-visuelles :
            # pas de dimensions, pas de fps, pas de piste audio.
            if self.width != 0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir une largeur de 0."
                )
            if self.height != 0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir une hauteur de 0."
                )
            if self.fps != 0.0:
                raise ValueError(
                    "Un MediaAsset sous-titre doit avoir un fps de 0.0."
                )
            if self.has_audio:
                raise ValueError(
                    "Un MediaAsset sous-titre ne doit pas porter has_audio=True."
                )
            # ``path`` peut être vide : les sous-titres sont inline dans
            # le ``Project``. La durée n'est pas contrainte ici.
        else:
            if self.duration < 0.0:
                raise ValueError(
                    "La durée d'un MediaAsset doit être positive ou nulle."
                )
            if self.width <= 0:
                raise ValueError(
                    "La largeur d'un MediaAsset doit être strictement positive."
                )
            if self.height <= 0:
                raise ValueError(
                    "La hauteur d'un MediaAsset doit être strictement positive."
                )
            if self.fps <= 0.0:
                raise ValueError(
                    "Le fps d'un MediaAsset doit être strictement positif."
                )


@dataclass
class Clip:
    """Une portion d'un MediaAsset placée sur une Track à un instant donné.

    Attributes:
        id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du MediaAsset source.
        track_id: Identifiant de la Track sur laquelle le clip est placée.
        timeline_start: Position de début sur la timeline (en secondes, >= 0).
        source_in: Point d'entrée dans le média source (en secondes, >= 0).
        source_out: Point de sortie du média source (en secondes, > source_in).
        enabled: Indique si le clip est actif (False = clip désactivé / muet).
        label: Nom affiché du clip dans la timeline (par défaut "").
        text: Contenu textuel éventuel du clip, notamment pour les
            sous-titres (par défaut "" ; reste vide pour les clips vidéo).
        transform: Transform 2D appliqué au clip (par défaut : identité).
        transform_keyframes: Images-clés d'animation du transform. Liste
            triée par ``(property_name, time_seconds)``.
        time_remapping: Remappage temporel (vitesse, reverse, freeze frame).
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
    transform: "ClipTransform" = field(default_factory=lambda: _default_transform())
    transform_keyframes: list["TransformKeyframe"] = field(default_factory=list)
    # --- Mixage audio (non destructif, ignoré par la vidéo) ---
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    # --- Remappage temporel ---
    time_remapping: "TimeRemapping" = field(
        default_factory=lambda: _default_time_remapping()
    )

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

        # --- Bornes audio ---
        # On normalise au lieu de lever : ces paramètres sont édités
        # par des faders et des poignées de fondu, un dépassement est
        # une gêne d'usage, pas une corruption de projet.
        self.gain_db = clamp_gain_db(self.gain_db)
        self.pan = clamp_pan(self.pan)
        self.fade_in = clamp_fade(self.fade_in)
        self.fade_out = clamp_fade(self.fade_out)
        # Deux fondus ne peuvent pas se chevaucher.
        duration = self.duration
        if self.fade_in + self.fade_out > duration:
            scale = duration / (self.fade_in + self.fade_out)
            self.fade_in *= scale
            self.fade_out *= scale

    @property
    def source_duration(self) -> float:
        """Durée source du clip (égale à ``source_out - source_in``).
        
        Cette propriété donne la durée originale du média source, indépendamment
        de la vitesse ou du freeze frame. Utilisez ``duration`` pour obtenir la
        durée sur la timeline.
        """
        return self.source_out - self.source_in

    @property
    def duration(self) -> float:
        """Durée du clip sur la timeline.
        
        La durée timeline dépend de la vitesse et du mode freeze frame :
        - Sans freeze : duration = source_duration / speed
        - Avec freeze : duration = freeze_duration
        """
        from .time_remapping import FreezeFrameMode
        
        if self.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
            return self.time_remapping.freeze_duration
        
        return self.source_duration / self.time_remapping.speed

    def set_fade_in(self, seconds: float) -> float:
        """Règle le fondu d'entrée sans empiéter sur le fondu de sortie.

        Le fondu de sortie n'est **pas** réajusté : une poignée ne doit
        jamais déplacer celle que l'utilisateur vient de régler.

        Returns:
            La valeur effectivement appliquée (bornée).
        """
        self.fade_in = min(clamp_fade(seconds), max(0.0, self.duration - self.fade_out))
        return self.fade_in

    def set_fade_out(self, seconds: float) -> float:
        """Règle le fondu de sortie sans empiéter sur le fondu d'entrée."""
        self.fade_out = min(clamp_fade(seconds), max(0.0, self.duration - self.fade_in))
        return self.fade_out

    def _rebalance_fades(self) -> None:
        """Force ``fade_in + fade_out <= duration`` en rognant proportionnellement.

        Réservé au chargement d'un projet : les setters ne l'appellent
        pas, pour ne pas déplacer un fondu déjà réglé par l'utilisateur.
        """
        duration = self.duration
        total = self.fade_in + self.fade_out
        if total > duration and total > 0.0:
            scale = duration / total
            self.fade_in *= scale
            self.fade_out *= scale

    def reset_fades(self) -> None:
        """Remet les deux fondus à zéro (double-clic / bouton Réinitialiser)."""
        self.fade_in = 0.0
        self.fade_out = 0.0

    # --- Remappage temporel ---

    @property
    def is_frozen(self) -> bool:
        """Le clip est-il en mode arrêt sur image ?"""
        return self.time_remapping.freeze_mode.value == "freeze"

    @property
    def is_reversed(self) -> bool:
        """Le clip est-il en mode reverse ?"""
        return self.time_remapping.reverse

    @property
    def speed(self) -> float:
        """Vitesse de lecture du clip."""
        return self.time_remapping.speed

    @property
    def is_time_remapped(self) -> bool:
        """Le clip a-t-il un remappage temporel non par défaut ?"""
        return not self.time_remapping.is_normal

    @property
    def is_audio_affected(self) -> bool:
        """Le clip porte-t-il des réglages audio à conserver."""
        return (
            self.gain_db != 0.0
            or self.pan != 0.0
            or self.fade_in != 0.0
            or self.fade_out != 0.0
        )


def _default_transform():  # pragma: no cover - import deferred
    """Retourne un :class:`ClipTransform` par défaut (import paresseux)."""
    from .visual_effects import ClipTransform

    return ClipTransform()


def _default_time_remapping():  # pragma: no cover - import deferred
    """Retourne un :class:`TimeRemapping` par défaut (import paresseux)."""
    from .time_remapping import TimeRemapping

    return TimeRemapping()


@dataclass
class Track:
    """Une piste de la timeline (vidéo, audio, sous-titres...).

    Attributes:
        id: Identifiant unique de la piste dans le projet.
        name: Nom humain de la piste (ex. "V1", "S1").
        type: Type logique de la piste ("video", "audio", "subtitle"...).
        clips: Liste des clips présents sur cette piste.
        locked: ``True`` si la piste refuse toute modification depuis
            l'éditeur (déplacement, trim, suppression, ajout, dépôt).
            Le verrouillage ne concerne que l'UI ; il n'a aucun effet
            sur le rendu / l'export, qui continuent à ignorer les
            pistes verrouillées de la même façon que les autres.
        visible: ``True`` si la piste doit apparaître dans l'aperçu et
            être exportée. Les clips des pistes vidéo invisibles ne
            sont pas rendus ; les sous-titres d'une piste invisible
            ne sont pas incrustés.
        muted: ``True`` si les clips audio de la piste doivent rester
            silencieux à l'export. Pour les pistes non audio, l'effet
            est limité (un sous-titre "muet" reste incrusté). Défini
            explicitement par piste pour offrir un comportement
            cohérent entre types.
        solo: ``True`` si cette piste est la seule de son type à être
            entendue ou vue dans l'aperçu. Le solo vidéo n'affecte pas
            l'audio.
        armed: réservé à un futur enregistrement audio. Sans effet
            sur le rendu actuel.
        height_mode: ``compact``, ``normal`` ou ``large``.
        collapsed: piste réduite à une ligne, sans waveform ni vignettes.
    """

    id: str
    name: str
    type: str
    clips: list[Clip] = field(default_factory=list)
    locked: bool = False
    visible: bool = True
    muted: bool = False
    solo: bool = False
    armed: bool = False
    height_mode: str = "normal"
    collapsed: bool = False
    # --- Mixage audio (non destructif, ignoré par la vidéo) ---
    volume_db: float = 0.0
    pan: float = 0.0

    def __post_init__(self) -> None:
        if self.height_mode not in {"compact", "normal", "large"}:
            self.height_mode = "normal"
        self.volume_db = clamp_gain_db(self.volume_db)
        self.pan = clamp_pan(self.pan)

    def set_volume_db(self, value: float) -> float:
        """Règle le volume de piste, borné, et retourne la valeur appliquée."""
        self.volume_db = clamp_gain_db(value)
        return self.volume_db

    def set_pan(self, value: float) -> float:
        """Règle le panoramique de piste, borné, et retourne la valeur appliquée."""
        self.pan = clamp_pan(value)
        return self.pan

    def reset_audio(self) -> None:
        """Remet volume et panoramique à leur valeur neutre."""
        self.volume_db = 0.0
        self.pan = 0.0

    @property
    def is_video(self) -> bool:
        return self.type == "video"

    @property
    def is_audio(self) -> bool:
        return self.type == "audio"

    @property
    def is_subtitle(self) -> bool:
        return self.type == "subtitle"


@dataclass
class Marker:
    """Repère posé sur la règle de la timeline.

    ``category`` prépare des couleurs futures (``standard``, ``todo``,
    ``chapter``). L'interface n'en distingue qu'une pour l'instant.
    """

    id: str
    time_seconds: float
    name: str = ""
    category: str = "standard"

    def __post_init__(self) -> None:
        if self.time_seconds < 0.0:
            raise ValueError("Un marqueur ne peut pas être avant 0 seconde.")
        if self.category not in {"standard", "todo", "chapter"}:
            self.category = "standard"


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
        markers: Repères de la règle, triés par l'éditeur à l'insertion.
    """

    name: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    media_assets: list[MediaAsset] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    transitions: list["Transition"] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Vérifie que les paramètres de rendu du projet sont cohérents."""
        if self.width <= 0:
            raise ValueError("La largeur du projet doit être strictement positive.")
        if self.height <= 0:
            raise ValueError("La hauteur du projet doit être strictement positive.")
        if self.fps <= 0.0:
            raise ValueError("Le fps du projet doit être strictement positif.")
