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

from dataclasses import dataclass

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
    "MulticamIssue",
    "TrackFilter",
    "angle_extent",
    "angle_offset",
    "angle_track",
    "audio_angle_ids",
    "is_multicam_clip",
    "multicam_issues",
    "resolve_angle",
    "track_filter_for",
]
