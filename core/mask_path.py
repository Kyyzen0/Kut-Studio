"""Masque en courbe de Bézier fermée : nœuds, tangentes, découpe (ADR-0006, étape 3, lot 3b). Calcul pur, sans Qt.

Un chemin est une suite d'au moins :data:`MIN_NODES` nœuds, reliés dans l'ordre puis refermés sur le premier. Un nœud
est une ancre ``(x, y)`` et une paire de poignées de tangente ``(in_dx, in_dy, out_dx, out_dy)`` **relatives à l'ancre**.
Tout est exprimé en fractions de la boîte du masque (``-0,5``..``0,5`` pour la boîte), comme les sommets d'un polygone :
la courbe suit donc la position, la taille et la rotation du masque. Le segment ``i`` va du nœud ``i`` au nœud
``(i + 1) % n`` ; ses points de contrôle sont ``ancre_i + out_i`` et ``ancre_{i+1} + in_{i+1}``. Un nœud « anguleux »
a ses deux poignées nulles ; un chemin sans aucune poignée est donc un polygone.

Bornes (lues par :class:`core.compositing.Mask`, par la validation et par les poignées du moniteur) : ancres à
``±POINT_LIMIT``, décalages de tangente à ``±HANDLE_LIMIT``, de :data:`MIN_NODES` à :data:`MAX_NODES` nœuds.
Les sommets ne s'animent pas (ils vivent dans le masque, pas dans ``Clip.animation``).
"""

from __future__ import annotations

import math

MIN_NODES = 3
MAX_NODES = 128
"""Plafond de nœuds : un contour tracé à la main en demande rarement plus de quelques dizaines ; au-delà, la matte coûte
cher à dessiner pour rien et un fichier ou un script ne doit pas pouvoir allouer sans limite."""
POINT_LIMIT = 2.0
HANDLE_LIMIT = 2.0
"""Un décalage de tangente de deux boîtes est déjà absurde : la courbe sort largement de la zone d'ancrage."""
KAPPA = 0.5522847498307936
"""Distance (en rayon) des poignées d'un cercle approché par quatre arcs de Bézier."""
_EPS = 1e-9

Node = tuple[float, float]
Tangent = tuple[float, float, float, float]

DEFAULT_NODES: tuple[Node, ...] = ((0.0, -0.5), (0.5, 0.0), (0.0, 0.5), (-0.5, 0.0))
DEFAULT_TANGENTS: tuple[Tangent, ...] = (
    (-KAPPA * 0.5, 0.0, KAPPA * 0.5, 0.0),
    (0.0, -KAPPA * 0.5, 0.0, KAPPA * 0.5),
    (KAPPA * 0.5, 0.0, -KAPPA * 0.5, 0.0),
    (0.0, KAPPA * 0.5, 0.0, -KAPPA * 0.5),
)
"""Forme par défaut d'un masque courbe : un cercle de quatre nœuds arrondis dans la boîte du masque."""


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


def _finite_pair(item: object, size: int, what: str) -> tuple[float, ...]:
    try:
        values = tuple(float(v) for v in item)  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        raise ValueError(f"{what} invalide : {size} nombres attendus.") from None
    if len(values) != size:
        raise ValueError(f"{what} invalide : {size} nombres attendus.")
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"{what} invalide : valeur non finie.")
    return values


def normalize_nodes(points: object, handles: object) -> tuple[tuple[Node, ...], tuple[Tangent, ...]]:
    """Forme canonique d'un chemin : ancres et décalages bornés, deux listes de même longueur.

    ``handles`` vide : tous les nœuds sont anguleux. Moins de :data:`MIN_NODES` ancres : la forme par défaut
    (comme un polygone sans sommets). Lève ``ValueError`` pour ce qui ne se corrige pas sans changer la forme : valeur
    non finie, nœud mal formé, longueurs différentes, plus de :data:`MAX_NODES` nœuds.
    """
    raw_points = tuple(points or ())
    raw_handles = tuple(handles or ())
    if len(raw_points) > MAX_NODES or len(raw_handles) > MAX_NODES:
        raise ValueError(f"Un masque courbe porte au plus {MAX_NODES} nœuds.")
    anchors = tuple(_finite_pair(p, 2, "Nœud") for p in raw_points)
    if len(anchors) < MIN_NODES:
        return DEFAULT_NODES, DEFAULT_TANGENTS
    if raw_handles and len(raw_handles) != len(anchors):
        raise ValueError("Un masque courbe porte autant de poignées que de nœuds.")
    tangents = tuple(_finite_pair(h, 4, "Poignée") for h in raw_handles) or ((0.0, 0.0, 0.0, 0.0),) * len(anchors)
    return (tuple((_clamp(x, POINT_LIMIT), _clamp(y, POINT_LIMIT)) for x, y in anchors),
            tuple(tuple(_clamp(v, HANDLE_LIMIT) for v in h) for h in tangents))  # type: ignore[return-value]


def problem(points: tuple, handles: tuple) -> str | None:
    """Pourquoi ``(points, handles)`` n'est pas un chemin valide tel quel (``None`` : il l'est). Rien n'est corrigé."""
    if not MIN_NODES <= len(points) <= MAX_NODES:
        return "count"
    if len(handles) != len(points):
        return "length"
    for x, y in points:
        if not (math.isfinite(x) and math.isfinite(y) and abs(x) <= POINT_LIMIT and abs(y) <= POINT_LIMIT):
            return "bounds"
    for h in handles:
        if not (all(math.isfinite(v) for v in h) and all(abs(v) <= HANDLE_LIMIT for v in h)):
            return "bounds"
    return None


# -- géométrie -----------------------------------------------------------------------------------------


def segment(points: tuple, handles: tuple, index: int) -> tuple[Node, Node, Node, Node]:
    """Les quatre points de contrôle du segment ``index`` (du nœud ``index`` au suivant, le dernier se referme)."""
    count = len(points)
    (x0, y0), (x1, y1) = points[index % count], points[(index + 1) % count]
    out, nxt = handles[index % count], handles[(index + 1) % count]
    return (x0, y0), (x0 + out[2], y0 + out[3]), (x1 + nxt[0], y1 + nxt[1]), (x1, y1)


def bezier_point(p0: Node, p1: Node, p2: Node, p3: Node, t: float) -> Node:
    s = 1.0 - t
    a, b, c, d = s * s * s, 3 * s * s * t, 3 * s * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def sample_outline(points: tuple, handles: tuple, steps: int = 16) -> list[Node]:
    """Contour échantillonné (``steps`` points par segment, le point d'arrivée de chaque segment étant le départ du suivant)."""
    result: list[Node] = []
    for index in range(len(points)):
        p0, p1, p2, p3 = segment(points, handles, index)
        result.extend(bezier_point(p0, p1, p2, p3, k / steps) for k in range(steps))
    return result


def _lerp(a: Node, b: Node, t: float) -> Node:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def split_segment(points: tuple, handles: tuple, index: int, t: float):
    """Insère un nœud au paramètre ``t`` du segment ``index`` (De Casteljau) : **la forme ne change pas**.

    Le nouveau nœud prend l'indice ``index + 1`` ; les poignées voisines sont raccourcies en conséquence.
    ``None`` : déjà au plafond de nœuds, ``t`` hors de ]0, 1[, ou le résultat sortirait des bornes (on ne l'écrête pas :
    la forme changerait).
    """
    count = len(points)
    if count >= MAX_NODES or not 0.0 < t < 1.0 or not 0 <= index < count:
        return None
    p0, p1, p2, p3 = segment(points, handles, index)
    q0, q1, q2 = _lerp(p0, p1, t), _lerp(p1, p2, t), _lerp(p2, p3, t)
    r0, r1 = _lerp(q0, q1, t), _lerp(q1, q2, t)
    s = _lerp(r0, r1, t)
    nxt = (index + 1) % count
    new_handles = list(handles)
    new_handles[index] = (*handles[index][:2], q0[0] - p0[0], q0[1] - p0[1])
    new_handles[nxt] = (q2[0] - p3[0], q2[1] - p3[1], *handles[nxt][2:])
    new_point = (s[0], s[1])
    new_tangent = (r0[0] - s[0], r0[1] - s[1], r1[0] - s[0], r1[1] - s[1])
    new_points = list(points)
    new_points.insert(index + 1, new_point)
    new_handles.insert(index + 1, new_tangent)
    result = (tuple(new_points), tuple(tuple(h) for h in new_handles))
    return result if problem(*result) is None else None


def nearest_on_outline(points: tuple, handles: tuple, project, target: tuple[float, float], steps: int = 32):
    """Point du contour le plus proche de ``target`` : ``(segment, t, distance)``.

    ``project`` : ``(u, v) -> (x, y)`` ramène les coordonnées du masque dans le repère de ``target`` (l'écran), pour que la
    distance soit celle que voit l'utilisateur. ``None`` si le chemin est vide.
    """
    best = None
    for index in range(len(points)):
        p0, p1, p2, p3 = segment(points, handles, index)
        previous = project(*p0)
        for k in range(1, steps + 1):
            current = project(*bezier_point(p0, p1, p2, p3, k / steps))
            ex, ey = current[0] - previous[0], current[1] - previous[1]
            length2 = ex * ex + ey * ey
            u = 0.0 if length2 < _EPS else max(0.0, min(1.0, ((target[0] - previous[0]) * ex + (target[1] - previous[1]) * ey) / length2))
            distance = math.hypot(previous[0] + ex * u - target[0], previous[1] + ey * u - target[1])
            if best is None or distance < best[2]:
                best = (index, (k - 1 + u) / steps, distance)
            previous = current
    return best


def is_angular(handle: Tangent) -> bool:
    return all(abs(v) <= _EPS for v in handle)


def toggle_smooth(points: tuple, handles: tuple, index: int):
    """Nœud anguleux → lisse (tangente parallèle à la corde des voisins, un sixième de sa longueur) ; lisse → anguleux."""
    count = len(points)
    if not 0 <= index < count:
        return None
    new_handles = list(handles)
    if is_angular(handles[index]):
        (px, py), (nx, ny) = points[(index - 1) % count], points[(index + 1) % count]
        dx, dy = (nx - px) / 6.0, (ny - py) / 6.0
        new_handles[index] = (-dx, -dy, dx, dy)
    else:
        new_handles[index] = (0.0, 0.0, 0.0, 0.0)
    result = (tuple(points), tuple(new_handles))
    return result if problem(*result) is None else None


def remove_node(points: tuple, handles: tuple, index: int):
    """Chemin sans le nœud ``index`` ; ``None`` sous :data:`MIN_NODES` nœuds (le masque ne devient pas une forme dégénérée)."""
    if len(points) <= MIN_NODES or not 0 <= index < len(points):
        return None
    return tuple(p for i, p in enumerate(points) if i != index), tuple(h for i, h in enumerate(handles) if i != index)


__all__ = [
    "DEFAULT_NODES", "DEFAULT_TANGENTS", "HANDLE_LIMIT", "KAPPA", "MAX_NODES", "MIN_NODES", "POINT_LIMIT",
    "bezier_point", "is_angular", "nearest_on_outline", "normalize_nodes", "problem", "remove_node", "sample_outline",
    "segment", "split_segment", "toggle_smooth",
]
