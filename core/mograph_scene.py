"""Scène motion graphics : hiérarchie, transforms du monde, évaluation à un instant.

Module **pur** (aucun Qt, aucun FFmpeg). Il reçoit les calques d'un plan de
rendu (:class:`core.render_plan.GraphicLayer`) et répond, pour un temps de
timeline ``t`` :

- quelle matrice place chaque calque dans le cadre (parentage, groupes,
  point d'ancrage, échelle X/Y, inclinaison, miroirs) ;
- quelle opacité, quelles propriétés de forme / texte, quels masques ;
- quels calques sont visibles, dans quel ordre.

Le rastériseur (:mod:`core.mograph_raster`), l'aperçu du viewer, l'export
et la manipulation directe dans le viewer utilisent tous **ces** matrices.

Matrices
--------

Une matrice affine est un 6-uplet ``(a, b, c, d, e, f)`` au sens de
``QTransform(m11=a, m12=b, m21=c, m22=d, dx=e, dy=f)`` ::

    x' = a·x + c·y + e        y' = b·x + d·y + f

Repère : pixels, ``y`` vers le bas, angle positif = sens horaire (comme le
filtre ``rotate`` de FFmpeg et ``QTransform.rotate``).

Transform d'un calque
---------------------

Dans l'espace de son parent (boîte ``Pw × Ph``, origine en haut à gauche ;
le cadre ``W × H`` pour un calque racine) ::

    M = T(Pw/2 + px·W, Ph/2 + py·H) · R(rotation) · K(inclinaison)
        · S(scale·scale_x·±1, scale·scale_y·±1) · T(−ax·w, −ay·h)

``(ax, ay)`` est le point d'ancrage (fraction de la boîte ``w × h`` du
calque) : la position désigne l'endroit où il se trouve, et rotation /
échelle pivotent autour de lui. Le monde est ``monde(parent) · M``.

Parent effectif : ``parent_id`` s'il existe, sinon le groupe
(``group_id``) — un groupe agit comme une transformation parente
supplémentaire. Les cycles (A → B → A…) sont détectés par
:func:`core.graph_cycles.find_cycles` et coupés : un calque d'un cycle est
traité comme racine, jamais en récursion infinie.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from . import graph_cycles
from .animation_targets import animation_curves
from .compositing import evaluate_mask_at
from .graphics import GraphicOverlay, GraphicType, LayerLayout
from .mograph_targets import GRAPHIC_PREFIX
from .visual_effects import ClipTransform, EvaluatedTransform, evaluate_transform

Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Algèbre affine
# ---------------------------------------------------------------------------


def mat_mul(m: Matrix, n: Matrix) -> Matrix:
    """``m · n`` : applique ``n`` puis ``m``."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_translate(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def mat_scale(sx: float, sy: float) -> Matrix:
    return (float(sx), 0.0, 0.0, float(sy), 0.0, 0.0)


def mat_rotate(degrees: float) -> Matrix:
    radians = math.radians(float(degrees))
    cos, sin = math.cos(radians), math.sin(radians)
    return (cos, sin, -sin, cos, 0.0, 0.0)


def mat_skew(degrees: float) -> Matrix:
    """Inclinaison horizontale : ``x' = x + tan(k)·y``."""
    return (1.0, 0.0, math.tan(math.radians(float(degrees))), 1.0, 0.0, 0.0)


def mat_apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def mat_determinant(m: Matrix) -> float:
    return m[0] * m[3] - m[1] * m[2]


def mat_invert(m: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise ValueError("Matrice non inversible (échelle nulle).")
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def mat_scale_factor(m: Matrix) -> float:
    """Facteur d'échelle moyen (racine du déterminant, ≥ 0)."""
    return math.sqrt(abs(mat_determinant(m)))


def mat_decompose(m: Matrix) -> tuple[float, float, float, float, float, float]:
    """``(tx, ty, rotation°, sx, sy, inclinaison°)`` tels que
    ``m = T · R · K · S`` (inverse de la composition d'un calque).

    ``sy`` porte le signe d'un éventuel miroir (déterminant négatif).
    """
    a, b, c, d, e, f = m
    sx = math.hypot(a, b)
    if sx < 1e-12:
        return (e, f, 0.0, 0.0, 0.0, 0.0)
    rotation = math.degrees(math.atan2(b, a))
    det = a * d - b * c
    sy = det / sx
    # Colonne 2 = R·(sx·0 + tan(k)·sy, sy) → tan(k)·sy = c·cos + d·sin
    cos, sin = a / sx, b / sx
    shear = (c * cos + d * sin) / sy if abs(sy) > 1e-12 else 0.0
    return (e, f, rotation, sx, sy, math.degrees(math.atan(shear)))


def mat_close(m: Matrix, n: Matrix, tolerance: float = 1e-9) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(m, n))


def map_box(m: Matrix, width: float, height: float) -> tuple[float, float, float, float]:
    """Rectangle englobant ``(x0, y0, x1, y1)`` de la boîte ``w × h`` transformée."""
    points = [mat_apply(m, x, y) for x, y in ((0, 0), (width, 0), (width, height), (0, height))]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def box_corners(m: Matrix, width: float, height: float) -> list[tuple[float, float]]:
    return [mat_apply(m, x, y) for x, y in ((0, 0), (width, 0), (width, height), (0, height))]


# ---------------------------------------------------------------------------
# Transform local d'un calque
# ---------------------------------------------------------------------------


def local_matrix(
    values: EvaluatedTransform,
    box: tuple[float, float],
    parent_box: tuple[float, float],
    canvas: tuple[float, float],
    *,
    layout: LayerLayout = LayerLayout.ANCHOR,
    allow_skew: bool = True,
) -> Matrix:
    """Matrice ``layer → parent`` (voir l'en-tête du module)."""
    w, h = box
    pw, ph = parent_box
    cw, ch = canvas
    sx = values.scale * values.scale_x * (-1.0 if values.flip_h else 1.0)
    sy = values.scale * values.scale_y * (-1.0 if values.flip_v else 1.0)
    if layout is LayerLayout.LEGACY:
        # Tâche 32 : la position place le coin haut-gauche de l'image tournée
        # (le filtre ``rotate`` l'agrandit en carré de côté hypot(w, h)).
        diagonal = math.hypot(w * abs(sx), h * abs(sy))
        px = values.position_x * cw + diagonal / 2.0
        py = values.position_y * ch + diagonal / 2.0
    else:
        px = pw / 2.0 + values.position_x * cw
        py = ph / 2.0 + values.position_y * ch
    m = mat_translate(px, py)
    if values.rotation:
        m = mat_mul(m, mat_rotate(values.rotation))
    if allow_skew and values.skew:
        m = mat_mul(m, mat_skew(values.skew))
    m = mat_mul(m, mat_scale(sx, sy))
    return mat_mul(m, mat_translate(-values.anchor_x * w, -values.anchor_y * h))


def transform_from_world(
    world: Matrix,
    parent_world: Matrix,
    box: tuple[float, float],
    parent_box: tuple[float, float],
    canvas: tuple[float, float],
    current: ClipTransform,
) -> ClipTransform:
    """Transform local qui place un calque à ``world`` sous un nouveau parent.

    Sert au parentage / déparentage sans saut visuel : on garde le point
    d'ancrage et l'opacité, on recalcule position, rotation, échelle,
    inclinaison et miroirs.
    """
    w, h = box
    pw, ph = parent_box
    cw, ch = canvas
    local = mat_mul(mat_invert(parent_world), world)
    # local = T(P) · R · K · S · T(−A) → enlever T(−A)
    core = mat_mul(local, mat_translate(current.anchor_x * w, current.anchor_y * h))
    tx, ty, rotation, sx, sy, skew = mat_decompose(core)
    flip_v = sy < 0
    sy = abs(sy)
    base = current.scale if current.scale > 0 else 1.0
    from .visual_effects import TRANSFORM_PROPERTIES

    def bounded(name: str, value: float) -> float:
        return TRANSFORM_PROPERTIES[name].clamp(value)

    return replace(
        current,
        position_x=bounded("position_x", (tx - pw / 2.0) / cw),
        position_y=bounded("position_y", (ty - ph / 2.0) / ch),
        rotation=bounded("rotation", rotation),
        scale_x=bounded("scale_x", sx / base),
        scale_y=bounded("scale_y", sy / base),
        skew=bounded("skew", skew),
        flip_h=False,
        flip_v=flip_v,
    )


# ---------------------------------------------------------------------------
# Scène
# ---------------------------------------------------------------------------


_INT_FIELDS = frozenset({"width", "height", "stroke_width", "font_size", "shadow_offset_x", "shadow_offset_y"})

Measure = Callable[[GraphicOverlay], tuple[float, float]]


@dataclass(frozen=True)
class EvaluatedLayer:
    """État d'un calque à un instant (tout ce qu'il faut pour le dessiner)."""

    clip_id: str
    graphic: GraphicOverlay | None
    transform: EvaluatedTransform
    box: tuple[float, float]
    world: Matrix
    opacity: float
    masks: tuple
    active: bool

    def state_key(self) -> tuple:
        """Clé hachable du rendu de ce calque (caches de frames)."""
        return (
            self.clip_id, repr(self.graphic), tuple(round(v, 6) for v in self.world),
            round(self.opacity, 6), self.box, repr(self.masks),
        )


def stack_key(layer) -> tuple:
    """Ordre dans la pile : piste, rang ``z_order``, début, identifiant (bas → haut)."""
    graphic = getattr(layer, "graphic", None)
    z = graphic.z_order if isinstance(graphic, GraphicOverlay) else 0
    return (layer.track_index, z, layer.timeline_start, layer.clip_id)


class GraphicsScene:
    """Hiérarchie d'un plan de rendu, évaluable à tout instant.

    Args:
        layers: calques du plan (``RenderPlan.graphics_layers``), y compris
            les calques « rig » (parents hors fenêtre, clips vidéo parents).
        width / height: taille du cadre de la séquence (pixels).
        measure: mesure ``(w, h)`` d'un texte à taille automatique (Qt) ;
            sans mesure, la boîte déclarée est utilisée.
    """

    def __init__(self, layers: Iterable, width: int, height: int, *, measure: Measure | None = None) -> None:
        self.width = float(width)
        self.height = float(height)
        self.measure = measure
        self.layers: dict[str, Any] = {}
        for layer in layers:
            self.layers.setdefault(layer.clip_id, layer)
        self._curves: dict[str, dict] = {}
        self._memo: dict[tuple[str, float], EvaluatedLayer] = {}
        self._groups = self._resolve_groups()
        self._parents = self._resolve_parents()
        self._members: dict[str, list[str]] = {}
        for clip_id, layer in self.layers.items():
            group = self.group_of(clip_id)
            if group:
                self._members.setdefault(group, []).append(clip_id)
        for members in self._members.values():
            members.sort(key=lambda cid: stack_key(self.layers[cid]))

    # -- hiérarchie ---------------------------------------------------------------------------

    def _resolve_parents(self) -> dict[str, str]:
        parents: dict[str, str] = {}
        for clip_id, layer in self.layers.items():
            graphic = getattr(layer, "graphic", None)
            if not isinstance(graphic, GraphicOverlay):
                continue
            candidate = ""
            if graphic.parent_id and graphic.parent_id in self.layers and graphic.parent_id != clip_id:
                candidate = graphic.parent_id
            elif self._groups.get(clip_id):
                candidate = self._groups[clip_id]
            if candidate:
                parents[clip_id] = candidate
        graph = {clip_id: ({parent} if parent else set()) for clip_id, parent in parents.items()}
        for clip_id in self.layers:
            graph.setdefault(clip_id, set())
        for cycle in graph_cycles.find_cycles(graph):
            for clip_id in cycle:
                parents.pop(clip_id, None)  # projet corrompu : le calque devient racine
        return parents

    def _is_group(self, clip_id: str) -> bool:
        graphic = getattr(self.layers.get(clip_id), "graphic", None)
        return isinstance(graphic, GraphicOverlay) and graphic.type == GraphicType.GROUP

    def parent_of(self, clip_id: str) -> str:
        return self._parents.get(clip_id, "")

    def group_of(self, clip_id: str) -> str:
        """Groupe qui **contient** le calque (pas le parent de transform)."""
        return self._groups.get(clip_id, "")

    def _resolve_groups(self) -> dict[str, str]:
        groups: dict[str, str] = {}
        for clip_id, layer in self.layers.items():
            graphic = getattr(layer, "graphic", None)
            if not isinstance(graphic, GraphicOverlay) or not graphic.group_id:
                continue
            if graphic.group_id != clip_id and self._is_group(graphic.group_id):
                groups[clip_id] = graphic.group_id
        graph = {clip_id: {group} for clip_id, group in groups.items()}
        for cycle in graph_cycles.find_cycles(graph):
            for clip_id in cycle:
                groups.pop(clip_id, None)  # un groupe ne peut pas se contenir lui-même
        return groups

    def members(self, group_id: str) -> list[str]:
        return list(self._members.get(group_id, ()))

    def top_level(self) -> list[str]:
        """Calques dessinables hors groupe, du bas vers le haut de la pile."""
        ids = [
            clip_id for clip_id, layer in self.layers.items()
            if getattr(layer, "role", "draw") == "draw" and not self.group_of(clip_id)
        ]
        ids.sort(key=lambda cid: stack_key(self.layers[cid]))
        return ids

    def ancestors(self, clip_id: str) -> list[str]:
        result = []
        current = self.parent_of(clip_id)
        while current and current not in result:
            result.append(current)
            current = self.parent_of(current)
        return result

    # -- évaluation ---------------------------------------------------------------------------

    def _curves_for(self, clip_id: str) -> dict:
        curves = self._curves.get(clip_id)
        if curves is None:
            layer = self.layers[clip_id]
            curves = animation_curves(_AnimationView(getattr(layer, "animation", ())))
            self._curves[clip_id] = curves
        return curves

    def local_time(self, clip_id: str, t: float) -> float:
        layer = self.layers[clip_id]
        return float(t) - float(layer.timeline_start)

    def is_active(self, clip_id: str, t: float) -> bool:
        layer = self.layers[clip_id]
        if not (layer.timeline_start - 1e-9 <= t < layer.timeline_end - 1e-9):
            return False
        graphic = getattr(layer, "graphic", None)
        if isinstance(graphic, GraphicOverlay) and not graphic.visible:
            return False
        group = self.group_of(clip_id)
        if group:
            return self.is_active(group, t) and getattr(self.layers[group], "role", "draw") == "draw"
        return True

    def evaluated_graphic(self, clip_id: str, t: float) -> GraphicOverlay | None:
        layer = self.layers[clip_id]
        graphic = getattr(layer, "graphic", None)
        if not isinstance(graphic, GraphicOverlay):
            return None
        curves = self._curves_for(clip_id)
        local = self.local_time(clip_id, t)
        duration = layer.timeline_end - layer.timeline_start
        local = max(0.0, min(duration, local))
        values: dict[str, Any] = {}
        for property_id, curve in curves.items():
            if not property_id.startswith(GRAPHIC_PREFIX) or not curve:
                continue
            name = property_id[len(GRAPHIC_PREFIX):]
            if not hasattr(graphic, name):
                continue
            value = curve.evaluate(local)
            values[name] = int(round(value)) if name in _INT_FIELDS else float(value)
        if graphic.type == GraphicType.LIGHT:
            # Lumière procédurale : son image dépend de son temps local (au dix-millième de seconde, la clé du cache).
            from .light_layers import STATIC_KINDS

            if graphic.light_kind not in STATIC_KINDS:
                values["light_time"] = round(local, 4)
        if graphic.word_times:
            # Mot par mot ou karaoké posés par les temps des mots : ``reveal`` en découle (il prime sur sa courbe).
            from .text_runs import reveal_from_times, word_count

            timed = reveal_from_times(graphic.word_times, local, word_count(graphic.text), graphic.word_reveal)
            if timed is not None:
                values["reveal"] = timed
        if values:
            try:
                graphic = replace(graphic, **values)
            except (TypeError, ValueError):
                pass
        return graphic

    def box(self, clip_id: str, graphic: GraphicOverlay | None) -> tuple[float, float]:
        if graphic is None:  # clip vidéo parent : sa boîte est le cadre
            return (self.width, self.height)
        if graphic.type == GraphicType.TEXT and graphic.autosize and self.measure is not None:
            return self.measure(graphic)
        return (float(graphic.width), float(graphic.height))

    def evaluate(self, clip_id: str, t: float) -> EvaluatedLayer:
        key = (clip_id, round(float(t), 9))
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        layer = self.layers[clip_id]
        duration = max(0.0, layer.timeline_end - layer.timeline_start)
        local = self.local_time(clip_id, t)
        values = evaluate_transform(layer.transform, layer.transform_keyframes, local, duration or None)
        graphic = self.evaluated_graphic(clip_id, t)
        box = self.box(clip_id, graphic)
        parent = self.parent_of(clip_id)
        if parent:
            parent_eval = self.evaluate(parent, t)
            parent_world, parent_box = parent_eval.world, parent_eval.box
        else:
            parent_eval = None
            parent_world, parent_box = IDENTITY, (self.width, self.height)
        layout = graphic.layout if graphic is not None else LayerLayout.ANCHOR
        local_m = local_matrix(
            values, box, parent_box, (self.width, self.height),
            layout=layout, allow_skew=graphic is not None,
        )
        world = mat_mul(parent_world, local_m)
        opacity = float(values.opacity)
        # Opacité héritée : un contrôleur (null) transmet la sienne à ses enfants.
        if parent_eval is not None and _is_null(self.layers[parent].graphic):
            opacity *= parent_eval.opacity
        masks: tuple = ()
        compositing = getattr(layer, "compositing", None)
        if compositing is not None and compositing.masks:
            mask_curves = {k: v for k, v in self._curves_for(clip_id).items() if k.startswith("mask.")}
            masks = tuple(
                evaluate_mask_at(mask, mask_curves, max(0.0, min(duration, local)))
                for mask in compositing.masks
            )
        result = EvaluatedLayer(
            clip_id=clip_id, graphic=graphic, transform=values, box=box, world=world,
            opacity=opacity, masks=masks, active=self.is_active(clip_id, t),
        )
        if len(self._memo) > 20000:
            self._memo.clear()
        self._memo[key] = result
        return result

    def world_matrix(self, clip_id: str, t: float) -> Matrix:
        return self.evaluate(clip_id, t).world


def _is_null(graphic) -> bool:
    return isinstance(graphic, GraphicOverlay) and graphic.type == GraphicType.NULL


class _AnimationView:
    """Adaptateur : :func:`animation_curves` lit ``.animation``."""

    __slots__ = ("animation",)

    def __init__(self, animation) -> None:
        self.animation = tuple(animation or ())


__all__ = [
    "EvaluatedLayer", "GraphicsScene", "IDENTITY", "Matrix", "box_corners", "local_matrix",
    "map_box", "mat_apply", "mat_close", "mat_decompose", "mat_determinant", "mat_invert",
    "mat_mul", "mat_rotate", "mat_scale", "mat_scale_factor", "mat_skew", "mat_translate",
    "stack_key", "transform_from_world",
]
