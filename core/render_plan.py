"""Plan de rendu vidéo pur pour Kut-Studio.

Ce module construit un :class:`RenderPlan` immuable à partir d'un
:class:`~core.project_model.Project`. Le plan décrit fidèlement la
timeline :

- position et durée des clips ;
- trims ``source_in`` / ``source_out`` ;
- pistes vidéo uniquement (audio et sous-titres sont ignorés pour
  cette itération) ;
- ordre des pistes : les pistes les plus basses dans ``project.tracks``
  sont visuellement au-dessus (overlay successif) ;
- clips activés (``enabled=True``) uniquement ;
- durée totale égale à :func:`core.timeline_evaluator.timeline_duration`.

Ce module n'importe ni PySide6 ni FFmpeg : c'est une couche métier
pure, réutilisable par tout consommateur (FFmpeg via
``core.export_engine``, futur exporteur GPU, tests…).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .project_model import MediaAsset, Project
from .timeline_evaluator import timeline_duration


# ---------------------------------------------------------------------------
# Couches vidéo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderLayer:
    """Description immuable d'un clip vidéo à rendre.

    Une :class:`RenderLayer` capture toutes les informations nécessaires
    pour composer une image sur la timeline finale, sans dépendre d'un
    état mutable. Les bornes ``source_in`` / ``source_out`` et
    ``timeline_start`` / ``timeline_end`` sont conservées à leur valeur
    d'origine (avant ou après déplacement / trim).

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du :class:`MediaAsset` source.
        track_id: Identifiant de la piste portant le clip.
        track_index: Position (0-based) de la piste dans
            ``project.tracks``.
        source_path: Chemin résolu du fichier média source.
        source_in: Point d'entrée dans le média source (secondes).
        source_out: Point de sortie du média source (secondes).
        timeline_start: Début du clip sur la timeline (secondes).
        timeline_end: Fin du clip sur la timeline (secondes).
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_index: int
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float


@dataclass(frozen=True)
class AudioLayer:
    """Description immuable d'un clip audio à mixer.

    Une :class:`AudioLayer` capture les mêmes informations qu'une
    :class:`RenderLayer` pour la partie son. Elle est utilisée pour :

    - les clips des pistes de type ``"audio"`` (ex. ``A1``) ;
    - les clips des pistes vidéo dont le :class:`MediaAsset`
      sous-jacent porte une piste audio (``has_audio=True``).

    Les clips dont le média source n'a pas de flux audio sont
    volontairement écartés en amont : on ne crée pas
    d':class:`AudioLayer` pour eux.

    Attributes:
        clip_id: Identifiant unique du clip dans le projet.
        asset_id: Identifiant du :class:`MediaAsset` source.
        track_id: Identifiant de la piste portant le clip.
        track_index: Position (0-based) de la piste dans
            ``project.tracks``.
        source_path: Chemin résolu du fichier média source.
        source_in: Point d'entrée dans le média source (secondes).
        source_out: Point de sortie du média source (secondes).
        timeline_start: Début du clip sur la timeline (secondes).
        timeline_end: Fin du clip sur la timeline (secondes).
    """

    clip_id: str
    asset_id: str
    track_id: str
    track_index: int
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float


# ---------------------------------------------------------------------------
# Plan complet
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderPlan:
    """Plan immuable décrivant un export.

    Attributes:
        width: Largeur cible de la vidéo finale (pixels).
        height: Hauteur cible de la vidéo finale (pixels).
        fps: Fréquence d'images cible de la vidéo finale.
        duration: Durée totale du plan (secondes). Égale à
            :func:`timeline_duration` du projet source.
        video_layers: Couches vidéo à composer, dans l'ordre des pistes
            (les premières listées sont rendues en premier, les
            dernières apparaissent au-dessus).
        audio_layers: Couches audio à mixer. Couvre les pistes audio
            (``A1``…) **et** les clips vidéo dont le média source porte
            une piste audio (``has_audio=True``).
    """

    width: int
    height: int
    fps: float
    duration: float
    video_layers: tuple[RenderLayer, ...] = field(default_factory=tuple)
    audio_layers: tuple[AudioLayer, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


def _find_asset(project: Project, asset_id: str) -> MediaAsset:
    """Retourne le :class:`MediaAsset` correspondant à ``asset_id``.

    Raises:
        KeyError: si aucun média du projet ne porte cet identifiant.
    """
    for asset in project.media_assets:
        if asset.id == asset_id:
            return asset
    raise KeyError(
        f"Média '{asset_id}' introuvable dans le projet '{project.name}'."
    )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def build_render_plan(project: Project) -> RenderPlan:
    """Construit un :class:`RenderPlan` à partir d'un :class:`Project`.

    Règles appliquées :

    - Pour la vidéo : seules les pistes de type ``"video"`` sont
      conservées, avec leurs clips activés uniquement. Les couches
      sont émises dans l'ordre des pistes du projet.
    - Pour l'audio : les pistes de type ``"audio"`` sont conservées
      intégralement ; les pistes vidéo ne contribuent une couche audio
      que si leur :class:`MediaAsset` porte ``has_audio=True``.
    - Chaque clip actif dont l'asset est introuvable lève une
      ``KeyError`` explicite.
    - La durée du plan est exactement
      :func:`core.timeline_evaluator.timeline_duration` du projet.

    Args:
        project: projet source (jamais muté).

    Returns:
        Le :class:`RenderPlan` correspondant au projet.
    """
    asset_cache: dict[str, MediaAsset] = {asset.id: asset for asset in project.media_assets}

    video_layers: list[RenderLayer] = []
    audio_layers: list[AudioLayer] = []
    for track_index, track in enumerate(project.tracks):
        if track.type not in {"video", "audio"}:
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            asset = _find_asset(project, clip.asset_id)
            if track.type == "video":
                video_layers.append(
                    RenderLayer(
                        clip_id=clip.id,
                        asset_id=clip.asset_id,
                        track_id=track.id,
                        track_index=track_index,
                        source_path=asset.path,
                        source_in=clip.source_in,
                        source_out=clip.source_out,
                        timeline_start=clip.timeline_start,
                        timeline_end=clip.timeline_start + clip.duration,
                    )
                )
                if asset.has_audio:
                    audio_layers.append(
                        _build_audio_layer(clip, asset, track.id, track_index)
                    )
            else:  # track.type == "audio"
                audio_layers.append(
                    _build_audio_layer(clip, asset, track.id, track_index)
                )
    return RenderPlan(
        width=project.width,
        height=project.height,
        fps=float(project.fps),
        duration=timeline_duration(project),
        video_layers=tuple(video_layers),
        audio_layers=tuple(audio_layers),
    )


def _build_audio_layer(
    clip, asset: MediaAsset, track_id: str, track_index: int
) -> AudioLayer:
    return AudioLayer(
        clip_id=clip.id,
        asset_id=clip.asset_id,
        track_id=track_id,
        track_index=track_index,
        source_path=asset.path,
        source_in=clip.source_in,
        source_out=clip.source_out,
        timeline_start=clip.timeline_start,
        timeline_end=clip.timeline_start + clip.duration,
    )