"""Règles communes des verbes de suivi et de stabilisation (lot 6 de l'extension 0.8.0) : bornes, verrous des suiveurs.

Aucun verbe ici (comme ``layer_rules``) : des fonctions sur le projet et des identifiants, sans importer la table des verbes.

- **Bornes** : un script ne reçoit que ce qu'il a écrit, et ne peut pas allouer sans limite. Un tracker range ses échantillons sur une
  grille **dense** (de sa première à sa dernière image, 10 octets par image) : un seul point posé très loin d'un autre allouerait donc
  des millions d'images. La plage d'un tracker, le nombre de trackers d'un clip, du projet (TOUTES les séquences : ``Project.tracks``
  n'est que la séquence active) et le total d'images suivies du projet sont plafonnés AVANT d'écrire ; les images clés dérivées que
  produisent ``bake_link`` et ``follow_reframe`` (une par image du clip, :func:`core.tracking_bindings.frame_times`) le sont aussi, par
  la durée du clip et la cadence de la séquence, avant tout calcul.
- **Verrous des suiveurs** : un clip qui suit un tracker (liaison active) change d'apparence quand les données de ce tracker, ou la
  stabilisation de sa source, changent ; un verbe qui les modifie refuse si l'un de ces clips est sur une piste verrouillée ou est un
  calque verrouillé (:func:`require_followers_editable`). ``remove_tracker`` réécrit en plus leurs liaisons
  (:func:`require_link_owners_editable`).

Pas de cycle à craindre entre liaisons : :meth:`core.tracking_bindings.TrackingContext.layer_matrix` ne lit, pour le clip source, que son
transform saisi, ses images clés saisies et sa stabilisation, jamais ses propres liaisons ; la source d'une liaison peut donc elle-même
suivre un autre clip (ou la cible), sans boucle d'évaluation.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..compositing_guard import ClipEditRefused, clip_edit_refusal
from ..project_model import Clip, Project
from ..tracking_ops import _still_tracked_elsewhere, tracking_of

MAX_TRACKERS_PER_CLIP = 32
"""Trackers d'un clip (l'interface en pose quatre pour une stabilisation automatique)."""
MAX_TRACKERS_IN_PROJECT = 1_000
"""Trackers du projet, toutes séquences confondues."""
MAX_TRACK_SPAN = 36_000
"""Images d'un tracker, de sa première à sa dernière (vingt minutes à 30 i/s)."""
MAX_PROJECT_TRACK_FRAMES = 5_000_000
"""Images suivies de tout le projet (≈ 50 Mo) au-delà desquelles un script n'en ajoute plus."""
MAX_DERIVED_FRAMES = 36_000
"""Images d'un clip au-delà desquelles ``bake_link`` et ``follow_reframe`` refusent (une image clé par image de la séquence)."""
_TOLERANCE = 1e-9


def project_clips(project: Project) -> Iterator[Clip]:
    """Les clips de TOUTES les séquences du projet."""
    for track in project.all_tracks():
        yield from track.clips


def known_tracker_ids(project: Project) -> set[str]:
    return {tracker.id for clip in project_clips(project) for tracker in tracking_of(clip).trackers}


def known_link_ids(project: Project) -> set[str]:
    return {link.id for clip in project_clips(project) for link in tracking_of(clip).links}


def tracked_frames(project: Project) -> int:
    return sum(tracker.data.count for clip in project_clips(project) for tracker in tracking_of(clip).trackers)


def require_tracker_room(project: Project, clip: Clip) -> None:
    """Refuse, AVANT toute création, un tracker de plus si le clip ou le projet (toutes séquences) dépasserait sa limite."""
    if len(tracking_of(clip).trackers) >= MAX_TRACKERS_PER_CLIP:
        raise ValueError(f"Le clip '{clip.id}' porte déjà {MAX_TRACKERS_PER_CLIP} trackers : c'est le maximum.")
    count = sum(len(tracking_of(other).trackers) for other in project_clips(project))
    if count >= MAX_TRACKERS_IN_PROJECT:
        raise ValueError(f"Le projet compte déjà {count} trackers : la limite est de {MAX_TRACKERS_IN_PROJECT}.")


def require_span(project: Project, data, index: int) -> None:
    """Refuse de poser une image suivie à ``index`` si la plage du tracker, ou le total du projet, dépasserait ses limites."""
    first, last = (index, index) if data.count == 0 else (min(data.first, index), max(data.last, index))
    count = last - first + 1
    if count > MAX_TRACK_SPAN:
        raise ValueError(
            f"Cette image porterait la plage du tracker à {count} images : le maximum est de {MAX_TRACK_SPAN} "
            f"(de la première à la dernière image suivie)."
        )
    growth = count - data.count
    if growth > 0 and tracked_frames(project) + growth > MAX_PROJECT_TRACK_FRAMES:
        raise ValueError(f"Le projet compte déjà trop d'images suivies (limite : {MAX_PROJECT_TRACK_FRAMES}).")


def require_within(clip: Clip, at: float, name: str = "at") -> None:
    """``at`` (secondes de la timeline) doit tomber dans le clip : le modèle le ramènerait sinon à son bord, en silence."""
    start = float(clip.timeline_start)
    end = start + float(clip.duration)
    if not start - _TOLERANCE <= at <= end + _TOLERANCE:
        raise ValueError(f"{name} = {at:g} s est hors du clip '{clip.id}' (de {start:g} à {end:g} s).")


def require_derived_size(project: Project, clip: Clip, what: str) -> None:
    """Une image clé dérivée par image du clip : refuse avant tout calcul un clip trop long pour la cadence de la séquence."""
    frames = float(clip.duration) * float(project.active_sequence.fps)
    if frames > MAX_DERIVED_FRAMES:
        raise ValueError(f"{what} : le clip '{clip.id}' compte environ {int(frames)} images, le maximum est de {MAX_DERIVED_FRAMES}.")


def _followers(project: Project, source_id: str, tracker_id: str | None) -> list[Clip]:
    result = []
    for track in project.active_sequence.tracks:
        for other in track.clips:
            if other.id == source_id or other.tracking is None:
                continue
            if any(
                link.enabled and link.follows(source_id) and (tracker_id is None or tracker_id in link.tracker_ids)
                for link in other.tracking.links
            ):
                result.append(other)
    return result


def _refuse_locked(project: Project, clips: list[Clip]) -> None:
    for other in clips:
        code = clip_edit_refusal(project, other.id, visual_only=False)
        if code is not None:
            raise ClipEditRefused(code, other.id)


def require_followers_editable(project: Project, source_id: str, tracker_id: str | None = None) -> None:
    """Aucun clip qui suit ``source_id`` (liaison active, ce tracker si donné) ne doit être verrouillé : son mouvement en dépend."""
    _refuse_locked(project, _followers(project, source_id, tracker_id))


def require_link_owners_editable(project: Project, source_id: str, tracker_id: str) -> None:
    """``remove_tracker`` réécrit les liaisons des clips qui suivent le tracker (sauf s'il reste porté par une autre moitié coupée)."""
    owners = []
    for track in project.active_sequence.tracks:
        for other in track.clips:
            if other.id == source_id or other.tracking is None:
                continue
            if any(
                link.follows(source_id) and tracker_id in link.tracker_ids and not _still_tracked_elsewhere(project, link, source_id, tracker_id)
                for link in other.tracking.links
            ):
                owners.append(other)
    _refuse_locked(project, owners)


__all__ = [
    "MAX_DERIVED_FRAMES", "MAX_PROJECT_TRACK_FRAMES", "MAX_TRACKERS_IN_PROJECT", "MAX_TRACKERS_PER_CLIP", "MAX_TRACK_SPAN",
    "known_link_ids", "known_tracker_ids", "project_clips", "require_derived_size", "require_followers_editable",
    "require_link_owners_editable", "require_span", "require_tracker_room", "require_within", "tracked_frames",
]
