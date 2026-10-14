"""Poignées d'un masque courbe (Bézier) dans le moniteur : nœuds, tangentes, ajout, suppression, lissage (ADR-0006, lot 3b).

Calcul pur, sans Qt, dans le prolongement de :mod:`core.mask_handles` (qui l'appelle depuis :func:`core.mask_handles.drag`).
Les coordonnées ``(u, v)`` sont celles de la boîte du masque (``-0,5``..``0,5``) ; les nœuds et les tangentes sont écrits
dans le masque (``Mask.points`` / ``Mask.handles``), jamais animés.

Noms de poignées (sans le préfixe ``m_`` du moniteur) : ``p<i>`` le nœud ``i``, ``hi<i>`` sa tangente entrante, ``ho<i>`` sa
tangente sortante. Glisser une tangente déplace aussi l'autre, en miroir (nœud lisse), sauf avec Alt (symétrie cassée).

Les actions qui changent le nombre de nœuds ou la nature d'un nœud (:func:`node_action`) ne collent rien aux bornes : le
résultat doit y tenir tel quel, sinon l'action est refusée (``None``) et la forme n'est pas modifiée.
"""

from __future__ import annotations

from . import mask_path
from .mask_handles import MaskEdit, MaskGeometry, _finish, canvas_point

ACTIONS = ("add", "remove", "toggle")


def _index(mode: str, prefix: str) -> int | None:
    tail = mode[len(prefix):]
    return int(tail) if mode.startswith(prefix) and tail.isdigit() else None


def drag_path(geometry: MaskGeometry, mode: str, u: float, v: float, *, alt: bool = False) -> MaskEdit | None:
    """Nœud ``p<i>`` ou tangente ``hi<i>`` / ``ho<i>`` amené en ``(u, v)`` (coordonnées de la boîte du masque).

    ``None`` : le mode n'en est pas un, l'indice est inconnu, ou rien ne change. Valeurs collées aux bornes (l'ancre à
    ±``POINT_LIMIT``, le décalage de tangente à ±``HANDLE_LIMIT``) avant d'être émises.
    """
    points = list(geometry.points)
    handles = [tuple(h) for h in geometry.handles]
    if len(handles) != len(points):
        return None                                        # modèle incohérent : on ne devine pas
    if (index := _index(mode, "p")) is not None:
        if index >= len(points):
            return None
        points[index] = (u, v)
        return _finish(geometry, {}, tuple(points))
    incoming = _index(mode, "hi")
    outgoing = _index(mode, "ho")
    index = incoming if incoming is not None else outgoing
    if index is None or index >= len(points):
        return None
    dx, dy = u - points[index][0], v - points[index][1]          # collés aux bornes par _finish (le miroir en est l'opposé)
    h_in, h_out = handles[index][:2], handles[index][2:]
    if incoming is not None:
        h_in, h_out = (dx, dy), (h_out if alt else (-dx, -dy))
    else:
        h_in, h_out = (h_in if alt else (-dx, -dy)), (dx, dy)
    handles[index] = (*h_in, *h_out)
    return _finish(geometry, {}, None, handles=tuple(handles))


def node_action(geometry: MaskGeometry, action: str, index: int | None = None, point: tuple[float, float] | None = None,
                tolerance: float = 8.0) -> MaskEdit | None:
    """Une action de structure sur la courbe, ou ``None`` si elle est refusée.

    - ``add`` : nœud sur le contour au plus près de ``point`` (pixels du cadre), à moins de ``tolerance`` pixels ; la forme
      est conservée (découpe de De Casteljau). Refusé au plafond de nœuds.
    - ``remove`` : retire le nœud ``index`` ; refusé sous :data:`core.mask_path.MIN_NODES` nœuds.
    - ``toggle`` : nœud ``index`` anguleux ↔ lisse.
    """
    if geometry.shape != "path" or len(geometry.handles) != len(geometry.points) or action not in ACTIONS:
        return None
    points, handles = tuple(geometry.points), tuple(tuple(h) for h in geometry.handles)
    if action == "add":
        if point is None:
            return None
        found = mask_path.nearest_on_outline(points, handles, lambda u, v: canvas_point(geometry, u, v), point)
        if found is None or found[2] > tolerance:
            return None
        result = mask_path.split_segment(points, handles, found[0], found[1])
    elif index is None:
        return None
    elif action == "remove":
        result = mask_path.remove_node(points, handles, index)
    else:
        result = mask_path.toggle_smooth(points, handles, index)
    return None if result is None else MaskEdit({}, result[0], 0.0, result[1])


def nodes_refusal(mask, points: tuple | None, handles: tuple | None) -> str | None:
    """Pourquoi écrire ``points`` / ``handles`` dans ``mask`` est refusé (``None`` : possible). Garde de la fenêtre principale.

    ``shape`` : des tangentes pour une forme qui n'est pas une courbe ; ``count`` / ``length`` / ``bounds`` : voir
    :func:`core.mask_path.problem` (appliqué au résultat, tangentes ou nœuds inchangés compris : un décalage entre les deux
    listes ne doit jamais atteindre le modèle).
    """
    if mask.shape.value != "path":
        return "shape" if handles is not None else None
    return mask_path.problem(tuple(mask.points if points is None else points),
                             tuple(mask.handles if handles is None else handles))


__all__ = ["ACTIONS", "drag_path", "node_action", "nodes_refusal"]
