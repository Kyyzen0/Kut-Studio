"""Identifiants des objets du projet : générés par le modèle, ou **fournis** par l'appelant (scripts) et alors validés.

Les fonctions de création de ``core/`` (clips, sous-titres, graphiques, transitions, repères, séquences, pistes) fabriquent
leur identifiant (``clip-`` + un UUID tronqué). Un script (ADR-0005) doit pouvoir **désigner ce qu'il vient de créer** et rejouer
le même script en obtenant le même projet : ces fonctions acceptent donc un identifiant explicite (``clip_id=``…). Absent
(``None``), rien ne change : l'identifiant est généré comme avant, et aucun appelant existant n'est touché.

Un identifiant fourni vient d'un script, donc d'un texte qu'on ne maîtrise pas : il suit la **même règle que les identifiants de
nœuds** (:func:`core.node_graph.is_safe_id`) : lettres, chiffres et ``_``, 64 caractères au plus. Un identifiant devient parfois
une étiquette de graphe FFmpeg ; c'est la règle qui a fermé l'injection de filtres de la 0.4.0. Il doit en plus être **libre** :
un doublon est refusé, jamais écrasé.
"""

from __future__ import annotations

import uuid
from collections.abc import Container

from .node_graph import MAX_ID_LENGTH, is_safe_id


class InvalidObjectId(ValueError):
    """Identifiant fourni invalide (forme) ou déjà pris."""


def new_object_id(prefix: str, length: int = 12) -> str:
    """Identifiant généré : ``<prefix>-<UUID tronqué>`` (le comportement historique de chaque fonction de création)."""
    return f"{prefix}-{uuid.uuid4().hex[:length]}"


def resolve_object_id(
    requested: str | None, *, prefix: str, kind: str, taken: Container[str], length: int = 12,
) -> str:
    """L'identifiant à utiliser : celui demandé, vérifié, ou un identifiant généré.

    Args:
        requested: identifiant fourni par l'appelant, ``None`` pour en générer un.
        prefix: préfixe de l'identifiant généré (``clip``, ``seq``…).
        kind: nom de l'objet, pour le message d'erreur (``clip``, ``séquence``…).
        taken: les identifiants déjà utilisés (un doublon est refusé).
    """
    if requested is None:
        return new_object_id(prefix, length)
    if not is_safe_id(requested):
        raise InvalidObjectId(
            f"Identifiant de {kind} invalide : {requested!r}. Lettres, chiffres et « _ » seulement, "
            f"{MAX_ID_LENGTH} caractères au plus."
        )
    if requested in taken:
        raise InvalidObjectId(f"L'identifiant de {kind} {requested!r} existe déjà.")
    return requested


def clip_ids(project) -> set[str]:
    """Les identifiants de tous les clips du projet, séquences imbriquées comprises (un identifiant de clip est unique au projet)."""
    return {
        clip.id for sequence in project.sequences for track in sequence.tracks for clip in track.clips
    }


def sequence_ids(project) -> set[str]:
    return {sequence.id for sequence in project.sequences}


__all__ = ["InvalidObjectId", "clip_ids", "new_object_id", "resolve_object_id", "sequence_ids"]
