"""Évaluation pure de la timeline de Kut-Studio.

Ce module détermine, à partir des dataclasses métier ``Project``, ``Track``
et ``Clip`` définies dans :mod:`core.project_model`, quels clips sont
actifs à un instant donné et expose une vue immuable de chacun d'eux via
la dataclass :class:`ActiveClip`.

L'objectif est de fournir une couche de calcul :

- sans dépendance à ``PySide6`` (donc utilisable hors d'un contexte Qt) ;
- sans dépendance à ``FFmpeg`` (donc testable sans binaire externe) ;
- sans aucune interaction avec l'interface graphique.

Elle sera consommée ultérieurement par l'aperçu vidéo et le moteur
d'export pour rendre leur comportement réellement fidèle à la timeline
(positions, trous, trims, pistes V1/V2, clips désactivés).
"""

from __future__ import annotations

from dataclasses import dataclass

from .project_model import Clip, MediaAsset, Project


# ---------------------------------------------------------------------------
# Vue immuable d'un clip actif
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActiveClip:
    """Vue immuable d'un ``Clip`` actif à un instant donné de la timeline.

    Cette dataclass est totalement indépendante des structures métier
    modifiables (``Clip``, ``Track``, ``Project``) : elle capture toutes
    les informations nécessaires au rendu ou à l'export pour un clip
    actif, sans permettre de muter la timeline sous-jacente.

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du ``MediaAsset`` source du clip.
        track_id: Identifiant de la ``Track`` portant le clip.
        track_type: Type logique de la piste (``"video"``, ``"audio"``...).
        track_index: Position (0-based) de la piste dans ``project.tracks``.
        source_path: Chemin résolu vers le fichier média sur le disque.
        source_time: Position correspondante dans le média source, en
            secondes (``clip.source_in + (time - clip.timeline_start)``).
        timeline_start: Début du clip sur la timeline, en secondes.
        timeline_end: Fin du clip sur la timeline, en secondes
            (``timeline_start + clip.duration``).
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_type: str
    track_index: int
    source_path: str
    source_time: float
    timeline_start: float
    timeline_end: float


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


def _find_asset(project: Project, asset_id: str) -> MediaAsset:
    """Retourne le ``MediaAsset`` correspondant à ``asset_id``.

    Raises:
        KeyError: si aucun média du projet ne porte cet identifiant.
    """
    for asset in project.media_assets:
        if asset.id == asset_id:
            return asset
    raise KeyError(
        f"Média '{asset_id}' introuvable dans le projet '{project.name}'."
    )


def _is_active(clip: Clip, time_seconds: float) -> bool:
    """Vrai si ``clip`` couvre l'instant ``time_seconds``.

    Le demi-ouvert ``[timeline_start, timeline_end)`` est conservé tel
    que décrit dans la spécification : un clip se termine exactement
    ``timeline_start + duration`` et n'est plus actif à cet instant.
    """
    return clip.timeline_start <= time_seconds < clip.timeline_start + clip.duration


def _build_active_clip(
    clip: Clip,
    track_index: int,
    track_type: str,
    source_path: str,
    time_seconds: float,
) -> ActiveClip:
    """Construit un :class:`ActiveClip` à partir d'un ``Clip`` et du temps."""
    source_time = clip.source_in + (time_seconds - clip.timeline_start)
    return ActiveClip(
        clip_id=clip.id,
        asset_id=clip.asset_id,
        track_id=clip.track_id,
        track_type=track_type,
        track_index=track_index,
        source_path=source_path,
        source_time=source_time,
        timeline_start=clip.timeline_start,
        timeline_end=clip.timeline_start + clip.duration,
    )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def evaluate_timeline(project: Project, time_seconds: float) -> list[ActiveClip]:
    """Retourne les clips actifs à ``time_seconds``, dans l'ordre des pistes.

    Le temps ``time_seconds`` est exprimé en secondes sur la timeline.
    Un clip est considéré actif s'il vérifie :

    .. code-block:: python

        clip.timeline_start <= time_seconds < clip.timeline_start + clip.duration

    Seuls les clips dont l'attribut ``enabled`` vaut ``True`` sont
    retenus. Les chevauchements sont conservés : à un instant donné,
    plusieurs clips peuvent être actifs simultanément (par exemple un
    clip sur V1 et un autre sur V2).

    Le média référencé par chaque clip actif est résolu via
    ``project.media_assets``. Tout clip actif dont l'identifiant de média
    est absent du projet provoque une :class:`KeyError` explicite.

    Args:
        project: projet à évaluer (non muté).
        time_seconds: instant de timeline à évaluer (en secondes, >= 0).

    Returns:
        Liste de :class:`ActiveClip` dans l'ordre des pistes du projet
        (``project.tracks``). Plusieurs clips d'une même piste sont
        conservés dans l'ordre de ``track.clips``.

    Raises:
        ValueError: si ``time_seconds`` est strictement négatif.
        KeyError: si un clip actif référence un média introuvable dans
            ``project.media_assets``.
    """
    if time_seconds < 0.0:
        raise ValueError(
            f"Le temps de timeline doit être positif ou nul "
            f"(reçu : {time_seconds})."
        )

    active: list[ActiveClip] = []
    for track_index, track in enumerate(project.tracks):
        for clip in track.clips:
            if not clip.enabled:
                continue
            if not _is_active(clip, time_seconds):
                continue
            asset = _find_asset(project, clip.asset_id)
            active.append(
                _build_active_clip(
                    clip=clip,
                    track_index=track_index,
                    track_type=track.type,
                    source_path=asset.path,
                    time_seconds=time_seconds,
                )
            )
    return active


def timeline_duration(project: Project) -> float:
    """Retourne la fin maximale des clips activés du projet.

    La durée est calculée comme :

    .. code-block:: python

        max(clip.timeline_start + clip.duration)

    sur l'ensemble des clips dont ``enabled`` vaut ``True``. Si aucun
    clip activé n'existe dans le projet, la durée retournée est ``0.0``.

    Args:
        project: projet à analyser (non muté).

    Returns:
        Durée maximale des clips activés, en secondes (``0.0`` si le
        projet ne contient aucun clip activé).
    """
    end_times = [
        clip.timeline_start + clip.duration
        for track in project.tracks
        for clip in track.clips
        if clip.enabled
    ]
    if not end_times:
        return 0.0
    return max(end_times)