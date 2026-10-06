"""Propriétés animables des calques motion graphics et des masques.

Importé par :mod:`core.animation_targets` : dès qu'un consommateur
(inspecteur, timeline, Graph Editor, rendu) ouvre le registre, ces
propriétés y figurent. Toutes stockent leurs keyframes dans
``Clip.animation`` (:func:`~core.animation_targets.generic_target`).

- ``graphic.<champ>`` : dimensions, contour, rayon d'angle, corps,
  approche (*tracking*), interlignage, ombre ;
- ``mask.<id>.<propriété>`` : une famille par masque (identifiant stable).
"""

from __future__ import annotations

from dataclasses import replace

from .animation import AnimatableProperty, ValueKind
from .animation_targets import generic_target, register_dynamic_targets, register_target
from .compositing import MASK_PROPERTY_ORDER, MASK_PROPERTY_SPECS, mask_property_id
from .graphics import GraphicOverlay, GraphicType

GRAPHIC_PREFIX = "graphic."

_TEXT = frozenset({GraphicType.TEXT})
_SHAPES = frozenset({GraphicType.SHAPE, GraphicType.RECTANGLE})
_SIZED = frozenset({
    GraphicType.TEXT, GraphicType.SHAPE, GraphicType.RECTANGLE, GraphicType.SOLID, GraphicType.IMAGE,
})

# (champ, défaut, minimum, maximum, pas, types concernés)
GRAPHIC_ANIMATABLE: tuple[tuple[str, float, float, float, float, frozenset], ...] = (
    ("width", 900.0, 2.0, 8192.0, 1.0, _SIZED),
    ("height", 180.0, 2.0, 8192.0, 1.0, _SIZED),
    ("corner_radius", 0.0, 0.0, 4096.0, 1.0, _SHAPES),
    ("stroke_width", 0.0, 0.0, 256.0, 1.0, _SHAPES | _TEXT),
    ("font_size", 64.0, 6.0, 512.0, 1.0, _TEXT),
    ("tracking", 0.0, -100.0, 500.0, 0.5, _TEXT),
    ("line_spacing", 1.0, 0.3, 5.0, 0.05, _TEXT),
    ("shadow_offset_x", 4.0, -256.0, 256.0, 1.0, _TEXT),
    ("shadow_offset_y", 4.0, -256.0, 256.0, 1.0, _TEXT),
    ("shadow_blur", 0.0, 0.0, 200.0, 0.5, _TEXT),
    ("reveal", 1.0, 0.0, 1.0, 0.01, _TEXT),
)
"""Propriétés intrinsèques animables (les couleurs restent statiques)."""

GRAPHIC_PROPERTY_SPECS: dict[str, AnimatableProperty] = {}


def graphic_property_id(name: str) -> str:
    return GRAPHIC_PREFIX + name


def _graphics_track(track_type: str) -> bool:
    return track_type == "graphics"


def _register_graphic(name: str, default: float, low: float, high: float, step: float, kinds) -> None:
    spec = AnimatableProperty(
        graphic_property_id(name), f"graphics.property.{name}", ValueKind.FLOAT,
        default, low, high, step, group="graphic",
    )
    GRAPHIC_PROPERTY_SPECS[spec.id] = spec

    def get_static(clip) -> float:
        return float(getattr(clip.graphic, name))

    def set_static(clip, value) -> None:
        from .graphics import update_graphic

        update_graphic(clip, name, spec.clamp(value))

    def applies_to_clip(clip) -> bool:
        graphic = getattr(clip, "graphic", None)
        return isinstance(graphic, GraphicOverlay) and graphic.type in kinds

    register_target(generic_target(
        spec, get_static=get_static, set_static=set_static,
        applies_to=_graphics_track, applies_to_clip=applies_to_clip,
    ))


for _entry in GRAPHIC_ANIMATABLE:
    _register_graphic(*_entry)


# ---------------------------------------------------------------------------
# Masques : ``mask.<id>.<propriété>``
# ---------------------------------------------------------------------------


def _visual_track(track_type: str) -> bool:
    return track_type in ("video", "graphics")


def _find_mask(clip, mask_id: str):
    compositing = getattr(clip, "compositing", None)
    return compositing.mask_by_id(mask_id) if compositing is not None else None


def _mask_target(property_id: str):
    parts = property_id.split(".")
    if len(parts) != 3 or parts[2] not in MASK_PROPERTY_SPECS:
        return None
    mask_id, name = parts[1], parts[2]
    base = MASK_PROPERTY_SPECS[name]
    spec = replace(base, id=property_id)

    def get_static(clip) -> float:
        mask = _find_mask(clip, mask_id)
        return float(getattr(mask, name)) if mask is not None else float(base.default)

    def set_static(clip, value) -> None:
        mask = _find_mask(clip, mask_id)
        if mask is None:
            raise KeyError(f"Masque introuvable : {mask_id!r}.")
        updated = replace(mask, **{name: base.clamp(value)}, id=mask.id)
        masks = tuple(updated if m.id == mask_id else m for m in clip.compositing.masks)
        clip.compositing = replace(clip.compositing, masks=masks)

    return generic_target(
        spec, get_static=get_static, set_static=set_static,
        applies_to=_visual_track, applies_to_clip=lambda clip: _find_mask(clip, mask_id) is not None,
    )


def _mask_ids(clip):
    compositing = getattr(clip, "compositing", None)
    for mask in getattr(compositing, "masks", ()) or ():
        for name in MASK_PROPERTY_ORDER:
            yield mask_property_id(mask.id, name)


register_dynamic_targets("mask.", _mask_target, _mask_ids)


__all__ = [
    "GRAPHIC_ANIMATABLE", "GRAPHIC_PREFIX", "GRAPHIC_PROPERTY_SPECS", "graphic_property_id",
]
