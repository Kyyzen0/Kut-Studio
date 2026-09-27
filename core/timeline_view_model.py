"""Projection immuable d'un ``Project`` vers l'interface graphique.

Ce module définit ``TimelineClipView`` et la fonction ``build_clip_views``
qui transforme un ``Project`` en une liste de vues prêtes à être
affichées. La projection est strictement en lecture : aucune vue ne
peut muter le ``Project`` source.

Trois responsabilités supplémentaires y sont regroupées :

- ``color_key_for_clip`` : couleur déterministe basée sur l'identifiant
  du clip (utilisée pour colorer les blocs sur la timeline).
- ``build_export_clips`` : adaptateur produisant un snapshot
  « clé-valeur » du projet, conservé pour la sauvegarde ``.srt`` des
  sous-titres (``core.effects.save_subtitles``). L'export vidéo
  principal utilise désormais :class:`core.render_plan.RenderPlan`.
- ``v1_transition_pairs`` : helper utilisé par la timeline pour repérer
  les jonctions entre clips V1.

Aucune dépendance PySide6 : ces vues sont de simples dataclass.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from .project_model import Clip, Project, Track


_CLIP_COLOR_PALETTE: tuple[str, ...] = (
    "#4da3ff",
    "#58c4a7",
    "#b070ff",
    "#e6c84f",
    "#f27686",
    "#67d6a3",
    "#f7c948",
    "#7C5CFC",
)


@dataclass(frozen=True)
class TimelineClipView:
    """Vue immuable d'un clip destinée à l'affichage.

    Attributes:
        id: Identifiant du clip (égal à ``Clip.id``).
        track_id: Identifiant de la piste contenant le clip.
        track_index: Position de la piste dans ``Project.tracks``.
        start: Position de début du clip sur la timeline, en secondes.
        end: Position de fin du clip sur la timeline, en secondes.
        label: Nom affiché du clip.
        text: Contenu textuel éventuel (sous-titres), ou chaîne vide.
        source_path: Chemin du ``MediaAsset`` lié, ou chaîne vide.
        color_key: Couleur hexadécimale déterministe pour l'affichage.
        track_type: Type logique de la piste (``"video"``, ``"audio"``...).
        enabled: Indique si le clip est activé.
        keyframes: Images-clés de transform portées par le clip (tâche 13).
        transform: Transform de base du clip (tâche 13).
        source_in: Point d'entrée dans le média source.
        source_out: Point de sortie du média source.
        source_duration: Durée source du clip (source_out - source_in).
        time_remapping: Remappage temporel (vitesse, reverse, freeze frame).
    """

    id: str
    track_id: str
    track_index: int
    start: float
    end: float
    label: str
    text: str
    source_path: str
    color_key: str
    track_type: str = "video"
    enabled: bool = True
    keyframes: tuple = ()
    transform: object = None
    source_in: float = 0.0
    source_out: float = 0.0
    source_duration: float = 0.0
    time_remapping: object = None


def color_key_for_clip(clip: Clip) -> str:
    """Retourne une couleur hexadécimaire déterministe pour un clip."""
    digest = zlib.crc32(clip.id.encode("utf-8"))
    return _CLIP_COLOR_PALETTE[digest % len(_CLIP_COLOR_PALETTE)]


def build_clip_views(project: Project) -> list[TimelineClipView]:
    """Projette un ``Project`` en liste de vues immuables.

    Cette fonction est pure : elle ne mute jamais le projet ni les
    dataclasses métier. Les vues reflètent strictement l'état du
    ``Project`` au moment de l'appel ; toute opération métier
    ultérieure doit être suivie d'un nouvel appel à ``build_clip_views``
    (ou ``set_project`` côté interface) pour reconstruire la projection.
    """
    asset_paths = {asset.id: asset.path for asset in project.media_assets}
    views: list[TimelineClipView] = []
    for track_index, track in enumerate(project.tracks):
        for clip in track.clips:
            views.append(
                TimelineClipView(
                    id=clip.id,
                    track_id=track.id,
                    track_index=track_index,
                    start=clip.timeline_start,
                    end=clip.timeline_start + clip.duration,
                    label=clip.label,
                    text=clip.text,
                    source_path=asset_paths.get(clip.asset_id, ""),
                    color_key=color_key_for_clip(clip),
                    track_type=track.type,
                    enabled=clip.enabled,
                    keyframes=tuple(clip.transform_keyframes),
                    transform=clip.transform,
                    source_in=clip.source_in,
                    source_out=clip.source_out,
                    source_duration=clip.source_duration,
                    time_remapping=clip.time_remapping,
                )
            )
    return views


def build_export_clips(project: Project) -> list[dict]:
    """Adaptateur : produit un snapshot clé-valeur du ``Project``.

    Utilisé par :func:`core.effects.save_subtitles` pour écrire un
    fichier ``.srt`` à partir des clips de la piste ``S1``. L'export
    vidéo principal (``core.export_engine``) n'utilise plus cette
    fonction ; il consomme désormais
    :class:`core.render_plan.RenderPlan`.

    Le format produit est stable (clés ``id``, ``track``, ``start``,
    ``end``, ``label``, ``text``, ``source_path``, ``color``) et
    constitue la source de vérité pour la sauvegarde ``.srt``.
    """
    asset_paths = {asset.id: asset.path for asset in project.media_assets}
    export_clips: list[dict] = []
    for track_index, track in enumerate(project.tracks):
        for clip in track.clips:
            export_clips.append(
                {
                    "id": clip.id,
                    "track": track_index,
                    "start": clip.timeline_start,
                    "end": clip.timeline_start + clip.duration,
                    "label": clip.label,
                    "text": clip.text,
                    "source_path": asset_paths.get(clip.asset_id, ""),
                    "color": color_key_for_clip(clip),
                }
            )
    return export_clips


# ---------------------------------------------------------------------------
# Helpers de rendu (jonctions V1)
# ---------------------------------------------------------------------------


def transition_gap_pixels(
    previous: TimelineClipView,
    following: TimelineClipView,
    pixels_per_second: float,
    zoom: float,
) -> float:
    """Largeur en pixels de l'écart entre deux clips successifs."""
    return (following.start - previous.end) * pixels_per_second * zoom


def v1_transition_pairs(
    views: list[TimelineClipView],
    pixels_per_second: float,
    zoom: float,
    max_gap_pixels: float = 10.0,
) -> list[tuple[TimelineClipView, TimelineClipView]]:
    """Retourne les paires de clips V1 suffisamment proches pour une jonction."""
    v1_views = sorted(
        (view for view in views if view.track_id == "V1"),
        key=lambda view: view.start,
    )
    return [
        (previous, following)
        for previous, following in zip(v1_views, v1_views[1:])
        if 0
        < transition_gap_pixels(previous, following, pixels_per_second, zoom)
        <= max_gap_pixels
    ]
