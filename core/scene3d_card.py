"""Carte de substitution d'un clip « scène 3D » dont le fichier manque ou ne se lit pas (lot B2b, ADR-0009).

Même politique qu'un média manquant (``docs/architecture.md``, « Politique d'erreur ») : **tolérant à l'écran** (une carte de
texte, à la place et à la taille du calque, animée comme lui ; l'aperçu ne lève jamais), **strict à l'export** (le plan porte le
fichier dans ``missing_media`` et l'export est refusé avec un message qui le nomme).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .graphics import GraphicOverlay, GraphicType
from .scene3d import problem

if TYPE_CHECKING:
    from .project_model import Clip
    from .render_plan import GraphicLayer

CARD_TEXT = "Scène 3D indisponible"
_CARD_INK = "#0B1220"
_CARD_BLUE = "#7FB4FF"


def scene3d_card(graphic: GraphicOverlay, reason: str) -> GraphicOverlay:
    """Carte de texte de la taille et de la place du calque 3D (hiérarchie, pile et mise en page conservées)."""
    side = min(graphic.width, graphic.height)
    return replace(
        graphic, type=GraphicType.TEXT, text=reason, source_path="", scene_time=0.0,
        font_size=max(12, round(side * 0.09)), fill_color=_CARD_BLUE, stroke_width=0, shadow_color="#00000000",
        shadow_offset_x=0, shadow_offset_y=0, background_enabled=True, background_color=_CARD_INK,
        background_padding=max(4, round(side * 0.05)), background_radius=float(round(side * 0.03)),
    )


def checked_scene3d_layer(layer: GraphicLayer, clip: Clip) -> tuple[GraphicLayer, tuple[str, str] | None]:
    """``(calque, problème)`` : le calque tel quel si la scène se lit, sinon sa carte de substitution.

    ``problème`` = ``(avertissement, élément de missing_media)``, ou ``None``. Jamais d'exception.
    """
    graphic = layer.graphic
    if not isinstance(graphic, GraphicOverlay) or graphic.type != GraphicType.SCENE3D:
        return layer, None
    issue = problem(graphic.source_path)
    if issue is None:
        return layer, None
    name = Path(graphic.source_path).name
    short_name = name if len(name) <= 80 else name[:79] + "…"
    warning = f"Scène 3D « {short_name} » (clip « {clip.label or clip.id} ») : {issue}"
    return replace(layer, graphic=scene3d_card(graphic, CARD_TEXT)), (warning, f"scène 3D « {short_name} »")


__all__ = ["CARD_TEXT", "checked_scene3d_layer", "scene3d_card"]
