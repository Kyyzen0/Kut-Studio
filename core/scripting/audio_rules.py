"""Règles communes des verbes d'audio avancé (lot 7 de l'extension 0.8.0) : pistes éligibles, bornes, points d'automation, ducking.

Aucun verbe ici : ces fonctions travaillent sur le projet et des identifiants (``audio_verbs.py`` et ``clip_audio_verbs.py`` les emploient).

- **Pistes** : la courbe de volume, le rôle et le ducking sont, dans l'interface, ceux des pistes **audio** (le menu ⋯ de la piste, la bande de
  courbe, ``_find_audio_track`` des gestionnaires) ; le modèle accepterait aussi une piste vidéo. Un verbe suit l'interface. Une piste
  verrouillée est refusée par le verbe puis par le gestionnaire de l'interface ; les mêmes refus valent ici.
- **Bornes** : une courbe a au plus :data:`MAX_AUTOMATION_POINTS` points, un projet (toutes séquences) au plus :data:`MAX_SIDECHAINS`
  associations de ducking. La limite se vérifie AVANT de créer, et sur tous les chemins de création.
- **Un point se désigne par son instant** (la description n'a pas d'identifiant de point) ; l'instant décrit est arrondi à 6 décimales,
  donc la recherche tolère :data:`TIME_TOLERANCE` et refuse une ambiguïté (deux points aussi proches) au lieu d'en choisir un.
- **Ducking** : le graphe « baissée par » se contrôle sur le graphe **résultant** : une association qui fermerait une boucle (A sous B, B sous A,
  ou plus long) est refusée, jamais ajoutée en silence.
"""

from __future__ import annotations

from typing import Any

from ..project_model import Project
from .verbs import _pick, _require_unlocked

MAX_AUTOMATION_POINTS = 1_000
"""Points d'une courbe de volume au plus (le tracé et l'expression de volume de l'export croissent avec leur nombre)."""
MAX_SIDECHAINS = 256
"""Associations de ducking d'un projet (toutes séquences) au plus : chacune ajoute un compresseur au graphe d'export."""
TIME_TOLERANCE = 5e-7
"""Secondes : l'instant d'un point tel que le lit ``describe`` (arrondi à 6 décimales) retrouve le point du modèle."""
EXACT_TOLERANCE = 1e-9
"""Secondes : l'identité d'un instant pour le modèle (``TrackAutomation.add_point`` remplace un point à moins de 1e-9)."""


def audio_track(project: Project, selector: str) -> Any:
    """La piste **audio** déverrouillée désignée (identifiant ou nom exact) ; ``ValueError`` sinon."""
    track = _pick(project.tracks, selector, "Piste")
    if track.type != "audio":
        raise ValueError(f"La piste '{track.id}' est une piste '{track.type}' : cette opération vise une piste audio.")
    _require_unlocked(track)
    return track


# --- Courbe de volume -------------------------------------------------------------------------------------------------------


def point_time(track: Any, requested: float) -> float:
    """L'instant exact du point de la piste proche de ``requested`` ; refus s'il n'y en a pas ou si deux points sont aussi proches."""
    matches = [float(point.time_seconds) for point in track.automation.points if abs(point.time_seconds - requested) <= TIME_TOLERANCE]
    if not matches:
        raise KeyError(f"La piste '{track.id}' n'a pas de point d'automation à {requested:g} s.")
    if len(matches) > 1:
        raise ValueError(f"Plusieurs points d'automation de la piste '{track.id}' sont à {requested:g} s près : désignez l'un d'eux plus précisément.")
    return matches[0]


def require_free_instant(track: Any, instant: float, *, ignoring: float | None = None) -> None:
    """Refuse un instant déjà pris par un autre point (le modèle remplacerait ce point en silence, ou refuserait un déplacement)."""
    for point in track.automation.points:
        if ignoring is not None and abs(point.time_seconds - ignoring) <= EXACT_TOLERANCE:
            continue
        if abs(point.time_seconds - instant) <= TIME_TOLERANCE:
            raise ValueError(
                f"La piste '{track.id}' a déjà un point d'automation à {point.time_seconds:g} s : modifiez-le (update_automation_point) "
                "ou choisissez un autre instant."
            )


def require_point_room(track: Any, adding: int) -> None:
    """Refuse, AVANT toute création, ``adding`` points de plus si la courbe dépasserait :data:`MAX_AUTOMATION_POINTS`."""
    count = len(track.automation.points)
    if count + adding > MAX_AUTOMATION_POINTS:
        raise ValueError(
            f"La courbe de la piste '{track.id}' compte déjà {count} points : {adding} de plus dépasseraient la limite de {MAX_AUTOMATION_POINTS}."
        )


# --- Ducking ----------------------------------------------------------------------------------------------------------------


def total_sidechains(project: Project) -> int:
    """Les associations de ducking de TOUTES les séquences (la limite est celle du projet, pas de la séquence active)."""
    return sum(len(sequence.ducking_sidechains or ()) for sequence in project.sequences)


def require_sidechain_room(project: Project, adding: int) -> None:
    """Refuse, AVANT toute création, ``adding`` associations de plus si le projet dépasserait :data:`MAX_SIDECHAINS`."""
    count = total_sidechains(project)
    if count + adding > MAX_SIDECHAINS:
        raise ValueError(f"Le projet compte déjà {count} associations de ducking : {adding} de plus dépasseraient la limite de {MAX_SIDECHAINS}.")


def require_no_ducking_loop(project: Project, music_id: str, voice_id: str, extra: tuple[tuple[str, str], ...] = ()) -> None:
    """Refuse l'association « ``music_id`` baisse sous ``voice_id`` » si, une fois ajoutée, le graphe « baissée par » aurait une boucle.

    La boucle se cherche sur le graphe résultant : il y en a une si ``voice_id`` est déjà (directement ou par d'autres pistes) baissée
    par ``music_id``. Les associations désactivées comptent (réactivées, elles referment la boucle). ``extra`` : des paires
    ``(musique, voix)`` à compter comme déjà là (une opération en masse qui en crée plusieurs)."""
    ducked_by: dict[str, set[str]] = {}
    for sidechain in project.ducking_sidechains:
        ducked_by.setdefault(sidechain.music_track_id, set()).add(sidechain.voice_track_id)
    for music, voice in extra:
        ducked_by.setdefault(music, set()).add(voice)
    seen: set[str] = set()
    pending = [voice_id]
    while pending:
        current = pending.pop()
        if current == music_id:
            raise ValueError(f"Cette association ferme une boucle : la piste '{voice_id}' est déjà baissée (directement ou non) par la piste '{music_id}'.")
        if current in seen:
            continue
        seen.add(current)
        pending.extend(ducked_by.get(current, ()))


def sidechain_ids(project: Project) -> set[str]:
    """Les identifiants d'association de TOUTES les séquences (un identifiant imposé ne doit en reprendre aucun)."""
    return {item.id for sequence in project.sequences for item in (sequence.ducking_sidechains or ())}
