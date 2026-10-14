"""Nœuds d'un masque courbe de Bézier écrits par un script (ADR-0006 étape 3b, ADR-0005 étape 4).

Un script décrit la courbe par une liste de nœuds ``{"x", "y", "in_dx", "in_dy", "out_dx", "out_dy"}`` (ancre et tangentes
relatives à l'ancre, en fractions de la boîte du masque : voir :mod:`core.mask_path`). Les bornes sont celles du modèle
(:data:`core.mask_path.POINT_LIMIT`, :data:`~core.mask_path.HANDLE_LIMIT`, de :data:`~core.mask_path.MIN_NODES` à
:data:`~core.mask_path.MAX_NODES` nœuds) et **rien n'est écrêté** : ``Mask`` ramènerait une valeur hors bornes à sa borne en
silence, un script la voit refusée. Le décodeur applique déjà ces bornes ; :func:`nodes_to_arrays` les revérifie pour qui appelle
un verbe sans passer par lui (façade Python).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .. import mask_path

ANCHOR_FIELDS = ("x", "y")
TANGENT_FIELDS = ("in_dx", "in_dy", "out_dx", "out_dy")
NODE_FIELDS = (*ANCHOR_FIELDS, *TANGENT_FIELDS)


def _number(node: Mapping[str, Any], name: str, limit: float, *, default: float | None) -> float:
    value = node.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Nœud : '{name}' doit être un nombre fini.")
    if abs(value) > limit:
        raise ValueError(f"Nœud : '{name}' est hors bornes (de {-limit} à {limit}).")
    return float(value)


def nodes_to_arrays(nodes: Any) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float, float, float], ...]]:
    """``(points, handles)`` de la liste de nœuds, ou ``ValueError`` (nombre de nœuds, champ inconnu, valeur non finie, hors bornes).

    Le nombre de nœuds est borné **avant** toute lecture des éléments ; les messages ne recopient jamais l'entrée.
    """
    if not isinstance(nodes, (list, tuple)):
        raise ValueError("Une courbe se décrit par une liste de nœuds.")
    if not mask_path.MIN_NODES <= len(nodes) <= mask_path.MAX_NODES:
        raise ValueError(f"Une courbe porte de {mask_path.MIN_NODES} à {mask_path.MAX_NODES} nœuds.")
    points: list[tuple[float, float]] = []
    handles: list[tuple[float, float, float, float]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("Un nœud est un objet {x, y, in_dx, in_dy, out_dx, out_dy}.")
        unknown = [key for key in node if key not in NODE_FIELDS]
        if unknown:
            raise ValueError(f"Nœud : champ inconnu (attendus : {', '.join(NODE_FIELDS)}).")
        points.append((_number(node, "x", mask_path.POINT_LIMIT, default=None),
                       _number(node, "y", mask_path.POINT_LIMIT, default=None)))
        handles.append((_number(node, "in_dx", mask_path.HANDLE_LIMIT, default=0.0),
                        _number(node, "in_dy", mask_path.HANDLE_LIMIT, default=0.0),
                        _number(node, "out_dx", mask_path.HANDLE_LIMIT, default=0.0),
                        _number(node, "out_dy", mask_path.HANDLE_LIMIT, default=0.0)))
    return tuple(points), tuple(handles)


__all__ = ["ANCHOR_FIELDS", "NODE_FIELDS", "TANGENT_FIELDS", "nodes_to_arrays"]
