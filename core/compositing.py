"""Masques, chroma-key et modes de fusion non destructifs.

Les valeurs sont normalisées (0..1) afin que le modèle soit indépendant de la
résolution. Le même état alimente l'aperçu et le graphe FFmpeg d'export.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable


class MaskShape(str, Enum):
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"


class BlendMode(str, Enum):
    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    ADD = "addition"


MASK_PROPERTIES = frozenset(
    {"position_x", "position_y", "width", "height", "rotation", "feather", "expansion", "opacity"}
)


@dataclass(frozen=True)
class MaskKeyframe:
    property_name: str
    time_seconds: float
    value: float

    def __post_init__(self):
        if self.property_name not in MASK_PROPERTIES:
            raise ValueError(f"Propriété de masque inconnue : {self.property_name}")
        if self.time_seconds < 0:
            raise ValueError("Le temps d'une image-clé doit être positif.")


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

    def __post_init__(self):
        object.__setattr__(self, "shape", MaskShape(self.shape))
        for name in ("position_x", "position_y", "width", "height", "feather", "opacity"):
            object.__setattr__(self, name, max(0.0, min(1.0, float(getattr(self, name)))))
        object.__setattr__(self, "expansion", max(-1.0, min(1.0, float(self.expansion))))
        object.__setattr__(self, "keyframes", tuple(sorted(self.keyframes, key=lambda k: (k.property_name, k.time_seconds))))


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
        object.__setattr__(self, "blend_mode", BlendMode(self.blend_mode))


def evaluate_mask(mask: Mask, time_seconds: float) -> Mask:
    """Évalue linéairement toutes les propriétés animées d'un masque."""
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
    return replace(mask, keyframes=(), **values)


def compositing_to_dict(value: Compositing) -> dict[str, Any]:
    return {"blend_mode": value.blend_mode.value, "chroma_key": vars(value.chroma_key), "masks": [
        {**{k: v for k, v in vars(m).items() if k != "keyframes"}, "shape": m.shape.value,
         "keyframes": [vars(k) for k in m.keyframes]} for m in value.masks]}


def compositing_from_dict(raw: Any) -> Compositing:
    if not isinstance(raw, dict):
        return Compositing()
    try:
        masks = tuple(Mask(**{**m, "keyframes": tuple(MaskKeyframe(**k) for k in m.get("keyframes", []))})
                      for m in raw.get("masks", []) if isinstance(m, dict))
        return Compositing(masks=masks, chroma_key=ChromaKey(**(raw.get("chroma_key") or {})),
                           blend_mode=raw.get("blend_mode", "normal"))
    except (TypeError, ValueError):
        return Compositing()


def build_ffmpeg_filters(value: Compositing, width: int, height: int) -> list[str]:
    """Produit les filtres alpha/chroma appliqués au flux RGBA d'un clip."""
    result = ["format=rgba"]
    key = value.chroma_key
    if key.enabled:
        result.append(f"chromakey=0x{key.color[1:]}:{key.tolerance:.6g}:{key.softness:.6g}")
        if key.spill_suppression:
            result.append(f"despill=green:mix={key.spill_suppression:.6g}")
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

