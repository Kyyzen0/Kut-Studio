"""Classement animé : un tableau (rang, nom, valeur, couleur) devient des lignes qui glissent l'une après l'autre.

Chaque ligne est faite de calques ordinaires (panneau, barre d'accent, trois textes) : on peut les retoucher un par un.
Le tableau lui-même est gardé dans la séquence (``Sequence.generated_groups``) avec la liste des calques qu'il a
produits : le rééditer remplace ces calques par ceux du nouveau tableau, au même endroit et au même moment.

La mise en page est dessinée pour un cadre 1080×1920 puis mise à l'échelle du cadre réel (comme un média ajusté) :
le même tableau tient dans un 9:16, un 4:5 ou un 1:1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .animation import InterpolationType
from .graphics import GraphicOverlay, GraphicType, ShapeKind, add_graphic_clip
from .project_model import Clip, Project
from .timeline_operations import delete_clip
from .visual_effects import ClipTransform, TransformKeyframe

KIND = "leaderboard"
DESIGN_W, DESIGN_H = 1080, 1920
PANEL_W, PANEL_H, ROW_STEP = 860, 116, 142
INK, PANEL = "#05060A", "#0B0F1ACC"
FONT = "Anton"
STAGGER, SLIDE_SECONDS = 0.25, 0.28
MAX_ROWS = 10


class LeaderboardError(ValueError):
    """Tableau inexploitable (aucune ligne, trop de lignes, durée nulle)."""


@dataclass(frozen=True)
class LeaderboardRow:
    rank: str
    name: str
    value: str
    color: str = "#22B8FF"


@dataclass(frozen=True)
class Leaderboard:
    """Le tableau et sa place : début et durée (secondes de la timeline), centre de la première ligne (fraction de la
    hauteur du cadre, 0 = milieu)."""

    rows: tuple[LeaderboardRow, ...]
    start: float = 0.0
    duration: float = 6.0
    top: float = -0.105
    delay: float = 0.3
    id: str = field(default_factory=lambda: f"lb-{uuid.uuid4().hex[:8]}")

    def __post_init__(self) -> None:
        if not self.rows:
            raise LeaderboardError("Un classement a au moins une ligne.")
        if len(self.rows) > MAX_ROWS:
            raise LeaderboardError(f"Un classement a au plus {MAX_ROWS} lignes.")
        if self.duration <= self.delay + STAGGER * (len(self.rows) - 1):
            raise LeaderboardError("Le classement est trop court pour que toutes ses lignes entrent.")


def leaderboard_to_dict(board: Leaderboard) -> dict:
    return {"rows": [[row.rank, row.name, row.value, row.color] for row in board.rows], "start": board.start,
            "duration": board.duration, "top": board.top, "delay": board.delay}


def leaderboard_from_dict(group_id: str, data: dict) -> Leaderboard:
    rows = tuple(LeaderboardRow(*(str(value) for value in row[:4])) for row in data.get("rows", ())
                 if isinstance(row, (list, tuple)) and len(row) >= 4)
    return Leaderboard(rows, float(data.get("start", 0.0)), float(data.get("duration", 6.0)),
                       float(data.get("top", -0.105)), float(data.get("delay", 0.3)), id=group_id)


def _unit(project: Project) -> float:
    return min(project.width / DESIGN_W, project.height / DESIGN_H)


def _place(clip: Clip, project: Project, x_px: float, y_px: float, start_x: float) -> None:
    """Position (pixels de la maquette, depuis le centre) et glissé depuis la droite."""
    u = _unit(project)
    x, y = x_px * u / project.width, y_px * u / project.height
    clip.transform = ClipTransform(position_x=x, position_y=y)
    clip.transform_keyframes = [
        TransformKeyframe("position_x", 0.0, x + start_x, InterpolationType.EASE_OUT),
        TransformKeyframe("position_x", SLIDE_SECONDS, x, InterpolationType.LINEAR),
        TransformKeyframe("opacity", 0.0, 0.0, InterpolationType.LINEAR),
        TransformKeyframe("opacity", 0.1, 1.0, InterpolationType.LINEAR),
    ]


def _text(text: str, color: str, size: int, box_w: int, align: str, u: float) -> GraphicOverlay:
    return GraphicOverlay(
        type=GraphicType.TEXT, text=text, font_family=FONT, font_size=max(8, round(size * u)), fill_color=color,
        stroke_color=INK, stroke_width=max(1, round(2 * u)), shadow_color="#000000C0", shadow_offset_x=0,
        shadow_offset_y=max(1, round(4 * u)), shadow_blur=10.0 * u, width=max(8, round(box_w * u)),
        height=max(8, round(PANEL_H * u)), align_h=align, align_v="center",
    )


def build_leaderboard(project: Project, board: Leaderboard) -> list[Clip]:
    """Pose les calques du classement et le mémorise dans la séquence active ; retourne les calques créés."""
    u = _unit(project)
    left = -PANEL_W / 2
    clips: list[Clip] = []
    for index, row in enumerate(board.rows):
        at = board.start + board.delay + index * STAGGER
        length = board.start + board.duration - at
        y_px = board.top * DESIGN_H + index * ROW_STEP
        parts = (
            (GraphicOverlay(type=GraphicType.SHAPE, shape=ShapeKind.ROUNDED_RECTANGLE, width=round(PANEL_W * u),
                            height=round(PANEL_H * u), fill_color=PANEL, corner_radius=18.0 * u, stroke_width=0,
                            shadow_color="#00000000"), 0.0, "panel"),
            (GraphicOverlay(type=GraphicType.SHAPE, shape=ShapeKind.ROUNDED_RECTANGLE, width=max(2, round(18 * u)),
                            height=round(PANEL_H * u), fill_color=row.color, corner_radius=6.0 * u, stroke_width=0,
                            shadow_color="#00000000"), left + 18, "accent"),
            (_text(row.rank, row.color, 64, 90, "center", u), left + 72, "rank"),
            (_text(row.name, "#FFFFFF", 64, 480, "left", u), left + 130 + 240, "name"),
            (_text(row.value, row.color, 58, 300, "right", u), left + PANEL_W - 34 - 150, "value"),
        )
        for graphic, x_px, part in parts:
            clip = add_graphic_clip(project, graphic.type.value, timeline_start=at, duration=length, graphic=graphic)
            clip.label = f"{row.rank} · {part}"
            _place(clip, project, x_px, y_px, 0.9)
            clips.append(clip)
    project.active_sequence.generated_groups[board.id] = {
        "kind": KIND, "data": leaderboard_to_dict(board), "clips": [clip.id for clip in clips]}
    return clips


def leaderboard_of(project: Project, clip_id: str) -> Leaderboard | None:
    """Le classement dont ce calque fait partie (pour le rééditer), sinon ``None``."""
    for group_id, group in project.active_sequence.generated_groups.items():
        if group.get("kind") == KIND and clip_id in group.get("clips", ()):
            return leaderboard_from_dict(group_id, group.get("data", {}))
    return None


def update_leaderboard(project: Project, board: Leaderboard) -> list[Clip]:
    """Remplace les calques d'un classement existant par ceux du nouveau tableau (même identifiant)."""
    group = project.active_sequence.generated_groups.pop(board.id, None)
    for clip_id in (group or {}).get("clips", ()):
        try:
            delete_clip(project, clip_id)
        except KeyError:                                  # calque déjà supprimé à la main
            continue
    return build_leaderboard(project, board)


__all__ = [
    "KIND", "Leaderboard", "LeaderboardError", "LeaderboardRow", "build_leaderboard", "leaderboard_from_dict",
    "leaderboard_of", "leaderboard_to_dict", "update_leaderboard",
]
