"""Guides utilisateur, zones de sécurité et magnétisme du viewer.

Tout ce module est **visuel** : rien ici n'est jamais rendu dans un
export. Les guides sont sauvegardés avec leur séquence (``.kut``) ; les
zones de sécurité et la grille sont des réglages d'affichage.

Les coordonnées sont normalisées au cadre (0 = bord haut/gauche, 1 = bord
bas/droit) : un guide reste à sa place si la résolution change.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from enum import Enum


class GuideOrientation(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


@dataclass(frozen=True)
class Guide:
    """Guide horizontal (``position`` = y normalisé) ou vertical (x normalisé)."""

    orientation: GuideOrientation = GuideOrientation.VERTICAL
    position: float = 0.5
    locked: bool = False
    id: str = ""

    def __post_init__(self) -> None:
        try:
            orientation = GuideOrientation(self.orientation)
        except ValueError:
            orientation = GuideOrientation.VERTICAL
        object.__setattr__(self, "orientation", orientation)
        try:
            position = float(self.position)
        except (TypeError, ValueError):
            position = 0.5
        object.__setattr__(self, "position", max(-1.0, min(2.0, position if position == position else 0.5)))
        object.__setattr__(self, "locked", bool(self.locked))
        if not self.id:
            object.__setattr__(self, "id", uuid.uuid4().hex[:8])


def guide_to_dict(guide: Guide) -> dict:
    return {"id": guide.id, "orientation": guide.orientation.value,
            "position": guide.position, "locked": guide.locked}


def guide_from_dict(raw: object) -> Guide | None:
    if not isinstance(raw, dict):
        return None
    return Guide(
        orientation=raw.get("orientation", "vertical"),
        position=raw.get("position", 0.5),
        locked=raw.get("locked", False),
        id=str(raw.get("id") or ""),
    )


# ---------------------------------------------------------------------------
# Opérations (pures) sur les guides d'une séquence
# ---------------------------------------------------------------------------


def add_guide(sequence, orientation: GuideOrientation | str, position: float) -> Guide:
    guide = Guide(orientation=orientation, position=position)
    sequence.guides = [*sequence.guides, guide]
    return guide


def move_guide(sequence, guide_id: str, position: float) -> Guide:
    guide = _require(sequence, guide_id)
    if guide.locked:
        raise ValueError("Ce guide est verrouillé.")
    moved = replace(guide, position=position)
    sequence.guides = [moved if g.id == guide_id else g for g in sequence.guides]
    return moved


def set_guide_locked(sequence, guide_id: str, locked: bool) -> Guide:
    guide = replace(_require(sequence, guide_id), locked=locked)
    sequence.guides = [guide if g.id == guide_id else g for g in sequence.guides]
    return guide


def remove_guide(sequence, guide_id: str) -> None:
    guide = _require(sequence, guide_id)
    if guide.locked:
        raise ValueError("Ce guide est verrouillé.")
    sequence.guides = [g for g in sequence.guides if g.id != guide_id]


def clear_guides(sequence) -> int:
    kept = [g for g in sequence.guides if g.locked]
    removed = len(sequence.guides) - len(kept)
    sequence.guides = kept
    return removed


def _require(sequence, guide_id: str) -> Guide:
    for guide in sequence.guides:
        if guide.id == guide_id:
            return guide
    raise KeyError(f"Guide introuvable : {guide_id!r}.")


# ---------------------------------------------------------------------------
# Zones de sécurité
# ---------------------------------------------------------------------------

SAFE_AREA_PRESETS: dict[str, dict[str, float]] = {
    # Marges (fraction de la largeur et de la hauteur) : SMPTE ST 2046-1
    # pour le 16:9 ; usages courants des plateformes pour le vertical.
    "16:9": {"action": 0.035, "title": 0.05},
    "9:16": {"action": 0.06, "title": 0.10},
    "1:1": {"action": 0.05, "title": 0.08},
}


def safe_area_preset_for(width: int, height: int) -> str:
    """Préréglage adapté au rapport du cadre."""
    ratio = float(width) / max(1.0, float(height))
    if ratio < 0.8:
        return "9:16"
    if ratio < 1.25:
        return "1:1"
    return "16:9"


def safe_area_rects(width: float, height: float, preset: str = "") -> dict[str, tuple[float, float, float, float]]:
    """Rectangles ``(x, y, w, h)`` en pixels de ``action`` et ``title``."""
    margins = SAFE_AREA_PRESETS.get(preset or safe_area_preset_for(int(width), int(height)), SAFE_AREA_PRESETS["16:9"])
    result = {}
    for name, margin in margins.items():
        dx, dy = width * margin, height * margin
        result[name] = (dx, dy, width - 2 * dx, height - 2 * dy)
    return result


# ---------------------------------------------------------------------------
# Magnétisme
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapLine:
    """Ligne de magnétisme active (affichée temporairement pendant un glisser)."""

    orientation: GuideOrientation
    position: float  # pixels du cadre
    source: str  # "frame", "center", "guide", "layer"


def snap_candidates(
    width: float,
    height: float,
    *,
    guides=(),
    layer_boxes=(),
) -> tuple[list[tuple[float, str]], list[tuple[float, str]]]:
    """Positions de magnétisme ``(x…)``, ``(y…)`` en pixels avec leur source.

    Bords et centre du cadre, guides, et bords / centres des autres calques
    (``layer_boxes`` : rectangles alignés ``(x0, y0, x1, y1)``).
    """
    xs: list[tuple[float, str]] = [(0.0, "frame"), (width / 2.0, "center"), (float(width), "frame")]
    ys: list[tuple[float, str]] = [(0.0, "frame"), (height / 2.0, "center"), (float(height), "frame")]
    for guide in guides:
        if guide.orientation is GuideOrientation.VERTICAL:
            xs.append((guide.position * width, "guide"))
        else:
            ys.append((guide.position * height, "guide"))
    for x0, y0, x1, y1 in layer_boxes:
        xs.extend([(x0, "layer"), ((x0 + x1) / 2.0, "layer"), (x1, "layer")])
        ys.extend([(y0, "layer"), ((y0 + y1) / 2.0, "layer"), (y1, "layer")])
    return xs, ys


def snap_value(values: list[float], candidates: list[tuple[float, str]], threshold: float):
    """Meilleur ajustement : ``(delta, position, source)`` ou ``None``.

    ``values`` : positions du calque déplacé (bord gauche, centre, bord
    droit…). Le plus petit écart sous ``threshold`` gagne.
    """
    best = None
    for value in values:
        for position, source in candidates:
            delta = position - value
            if abs(delta) <= threshold and (best is None or abs(delta) < abs(best[0])):
                best = (delta, position, source)
    return best


def snap_box(
    box: tuple[float, float, float, float],
    width: float,
    height: float,
    *,
    threshold: float,
    guides=(),
    layer_boxes=(),
) -> tuple[float, float, list[SnapLine]]:
    """Décalage ``(dx, dy)`` qui aimante ``box`` et lignes à afficher."""
    xs, ys = snap_candidates(width, height, guides=guides, layer_boxes=layer_boxes)
    x0, y0, x1, y1 = box
    lines: list[SnapLine] = []
    dx = dy = 0.0
    hit_x = snap_value([x0, (x0 + x1) / 2.0, x1], xs, threshold)
    if hit_x is not None:
        dx = hit_x[0]
        lines.append(SnapLine(GuideOrientation.VERTICAL, hit_x[1], hit_x[2]))
    hit_y = snap_value([y0, (y0 + y1) / 2.0, y1], ys, threshold)
    if hit_y is not None:
        dy = hit_y[0]
        lines.append(SnapLine(GuideOrientation.HORIZONTAL, hit_y[1], hit_y[2]))
    return dx, dy, lines


__all__ = [
    "Guide", "GuideOrientation", "SAFE_AREA_PRESETS", "SnapLine", "add_guide", "clear_guides",
    "guide_from_dict", "guide_to_dict", "move_guide", "remove_guide", "safe_area_preset_for",
    "safe_area_rects", "set_guide_locked", "snap_box", "snap_candidates", "snap_value",
]
