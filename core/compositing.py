"""Masques, chroma-key et modes de fusion non destructifs.

Les valeurs sont normalisées (0..1) afin que le modèle soit indépendant de la
résolution. Le même état alimente l'aperçu et le graphe FFmpeg d'export.

Masques
-------

Un calque porte une **pile** de masques, combinés dans l'ordre :

- ``add`` : union additive (couverture ``a + m``, bornée à 1) ;
- ``subtract`` : retire la zone (``a × (1 − m)``) ;
- ``intersect`` : ne garde que l'intersection (``a × m``).

Le premier masque part d'un calque vide s'il est ``add``, plein sinon.
Chaque masque a une forme (rectangle, ellipse, polygone), un contour adouci
(``feather``), une dilatation / contraction (``expansion``), une opacité et
une inversion. Position, taille, rotation, contour, dilatation et opacité
sont animables par le moteur central (:mod:`core.animation`) : leurs
keyframes vivent dans ``Clip.animation`` sous l'identifiant
``mask.<id>.<propriété>``. La rastérisation (aperçu **et** export) est
faite par :mod:`core.mograph_raster`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields, replace
from enum import Enum
from typing import Any

from .animation import AnimatableProperty, InterpolationType, Keyframe, ValueKind
from .blend_modes import BlendMode, coerce_blend_mode


class MaskShape(str, Enum):
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    POLYGON = "polygon"


class MaskMode(str, Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    INTERSECT = "intersect"


MASK_PROPERTIES = frozenset(
    {"position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity"}
)

MASK_PROPERTY_ORDER: tuple[str, ...] = (
    "position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity",
)

_MASK_BOUNDS: dict[str, tuple[float, float]] = {
    "position_x": (-2.0, 3.0),
    "position_y": (-2.0, 3.0),
    "width": (0.0, 4.0),
    "height": (0.0, 4.0),
    "rotation": (-3600.0, 3600.0),
    "feather": (0.0, 1.0),
    "expansion": (-1.0, 1.0),
    "opacity": (0.0, 1.0),
}

MASK_PROPERTY_SPECS: dict[str, AnimatableProperty] = {
    name: AnimatableProperty(
        name, f"mask.property.{name}", ValueKind.FLOAT,
        {"position_x": .5, "position_y": .5, "width": .5, "height": .5, "opacity": 1.0}.get(name, 0.0),
        low, high, 1.0 if name == "rotation" else 0.01, group="mask",
    )
    for name, (low, high) in _MASK_BOUNDS.items()
}
"""Description des propriétés animables d'un masque (bornes, défauts)."""

DEFAULT_POLYGON: tuple[tuple[float, float], ...] = (
    (0.0, -0.5), (0.5, -0.15), (0.31, 0.5), (-0.31, 0.5), (-0.5, -0.15),
)
"""Pentagone par défaut. Les sommets sont exprimés dans la boîte du masque :
``(-0.5, -0.5)`` = coin haut-gauche, ``(0.5, 0.5)`` = coin bas-droit."""


def new_mask_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class MaskKeyframe:
    """Image-clé **historique** d'un masque (format v12, linéaire).

    Conservée pour lire les anciens projets ; :func:`migrate_legacy_mask_keyframes`
    la convertit en keyframes du moteur central à l'ouverture.
    """

    property_name: str
    time_seconds: float
    value: float

    def __post_init__(self):
        if self.property_name not in MASK_PROPERTIES:
            raise ValueError(f"Propriété de masque inconnue : {self.property_name}")
        if self.time_seconds < 0:
            raise ValueError("Le temps d'une image-clé doit être positif.")


def _clamp(name: str, value: object) -> float:
    low, high = _MASK_BOUNDS[name]
    number = float(value)
    if number != number:
        number = MASK_PROPERTY_SPECS[name].default
    return max(low, min(high, number))


def _points(raw: object) -> tuple[tuple[float, float], ...]:
    result = []
    for item in raw or ():
        try:
            x, y = item
            result.append((max(-2.0, min(2.0, float(x))), max(-2.0, min(2.0, float(y)))))
        except (TypeError, ValueError):
            continue
    return tuple(result)


@dataclass(frozen=True)
class Mask:
    shape: MaskShape = MaskShape.RECTANGLE
    position_x: float = .5
    position_y: float = .5
    width: float = .5
    height: float = .5
    rotation: float = 0.0
    feather: float = 0.0
    expansion: float = 0.0
    opacity: float = 1.0
    inverted: bool = False
    keyframes: tuple[MaskKeyframe, ...] = field(default_factory=tuple)
    mode: MaskMode = MaskMode.ADD
    points: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    name: str = ""
    id: str = field(default="", compare=False)

    def __post_init__(self):
        object.__setattr__(self, "shape", MaskShape(self.shape))
        try:
            mode = MaskMode(self.mode)
        except ValueError:
            mode = MaskMode.ADD
        object.__setattr__(self, "mode", mode)
        for name in MASK_PROPERTY_ORDER:
            object.__setattr__(self, name, _clamp(name, getattr(self, name)))
        object.__setattr__(self, "inverted", bool(self.inverted))
        object.__setattr__(self, "keyframes", tuple(sorted(self.keyframes, key=lambda k: (k.property_name, k.time_seconds))))
        points = _points(self.points)
        if self.shape is MaskShape.POLYGON and len(points) < 3:
            points = DEFAULT_POLYGON
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "name", str(self.name or ""))
        if not self.id:
            object.__setattr__(self, "id", new_mask_id())


@dataclass(frozen=True)
class ChromaKey:
    enabled: bool = False
    color: str = "#00FF00"
    tolerance: float = .1
    softness: float = .05
    spill_suppression: float = 0.0

    def __post_init__(self):
        color = str(self.color).upper()
        if len(color) != 7 or not color.startswith("#"):
            color = "#00FF00"
        object.__setattr__(self, "color", color)
        for name in ("tolerance", "softness", "spill_suppression"):
            object.__setattr__(self, name, max(0.0, min(1.0, float(getattr(self, name)))))


@dataclass(frozen=True)
class Compositing:
    masks: tuple[Mask, ...] = field(default_factory=tuple)
    chroma_key: ChromaKey = field(default_factory=ChromaKey)
    blend_mode: BlendMode = BlendMode.NORMAL

    def __post_init__(self):
        object.__setattr__(self, "masks", tuple(self.masks))
        object.__setattr__(self, "blend_mode", coerce_blend_mode(self.blend_mode))

    def mask_by_id(self, mask_id: str) -> Mask | None:
        return next((mask for mask in self.masks if mask.id == mask_id), None)

    @property
    def has_masks(self) -> bool:
        return bool(self.masks)


def evaluate_mask(mask: Mask, time_seconds: float) -> Mask:
    """Évalue linéairement les images-clés **historiques** d'un masque."""
    values: dict[str, float] = {}
    for prop in MASK_PROPERTIES:
        frames = [k for k in mask.keyframes if k.property_name == prop]
        if not frames:
            continue
        base = float(getattr(mask, prop)); t = max(0.0, float(time_seconds))
        if t < frames[0].time_seconds:
            values[prop] = base
        elif t >= frames[-1].time_seconds:
            values[prop] = frames[-1].value
        else:
            left, right = next((a, b) for a, b in zip(frames, frames[1:]) if a.time_seconds <= t < b.time_seconds)
            ratio = (t-left.time_seconds)/(right.time_seconds-left.time_seconds)
            values[prop] = left.value + (right.value-left.value)*ratio
    return replace(mask, keyframes=(), **values, id=mask.id)


def mask_property_id(mask_id: str, name: str) -> str:
    """Identifiant animable d'une propriété de masque : ``mask.<id>.<nom>``."""
    return f"mask.{mask_id}.{name}"


def evaluate_mask_at(mask: Mask, curves: dict, time_seconds: float) -> Mask:
    """Masque à ``time_seconds`` : courbes du moteur central (``Clip.animation``).

    ``curves`` : ``{identifiant: AnimationCurve}`` (voir
    :func:`core.animation_targets.animation_curves`). Les images-clés
    historiques éventuelles sont appliquées d'abord.
    """
    current = evaluate_mask(mask, time_seconds) if mask.keyframes else mask
    values: dict[str, float] = {}
    for name in MASK_PROPERTY_ORDER:
        curve = curves.get(mask_property_id(mask.id, name))
        if curve:
            values[name] = MASK_PROPERTY_SPECS[name].evaluate(curve, getattr(current, name), time_seconds)
    if not values:
        return current
    return replace(current, **values, id=mask.id)


def migrate_legacy_mask_keyframes(value: Compositing) -> tuple[Compositing, list[Keyframe]]:
    """Convertit les images-clés historiques des masques sans changer leur courbe.

    L'ancien modèle gardait la valeur de base avant la première image-clé :
    un keyframe ``hold`` à 0 portant la base reproduit ce comportement
    (comme :func:`core.visual_effects.migrate_legacy_keyframes`).
    """
    if not any(mask.keyframes for mask in value.masks):
        return value, []
    generic: list[Keyframe] = []
    masks = []
    for mask in value.masks:
        by_property: dict[str, list[MaskKeyframe]] = {}
        for kf in mask.keyframes:
            by_property.setdefault(kf.property_name, []).append(kf)
        for name, frames in by_property.items():
            pid = mask_property_id(mask.id, name)
            frames.sort(key=lambda k: k.time_seconds)
            base = float(getattr(mask, name))
            if frames[0].time_seconds > 0 and abs(frames[0].value - base) > 1e-12:
                generic.append(Keyframe(pid, 0.0, base, InterpolationType.HOLD))
            generic.extend(Keyframe(pid, kf.time_seconds, _clamp(name, kf.value)) for kf in frames)
        masks.append(replace(mask, keyframes=(), id=mask.id))
    return replace(value, masks=tuple(masks)), generic


_MASK_FIELDS = frozenset(f.name for f in fields(Mask))


def mask_to_dict(m: Mask) -> dict[str, Any]:
    data = {k: v for k, v in vars(m).items() if k not in {"keyframes", "points"}}
    data["shape"] = m.shape.value
    data["mode"] = m.mode.value
    data["keyframes"] = [vars(k) for k in m.keyframes]
    if m.points:
        data["points"] = [list(p) for p in m.points]
    return data


def mask_from_dict(raw: dict) -> Mask:
    kwargs = {k: v for k, v in raw.items() if k in _MASK_FIELDS and k not in {"keyframes", "points"}}
    kwargs["keyframes"] = tuple(
        MaskKeyframe(**k) for k in raw.get("keyframes", []) or [] if isinstance(k, dict)
    )
    kwargs["points"] = _points(raw.get("points"))
    return Mask(**kwargs)


def compositing_to_dict(value: Compositing) -> dict[str, Any]:
    return {"blend_mode": value.blend_mode.value, "chroma_key": vars(value.chroma_key),
            "masks": [mask_to_dict(m) for m in value.masks]}


def compositing_from_dict(raw: Any) -> Compositing:
    if not isinstance(raw, dict):
        return Compositing()
    try:
        masks = []
        for m in raw.get("masks", []) or []:
            if not isinstance(m, dict):
                continue
            try:
                masks.append(mask_from_dict(m))
            except (TypeError, ValueError):
                continue  # un masque corrompu est ignoré, pas tout le compositing
        return Compositing(masks=tuple(masks), chroma_key=ChromaKey(**(raw.get("chroma_key") or {})),
                           blend_mode=raw.get("blend_mode", "normal"))
    except (TypeError, ValueError):
        return Compositing()


def chroma_key_filters(value: Compositing) -> list[str]:
    """Filtres FFmpeg d'incrustation (chroma-key + suppression du débordement)."""
    key = value.chroma_key
    if not key.enabled:
        return []
    result = [f"chromakey=0x{key.color[1:]}:{key.tolerance:.6g}:{key.softness:.6g}"]
    if key.spill_suppression:
        result.append(f"despill=green:mix={key.spill_suppression:.6g}")
    return result


def build_ffmpeg_filters(value: Compositing, width: int, height: int) -> list[str]:
    """Filtres alpha/chroma d'un clip (masques **statiques** en expressions ``geq``).

    Conservé pour les consommateurs historiques. L'export passe désormais par
    une matte rastérisée (:mod:`core.mograph_raster`) qui gère contour adouci,
    rotation, polygones, opérations et animation.
    """
    result = ["format=rgba", *chroma_key_filters(value)]
    # Les expressions utilisent les coordonnées du canvas et peuvent donc être
    # partagées par le rendu fidèle et les exports de toute résolution.
    for mask in value.masks:
        m = evaluate_mask(mask, 0.0)
        cx, cy = m.position_x*width, m.position_y*height
        rx = max(1.0, (m.width+m.expansion)*width/2); ry = max(1.0, (m.height+m.expansion)*height/2)
        if m.shape == MaskShape.ELLIPSE:
            inside = f"lte(pow((X-{cx:.6g})/{rx:.6g},2)+pow((Y-{cy:.6g})/{ry:.6g},2),1)"
        else:
            inside = f"between(X,{cx-rx:.6g},{cx+rx:.6g})*between(Y,{cy-ry:.6g},{cy+ry:.6g})"
        if m.inverted: inside = f"1-({inside})"
        result.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({inside})*{m.opacity:.6g}'")
    return result


__all__ = [
    "BlendMode", "ChromaKey", "Compositing", "DEFAULT_POLYGON", "MASK_PROPERTIES",
    "MASK_PROPERTY_ORDER", "MASK_PROPERTY_SPECS", "Mask", "MaskKeyframe", "MaskMode",
    "MaskShape", "build_ffmpeg_filters", "chroma_key_filters", "compositing_from_dict",
    "compositing_to_dict", "evaluate_mask", "evaluate_mask_at", "mask_from_dict",
    "mask_property_id", "mask_to_dict", "migrate_legacy_mask_keyframes", "new_mask_id",
]
