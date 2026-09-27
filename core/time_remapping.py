"""Système de remappage temporel non destructif pour Kut-Studio.

Ce module fournit les types et fonctions pures pour gérer la vitesse,
le reverse et l'arrêt sur image des clips vidéo et audio.

Concepts clés :
- La durée timeline d'un clip dépend de sa vitesse : duration_timeline = duration_source / speed
- Le reverse est un booléen séparé (pas de vitesse négative)
- Un arrêt sur image a une durée timeline positive mais ne lit qu'une image source
- Les clips audio ne peuvent pas devenir un arrêt sur image

Toutes les fonctions sont pures et testables sans dépendance à Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project_model import Clip


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MIN_SPEED: float = 0.1
"""Vitesse minimale autorisée (0.1x)."""

MAX_SPEED: float = 8.0
"""Vitesse maximale autorisée (8x)."""

DEFAULT_SPEED: float = 1.0
"""Vitesse par défaut (1.0x = normale)."""

# Seuil de durée pour refuser un reverse trop long (en secondes)
# Au-delà, FFmpeg pourrait consommer trop de mémoire avec le filtre reverse
MAX_REVERSE_DURATION_SECONDS: float = 3600.0  # 1 heure


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class FreezeFrameMode(str, Enum):
    """Mode d'arrêt sur image pour un clip."""

    NONE = "none"
    FREEZE = "freeze"


@dataclass(frozen=True)
class TimeRemapping:
    """Remappage temporel d'un clip.

    Attributes:
        speed: Vitesse de lecture (0.1 à 8.0). 1.0 = normale.
        reverse: Si True, le clip est lu à l'envers.
        freeze_mode: Mode d'arrêt sur image (NONE ou FREEZE).
        freeze_source_time: Instant source pour l'arrêt sur image (en secondes).
            Utilisé uniquement si freeze_mode == FREEZE.
        freeze_duration: Durée de l'arrêt sur image sur la timeline (en secondes).
            Doit être > 0. Utilisé uniquement si freeze_mode == FREEZE.
    """

    speed: float = DEFAULT_SPEED
    reverse: bool = False
    freeze_mode: FreezeFrameMode = FreezeFrameMode.NONE
    freeze_source_time: float = 0.0
    freeze_duration: float = 1.0

    def __post_init__(self) -> None:
        """Valide les invariants du remappage temporel."""
        # Vitesse
        if not (MIN_SPEED <= self.speed <= MAX_SPEED):
            raise ValueError(
                f"La vitesse doit être entre {MIN_SPEED}x et {MAX_SPEED}x, "
                f"got {self.speed}x."
            )

        # Freeze frame
        if self.freeze_mode == FreezeFrameMode.FREEZE:
            if self.freeze_duration <= 0.0:
                raise ValueError(
                    "La durée d'un arrêt sur image doit être strictement positive."
                )

    @property
    def is_frozen(self) -> bool:
        """Le clip est-il en mode arrêt sur image ?"""
        return self.freeze_mode == FreezeFrameMode.FREEZE

    @property
    def is_normal(self) -> bool:
        """Le clip a-t-il une vitesse normale sans reverse ni freeze ?"""
        return (
            self.speed == DEFAULT_SPEED
            and not self.reverse
            and self.freeze_mode == FreezeFrameMode.NONE
        )

    @classmethod
    def default(cls) -> "TimeRemapping":
        """Retourne un TimeRemapping avec les valeurs par défaut."""
        return cls()


# ---------------------------------------------------------------------------
# Fonctions pures de conversion
# ---------------------------------------------------------------------------

def clamp_speed(value: object) -> float:
    """Ramène une vitesse dans la plage autorisée [MIN_SPEED, MAX_SPEED].

    Une valeur non numérique, NaN ou hors bornes est corrigée.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if number != number:  # NaN
        return DEFAULT_SPEED
    return max(MIN_SPEED, min(MAX_SPEED, number))


def source_to_timeline_time(
    source_time: float,
    source_in: float,
    source_out: float,
    speed: float,
    reverse: bool,
    freeze_mode: FreezeFrameMode,
    freeze_source_time: float,
) -> float:
    """Convertit un temps source en temps timeline.

    Args:
        source_time: Temps dans le média source (en secondes).
        source_in: Point d'entrée source du clip.
        source_out: Point de sortie source du clip.
        speed: Vitesse de lecture.
        reverse: Si True, lecture à l'envers.
        freeze_mode: Mode d'arrêt sur image.
        freeze_source_time: Instant source pour le freeze.

    Returns:
        Temps sur la timeline (en secondes, relatif au début du clip).

    Raises:
        ValueError: Si source_time est hors des bornes [source_in, source_out].
    """
    source_duration = source_out - source_in

    if freeze_mode == FreezeFrameMode.FREEZE:
        # En mode freeze, toute la durée timeline correspond à un seul instant source
        # On vérifie que source_time correspond bien à freeze_source_time
        if abs(source_time - freeze_source_time) > 0.0001:  # Tolérance numérique
            raise ValueError(
                f"En mode arrêt sur image, source_time doit être {freeze_source_time}, "
                f"got {source_time}."
            )
        # Le temps timeline est simplement l'offsets dans la durée freeze
        return 0.0  # Le freeze commence à t=0 sur la timeline du clip

    # Normaliser le temps source dans la plage du clip
    if source_time < source_in or source_time > source_out:
        raise ValueError(
            f"source_time {source_time} doit être dans [{source_in}, {source_out}]."
        )

    # Calculer le temps relatif dans le clip source
    relative_source = source_time - source_in

    if reverse:
        # En reverse, on inverse le temps relatif
        relative_source = source_duration - relative_source

    # Appliquer la vitesse
    timeline_time = relative_source / speed

    return timeline_time


def timeline_to_source_time(
    timeline_time: float,
    source_in: float,
    source_out: float,
    speed: float,
    reverse: bool,
    freeze_mode: FreezeFrameMode,
    freeze_source_time: float,
) -> float:
    """Convertit un temps timeline en temps source.

    Args:
        timeline_time: Temps sur la timeline (en secondes, relatif au début du clip).
        source_in: Point d'entrée source du clip.
        source_out: Point de sortie source du clip.
        speed: Vitesse de lecture.
        reverse: Si True, lecture à l'envers.
        freeze_mode: Mode d'arrêt sur image.
        freeze_source_time: Instant source pour le freeze.

    Returns:
        Temps dans le média source (en secondes).

    Raises:
        ValueError: Si timeline_time est hors des bornes valides.
    """
    source_duration = source_out - source_in

    if freeze_mode == FreezeFrameMode.FREEZE:
        # En mode freeze, tout temps timeline donne le même temps source
        return freeze_source_time

    # Calculer le temps source relatif
    relative_source = timeline_time * speed

    if reverse:
        # En reverse, on inverse le temps relatif
        relative_source = source_duration - relative_source

    # Vérifier les bornes
    if relative_source < 0.0 or relative_source > source_duration:
        raise ValueError(
            f"timeline_time {timeline_time} avec speed {speed} et reverse {reverse} "
            f"donne un temps source hors des bornes [{source_in}, {source_out}]."
        )

    return source_in + relative_source


def compute_timeline_duration(
    source_in: float,
    source_out: float,
    speed: float,
    freeze_mode: FreezeFrameMode,
    freeze_duration: float,
) -> float:
    """Calcule la durée du clip sur la timeline.

    Args:
        source_in: Point d'entrée source du clip.
        source_out: Point de sortie source du clip.
        speed: Vitesse de lecture.
        freeze_mode: Mode d'arrêt sur image.
        freeze_duration: Durée de l'arrêt sur image.

    Returns:
        Durée sur la timeline (en secondes).
    """
    source_duration = source_out - source_in

    if freeze_mode == FreezeFrameMode.FREEZE:
        return freeze_duration

    return source_duration / speed


def validate_time_remapping(
    speed: float,
    reverse: bool,
    freeze_mode: FreezeFrameMode,
    freeze_source_time: float,
    freeze_duration: float,
    source_in: float,
    source_out: float,
    media_type: str,
) -> list[str]:
    """Valide les paramètres de remappage temporel.

    Args:
        speed: Vitesse de lecture.
        reverse: Si True, lecture à l'envers.
        freeze_mode: Mode d'arrêt sur image.
        freeze_source_time: Instant source pour le freeze.
        freeze_duration: Durée de l'arrêt sur image.
        source_in: Point d'entrée source.
        source_out: Point de sortie source.
        media_type: Type de média ('video', 'audio', 'image', 'subtitle').

    Returns:
        Liste des erreurs (vide si tout est valide).
    """
    errors = []

    # Vitesse
    if not (MIN_SPEED <= speed <= MAX_SPEED):
        errors.append(
            f"La vitesse doit être entre {MIN_SPEED}x et {MAX_SPEED}x, got {speed}x."
        )

    # Freeze frame
    if freeze_mode == FreezeFrameMode.FREEZE:
        if freeze_duration <= 0.0:
            errors.append("La durée d'un arrêt sur image doit être strictement positive.")

        if freeze_source_time < source_in or freeze_source_time > source_out:
            errors.append(
                f"Le temps source de l'arrêt sur image ({freeze_source_time}) "
                f"doit être dans [{source_in}, {source_out}]."
            )

        # Les clips audio ne peuvent pas être en freeze frame
        if media_type == "audio":
            errors.append("Les clips audio ne peuvent pas être en mode arrêt sur image.")

    # Reverse trop long
    if reverse:
        source_duration = source_out - source_in
        if source_duration > MAX_REVERSE_DURATION_SECONDS:
            errors.append(
                f"Un clip reverse de {source_duration}s dépasse la limite de "
                f"{MAX_REVERSE_DURATION_SECONDS}s pour des raisons de mémoire FFmpeg."
            )

    return errors


def create_freeze_frame(
    source_in: float,
    source_out: float,
    freeze_source_time: float | None = None,
    freeze_duration: float = 1.0,
) -> TimeRemapping:
    """Crée un TimeRemapping en mode arrêt sur image.

    Args:
        source_in: Point d'entrée source du clip.
        source_out: Point de sortie source du clip.
        freeze_source_time: Instant source pour l'arrêt sur image.
            Si None, utilise le milieu du clip.
        freeze_duration: Durée de l'arrêt sur image (doit être > 0).

    Returns:
        TimeRemapping configuré pour l'arrêt sur image.

    Raises:
        ValueError: Si freeze_duration <= 0 ou si freeze_source_time est hors bornes.
    """
    if freeze_duration <= 0.0:
        raise ValueError("La durée d'un arrêt sur image doit être strictement positive.")

    if freeze_source_time is None:
        # Par défaut, on utilise le milieu du clip
        freeze_source_time = source_in + (source_out - source_in) / 2.0

    if freeze_source_time < source_in or freeze_source_time > source_out:
        raise ValueError(
            f"freeze_source_time ({freeze_source_time}) doit être dans "
            f"[{source_in}, {source_out}]."
        )

    return TimeRemapping(
        speed=DEFAULT_SPEED,
        reverse=False,
        freeze_mode=FreezeFrameMode.FREEZE,
        freeze_source_time=freeze_source_time,
        freeze_duration=freeze_duration,
    )


def remove_freeze_frame() -> TimeRemapping:
    """Supprime le mode arrêt sur image et retourne aux valeurs par défaut."""
    return TimeRemapping.default()


def set_speed(speed: float) -> TimeRemapping:
    """Crée un TimeRemapping avec une vitesse donnée."""
    return TimeRemapping(speed=clamp_speed(speed), reverse=False)


def set_reverse(reverse: bool) -> TimeRemapping:
    """Crée un TimeRemapping avec un état reverse donné."""
    return TimeRemapping(speed=DEFAULT_SPEED, reverse=reverse)


def toggle_reverse(current: TimeRemapping) -> TimeRemapping:
    """Inverse l'état reverse d'un TimeRemapping."""
    return TimeRemapping(
        speed=current.speed,
        reverse=not current.reverse,
        freeze_mode=current.freeze_mode,
        freeze_source_time=current.freeze_source_time,
        freeze_duration=current.freeze_duration,
    )


def compute_source_duration(
    timeline_start: float,
    timeline_end: float,
    speed: float,
    freeze_mode: FreezeFrameMode,
) -> float:
    """Calcule la durée source à partir de la durée timeline.

    Args:
        timeline_start: Début du clip sur la timeline.
        timeline_end: Fin du clip sur la timeline.
        speed: Vitesse de lecture.
        freeze_mode: Mode d'arrêt sur image.

    Returns:
        Durée source (en secondes).
    """
    timeline_duration = timeline_end - timeline_start

    if freeze_mode == FreezeFrameMode.FREEZE:
        # En freeze, la durée source est 0 (une seule image)
        return 0.0

    return timeline_duration * speed


def can_be_frozen(media_type: str) -> bool:
    """Indique si un clip peut être en mode arrêt sur image.

    Args:
        media_type: Type de média.

    Returns:
        True si le clip peut être en freeze, False sinon.
    """
    # Seuls les médias vidéo et image peuvent être en freeze
    return media_type in ("video", "image")


def get_speed_from_preset(preset: str) -> float:
    """Retourne une vitesse à partir d'un preset textuel.

    Args:
        preset: Un des presets : "0.25x", "0.5x", "1x", "2x", "4x".

    Returns:
        La vitesse correspondante.

    Raises:
        ValueError: Si le preset n'est pas reconnu.
    """
    presets = {
        "0.25x": 0.25,
        "0.5x": 0.5,
        "1x": 1.0,
        "2x": 2.0,
        "4x": 4.0,
    }
    if preset not in presets:
        raise ValueError(f"Preset inconnu: {preset}. Presets valides: {list(presets.keys())}")
    return presets[preset]


def get_ffmpeg_speed_filter(speed: float) -> list[str]:
    """Génère les filtres FFmpeg pour une vitesse donnée.

    FFmpeg a une limite native pour atempo (0.5x à 2.0x).
    Pour des vitesses hors de cet intervalle, on chaîne les filtres atempo.

    Args:
        speed: Vitesse de lecture (entre MIN_SPEED et MAX_SPEED).

    Returns:
        Liste des filtres FFmpeg (ex: ['atempo=0.5', 'atempo=0.5'] pour 0.25x).
    """
    # Clamp la vitesse dans la plage valide
    speed = clamp_speed(speed)

    if speed == 1.0:
        # Pas de filtre nécessaire
        return []

    # FFmpeg atempo native range: 0.5x to 2.0x
    # On décompose la vitesse en facteurs dans [0.5, 2.0]
    filters = []
    remaining_speed = speed

    # Pour les vitesses > 2.0x, on chaîne des atempo=2.0
    # Ex: 4.0 = 2.0 * 2.0, 8.0 = 2.0 * 2.0 * 2.0
    if remaining_speed > 2.0:
        count = 0
        while remaining_speed > 2.0 and count < 10:
            filters.append("atempo=2.0")
            remaining_speed /= 2.0
            count += 1

    # Pour les vitesses < 0.5x, on chaîne des atempo=0.5
    # Ex: 0.25 = 0.5 * 0.5, 0.125 = 0.5 * 0.5 * 0.5
    if remaining_speed < 0.5:
        count = 0
        while remaining_speed < 0.5 and count < 10:
            filters.append("atempo=0.5")
            remaining_speed /= 0.5
            count += 1

    # Ajouter le facteur final si nécessaire
    if abs(remaining_speed - 1.0) > 0.0001:
        # Formater proprement
        if abs(remaining_speed - 0.5) < 0.0001:
            filters.append("atempo=0.5")
        elif abs(remaining_speed - 2.0) < 0.0001:
            filters.append("atempo=2.0")
        elif remaining_speed == int(remaining_speed):
            filters.append(f"atempo={int(remaining_speed)}.0")
        else:
            filters.append(f"atempo={remaining_speed:.6f}")

    return filters


def get_ffmpeg_reverse_filter(has_audio: bool, has_video: bool) -> list[str]:
    """Génère les filtres FFmpeg pour le reverse.

    Args:
        has_audio: Si True, le clip a une piste audio.
        has_video: Si True, le clip a une piste vidéo.

    Returns:
        Liste des filtres FFmpeg pour le reverse.
        Pour la vidéo: ['reverse']
        Pour l'audio: ['areverse']
    """
    filters = []
    if has_video:
        filters.append("reverse")
    if has_audio:
        filters.append("areverse")
    return filters


def get_ffmpeg_freeze_filter(
    freeze_source_time: float,
    asset_fps: float,
) -> list[str]:
    """Génère les filtres FFmpeg pour un arrêt sur image.

    Args:
        freeze_source_time: Instant source pour l'arrêt sur image.
        asset_fps: FPS du média source.

    Returns:
        Liste des filtres FFmpeg.
        Ex: ['select=eq(n,125)', 'setpts=N/FRAME_RATE/TB']
    """
    # Calculer le frame number à partir du temps
    frame_number = int(freeze_source_time * asset_fps)
    return [f"select=eq(n,{frame_number})", "setpts=N/FRAME_RATE/TB"]


def estimate_ffmpeg_memory_usage(
    source_duration: float,
    has_audio: bool,
    has_video: bool,
    width: int,
    height: int,
    fps: float,
) -> int:
    """Estime la consommation mémoire de FFmpeg pour un reverse.

    Args:
        source_duration: Durée source en secondes.
        has_audio: Si True, le clip a de l'audio.
        has_video: Si True, le clip a de la vidéo.
        width: Largeur de la vidéo.
        height: Hauteur de la vidéo.
        fps: FPS de la vidéo.

    Returns:
        Estimation de la mémoire en Mo.
    """
    # Estimation très conservative
    # FFmpeg doit bufferer tout le clip pour le reverse
    if not has_video and not has_audio:
        return 0

    # Mémoire pour la vidéo: width * height * 4 bytes par frame * fps * duration
    video_memory_bytes = 0
    if has_video:
        bits_per_pixel = 24  # RGB24 ou similaire
        bytes_per_frame = width * height * (bits_per_pixel / 8)
        total_frames = source_duration * fps
        video_memory_bytes = bytes_per_frame * total_frames

    # Mémoire pour l'audio: 44100 Hz * 16 bits * 2 channels * duration
    audio_memory_bytes = 0
    if has_audio:
        sample_rate = 44100
        bits_per_sample = 16
        channels = 2
        audio_memory_bytes = sample_rate * (bits_per_sample / 8) * channels * source_duration

    total_bytes = video_memory_bytes + audio_memory_bytes
    total_mb = int(total_bytes / (1024 * 1024))

    return total_mb
