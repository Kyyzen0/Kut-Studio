"""Calques graphiques non destructifs (tâche 32).

Les graphiques sont de vrais clips placés sur une piste ``graphics``.
Leur durée, leur transform et leurs images-clés utilisent donc les mêmes
primitives que les clips vidéo. Ce module reste pur : aucune dépendance Qt.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .project_model import Clip, MediaAsset, Project, Track


class GraphicType(str, Enum):
    TEXT = "text"
    RECTANGLE = "rectangle"
    SOLID = "solid"
    IMAGE = "image"


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")


def normalize_color(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text.upper() if _HEX_COLOR.fullmatch(text) else fallback


@dataclass(frozen=True)
class GraphicOverlay:
    """Contenu et apparence d'un clip graphique.

    La géométrie animable (position, échelle, rotation, opacité) reste dans
    ``Clip.transform``. Ici vivent uniquement les propriétés intrinsèques du
    calque, ce qui évite deux sources de vérité.
    """

    type: GraphicType = GraphicType.TEXT
    text: str = "Titre"
    source_path: str = ""
    width: int = 900
    height: int = 180
    fill_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = 0
    shadow_color: str = "#000000AA"
    shadow_offset_x: int = 4
    shadow_offset_y: int = 4
    font_family: str = "Sans Serif"
    font_size: int = 64

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", GraphicType(self.type))
        object.__setattr__(self, "width", max(2, min(8192, int(self.width))))
        object.__setattr__(self, "height", max(2, min(8192, int(self.height))))
        object.__setattr__(self, "stroke_width", max(0, min(64, int(self.stroke_width))))
        object.__setattr__(self, "font_size", max(6, min(512, int(self.font_size))))
        object.__setattr__(self, "shadow_offset_x", max(-256, min(256, int(self.shadow_offset_x))))
        object.__setattr__(self, "shadow_offset_y", max(-256, min(256, int(self.shadow_offset_y))))
        object.__setattr__(self, "fill_color", normalize_color(self.fill_color, "#FFFFFF"))
        object.__setattr__(self, "stroke_color", normalize_color(self.stroke_color, "#000000"))
        object.__setattr__(self, "shadow_color", normalize_color(self.shadow_color, "#000000AA"))
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "source_path", str(self.source_path or ""))
        object.__setattr__(self, "font_family", str(self.font_family or "Sans Serif"))
        if self.type == GraphicType.IMAGE and not self.source_path:
            raise ValueError("Un calque image doit référencer un fichier source.")


def graphic_defaults(
    graphic_type: GraphicType | str,
    *,
    project_width: int = 1920,
    project_height: int = 1080,
    source_path: str = "",
) -> GraphicOverlay:
    kind = GraphicType(graphic_type)
    if kind == GraphicType.TEXT:
        return GraphicOverlay(type=kind, text="Votre titre")
    if kind == GraphicType.RECTANGLE:
        return GraphicOverlay(
            type=kind, text="", width=480, height=270,
            fill_color="#36E6C3", stroke_color="#FFFFFF", stroke_width=0,
        )
    if kind == GraphicType.SOLID:
        return GraphicOverlay(
            type=kind, text="", width=project_width, height=project_height,
            fill_color="#061514", shadow_offset_x=0, shadow_offset_y=0,
        )
    return GraphicOverlay(
        type=kind, text="", source_path=source_path,
        width=min(960, project_width), height=min(540, project_height),
    )


def ensure_graphics_track(project: Project) -> Track:
    existing = next((track for track in project.tracks if track.type == "graphics"), None)
    if existing is not None:
        return existing
    numbers = []
    for track in project.tracks:
        if track.id.startswith("G") and track.id[1:].isdigit():
            numbers.append(int(track.id[1:]))
    number = max(numbers, default=0) + 1
    track = Track(id=f"G{number}", name=f"G{number}", type="graphics")
    project.tracks.append(track)
    return track


def add_graphic_clip(
    project: Project,
    graphic_type: GraphicType | str,
    *,
    timeline_start: float,
    duration: float = 5.0,
    source_path: str = "",
) -> Clip:
    """Crée un média technique, une piste G si nécessaire et le clip."""
    start = max(0.0, float(timeline_start))
    duration = float(duration)
    if duration <= 0.0:
        raise ValueError("La durée d'un graphique doit être positive.")
    kind = GraphicType(graphic_type)
    if kind == GraphicType.IMAGE:
        path = Path(source_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Image graphique introuvable : {path}")
        source_path = str(path.resolve())
    graphic = graphic_defaults(
        kind,
        project_width=project.width,
        project_height=project.height,
        source_path=source_path,
    )
    token = uuid.uuid4().hex[:12]
    asset_id = f"graphic-asset-{token}"
    label = {
        GraphicType.TEXT: "Titre",
        GraphicType.RECTANGLE: "Rectangle",
        GraphicType.SOLID: "Aplat",
        GraphicType.IMAGE: Path(source_path).stem or "Image",
    }[kind]
    project.media_assets.append(
        MediaAsset(
            id=asset_id,
            path=source_path,
            name=label,
            duration=duration,
            width=graphic.width,
            height=graphic.height,
            fps=float(project.fps),
            media_type="graphic",
        )
    )
    track = ensure_graphics_track(project)
    clip = Clip(
        id=f"graphic-{token}",
        asset_id=asset_id,
        track_id=track.id,
        timeline_start=start,
        source_in=0.0,
        source_out=duration,
        label=label,
        text=graphic.text,
        graphic=graphic,
    )
    track.clips.append(clip)
    track.clips.sort(key=lambda item: (item.timeline_start, item.id))
    return clip


def update_graphic(clip: Clip, field_name: str, value: object) -> GraphicOverlay:
    if not isinstance(clip.graphic, GraphicOverlay):
        raise ValueError("Ce clip ne porte pas de calque graphique.")
    if field_name not in GraphicOverlay.__dataclass_fields__ or field_name == "type":
        raise ValueError(f"Propriété graphique inconnue : {field_name!r}.")
    graphic = replace(clip.graphic, **{field_name: value})
    clip.graphic = graphic
    if field_name == "text":
        clip.text = graphic.text
    return graphic


def graphic_to_dict(graphic: GraphicOverlay | None) -> dict | None:
    if not isinstance(graphic, GraphicOverlay):
        return None
    return {
        "type": graphic.type.value,
        "text": graphic.text,
        "source_path": graphic.source_path,
        "width": graphic.width,
        "height": graphic.height,
        "fill_color": graphic.fill_color,
        "stroke_color": graphic.stroke_color,
        "stroke_width": graphic.stroke_width,
        "shadow_color": graphic.shadow_color,
        "shadow_offset_x": graphic.shadow_offset_x,
        "shadow_offset_y": graphic.shadow_offset_y,
        "font_family": graphic.font_family,
        "font_size": graphic.font_size,
    }


def graphic_from_dict(raw: object) -> GraphicOverlay | None:
    if not isinstance(raw, dict):
        return None
    try:
        allowed = GraphicOverlay.__dataclass_fields__
        return GraphicOverlay(**{key: value for key, value in raw.items() if key in allowed})
    except (TypeError, ValueError):
        return None


__all__ = [
    "GraphicOverlay", "GraphicType", "add_graphic_clip", "ensure_graphics_track",
    "graphic_defaults", "graphic_from_dict", "graphic_to_dict", "update_graphic",
]
