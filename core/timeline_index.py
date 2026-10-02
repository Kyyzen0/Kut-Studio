"""Index de lecture pour évaluer une timeline sans la rescanner entièrement.

``evaluate_timeline`` parcourt toutes les pistes, tous les clips et
tous les médias à chaque appel. C'est exact, mais trop cher pour une
horloge à 25 Hz dès que le projet grossit.

:class:`TimelineIndex` prépare, une fois par modification structurelle,
la liste des clips triée par début. Une requête ne regarde ensuite que
les clips susceptibles de couvrir l'instant demandé. Les champs lus au
moment de la requête (chemin, texte, activation, visibilité) restent
ceux des objets du ``Project`` : un changement de chemin ou de texte
est donc visible sans reconstruire l'index.

L'index doit être reconstruit après un déplacement, un trim, un ajout
ou une suppression. Tant que l'ordre des débuts mis en cache ne
correspond plus au projet, une requête peut manquer un clip. Ce
compromis est volontaire : le tenir à jour à chaque image annulerait
le gain. On peut le retirer le jour où la timeline tiendra elle-même
un arbre d'intervalles incrémental.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .project_model import Clip, Project, Sequence, Track
from .timeline_evaluator import (
    ActiveClip,
    _build_active_clip,
    _is_active,
    expand_nested_clip,
)


@dataclass
class _TrackEntry:
    """Piste indexée : références vivantes + ordre figé au dernier build."""

    track: Track
    track_index: int
    clips: list[Clip]
    order: list[int]
    starts: list[float]
    max_duration: float


class TimelineIndex:
    """Accélérateur de ``evaluate_timeline`` pour un instant donné.

    Le résultat d' :meth:`active_at` suit le même contrat que
    :func:`core.timeline_evaluator.evaluate_timeline` : ordre des
    pistes, puis ordre d'origine des clips, intervalle demi-ouvert,
    pistes invisibles ignorées, erreur si un média actif est absent.
    """

    def __init__(self, project: Project, sequence: Sequence | None = None) -> None:
        self.clip_count = 0
        self.duration = 0.0
        self._entries: list[_TrackEntry] = []
        self._clips_by_id: dict[str, Clip] = {}
        self.sequence = sequence if sequence is not None else project.active_sequence
        self.sequence_id = self.sequence.id
        # Index des séquences imbriquées, construits à la première lecture.
        self._sub_indexes: dict[str, TimelineIndex] = {}
        self._project = project
        end = 0.0
        for track_index, track in enumerate(self.sequence.tracks):
            decorated = list(enumerate(track.clips))
            decorated.sort(key=lambda item: item[1].timeline_start)
            starts = [clip.timeline_start for _, clip in decorated]
            durations = [clip.duration for _, clip in decorated]
            self._entries.append(
                _TrackEntry(
                    track=track,
                    track_index=track_index,
                    clips=[clip for _, clip in decorated],
                    order=[index for index, _ in decorated],
                    starts=starts,
                    max_duration=max(durations) if durations else 0.0,
                )
            )
            for clip in track.clips:
                self._clips_by_id[clip.id] = clip
                self.clip_count += 1
                if clip.enabled:
                    end = max(end, clip.timeline_start + clip.duration)
        self.duration = end

    def clip(self, clip_id: str) -> Clip | None:
        """Retourne le clip indexé, ou ``None`` s'il est inconnu."""
        return self._clips_by_id.get(clip_id)

    def clips_overlapping(
        self, track_index: int, t0: float, t1: float, track: Track | None = None
    ) -> list[Clip] | None:
        """Clips d'une piste qui recouvrent ``]t0, t1[``, **dans l'ordre d'origine**.

        Retourne ``None`` si l'index ne correspond plus à cette piste (piste
        ajoutée, retirée ou remplacée depuis sa construction) : l'appelant
        doit alors parcourir la piste. Même règle de chevauchement que
        :func:`core.render_plan.build_render_plan` (bornes exclues).
        """
        if not 0 <= track_index < len(self._entries):
            return None
        entry = self._entries[track_index]
        if track is not None and entry.track is not track:
            return None
        hi = bisect.bisect_left(entry.starts, t1)
        lo = bisect.bisect_left(entry.starts, t0 - entry.max_duration - 1e-9)
        matched: list[tuple[int, Clip]] = []
        for position in range(lo, hi):
            clip = entry.clips[position]
            if clip.timeline_start + clip.duration > t0:
                matched.append((entry.order[position], clip))
        matched.sort(key=lambda item: item[0])
        return [clip for _, clip in matched]

    def sub_index(self, sequence: Sequence) -> "TimelineIndex":
        """Index d'une séquence imbriquée (construit une fois, puis réutilisé)."""
        return self._sub_index(sequence)

    def _sub_index(self, sequence: Sequence) -> "TimelineIndex":
        index = self._sub_indexes.get(sequence.id)
        if index is None or index.sequence is not sequence:
            index = TimelineIndex(self._project, sequence)
            self._sub_indexes[sequence.id] = index
        return index

    def active_at(
        self, project: Project, time_seconds: float, _stack: tuple[str, ...] | None = None
    ) -> list[ActiveClip]:
        """Clips actifs à ``time_seconds``.

        ``project`` sert à résoudre les chemins au moment de la requête.
        Il doit être le projet qui a servi à construire l'index, ou le
        même objet après une mutation qui ne change pas l'ordre des
        débuts de clips.
        """
        if time_seconds < 0.0:
            raise ValueError(
                "Le temps de timeline doit être positif ou nul "
                f"(reçu : {time_seconds})."
            )
        assets = {asset.id: asset for asset in project.media_assets}
        stack = _stack if _stack is not None else (self.sequence_id,)
        active: list[ActiveClip] = []
        for entry in self._entries:
            track = entry.track
            if not track.visible:
                continue
            if not entry.clips:
                continue
            # Un clip qui couvre ``t`` a forcément commencé après
            # ``t - durée max`` de la piste. On ne remonte pas plus loin.
            lower_bound = time_seconds - entry.max_duration - 1e-9
            hi = bisect.bisect_right(entry.starts, time_seconds)
            matched: list[tuple[int, Clip]] = []
            for position in range(hi - 1, -1, -1):
                if entry.starts[position] < lower_bound:
                    break
                clip = entry.clips[position]
                if not clip.enabled or not _is_active(clip, time_seconds):
                    continue
                matched.append((entry.order[position], clip))
            matched.sort(key=lambda item: item[0])
            for _, clip in matched:
                if clip.sequence_id:
                    active.extend(
                        expand_nested_clip(
                            project, clip, track, entry.track_index, time_seconds, stack,
                            lambda child, inner_time, inner_stack: self._sub_index(
                                child
                            ).active_at(project, inner_time, inner_stack),
                            lambda child: self._sub_index(child).duration,
                        )
                    )
                    continue
                try:
                    asset = assets[clip.asset_id]
                except KeyError as error:
                    raise KeyError(
                        f"Média '{clip.asset_id}' introuvable dans le projet "
                        f"'{project.name}'."
                    ) from error
                active.append(
                    _build_active_clip(
                        clip=clip,
                        track_index=entry.track_index,
                        track_type=track.type,
                        source_path=asset.path,
                        time_seconds=time_seconds,
                    )
                )
        return active


def build_timeline_index(project: Project, sequence: Sequence | None = None) -> TimelineIndex:
    """Construit un index de lecture pour ``project`` (séquence active par défaut)."""
    return TimelineIndex(project, sequence)
