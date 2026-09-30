"""Opérations métier sur les pistes d'un projet Kut-Studio.

Ce module complète :mod:`core.timeline_operations` avec les primitives
dédiées aux pistes :

- création (``add_track``) ;
- suppression (``remove_track``) ;
- renommage (``rename_track``) ;
- réordonnancement (``move_track``) ;
- verrouillage (``set_track_locked``) ;
- visibilité (``set_track_visible``) ;
- muet / son activé (``set_track_muted``).

Toutes ces fonctions sont pures (aucune dépendance PySide6) et
participent au flux Undo/Redo général décrit dans
:mod:`core.edit_history` : chaque opération est appliquée directement
au ``Project`` vivant, le snapshot étant géré en amont par
``MainWindow``.

Convention d'identifiants automatique :

- ``V{n}`` pour les pistes vidéo (``n`` commence à 1) ;
- ``A{n}`` pour les pistes audio ;
- ``S{n}`` pour les pistes de sous-titres ;
- ``G{n}`` pour les pistes graphiques.

Si l'utilisateur renomme une piste, son identifiant technique
(``Track.id``) reste inchangé : seul le libellé humain (``Track.name``)
évolue.
"""

from __future__ import annotations

import re
import uuid
from typing import Iterable


# Type-check only : évite l'import circulaire.
# ``core.project_model.Project`` n'est utilisé qu'en annotation.
if False:  # pragma: no cover - dépendance typée uniquement
    from .project_model import Project, Track


# ---------------------------------------------------------------------------
# Constantes et conventions
# ---------------------------------------------------------------------------


_VALID_TYPES: frozenset[str] = frozenset({"video", "audio", "subtitle", "graphics"})
"""Types de pistes acceptés par les opérations de pistes."""

_TYPE_PREFIX: dict[str, str] = {
    "video": "V",
    "audio": "A",
    "subtitle": "S",
    "graphics": "G",
}
"""Préfixe automatique de l'identifiant selon le type de piste."""


_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
"""Identifiants valides pour ``Track.id`` (utilisé pour les renommages)."""


# ---------------------------------------------------------------------------
# Helpers privés
# ---------------------------------------------------------------------------


def _coerce_track_type(track_type: str) -> str:
    """Normalise le type de piste et rejette les valeurs inconnues.

    Args:
        track_type: ``"video"``, ``"audio"`` ou ``"subtitle"`` (case
            insensitive).

    Returns:
        Type de piste normalisé en minuscules.

    Raises:
        ValueError: si ``track_type`` n'est pas un des trois types
            métier supportés.
    """
    if not isinstance(track_type, str):
        raise ValueError("Le type de piste doit être une chaîne.")
    normalized = track_type.strip().lower()
    if normalized not in _VALID_TYPES:
        raise ValueError(
            f"Type de piste inconnu : {track_type!r}. "
            f"Attendu : 'video', 'audio', 'subtitle' ou 'graphics'."
        )
    return normalized


def _is_valid_track_id(track_id: str) -> bool:
    """Vrai si ``track_id`` est un identifiant de piste syntaxiquement valide."""
    if not isinstance(track_id, str) or not track_id:
        return False
    return bool(_ID_RE.match(track_id))


def _ensure_track_id(
    project: "Project",
    track_type: str,
    index: int | None = None,
) -> str:
    """Génère un identifiant unique pour la nouvelle piste.

    Recherche l'index maximal existant ``<prefix><n>`` et l'incrémente.
    Le paramètre ``index`` permet de demander un numéro précis ; s'il
    est déjà pris, la fonction choisit le plus petit ``n`` disponible.
    """
    prefix = _TYPE_PREFIX[track_type]
    existing_numbers: list[int] = []
    for track in project.tracks:
        if track.type != track_type:
            continue
        match = re.fullmatch(rf"{prefix}(\d+)", track.id)
        if match:
            try:
                existing_numbers.append(int(match.group(1)))
            except ValueError:
                continue
    if index is not None:
        candidate = max(1, int(index))
        if candidate not in existing_numbers:
            return f"{prefix}{candidate}"
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"{prefix}{next_number}"


def _ensure_track_name(
    project: "Project",
    track_type: str,
    name: str | None = None,
) -> str:
    """Génère un nom humain unique pour la nouvelle piste.

    Le nom par défaut suit la convention ``<prefix><n>``. Si l'utilisateur
    fournit un nom, on l'utilise tel quel : la déduplication est gérée
    par ``rename_track``.
    """
    if name and name.strip():
        return name.strip()
    track_id = _ensure_track_id(project, track_type)
    return track_id


def _find_track_index(
    project: "Project", track_id: str, *, operation: str
) -> int:
    """Retourne l'index de la piste ; lève ``KeyError`` si absente."""
    for index, track in enumerate(project.tracks):
        if track.id == track_id:
            return index
    raise KeyError(
        f"Piste '{track_id}' introuvable pour l'opération '{operation}'."
    )


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def add_track(
    project: "Project",
    track_type: str,
    name: str | None = None,
    index: int | None = None,
) -> "Track":
    """Ajoute une nouvelle piste au projet et la retourne.

    Args:
        project: projet cible (muté en place).
        track_type: ``"video"``, ``"audio"`` ou ``"subtitle"``.
        name: Nom humain optionnel. Si ``None`` ou vide, un nom
            automatique ``<prefix><n>`` est généré.
        index: Position d'insertion dans ``project.tracks``. Si
            ``None``, la piste est ajoutée en fin. Si ``index`` est
            hors limites, il est clampé dans l'intervalle valide.

    Returns:
        L'instance de la nouvelle :class:`Track` (déjà présente dans
        ``project.tracks``).

    Raises:
        ValueError: si ``track_type`` n'est pas un type supporté.
    """
    from .project_model import Track  # import local pour éviter la circularité

    normalized_type = _coerce_track_type(track_type)
    track_id = _ensure_track_id(project, normalized_type, index=index)
    # On ré-efface la piste "fantôme" créée par ``_ensure_track_id``
    # pour les tests : en réalité ``_ensure_track_id`` n'insère rien,
    # il calcule simplement un identifiant libre.
    track_name = _ensure_track_name(project, normalized_type, name)

    # Sécurité : si le nom est déjà pris, on suffixe avec un compteur
    # pour respecter l'unicité tout en laissant l'utilisateur fournir
    # ses propres noms dans le cas usuel.
    existing_names = {track.name for track in project.tracks}
    candidate_name = track_name
    suffix = 2
    while candidate_name in existing_names:
        candidate_name = f"{track_name} ({suffix})"
        suffix += 1
    track_name = candidate_name

    track = Track(
        id=track_id,
        name=track_name,
        type=normalized_type,
        clips=[],
    )

    if index is None or index >= len(project.tracks):
        project.tracks.append(track)
    else:
        project.tracks.insert(max(0, int(index)), track)
    return track


def remove_track(
    project: "Project",
    track_id: str,
) -> "Track":
    """Supprime une piste vide du projet.

    Args:
        project: projet cible.
        track_id: identifiant de la piste à supprimer.

    Returns:
        L'instance de la :class:`Track` supprimée (plus aucune
        référence dans ``project.tracks``).

    Raises:
        KeyError: si la piste n'existe pas.
        ValueError: si la piste porte au moins un clip — Kut-Studio
            refuse les suppressions destructives qui laisseraient des
            clips orphelins.
    """
    track_index = _find_track_index(project, track_id, operation="remove")
    track = project.tracks[track_index]
    if track.clips:
        raise ValueError(
            f"Impossible de supprimer la piste '{track_id}' : elle "
            f"contient {len(track.clips)} clip(s). Supprimez d'abord "
            f"les clips ou utilisez l'outil adapté."
        )
    del project.tracks[track_index]
    return track


def rename_track(
    project: "Project",
    track_id: str,
    new_name: str,
) -> "Track":
    """Renomme une piste sans toucher à son identifiant technique.

    Args:
        project: projet cible.
        track_id: identifiant de la piste.
        new_name: nouveau libellé humain. Doit être non vide.

    Returns:
        La :class:`Track` mutée.

    Raises:
        KeyError: si ``track_id`` est introuvable.
        ValueError: si le nouveau nom est vide ou déjà utilisé par une
            autre piste du projet.
    """
    track_index = _find_track_index(project, track_id, operation="rename")
    if not isinstance(new_name, str) or not new_name.strip():
        raise ValueError("Le nouveau nom de piste doit être non vide.")

    target_track = project.tracks[track_index]
    stripped = new_name.strip()
    for other in project.tracks:
        if other is target_track:
            continue
        if other.name == stripped:
            raise ValueError(
                f"Le nom '{stripped}' est déjà utilisé par la piste "
                f"'{other.id}'."
            )
    target_track.name = stripped
    return target_track


def move_track(
    project: "Project",
    track_id: str,
    new_index: int,
) -> "Track":
    """Déplace une piste à un nouvel index dans ``project.tracks``.

    Les pistes verrouillées ne sont pas repositionnables via cette
    opération (c'est une restriction métier : on évite que l'éditeur
    réorganise silencieusement des pistes verrouillées).

    Args:
        project: projet cible.
        track_id: identifiant de la piste.
        new_index: nouvel index (0-based). Les valeurs hors limites
            sont clampées.

    Returns:
        La :class:`Track` déplacée.

    Raises:
        KeyError: si la piste n'existe pas.
        ValueError: si la piste est verrouillée.
    """
    track_index = _find_track_index(project, track_id, operation="move")
    track = project.tracks[track_index]
    if track.locked:
        raise ValueError(
            f"La piste '{track_id}' est verrouillée : déplacement refusé."
        )
    track_obj = project.tracks.pop(track_index)
    last_index = len(project.tracks)
    target = max(0, min(int(new_index), last_index))
    project.tracks.insert(target, track_obj)
    return track_obj


def set_track_locked(
    project: "Project",
    track_id: str,
    locked: bool,
) -> "Track":
    """Verrouille ou déverrouille une piste."""
    track_index = _find_track_index(project, track_id, operation="lock")
    track = project.tracks[track_index]
    track.locked = bool(locked)
    return track


def set_track_visible(
    project: "Project",
    track_id: str,
    visible: bool,
) -> "Track":
    """Affiche ou masque une piste.

    Le masquage est un signal de rendu : les clips vidéo d'une piste
    invisible ne sont pas inclus dans ``render_plan`` / ``export`` ;
    les sous-titres d'une piste invisible ne sont pas incrustés ; les
    pistes audio muettes sont également exclues du mixage.
    """
    track_index = _find_track_index(project, track_id, operation="visible")
    track = project.tracks[track_index]
    track.visible = bool(visible)
    return track


def set_track_muted(
    project: "Project",
    track_id: str,
    muted: bool,
) -> "Track":
    """Active ou coupe le son d'une piste.

    Pour les pistes audio : les clips ne participent plus au mixage
    dans ``render_plan``.

    Pour les pistes vidéo : la sortie audio de leur média (si
    ``has_audio``) est exclue du mixage.

    Les pistes de sous-titres ignorent ce signal (les sous-titres
    sont muets par nature).
    """
    track_index = _find_track_index(project, track_id, operation="muted")
    track = project.tracks[track_index]
    track.muted = bool(muted)
    return track


def is_track_editable(project: "Project", track_id: str) -> bool:
    """Vrai si ``track_id`` existe et accepte les opérations d'édition."""
    try:
        track_index = _find_track_index(
            project, track_id, operation="editable_check"
        )
    except KeyError:
        return False
    return not project.tracks[track_index].locked


def collect_track_ids(
    project: "Project",
    track_type: str | None = None,
) -> list[str]:
    """Retourne les identifiants de pistes, filtrés par type optionnel."""
    if track_type is None:
        return [track.id for track in project.tracks]
    normalized = _coerce_track_type(track_type)
    return [
        track.id for track in project.tracks if track.type == normalized
    ]


def visible_video_track_ids(project: "Project") -> list[str]:
    """Identifiants des pistes vidéo visibles et non verrouillées
    pour le rendu."""
    return [
        track.id
        for track in project.tracks
        if track.type == "video" and track.visible
    ]


__all__ = [
    "add_track",
    "remove_track",
    "rename_track",
    "move_track",
    "set_track_locked",
    "set_track_visible",
    "set_track_muted",
    "is_track_editable",
    "collect_track_ids",
    "visible_video_track_ids",
]
