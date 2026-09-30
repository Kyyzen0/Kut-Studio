"""Plan de rendu vidéo pur pour Kut-Studio.

Ce module construit un :class:`RenderPlan` immuable à partir d'un
:class:`~core.project_model.Project`. Le plan décrit fidèlement la
timeline :

- position et durée des clips ;
- trims ``source_in`` / ``source_out`` ;
- pistes vidéo, audio, sous-titres et graphiques ;
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

from .effects_model import ClipEffect
from .project_model import Clip, MediaAsset, Project
from .subtitle_io import SubtitleCue
from .time_remapping import TimeRemapping
from .transitions import TransitionType
from .timeline_evaluator import timeline_duration
from .visual_effects import ClipTransform, TransformKeyframe


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
    source_fps: float = 0.0
    transform: ClipTransform = field(default_factory=ClipTransform)
    transform_keyframes: tuple[TransformKeyframe, ...] = field(default_factory=tuple)
    time_remapping: TimeRemapping = field(default_factory=TimeRemapping)
    effects: tuple[ClipEffect, ...] = field(default_factory=tuple)
    # Étalonnage couleur (tâche 29) : un objet ``ColorGrade`` ou
    # ``None`` si l'identité. On garde un type ``object`` pour ne
    # pas coupler le plan de rendu au module ``color_grading``.
    color_grade: object = None


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
        gain_db: Gain du clip seul (dB), hors volume de piste.
        pan: Panoramique du clip, borné à ``[-1, 1]``.
        fade_in: Durée du fondu d'entrée (secondes, ``>= 0``).
        fade_out: Durée du fondu de sortie (secondes, ``>= 0``).
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
    source_fps: float = 0.0
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    track_volume_db: float = 0.0
    track_pan: float = 0.0
    time_remapping: TimeRemapping = field(default_factory=TimeRemapping)
    # Effets audio non destructifs (tâche 27). Tuple pour respecter le
    # caractère immuable de l'AudioLayer. Les effets sont appliqués
    # dans l'ordre de la séquence au moment du rendu.
    audio_effects: tuple = field(default_factory=tuple)
    # Automation de volume par piste (tâche 28). Liste ordonnée de
    # :class:`AutomationPoint` ; ``gain_at`` est appelé pour appliquer
    # une enveloppe de gain à l'export. Vide par défaut.
    track_automation: tuple = field(default_factory=tuple)
    # Liste des :class:`DuckingSidechain` qui ciblent cette piste.
    # Vide par défaut : pas de ducking automatique.
    ducking_sidechains: tuple = field(default_factory=tuple)

    @property
    def duration(self) -> float:
        """Durée du clip sur la timeline."""
        return max(0.0, self.timeline_end - self.timeline_start)

    @property
    def total_gain_db(self) -> float:
        """Gain total appliqué : volume de piste + gain de clip."""
        return float(self.track_volume_db) + float(self.gain_db)

    @property
    def total_pan(self) -> float:
        """Panoramique total, borné (réglage clip + réglage piste)."""
        return max(-1.0, min(1.0, float(self.pan) + float(self.track_pan)))


@dataclass(frozen=True)
class RenderTransition:
    id: str
    from_clip_id: str
    to_clip_id: str
    type: TransitionType
    duration: float


@dataclass(frozen=True)
class GraphicLayer:
    """Calque graphique généré ou image, composé au-dessus de la vidéo."""

    clip_id: str
    track_id: str
    track_index: int
    timeline_start: float
    timeline_end: float
    graphic: object
    transform: ClipTransform = field(default_factory=ClipTransform)
    transform_keyframes: tuple[TransformKeyframe, ...] = field(default_factory=tuple)


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
        subtitle_cues: Cues de sous-titres actifs du projet, triés par
            ``(start, end)``. Vides si le projet n'a aucun sous-titre
            actif ; le moteur d'export les utilise pour générer un
            fichier SRT temporaire et appliquer le filtre ``subtitles``.
        graphics_layers: Titres, formes, aplats et images à composer après
            les pistes vidéo et avant les sous-titres.
    """

    width: int
    height: int
    fps: float
    duration: float
    video_layers: tuple[RenderLayer, ...] = field(default_factory=tuple)
    audio_layers: tuple[AudioLayer, ...] = field(default_factory=tuple)
    subtitle_cues: tuple[SubtitleCue, ...] = field(default_factory=tuple)
    subtitle_styles: tuple["TextStyle", ...] = field(default_factory=tuple)
    master_gain_db: float = 0.0
    master_muted: bool = False
    transitions: tuple[RenderTransition, ...] = field(default_factory=tuple)
    graphics_layers: tuple[GraphicLayer, ...] = field(default_factory=tuple)

    @property
    def is_audio_silent(self) -> bool:
        """Aucun son ne doit sortir : l'export vidéo reste valide."""
        if self.master_muted:
            return True
        return not self.audio_layers


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


def build_render_plan(
    project: Project,
    *,
    master_gain_db: float = 0.0,
    master_muted: bool = False,
) -> RenderPlan:
    """Construit un :class:`RenderPlan` à partir d'un :class:`Project`.

    Règles appliquées :

    - Pour la vidéo : seules les pistes de type ``"video"`` visibles
      (``track.visible is True``) sont conservées, avec leurs clips
      activés uniquement. Les couches sont émises dans l'ordre des
      pistes du projet (les pistes basses sont rendues en premier /
      au fond).
    - Pour l'audio : les pistes de type ``"audio"`` non muettes
      (``track.muted is False``) sont conservées intégralement ; les
      pistes vidéo non muettes ne contribuent une couche audio que si
      leur :class:`MediaAsset` porte ``has_audio=True``.
    - Chaque clip actif dont l'asset est introuvable lève une
      ``KeyError`` explicite.
    - La durée du plan est exactement
      :func:`core.timeline_evaluator.timeline_duration` du projet
      (les pistes invisibles sont prises en compte via ``enabled`` ;
      les pistes verrouillées ne modifient pas la durée).

    Args:
        project: projet source (jamais muté).

    Returns:
        Le :class:`RenderPlan` correspondant au projet.
    """
    video_layers: list[RenderLayer] = []
    audio_layers: list[AudioLayer] = []
    graphics_layers: list[GraphicLayer] = []
    video_solo = {track.id for track in project.tracks if track.type == "video" and track.solo}
    audio_solo = {track.id for track in project.tracks if track.type == "audio" and track.solo}
    graphics_solo = {
        track.id for track in project.tracks
        if track.type == "graphics" and track.solo
    }
    for track_index, track in enumerate(project.tracks):
        if track.type == "graphics":
            if not track.visible or (graphics_solo and track.id not in graphics_solo):
                continue
            for clip in track.clips:
                graphic = getattr(clip, "graphic", None)
                if not clip.enabled or graphic is None:
                    continue
                graphics_layers.append(
                    GraphicLayer(
                        clip_id=clip.id,
                        track_id=track.id,
                        track_index=track_index,
                        timeline_start=clip.timeline_start,
                        timeline_end=clip.timeline_start + clip.duration,
                        graphic=graphic,
                        transform=clip.transform,
                        transform_keyframes=tuple(clip.transform_keyframes),
                    )
                )
            continue
        if track.type not in {"video", "audio"}:
            continue
        if track.type == "video" and not track.visible:
            # Une piste vidéo invisible n'apparaît pas dans le rendu.
            continue
        if video_solo and track.type == "video" and track.id not in video_solo:
            continue
        if track.type == "audio" and track.muted:
            # Une piste audio muette ne participe pas au mixage.
            continue
        if audio_solo and track.type == "audio" and track.id not in audio_solo:
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
                        source_fps=float(asset.fps),
                        transform=clip.transform,
                        transform_keyframes=tuple(clip.transform_keyframes),
                        time_remapping=clip.time_remapping,
                        effects=tuple(clip.effects),
                        # Étalonnage couleur (tâche 29) : si le clip ne
                        # porte pas de ``ColorGrade``, on garde ``None``
                        # pour signaler l'identité et économiser du
                        # travail au moteur d'export.
                        color_grade=getattr(clip, "color_grade", None),
                    )
                )
                # Un solo audio ne laisse passer que les pistes audio armées
                # en solo : le son embarqué des pistes vidéo est alors exclu.
                if asset.has_audio and not track.muted and not audio_solo:
                    audio_layers.append(
                        _build_audio_layer(
                            clip, asset, track.id, track_index, track,
                            ducking_sidechains=[
                                s
                                for s in getattr(
                                    project, "ducking_sidechains", []
                                ) or []
                                if getattr(s, "music_track_id", None) == track.id
                            ],
                        )
                    )
            else:  # track.type == "audio"
                audio_layers.append(
                    _build_audio_layer(
                        clip, asset, track.id, track_index, track,
                        ducking_sidechains=[
                            s
                            for s in getattr(
                                project, "ducking_sidechains", []
                            ) or []
                            if getattr(s, "music_track_id", None) == track.id
                        ],
                    )
                )
    layer_ids = {layer.clip_id for layer in video_layers}
    transitions = tuple(
        RenderTransition(
            id=transition.id,
            from_clip_id=transition.from_clip_id,
            to_clip_id=transition.to_clip_id,
            type=transition.type,
            duration=transition.duration,
        )
        for transition in project.transitions
        if transition.from_clip_id in layer_ids and transition.to_clip_id in layer_ids
    )
    _subtitle_entries = _subtitle_cues_for_export(project)
    return RenderPlan(
        width=project.width,
        height=project.height,
        fps=float(project.fps),
        duration=timeline_duration(project),
        video_layers=tuple(video_layers),
        audio_layers=tuple(audio_layers),
        subtitle_cues=tuple(cue for cue, _style in _subtitle_entries),
        subtitle_styles=tuple(style for _cue, style in _subtitle_entries),
        master_gain_db=float(master_gain_db),
        master_muted=bool(master_muted),
        transitions=transitions,
        graphics_layers=tuple(graphics_layers),
    )


def _subtitle_cues_for_export(project: Project):
    """Sous-titres exportés, en respectant visibilité et solo.

    Renvoie une liste de tuples ``(SubtitleCue, TextStyle)`` alignés sur
    l'ordre de tri ``(start, end)``. Les clips qui n'ont pas de style
    personnalisé portent le :class:`TextStyle` standard (cf.
    :mod:`core.text_style`).
    """
    from .subtitle_io import SubtitleCue
    from .text_style import default_text_style

    solo = {track.id for track in project.tracks if track.type == "subtitle" and track.solo}
    fallback = default_text_style()
    cues: list = []
    for track in project.tracks:
        if track.type != "subtitle" or not track.visible:
            continue
        if solo and track.id not in solo:
            continue
        for clip in track.clips:
            if not clip.enabled or not (clip.text or "").strip():
                continue
            cues.append(
                (
                    SubtitleCue(
                        start=float(clip.timeline_start),
                        end=float(clip.timeline_start + clip.duration),
                        text=clip.text.strip(),
                    ),
                    getattr(clip, "text_style", fallback),
                )
            )
    cues.sort(key=lambda pair: (pair[0].start, pair[0].end))
    return cues


def _build_audio_layer(
    clip, asset: MediaAsset, track_id: str, track_index: int, track=None,
    *,
    ducking_sidechains: list | None = None,
) -> AudioLayer:
    # Automation de volume (tâche 28) : on la récupère depuis le track
    # parent. L'automation peut être soit une liste (cas historique),
    # soit une instance de :class:`TrackAutomation` (cas enrichi par
    # :class:`AudioAutomationService`). On normalise dans les deux cas
    # vers une liste ordonnée de :class:`AutomationPoint`.
    track_automation = getattr(track, "automation", None)
    if hasattr(track_automation, "points"):
        track_automation_points = list(getattr(track_automation, "points", []) or [])
    elif isinstance(track_automation, list):
        track_automation_points = list(track_automation)
    else:
        track_automation_points = []
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
        source_fps=float(asset.fps),
        gain_db=float(getattr(clip, "gain_db", 0.0)),
        pan=float(getattr(clip, "pan", 0.0)),
        fade_in=float(getattr(clip, "fade_in", 0.0)),
        fade_out=float(getattr(clip, "fade_out", 0.0)),
        track_volume_db=float(getattr(track, "volume_db", 0.0)),
        track_pan=float(getattr(track, "pan", 0.0)),
        time_remapping=getattr(clip, "time_remapping", TimeRemapping()),
        audio_effects=tuple(getattr(clip, "audio_effects", []) or []),
        track_automation=tuple(track_automation_points),
        ducking_sidechains=tuple(ducking_sidechains or []),
    )
