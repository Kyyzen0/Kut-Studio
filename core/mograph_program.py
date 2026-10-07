"""Découpage de la pile motion graphics en éléments de rendu.

Le rastériseur Qt sait aplatir des calques « normaux » entre eux, mais trois
choses doivent voir **tout ce qui est en dessous**, vidéo comprise, et
passent donc par FFmpeg :

- un calque en mode de fusion autre que Normal ;
- un calque (ou un groupe) qui porte des effets / un étalonnage ;
- un adjustment layer.

La pile (du bas vers le haut) devient une suite d'éléments :

``band``
    calques normaux consécutifs, aplatis en **un** flux RGBA. Composer des
    calques « par-dessus » est associatif : aplatir puis poser revient à
    poser un par un.
``layer``
    un calque (ou groupe) seul : flux RGBA → effets FFmpeg → fusion.
``adjustment``
    un adjustment layer : la composition en dessous est dupliquée, les
    effets s'appliquent à la copie, qui est reposée à travers la
    couverture du calque (opacité × masques).

Le découpage est déterministe et ne dépend que de la pile : l'aperçu
fidèle et l'export produisent les mêmes éléments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .blend_modes import BlendMode, coerce_blend_mode
from .graphics import GraphicOverlay, GraphicType
from .mograph_scene import GraphicsScene

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphicsElement:
    """Élément de rendu : un flux RGBA composé par FFmpeg."""

    kind: str  # "band" | "layer" | "adjustment"
    layer_ids: tuple[str, ...]
    start: float
    end: float
    blend: BlendMode = BlendMode.NORMAL
    effects: tuple = ()
    color_grade: object = None

    @property
    def clip_id(self) -> str:
        return self.layer_ids[0] if self.layer_ids else ""


def _grade_active(grade) -> bool:
    if grade is None:
        return False
    try:
        from .export_engine import _build_color_grade_filters

        return bool(_build_color_grade_filters(grade))
    except Exception:
        LOGGER.debug("Filtres d'étalonnage non construits : étalonnage traité comme actif", exc_info=True)
        return True


def _active_effects(layer) -> tuple:
    return tuple(effect for effect in getattr(layer, "effects", ()) or () if getattr(effect, "enabled", True))


def _span(scene: GraphicsScene, clip_ids) -> tuple[float, float]:
    starts = [scene.layers[cid].timeline_start for cid in clip_ids]
    ends = [scene.layers[cid].timeline_end for cid in clip_ids]
    return (min(starts), max(ends)) if starts else (0.0, 0.0)


def graphics_program(scene: GraphicsScene) -> list[GraphicsElement]:
    """Éléments de rendu de la pile, du bas vers le haut."""
    elements: list[GraphicsElement] = []
    band: list[str] = []

    def flush() -> None:
        if band:
            start, end = _span(scene, band)
            elements.append(GraphicsElement("band", tuple(band), start, end))
            band.clear()

    for clip_id in scene.top_level():
        layer = scene.layers[clip_id]
        graphic = layer.graphic
        if not isinstance(graphic, GraphicOverlay):
            continue
        if graphic.type == GraphicType.NULL:
            continue  # contrôleur : aucun pixel, ne coupe pas la bande
        blend = coerce_blend_mode(getattr(getattr(layer, "compositing", None), "blend_mode", "normal"))
        effects = _active_effects(layer)
        grade = getattr(layer, "color_grade", None)
        grade = grade if _grade_active(grade) else None
        if graphic.type == GraphicType.ADJUSTMENT:
            flush()
            if effects or grade is not None:
                elements.append(GraphicsElement(
                    "adjustment", (clip_id,), layer.timeline_start, layer.timeline_end,
                    effects=effects, color_grade=grade,
                ))
            continue
        if blend is not BlendMode.NORMAL or effects or grade is not None:
            flush()
            elements.append(GraphicsElement(
                "layer", (clip_id,), layer.timeline_start, layer.timeline_end,
                blend=blend, effects=effects, color_grade=grade,
            ))
            continue
        band.append(clip_id)
    flush()
    return elements


__all__ = ["GraphicsElement", "graphics_program"]
