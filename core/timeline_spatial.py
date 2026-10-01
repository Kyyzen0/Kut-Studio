"""Index spatiaux de la timeline : requêtes en O(log n + k) au lieu de O(n).

Deux structures, toutes deux **pures** (aucun Qt) et reconstruites par
l'appelant à chaque modification structurelle de la timeline :

- :class:`SpanIndex` : « quels clips recouvrent l'intervalle de temps
  ``[t0, t1]`` (et ces pistes) ? ». Sert au *culling* : monter seulement
  les widgets visibles, sélectionner au lasso, sans balayer les milliers
  de clips d'un gros montage à chaque défilement ou zoom.
- :class:`SnapIndex` : « quel bord de clip est le plus proche de ``t`` ? ».
  Sert à l'aimantation pendant un glisser : le coût dépend du nombre de
  bords **dans le seuil d'aimantation**, pas du nombre de clips (avant :
  tous les clips parcourus à chaque mouvement de souris).

Les deux conservent exactement la sémantique des parcours complets
qu'ils remplacent ; des tests comparent les résultats à ces parcours sur
des projets aléatoires.
"""

from __future__ import annotations

import bisect
from collections.abc import Hashable, Iterable, Sequence

# ---------------------------------------------------------------------------
# Intervalles par piste
# ---------------------------------------------------------------------------


class _Lane:
    """Clips d'une piste, triés par début, avec le maximum cumulé des fins."""

    __slots__ = ("starts", "ends", "keys", "prefix_max_end")

    def __init__(self, items: list[tuple[float, float, Hashable]]) -> None:
        items.sort(key=lambda item: item[0])
        self.starts = [item[0] for item in items]
        self.ends = [item[1] for item in items]
        self.keys = [item[2] for item in items]
        running = float("-inf")
        prefix: list[float] = []
        for end in self.ends:
            running = max(running, end)
            prefix.append(running)
        self.prefix_max_end = prefix

    def overlapping(self, t0: float, t1: float) -> list[Hashable]:
        """Clés des clips dont ``end >= t0`` et ``start <= t1`` (bornes incluses).

        ``prefix_max_end`` est croissant : le premier indice dont le
        maximum cumulé atteint ``t0`` borne à gauche ; aucun clip plus
        ancien ne peut finir après ``t0``. Pour des clips qui ne se
        chevauchent pas (le cas courant) l'intervalle est exact ; un clip
        très long placé tôt élargit le balayage, sans jamais le fausser.
        """
        hi = bisect.bisect_right(self.starts, t1)
        lo = bisect.bisect_left(self.prefix_max_end, t0)
        ends, keys = self.ends, self.keys
        return [keys[i] for i in range(lo, hi) if ends[i] >= t0]


class SpanIndex:
    """Index d'intervalles ``[start, end]`` répartis sur des pistes (``lane``)."""

    def __init__(self, spans: Iterable[tuple[Hashable, int, float, float]]) -> None:
        grouped: dict[int, list[tuple[float, float, Hashable]]] = {}
        count = 0
        for key, lane, start, end in spans:
            grouped.setdefault(int(lane), []).append((float(start), float(end), key))
            count += 1
        self._lanes = {lane: _Lane(items) for lane, items in grouped.items()}
        self.count = count

    def query(
        self,
        t0: float,
        t1: float,
        lanes: tuple[int, int] | None = None,
    ) -> list[Hashable]:
        """Clés des intervalles qui recouvrent ``[t0, t1]`` sur les pistes ``lanes``.

        ``lanes`` = ``(première, dernière)`` incluses, ``None`` = toutes.
        L'ordre du résultat n'est pas significatif : l'appelant trie par
        l'ordre d'origine si besoin.
        """
        found: list[Hashable] = []
        for lane, data in self._lanes.items():
            if lanes is not None and not (lanes[0] <= lane <= lanes[1]):
                continue
            found.extend(data.overlapping(t0, t1))
        return found


# ---------------------------------------------------------------------------
# Aimantation
# ---------------------------------------------------------------------------

_SPECIAL_BASE = 0
_CLIP_BASE = 10**9  # les bords de clips passent toujours après les points spéciaux


class SnapIndex:
    """Bords de clips triés, pour trouver le plus proche en O(log n + k).

    Reproduit l'ordre de préférence des fonctions d'origine
    (:func:`core.timeline_operations.snap_timeline_position` et
    :func:`core.timeline_editing.snap_edit_position`) : parmi des
    candidats à égale distance, **le dernier de la liste d'origine**
    l'emporte (``0``, points supplémentaires, tête de lecture, puis les
    pistes et clips dans l'ordre du projet).
    """

    def __init__(self, project, *, with_keyframes: bool) -> None:
        self.with_keyframes = with_keyframes
        times: list[float] = []
        orders: list[int] = []
        owners: list[str] = []
        entries: list[tuple[float, int, str]] = []
        order = _CLIP_BASE
        for track in project.tracks:
            for clip in track.clips:
                start = float(clip.timeline_start)
                end = float(clip.timeline_start + clip.duration)
                entries.append((start, order, clip.id))
                entries.append((end, order + 1, clip.id))
                order += 2
                if with_keyframes:
                    for keyframe in clip.transform_keyframes:
                        entries.append(
                            (float(clip.timeline_start + keyframe.time_seconds), order, clip.id)
                        )
                        order += 1
        entries.sort(key=lambda item: (item[0], item[1]))
        for time_value, entry_order, owner in entries:
            times.append(time_value)
            orders.append(entry_order)
            owners.append(owner)
        self._times = times
        self._orders = orders
        self._owners = owners

    def nearest(
        self,
        proposed: float,
        threshold: float,
        *,
        excluded_ids: Iterable[str] | None = None,
        extra_points: Sequence[float] = (),
        playhead: float | None = None,
    ) -> float:
        """Candidat le plus proche de ``proposed`` dans le seuil, sinon ``proposed``."""
        if proposed < 0.0:
            proposed = 0.0
        if threshold <= 0.0:
            return proposed
        excluded = set(excluded_ids or ())
        best_value = proposed
        best_distance = threshold
        best_order = -1

        def consider(value: float, order: int) -> None:
            nonlocal best_value, best_distance, best_order
            distance = abs(value - proposed)
            if distance < best_distance or (
                distance == best_distance and order > best_order
            ):
                best_value, best_distance, best_order = value, distance, order

        rank = _SPECIAL_BASE
        consider(0.0, rank)
        for point in extra_points:
            rank += 1
            consider(float(point), rank)
        if playhead is not None and playhead >= 0.0:
            rank += 1
            consider(float(playhead), rank)
        times, orders, owners = self._times, self._orders, self._owners
        low = bisect.bisect_left(times, proposed - threshold)
        high = bisect.bisect_right(times, proposed + threshold)
        for index in range(low, high):
            if owners[index] in excluded:
                continue
            consider(times[index], orders[index])
        return best_value


__all__ = ["SnapIndex", "SpanIndex"]
