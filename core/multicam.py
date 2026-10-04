"""Résolution Multicam : quel angle est actif pour un segment, et quelles pistes de la source il faut rendre.

Ce module est **pur** (aucune E/S, aucun Qt) et ne contient que des lectures. Il répond à deux questions :

* ``track_filter_for(source, clip)`` : pour un clip imbriqué qui désigne une source Multicam, quelles pistes de la
  source sont *masquées* (image et son séparément) ? Le :class:`~core.render_plan.RenderPlan` et l'évaluateur de la
  timeline l'appliquent en construisant le sous-plan : seul l'angle actif est rendu, il n'y a pas de second moteur.
* ``multicam_issues(project)`` : ce qui est cassé dans les sources Multicam (piste d'angle disparue, segment qui
  désigne un angle inconnu…), pour le dire à l'écran au lieu de rendre un segment vide en silence.

Les pistes de la source qui n'appartiennent à aucun angle (graphiques, sous-titres, calque d'effets ajoutés dans la
source) ne sont jamais masquées.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .multicam_model import AudioMode, MulticamAngle, MulticamSource
from .project_model import Clip, Project, Sequence, Track


@dataclass(frozen=True)
class TrackFilter:
    """Pistes d'une source Multicam à omettre pour un segment donné.

    ``hide_video`` : pistes dont l'image n'est pas rendue ; ``hide_audio`` : pistes dont le son n'est pas mixé. Une
    piste vidéo peut cacher son image et garder son son (politique audio fixe ou mixée). Filtre vide : tout est rendu.
    """

    hide_video: frozenset[str] = frozenset()
    hide_audio: frozenset[str] = frozenset()

    @property
    def is_identity(self) -> bool:
        return not self.hide_video and not self.hide_audio

    @property
    def key(self) -> str:
        """Suffixe de clé de sous-plan : vide pour l'identité, déterministe sinon.

        Deux segments de la même source qui montrent le **même** angle (et la même politique audio) ont la même clé
        et partagent donc un seul sous-plan ; deux angles différents ont deux sous-plans.
        """
        if self.is_identity:
            return ""
        return "#v-" + ",".join(sorted(self.hide_video)) + "#a-" + ",".join(sorted(self.hide_audio))

    def hides_track(self, track_id: str) -> bool:
        """La piste n'apporte ni image ni son."""
        return track_id in self.hide_video and track_id in self.hide_audio


NO_FILTER = TrackFilter()


def resolve_angle(source: MulticamSource, angle_id: str) -> MulticamAngle | None:
    """Angle désigné par un segment : le premier si ``angle_id`` est vide, ``None`` s'il est inconnu."""
    if not source.angles:
        return None
    if not angle_id:
        return source.angles[0]
    return source.angle(angle_id)


def audio_angle_ids(source: MulticamSource, active: MulticamAngle | None) -> tuple[str, ...]:
    """Angles dont le son est mixé pour un segment dont l'angle actif est ``active``.

    Politique « le son suit l'image » : l'angle actif. Politique « fixe » : le premier angle de la liste, sinon l'angle
    actif. Politique « mixte » : toute la liste, sinon l'angle actif. Les identifiants inconnus sont ignorés.
    """
    audio = source.audio
    chosen: tuple[str, ...] = ()
    if audio.mode is AudioMode.FIXED:
        chosen = audio.angle_ids[:1]
    elif audio.mode is AudioMode.MIX:
        chosen = audio.angle_ids
    known = tuple(angle_id for angle_id in chosen if source.angle(angle_id) is not None)
    if known:
        return known
    # Politique « suit l'image », ou sources désignées toutes disparues : le son est celui de l'angle actif.
    return (active.id,) if active is not None else ()


def track_filter_for(child: Sequence, clip: Clip) -> TrackFilter:
    """Filtre de pistes que ``clip`` (clip imbriqué de ``child``) impose au sous-plan de la source.

    Séquence ordinaire (pas de ``multicam``) : aucun filtre. Angle inconnu : toutes les pistes d'angle sont masquées
    (le segment est rendu vide, ``multicam_issues`` le signale).
    """
    source = child.multicam
    if source is None or not source.angles:
        return NO_FILTER
    active = resolve_angle(source, clip.angle_id)
    audible = set(audio_angle_ids(source, active)) if active is not None else set()
    hide_video: set[str] = set()
    hide_audio: set[str] = set()
    for angle in source.angles:
        if active is None or angle.id != active.id:
            hide_video.add(angle.track_id)
        if angle.id not in audible:
            hide_audio.add(angle.track_id)
    return TrackFilter(frozenset(hide_video), frozenset(hide_audio))


def is_multicam_clip(project: Project, clip: Clip) -> bool:
    """Le clip est-il un segment d'une source Multicam ?"""
    if not clip.sequence_id:
        return False
    child = project.get_sequence(clip.sequence_id)
    return child is not None and child.multicam is not None


def angle_track(sequence: Sequence, angle: MulticamAngle) -> Track | None:
    return next((track for track in sequence.tracks if track.id == angle.track_id), None)


def angle_extent(sequence: Sequence, angle: MulticamAngle) -> tuple[float, float] | None:
    """Plage ``(début, fin)`` couverte par les clips de l'angle dans la source (``None`` : aucun clip actif).

    Le début **est** le décalage de synchronisation de l'angle.
    """
    track = angle_track(sequence, angle)
    if track is None:
        return None
    spans = [(clip.timeline_start, clip.timeline_start + clip.duration) for clip in track.clips if clip.enabled]
    if not spans:
        return None
    return min(start for start, _end in spans), max(end for _start, end in spans)


def angle_offset(sequence: Sequence, angle: MulticamAngle) -> float | None:
    """Décalage de l'angle : position de son premier clip dans la source (``None`` : angle sans clip)."""
    extent = angle_extent(sequence, angle)
    return None if extent is None else extent[0]


class AngleState(str, Enum):
    """Ce que montre un angle à un instant donné."""

    LIVE = "live"
    """Un média est lu."""
    NO_SIGNAL = "no_signal"
    """L'angle n'a pas encore commencé ou est déjà fini (synchronisation partielle) : rien à montrer."""
    OFFLINE = "offline"
    """Le média de l'angle est introuvable (déplacé, supprimé) : « MEDIA OFFLINE », les autres angles continuent."""


@dataclass(frozen=True)
class AngleSample:
    """État d'un angle sous la tête de lecture : média à lire, instant dans ce média, rôle dans le segment."""

    angle: MulticamAngle
    index: int
    state: AngleState
    path: str = ""
    media_time: float = 0.0
    active: bool = False
    """C'est l'angle que le segment montre (image du programme)."""
    audible: bool = False
    """Son mixé pour ce segment selon la politique audio."""
    audio_only: bool = False


def angle_samples_at(
    project: Project, segment: Clip, time_seconds: float, *, exists: Callable[[str], bool] | None = None,
) -> list[AngleSample]:
    """Pour chaque angle de la source du segment, ce qu'il montre à ``time_seconds`` (temps de la séquence du segment).

    Lecture seule, sans E/S sauf ``exists`` (existence du fichier : mémorisée par défaut). Un angle sans clip à cet
    instant est ``NO_SIGNAL`` ; un média absent du projet ou du disque est ``OFFLINE`` : le moniteur Multicam l'affiche
    sans rien casser. Retourne ``[]`` si ``segment`` n'est pas un segment Multicam ou si l'instant est hors du segment.
    """
    from .cache_keys import file_exists
    from .sequences import nested_source_time

    child = project.get_sequence(segment.sequence_id) if segment.sequence_id else None
    source = child.multicam if child is not None else None
    if child is None or source is None:
        return []
    inner = nested_source_time(segment, time_seconds)
    if inner is None:
        return []
    check = exists or file_exists
    active = resolve_angle(source, segment.angle_id)
    audible = set(audio_angle_ids(source, active)) if active is not None else set()
    assets = {asset.id: asset for asset in project.media_assets}
    samples: list[AngleSample] = []
    for index, angle in enumerate(source.angles):
        track = angle_track(child, angle)
        base = dict(angle=angle, index=index, active=active is not None and angle.id == active.id,
                    audible=angle.id in audible, audio_only=track is not None and track.type == "audio")
        clip = None
        if track is not None:
            clip = next((item for item in track.clips if item.enabled and not item.is_nested
                         and item.timeline_start <= inner < item.timeline_start + item.duration), None)
        if clip is None:
            samples.append(AngleSample(state=AngleState.NO_SIGNAL, **base))  # type: ignore[arg-type]
            continue
        asset = assets.get(clip.asset_id)
        if asset is None or not asset.path or not check(asset.path):
            samples.append(AngleSample(state=AngleState.OFFLINE, path=asset.path if asset else "", **base))  # type: ignore[arg-type]
            continue
        media_time = clip.time_map.source_time(inner - clip.timeline_start)
        samples.append(AngleSample(state=AngleState.LIVE, path=asset.path, media_time=media_time, **base))  # type: ignore[arg-type]
    return samples


PAGE_SIZE = 16
"""Nombre maximal de tuiles affichées en même temps ; au-delà, le moniteur Multicam pagine."""


def grid_shape(count: int) -> tuple[int, int]:
    """``(lignes, colonnes)`` de la grille d'angles : 2 → 1×2, 3-4 → 2×2, 5-9 → 3×3, 10-16 → 4×4 (pages au-delà)."""
    if count <= 1:
        return 1, 1
    if count == 2:
        return 1, 2
    for side in (2, 3, 4):
        if count <= side * side:
            return side, side
    return 4, 4


def page_count(count: int) -> int:
    """Nombre de pages de tuiles (16 par page)."""
    return max(1, -(-count // PAGE_SIZE))


@dataclass(frozen=True)
class MulticamIssue:
    """Un défaut d'une source Multicam ou d'un de ses segments.

    ``code`` : ``missing_track`` (la piste d'un angle n'existe plus), ``empty_angle`` (aucun clip),
    ``unknown_angle`` (un segment désigne un angle qui n'existe plus), ``nested_multicam`` (un angle est lui-même une
    source Multicam), ``audio_source_missing`` (la politique audio désigne un angle disparu).
    """

    code: str
    sequence_id: str
    angle_id: str = ""
    clip_id: str = ""


def multicam_issues(project: Project) -> list[MulticamIssue]:
    """Défauts de toutes les sources Multicam du projet et de leurs segments (lecture seule, sans lever)."""
    issues: list[MulticamIssue] = []
    for sequence in project.sequences:
        source = sequence.multicam
        if source is None:
            continue
        tracks = {track.id: track for track in sequence.tracks}
        for angle in source.angles:
            track = tracks.get(angle.track_id)
            if track is None:
                issues.append(MulticamIssue("missing_track", sequence.id, angle.id))
                continue
            if not track.clips:
                issues.append(MulticamIssue("empty_angle", sequence.id, angle.id))
            for clip in track.clips:
                if is_multicam_clip(project, clip):
                    issues.append(MulticamIssue("nested_multicam", sequence.id, angle.id, clip.id))
        for angle_id in source.audio.angle_ids:
            if source.angle(angle_id) is None:
                issues.append(MulticamIssue("audio_source_missing", sequence.id, angle_id))
    for sequence in project.sequences:
        for track in sequence.tracks:
            for clip in track.clips:
                if not clip.sequence_id or not clip.angle_id:
                    continue
                child = project.get_sequence(clip.sequence_id)
                if child is not None and child.multicam is not None and child.multicam.angle(clip.angle_id) is None:
                    issues.append(MulticamIssue("unknown_angle", sequence.id, clip.angle_id, clip.id))
    return issues


__all__ = [
    "NO_FILTER",
    "PAGE_SIZE",
    "AngleSample",
    "AngleState",
    "MulticamIssue",
    "TrackFilter",
    "angle_extent",
    "angle_offset",
    "angle_samples_at",
    "angle_track",
    "audio_angle_ids",
    "grid_shape",
    "is_multicam_clip",
    "multicam_issues",
    "page_count",
    "resolve_angle",
    "track_filter_for",
]
