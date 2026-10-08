"""Projection immuable d'un ``Project`` vers l'interface graphique.

Ce module définit ``TimelineClipView`` et la fonction ``build_clip_views``
qui transforme un ``Project`` en une liste de vues prêtes à être
affichées. La projection est strictement en lecture : aucune vue ne
peut muter le ``Project`` source.

``color_key_for_clip`` donne une couleur déterministe basée sur
l'identifiant du clip (utilisée pour colorer les blocs sur la timeline).
L'export (vidéo comme sous-titres ``.srt``) ne passe pas par ce module : il
consomme :class:`core.render_plan.RenderPlan`.

Aucune dépendance PySide6 : ces vues sont de simples dataclass.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from .project_model import Clip, Project
from .time_map import speed_keyframes


_CLIP_COLOR_PALETTE: tuple[str, ...] = (
    # Palette turquoise / vert-noir cohérente avec l'identité visuelle.
    # Les clips vidéo tirent des bleus discrets, les audios des
    # turquoise, et les titres / calques d'ajustement des violets.
    "#4FA3D9",  # bleu vidéo 1
    "#36E6C3",  # turquoise signature
    "#B58EF9",  # violet titres
    "#5BEFD0",  # turquoise clair
    "#7B5BE3",  # violet adjustment layer
    "#235F8A",  # bleu vidéo 2 (clip long)
    "#94E0CC",  # vert d'eau (audio secondaire)
    "#3FB59B",  # vert signature secondaire
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
        effects: Effets visuels du clip, dans leur ordre d'application
            (vide pour l'audio et les sous-titres).
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
    effects: tuple = ()
    color_grade: object = None
    locked: bool = False
    # Style texte non destructif (tâche 24) : ``None`` pour les clips
    # non textuels, un :class:`~core.text_style.TextStyle` pour les
    # sous-titres.
    text_style: object = None
    graphic: object = None
    # Masques, mode de fusion, incrustation (éditeur Compositing).
    compositing: object = None
    # --- Séquence imbriquée ---
    # Séquence référencée (vide pour un clip média).
    sequence_id: str = ""
    # ``""`` (normal), ``"missing"`` (séquence supprimée : clip hors
    # ligne), ``"cycle"`` (imbrication circulaire) ou ``"overflow"`` (le
    # clip dépasse la fin de sa séquence source).
    nested_status: str = ""
    # Instant (temps timeline) à partir duquel le clip dépasse sa source ;
    # ``None`` s'il n'y a pas de débordement.
    nested_overflow_start: float | None = None
    # --- Multicam ---
    # ``angle_index`` : rang (0 = Angle 1) de l'angle montré par ce segment dans sa source Multicam, ``-1`` si le clip
    # n'est pas un segment Multicam. L'interface en tire la couleur (palette du thème) ; le modèle ne connaît aucune
    # couleur d'angle, seulement ``angle_color_index``, le rang de palette que l'utilisateur a choisi pour l'angle.
    angle_index: int = -1
    angle_count: int = 0
    angle_name: str = ""
    angle_color_index: int = 0
    # --- Temps ---
    # Points de la courbe de vitesse (keyframes ``time.speed``, temps local du clip) : l'inspecteur en donne le nombre et la
    # timeline en dessine la courbe. Vide pour un clip à vitesse constante.
    speed_points: tuple = ()

    @property
    def is_nested(self) -> bool:
        return bool(self.sequence_id)

    @property
    def is_multicam(self) -> bool:
        return self.angle_index >= 0


NESTED_CLIP_COLOR = "#C9A227"
"""Couleur des clips imbriqués : distincte de la palette des médias."""

BROKEN_NESTED_CLIP_COLOR = "#B5474B"
"""Couleur d'un clip imbriqué hors ligne ou circulaire."""


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
    cycles = None
    for track_index, track in enumerate(project.tracks):
        for clip in track.clips:
            label = clip.label
            color = color_key_for_clip(clip)
            status = ""
            overflow_start = None
            angle_index, angle_count, angle_name, angle_color = -1, 0, "", 0
            if clip.sequence_id:
                from .sequences import find_cycles, nested_clip_status

                if cycles is None:
                    cycles = find_cycles(project)
                status = nested_clip_status(project, clip, cycles=cycles)
                sequence = project.get_sequence(clip.sequence_id)
                # Le nom affiché suit la séquence : la renommer renomme
                # toutes ses occurrences.
                label = sequence.name if sequence is not None else (clip.label or clip.sequence_id)
                color = (
                    BROKEN_NESTED_CLIP_COLOR if status in {"missing", "cycle"}
                    else NESTED_CLIP_COLOR
                )
                if status == "overflow" and sequence is not None:
                    overflow_start = _overflow_start(clip, sequence.duration)
                source = sequence.multicam if sequence is not None else None
                if source is not None and source.angles:
                    from .multicam import resolve_angle

                    angle = resolve_angle(source, clip.angle_id)
                    angle_count = len(source.angles)
                    if angle is None:
                        # Le segment désigne un angle qui n'existe plus : rendu vide, signalé comme un clip hors ligne.
                        status = status or "angle_missing"
                        color = BROKEN_NESTED_CLIP_COLOR
                        angle_index = 0
                    else:
                        angle_index = source.index_of(angle.id)
                        angle_name = angle.name
                        angle_color = angle.color_index
            views.append(
                TimelineClipView(
                    id=clip.id,
                    track_id=track.id,
                    track_index=track_index,
                    start=clip.timeline_start,
                    end=clip.timeline_start + clip.duration,
                    label=label,
                    text=clip.text,
                    source_path=asset_paths.get(clip.asset_id, ""),
                    color_key=color,
                    track_type=track.type,
                    enabled=clip.enabled,
                    keyframes=tuple(clip.transform_keyframes),
                    transform=clip.transform,
                    source_in=clip.source_in,
                    source_out=clip.source_out,
                    source_duration=clip.source_duration,
                    time_remapping=clip.time_remapping,
                    effects=tuple(clip.effects),
                    color_grade=getattr(clip, "color_grade", None),
                    locked=bool(track.locked) or bool(getattr(getattr(clip, "graphic", None), "locked", False)),
                    text_style=(
                        getattr(clip, "text_style", None)
                        if track.type in {"subtitle", "graphics"}
                        else None
                    ),
                    graphic=getattr(clip, "graphic", None),
                    compositing=getattr(clip, "compositing", None),
                    sequence_id=clip.sequence_id,
                    nested_status=status,
                    nested_overflow_start=overflow_start,
                    angle_index=angle_index,
                    angle_count=angle_count,
                    angle_name=angle_name,
                    angle_color_index=angle_color,
                    speed_points=speed_keyframes(clip),
                )
            )
    return views


def _overflow_start(clip: Clip, source_duration: float) -> float | None:
    """Instant timeline où ``clip`` cesse d'avoir une source (vitesse incluse)."""
    remapping = clip.time_remapping
    time_map = clip.time_map
    # En arrêt sur image ou en reverse, la zone sans source n'est pas une
    # fin de clip : seul le badge d'état la signale.
    if remapping.reverse or time_map.is_hold:
        return None
    if time_map.is_constant:
        local = (source_duration - clip.source_in) / time_map.speed_at(0.0)
        return clip.timeline_start + max(0.0, local)
    # Courbe de vitesse : premier instant où le mapping atteint la fin de la source.
    reached = time_map.times_at_source(source_duration)
    return clip.timeline_start + reached[0] if reached else None


