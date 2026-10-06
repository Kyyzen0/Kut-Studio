"""Calques graphiques non destructifs (tâche 32, motion graphics).

Les graphiques sont de vrais clips placés sur une piste ``graphics``.
Leur durée, leur transform et leurs images-clés utilisent donc les mêmes
primitives que les clips vidéo. Ce module reste pur : aucune dépendance Qt.

Modèle de calque
----------------

Un seul type, :class:`GraphicOverlay`, décrit **tous** les calques motion
graphics ; seul ``type`` change ce qui est dessiné :

=============  ==============================================================
``text``       texte (point ou boîte), contour, ombre, fond, alignements
``shape``      rectangle, rectangle arrondi, ellipse, ligne, polygone
``rectangle``  rectangle historique (tâche 32), dessiné comme une forme
``solid``      aplat de couleur
``image``      fichier image
``group``      groupe : ses membres (``group_id``) sont composés ensemble
``adjustment`` adjustment layer : applique ses effets à tout ce qui est dessous
``null``       contrôleur invisible, sert de parent
=============  ==============================================================

Ce qui est commun à tout calque vit ailleurs, sans duplication :
identifiant, nom, durée, in/out → le :class:`~core.project_model.Clip` ;
transform (avec ancrage, inclinaison, miroirs) → ``Clip.transform`` ;
masques et mode de fusion → ``Clip.compositing`` ; effets → ``Clip.effects`` ;
propriétés animées → ``Clip.transform_keyframes`` / ``Clip.animation``.
Ici : contenu, apparence, visibilité, verrou, parent, groupe, ordre.

Ajouter un type de calque : voir ``docs/motion-graphics.md``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, fields, replace
from enum import Enum
from pathlib import Path

from .project_model import Clip, MediaAsset, Project, Track


class GraphicType(str, Enum):
    TEXT = "text"
    RECTANGLE = "rectangle"
    SOLID = "solid"
    IMAGE = "image"
    SHAPE = "shape"
    GROUP = "group"
    ADJUSTMENT = "adjustment"
    NULL = "null"
    LIGHT = "light"


class ShapeKind(str, Enum):
    RECTANGLE = "rectangle"
    ROUNDED_RECTANGLE = "rounded_rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    POLYGON = "polygon"


class LayerLayout(str, Enum):
    """Interprétation de la position d'un calque.

    - ``anchor`` (calques créés depuis le moteur motion graphics) : la
      position (0 = centre du cadre) désigne l'endroit où se trouve le point
      d'ancrage, comme pour un clip vidéo.
    - ``legacy`` (projets de la tâche 32) : la position place le coin
      haut-gauche de l'image tournée. Gardé tel quel à l'ouverture d'un
      ancien projet pour ne rien déplacer.
    """

    ANCHOR = "anchor"
    LEGACY = "legacy"


CONTAINER_TYPES = frozenset({GraphicType.GROUP, GraphicType.NULL, GraphicType.ADJUSTMENT})
"""Calques sans pixels propres (aucun contenu rastérisé)."""

TEXT_ALIGN_H = ("left", "center", "right")
TEXT_ALIGN_V = ("top", "center", "bottom")
STROKE_POSITIONS = ("center", "outside")
"""Contour d'un texte : centré sur le bord des lettres et dessiné par-dessus (historique), ou **extérieur** (dessiné
sous le remplissage : l'intérieur des lettres reste intact, même en police condensée)."""
WORD_REVEALS = ("none", "word", "typewriter", "karaoke")
"""Apparition d'un texte selon ``reveal`` (0 → 1) : tout de suite, mot par mot, lettre par lettre, ou karaoké (tout est
visible, le mot courant prend ``highlight_color``)."""
MAX_TEXT_WORDS = 512
LIGHT_KINDS = ("leak", "anamorphic_flare", "speed_lines", "light_trails", "sparks", "flash", "grain")
"""Calques de lumière (dessin : :mod:`core.light_layers`) ; identifiants stables, clés ``light.kind.<id>``."""

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")


def normalize_color(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text.upper() if _HEX_COLOR.fullmatch(text) else fallback


def _bounded(value: object, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:
        return fallback
    return max(low, min(high, number))


def _int_tuple(value: object) -> tuple[int, ...]:
    """Indices de mots (uniques, triés, bornés) ; une valeur illisible donne un tuple vide."""
    try:
        items = {int(item) for item in (value or ())}
    except (TypeError, ValueError):
        return ()
    return tuple(sorted(item for item in items if 0 <= item < MAX_TEXT_WORDS))


def _time_tuple(value: object) -> tuple[float, ...]:
    """Temps des mots (secondes du clip), croissants et finis ; une liste abîmée est ignorée en entier."""
    try:
        times = tuple(float(item) for item in (value or ()))
    except (TypeError, ValueError):
        return ()
    if len(times) > MAX_TEXT_WORDS or any(t != t or t < 0.0 or t > 86400.0 for t in times):
        return ()
    if any(b < a for a, b in zip(times, times[1:])):
        return ()
    return times


@dataclass(frozen=True)
class GraphicOverlay:
    """Contenu, apparence et place dans la pile d'un calque graphique.

    La géométrie animable (position, échelle, rotation, opacité, ancrage…)
    reste dans ``Clip.transform``. Ici vivent uniquement les propriétés
    intrinsèques du calque, ce qui évite deux sources de vérité.
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
    # --- Calque (pile, hiérarchie) ---
    visible: bool = True
    locked: bool = False
    parent_id: str = ""
    group_id: str = ""
    z_order: int = 0
    motion_blur: bool = False
    layout: LayerLayout = LayerLayout.ANCHOR
    # --- Forme ---
    shape: ShapeKind = ShapeKind.RECTANGLE
    corner_radius: float = 0.0
    polygon_sides: int = 6
    fill_enabled: bool = True
    # --- Texte ---
    align_h: str = "center"
    align_v: str = "center"
    tracking: float = 0.0
    line_spacing: float = 1.0
    bold: bool = False
    italic: bool = False
    box_text: bool = True
    autosize: bool = False
    shadow_blur: float = 0.0
    background_enabled: bool = False
    background_color: str = "#000000AA"
    background_padding: int = 16
    background_radius: float = 0.0
    # --- Texte animé (vidéo sociale) ---
    stroke_position: str = "center"
    word_reveal: str = "none"
    reveal: float = 1.0
    highlight_color: str = "#FFD84D"
    highlight_words: tuple[int, ...] = ()
    word_times: tuple[float, ...] = ()
    # --- Néon (textes et formes) : halo additif flou autour du contenu ; rayon 0 = aucun ---
    glow_color: str = "#22B8FF"
    glow_radius: float = 0.0
    glow_strength: float = 1.0
    # --- Calque de lumière (core.light_layers) ---
    light_kind: str = "leak"
    light_seed: int = 1
    light_speed: float = 1.0
    light_angle: float = 0.0
    light_density: float = 1.0
    light_time: float = 0.0          # temps local de l'image dessinée : posé par la scène, jamais saisi

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", GraphicType(self.type))
        object.__setattr__(self, "width", max(2, min(8192, int(self.width))))
        object.__setattr__(self, "height", max(2, min(8192, int(self.height))))
        object.__setattr__(self, "stroke_width", max(0, min(256, int(self.stroke_width))))
        object.__setattr__(self, "font_size", max(6, min(512, int(self.font_size))))
        object.__setattr__(self, "shadow_offset_x", max(-256, min(256, int(self.shadow_offset_x))))
        object.__setattr__(self, "shadow_offset_y", max(-256, min(256, int(self.shadow_offset_y))))
        object.__setattr__(self, "fill_color", normalize_color(self.fill_color, "#FFFFFF"))
        object.__setattr__(self, "stroke_color", normalize_color(self.stroke_color, "#000000"))
        object.__setattr__(self, "shadow_color", normalize_color(self.shadow_color, "#000000AA"))
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "source_path", str(self.source_path or ""))
        object.__setattr__(self, "font_family", str(self.font_family or "Sans Serif"))
        # Calque
        object.__setattr__(self, "visible", bool(self.visible))
        object.__setattr__(self, "locked", bool(self.locked))
        object.__setattr__(self, "parent_id", str(self.parent_id or ""))
        object.__setattr__(self, "group_id", str(self.group_id or ""))
        object.__setattr__(self, "z_order", int(_bounded(self.z_order, -1_000_000, 1_000_000, 0)))
        object.__setattr__(self, "motion_blur", bool(self.motion_blur))
        try:
            layout = LayerLayout(self.layout)
        except ValueError:
            layout = LayerLayout.ANCHOR
        object.__setattr__(self, "layout", layout)
        # Forme
        try:
            shape = ShapeKind(self.shape)
        except ValueError:
            shape = ShapeKind.RECTANGLE
        object.__setattr__(self, "shape", shape)
        object.__setattr__(self, "corner_radius", _bounded(self.corner_radius, 0.0, 4096.0, 0.0))
        object.__setattr__(self, "polygon_sides", int(_bounded(self.polygon_sides, 3, 64, 6)))
        object.__setattr__(self, "fill_enabled", bool(self.fill_enabled))
        # Texte
        object.__setattr__(self, "align_h", self.align_h if self.align_h in TEXT_ALIGN_H else "center")
        object.__setattr__(self, "align_v", self.align_v if self.align_v in TEXT_ALIGN_V else "center")
        object.__setattr__(self, "tracking", _bounded(self.tracking, -100.0, 500.0, 0.0))
        object.__setattr__(self, "line_spacing", _bounded(self.line_spacing, 0.3, 5.0, 1.0))
        object.__setattr__(self, "bold", bool(self.bold))
        object.__setattr__(self, "italic", bool(self.italic))
        object.__setattr__(self, "box_text", bool(self.box_text))
        object.__setattr__(self, "autosize", bool(self.autosize))
        object.__setattr__(self, "shadow_blur", _bounded(self.shadow_blur, 0.0, 200.0, 0.0))
        object.__setattr__(self, "background_enabled", bool(self.background_enabled))
        object.__setattr__(self, "background_color", normalize_color(self.background_color, "#000000AA"))
        object.__setattr__(self, "background_padding", int(_bounded(self.background_padding, 0, 1024, 16)))
        object.__setattr__(self, "background_radius", _bounded(self.background_radius, 0.0, 1024.0, 0.0))
        # Texte animé
        object.__setattr__(self, "stroke_position",
                           self.stroke_position if self.stroke_position in STROKE_POSITIONS else "center")
        object.__setattr__(self, "word_reveal", self.word_reveal if self.word_reveal in WORD_REVEALS else "none")
        object.__setattr__(self, "reveal", _bounded(self.reveal, 0.0, 1.0, 1.0))
        object.__setattr__(self, "highlight_color", normalize_color(self.highlight_color, "#FFD84D"))
        object.__setattr__(self, "highlight_words", _int_tuple(self.highlight_words))
        object.__setattr__(self, "word_times", _time_tuple(self.word_times))
        object.__setattr__(self, "glow_color", normalize_color(self.glow_color, "#22B8FF"))
        object.__setattr__(self, "glow_radius", _bounded(self.glow_radius, 0.0, 400.0, 0.0))
        object.__setattr__(self, "glow_strength", _bounded(self.glow_strength, 0.0, 4.0, 1.0))
        object.__setattr__(self, "light_kind", self.light_kind if self.light_kind in LIGHT_KINDS else "leak")
        object.__setattr__(self, "light_seed", int(_bounded(self.light_seed, 0, 1_000_000, 1)))
        object.__setattr__(self, "light_speed", _bounded(self.light_speed, 0.0, 10.0, 1.0))
        object.__setattr__(self, "light_angle", _bounded(self.light_angle, -360.0, 360.0, 0.0))
        object.__setattr__(self, "light_density", _bounded(self.light_density, 0.1, 4.0, 1.0))
        object.__setattr__(self, "light_time", _bounded(self.light_time, 0.0, 86400.0, 0.0))
        if self.type == GraphicType.IMAGE and not self.source_path:
            raise ValueError("Un calque image doit référencer un fichier source.")

    @property
    def is_container(self) -> bool:
        """Groupe, contrôleur ou adjustment layer : pas de pixels propres."""
        return self.type in CONTAINER_TYPES

    @property
    def is_shape(self) -> bool:
        return self.type in (GraphicType.SHAPE, GraphicType.RECTANGLE)


_GRAPHIC_FIELDS = frozenset(f.name for f in fields(GraphicOverlay))


def graphic_defaults(
    graphic_type: GraphicType | str,
    *,
    project_width: int = 1920,
    project_height: int = 1080,
    source_path: str = "",
    shape: ShapeKind | str = ShapeKind.RECTANGLE,
) -> GraphicOverlay:
    kind = GraphicType(graphic_type)
    if kind == GraphicType.TEXT:
        return GraphicOverlay(type=kind, text="Votre titre")
    if kind == GraphicType.RECTANGLE:
        return GraphicOverlay(
            type=kind, text="", width=480, height=270,
            fill_color="#36E6C3", stroke_color="#FFFFFF", stroke_width=0,
        )
    if kind == GraphicType.SHAPE:
        shape_kind = ShapeKind(shape)
        if shape_kind == ShapeKind.LINE:
            return GraphicOverlay(
                type=kind, text="", width=480, height=24, shape=shape_kind,
                fill_enabled=False, stroke_color="#FFFFFF", stroke_width=6,
            )
        size = (320, 320) if shape_kind in (ShapeKind.ELLIPSE, ShapeKind.POLYGON) else (480, 270)
        return GraphicOverlay(
            type=kind, text="", width=size[0], height=size[1], shape=shape_kind,
            fill_color="#36E6C3", stroke_color="#FFFFFF", stroke_width=0,
            corner_radius=32.0 if shape_kind == ShapeKind.ROUNDED_RECTANGLE else 0.0,
        )
    if kind == GraphicType.SOLID:
        return GraphicOverlay(
            type=kind, text="", width=project_width, height=project_height,
            fill_color="#061514", shadow_offset_x=0, shadow_offset_y=0,
        )
    if kind in (GraphicType.GROUP, GraphicType.ADJUSTMENT):
        return GraphicOverlay(
            type=kind, text="", width=project_width, height=project_height,
            shadow_offset_x=0, shadow_offset_y=0,
        )
    if kind == GraphicType.NULL:
        return GraphicOverlay(type=kind, text="", width=100, height=100, shadow_offset_x=0, shadow_offset_y=0)
    if kind == GraphicType.LIGHT:
        # Couleurs « Night » : chaud (ambre) et bleu électrique ; le calque couvre le cadre.
        return GraphicOverlay(
            type=kind, text="", width=project_width, height=project_height, fill_color="#FFB040",
            glow_color="#22B8FF", shadow_offset_x=0, shadow_offset_y=0,
        )
    return GraphicOverlay(
        type=kind, text="", source_path=source_path,
        width=min(960, project_width), height=min(540, project_height),
    )


def ensure_graphics_track(project: Project) -> Track:
    # Ne jamais injecter silencieusement un nouveau clip dans une piste que
    # l'utilisateur a verrouillée ou masquée. Une nouvelle piste G visible est
    # moins surprenante et respecte les mêmes garanties d'édition que l'UI.
    existing = next(
        (
            track for track in project.tracks
            if track.type == "graphics" and not track.locked and track.visible
        ),
        None,
    )
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


_LABELS = {
    GraphicType.TEXT: "Titre",
    GraphicType.RECTANGLE: "Rectangle",
    GraphicType.SOLID: "Aplat",
    GraphicType.SHAPE: "Forme",
    GraphicType.GROUP: "Groupe",
    GraphicType.ADJUSTMENT: "Calque d'effets",
    GraphicType.NULL: "Contrôleur",
    GraphicType.LIGHT: "Lumière",
}

_SHAPE_LABELS = {
    ShapeKind.RECTANGLE: "Rectangle",
    ShapeKind.ROUNDED_RECTANGLE: "Rectangle arrondi",
    ShapeKind.ELLIPSE: "Ellipse",
    ShapeKind.LINE: "Ligne",
    ShapeKind.POLYGON: "Polygone",
}


def next_z_order(track: Track) -> int:
    """Rang au-dessus de tous les calques de la piste."""
    values = [
        clip.graphic.z_order for clip in track.clips if isinstance(clip.graphic, GraphicOverlay)
    ]
    return max(values, default=-1) + 1


def add_graphic_clip(
    project: Project,
    graphic_type: GraphicType | str,
    *,
    timeline_start: float,
    duration: float = 5.0,
    source_path: str = "",
    shape: ShapeKind | str = ShapeKind.RECTANGLE,
    graphic: GraphicOverlay | None = None,
    track: Track | None = None,
) -> Clip:
    """Crée un média technique, une piste G si nécessaire et le clip.

    ``graphic`` remplace le contenu par défaut (presets, collage) ; ``track``
    force la piste (sinon la première piste G éditable).
    """
    start = max(0.0, float(timeline_start))
    duration = float(duration)
    if duration <= 0.0:
        raise ValueError("La durée d'un graphique doit être positive.")
    kind = GraphicType(graphic.type if graphic is not None else graphic_type)
    if kind == GraphicType.IMAGE:
        path = Path(graphic.source_path if graphic is not None else source_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Image graphique introuvable : {path}")
        source_path = str(path.resolve())
    if graphic is None:
        graphic = graphic_defaults(
            kind,
            project_width=project.width,
            project_height=project.height,
            source_path=source_path,
            shape=shape,
        )
    elif kind == GraphicType.IMAGE:
        graphic = replace(graphic, source_path=source_path)
    target_track = track if track is not None else ensure_graphics_track(project)
    graphic = replace(graphic, z_order=next_z_order(target_track))
    token = uuid.uuid4().hex[:12]
    asset_id = f"graphic-asset-{token}"
    if kind == GraphicType.IMAGE:
        label = Path(source_path).stem or "Image"
    elif kind == GraphicType.SHAPE:
        label = _SHAPE_LABELS[graphic.shape]
    else:
        label = _LABELS[kind]
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
    clip = Clip(
        id=f"graphic-{token}",
        asset_id=asset_id,
        track_id=target_track.id,
        timeline_start=start,
        source_in=0.0,
        source_out=duration,
        label=label,
        text=graphic.text,
        graphic=graphic,
    )
    if kind == GraphicType.LIGHT:
        # La lumière s'**ajoute** à l'image : Addition par défaut ; le grain se fond en Incrustation (gris neutre).
        from .blend_modes import BlendMode
        from .compositing import Compositing

        grain = graphic.light_kind == "grain"
        clip.compositing = Compositing(blend_mode=BlendMode.OVERLAY if grain else BlendMode.ADD)
        if grain:
            from .visual_effects import ClipTransform

            clip.transform = ClipTransform(opacity=0.35)
    target_track.clips.append(clip)
    target_track.clips.sort(key=lambda item: (item.timeline_start, item.id))
    return clip


def update_graphic(clip: Clip, field_name: str, value: object) -> GraphicOverlay:
    if not isinstance(clip.graphic, GraphicOverlay):
        raise ValueError("Ce clip ne porte pas de calque graphique.")
    if field_name not in _GRAPHIC_FIELDS or field_name == "type":
        raise ValueError(f"Propriété graphique inconnue : {field_name!r}.")
    current = clip.graphic
    graphic = replace(current, **{field_name: value})
    if graphic == current:
        return current
    clip.graphic = graphic
    if field_name == "text":
        clip.text = graphic.text
    return graphic


def graphic_to_dict(graphic: GraphicOverlay | None) -> dict | None:
    if not isinstance(graphic, GraphicOverlay):
        return None
    data = {}
    for item in fields(GraphicOverlay):
        value = getattr(graphic, item.name)
        if isinstance(value, tuple):
            value = list(value)
        data[item.name] = value.value if isinstance(value, Enum) else value
    return data


def graphic_from_dict(raw: object) -> GraphicOverlay | None:
    if not isinstance(raw, dict):
        return None
    try:
        kwargs = {key: value for key, value in raw.items() if key in _GRAPHIC_FIELDS}
        # Un calque écrit avant le moteur motion graphics n'a pas de ``layout`` :
        # sa position désigne le coin haut-gauche (rendu historique conservé).
        kwargs.setdefault("layout", LayerLayout.LEGACY.value)
        return GraphicOverlay(**kwargs)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CONTAINER_TYPES", "GraphicOverlay", "GraphicType", "LayerLayout", "ShapeKind",
    "TEXT_ALIGN_H", "TEXT_ALIGN_V", "add_graphic_clip", "ensure_graphics_track",
    "graphic_defaults", "graphic_from_dict", "graphic_to_dict", "next_z_order",
    "normalize_color", "update_graphic",
]
