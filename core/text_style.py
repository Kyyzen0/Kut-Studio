"""Style non destructif des sous-titres / textes (tâche 24).

Ce module définit :class:`TextStyle` : un style typographique non
destructif appliqué aux clips de sous-titres (piste ``subtitle``).

Règles de conception :

- module pur (aucune dépendance PySide6 / FFmpeg) ;
- une seule classe porte tous les attributs visuels (police, taille,
  couleur, opacité, contour, ombre, alignement, position, fond et
  marges) ;
- les valeurs sont validées et bornées : la conversion vers ASS doit
  toujours produire un style cohérent ;
- les défauts représentent le *sous-titre standard* — un sous-titre sans
  style hérite de ce modèle à la lecture, garantissant la compatibilité
  ascendante des anciens fichiers ``.kut`` ;
- les fabriques statiques ``default_text_style`` et
  ``standard_subtitle_style`` partagent la même instance neutre pour
  économiser la mémoire.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------------
# Alignement
# ---------------------------------------------------------------------------


class TextAlignment(str, Enum):
    """Alignement 3×3 du bloc texte dans le cadre vidéo.

    Les valeurs sont ordonnées logiquement (haut → bas, gauche →
    droite) pour offrir un ordre stable aux IHM (la grille du panneau
    inspecteur).
    """

    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    MIDDLE_LEFT = "middle_left"
    MIDDLE_CENTER = "middle_center"
    MIDDLE_RIGHT = "middle_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


# ---------------------------------------------------------------------------
# Couleurs et conversions ASS
# ---------------------------------------------------------------------------


def _normalize_color(value: Any) -> str:
    """Convertit ``value`` en ``#rrggbb``.

    Formats acceptés :
    - ``"#RRGGBB"`` (forme canonique),
    - ``"#RGB"`` (forme courte → expandée),
    - ``"white"``, ``"red"``, etc. — uniquement les noms de la spec
      ASS : ``white``, ``black``, ``red``, ``green``, ``blue``,
      ``yellow``, ``cyan``, ``magenta``. Les autres noms lèvent
      ``ValueError`` pour éviter une mauvaise surprise à l'export.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("#"):
            hex_value = stripped[1:]
            if len(hex_value) == 3:
                hex_value = "".join(ch * 2 for ch in hex_value)
            if len(hex_value) != 6:
                raise ValueError(
                    f"Couleur hexadécimale invalide : {value!r}"
                )
            try:
                int(hex_value, 16)
            except ValueError as exc:
                raise ValueError(
                    f"Couleur hexadécimale invalide : {value!r}"
                ) from exc
            return f"#{hex_value.lower()}"
        if stripped.lower() in _ASS_COLOR_NAMES:
            return _ASS_COLOR_NAMES[stripped.lower()]
    raise ValueError(f"Couleur non supportée : {value!r}")


_ASS_COLOR_NAMES: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "green": "#00ff00",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "gray": "#808080",
    "grey": "#808080",
}


def _normalize_opacity(value: Any) -> float:
    """Borne ``value`` dans ``[0.0, 1.0]``."""
    try:
        opacity = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Opacité invalide : {value!r}") from exc
    if math.isnan(opacity) or math.isinf(opacity):
        raise ValueError(f"Opacité non finie : {value!r}")
    if opacity < 0.0:
        opacity = 0.0
    if opacity > 1.0:
        opacity = 1.0
    return opacity


def _normalize_size(value: Any) -> float:
    """Borne la taille de police dans ``[6, 240]`` (pt)."""
    try:
        size = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Taille de police invalide : {value!r}") from exc
    if math.isnan(size) or math.isinf(size):
        raise ValueError(f"Taille de police non finie : {value!r}")
    if size < 6.0:
        size = 6.0
    if size > 240.0:
        size = 240.0
    return size


def _normalize_thickness(value: Any) -> float:
    """Borne l'épaisseur du contour / le décalage de l'ombre."""
    try:
        thickness = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Valeur numérique invalide : {value!r}") from exc
    if math.isnan(thickness) or math.isinf(thickness):
        raise ValueError(f"Valeur non finie : {value!r}")
    if thickness < 0.0:
        thickness = 0.0
    if thickness > 20.0:
        thickness = 20.0
    return thickness


def _normalize_offset(value: Any) -> float:
    """Borne un décalage (position ou ombre) dans ``[-100, 100]``."""
    try:
        offset = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Décalage invalide : {value!r}") from exc
    if math.isnan(offset) or math.isinf(offset):
        raise ValueError(f"Décalage non fini : {value!r}")
    if offset < -100.0:
        offset = -100.0
    if offset > 100.0:
        offset = 100.0
    return offset


def _normalize_padding(value: Any) -> float:
    """Borne une marge interne dans ``[0, 80]`` (px)."""
    try:
        padding = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Marge invalide : {value!r}") from exc
    if math.isnan(padding) or math.isinf(padding):
        raise ValueError(f"Marge non finie : {value!r}")
    if padding < 0.0:
        padding = 0.0
    if padding > 80.0:
        padding = 80.0
    return padding


# ---------------------------------------------------------------------------
# Modèle principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextStyle:
    """Style typographique non destructif appliqué à un clip de sous-titre.

    Tous les attributs sont immuables : toute modification passe par
    :func:`dataclasses.replace`. Cela permet de réutiliser la même
    instance entre plusieurs clips et de garder le diffing d'historique
    rapide.

    Attributes:
        font_family: Famille de police (nom logique ou chemin absolu).
        font_size: Taille en points.
        color: Couleur du texte (forme ``#rrggbb``).
        opacity: Opacité du texte dans ``[0.0, 1.0]``.
        outline_color: Couleur du contour (forme ``#rrggbb``).
        outline_width: Épaisseur du contour en pixels (0 = pas de contour).
        shadow_color: Couleur de l'ombre portée.
        shadow_offset: Décalage vertical de l'ombre (px ; positif = bas).
        background_color: Couleur de fond du bloc texte (``None`` =
            aucun fond).
        background_opacity: Opacité du fond dans ``[0.0, 1.0]``.
        padding_x: Marge interne horizontale du fond (px).
        padding_y: Marge interne verticale du fond (px).
        alignment: Alignement 3×3 dans le cadre vidéo.
        position_x: Décalage horizontal fin (%, ``-100`` à ``100``).
        position_y: Décalage vertical fin (%, ``-100`` à ``100``).
        margin_x: Marge latérale vis-à-vis du bord (px).
        margin_y: Marge haute/basse vis-à-vis du bord (px).
    """

    font_family: str = "Arial"
    font_size: float = 32.0
    color: str = "#ffffff"
    opacity: float = 1.0
    outline_color: str = "#000000"
    outline_width: float = 2.0
    shadow_color: str = "#000000"
    shadow_offset: float = 1.5
    background_color: str | None = None
    background_opacity: float = 0.0
    padding_x: float = 12.0
    padding_y: float = 6.0
    alignment: TextAlignment = TextAlignment.BOTTOM_CENTER
    position_x: float = 0.0
    position_y: float = 0.0
    margin_x: float = 32.0
    margin_y: float = 48.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "font_family", str(self.font_family))
        object.__setattr__(self, "font_size", _normalize_size(self.font_size))
        object.__setattr__(self, "color", _normalize_color(self.color))
        object.__setattr__(self, "opacity", _normalize_opacity(self.opacity))
        object.__setattr__(self, "outline_color", _normalize_color(self.outline_color))
        object.__setattr__(self, "outline_width", _normalize_thickness(self.outline_width))
        object.__setattr__(self, "shadow_color", _normalize_color(self.shadow_color))
        object.__setattr__(self, "shadow_offset", _normalize_thickness(self.shadow_offset))
        if self.background_color is not None:
            object.__setattr__(self, "background_color", _normalize_color(self.background_color))
        object.__setattr__(
            self, "background_opacity", _normalize_opacity(self.background_opacity)
        )
        object.__setattr__(self, "padding_x", _normalize_padding(self.padding_x))
        object.__setattr__(self, "padding_y", _normalize_padding(self.padding_y))
        object.__setattr__(self, "alignment", TextAlignment(self.alignment))
        object.__setattr__(self, "position_x", _normalize_offset(self.position_x))
        object.__setattr__(self, "position_y", _normalize_offset(self.position_y))
        object.__setattr__(self, "margin_x", _normalize_padding(self.margin_x))
        object.__setattr__(self, "margin_y", _normalize_padding(self.margin_y))

    # ----- Accesseurs immuables -----------------------------------------

    def with_updates(self, **changes: Any) -> "TextStyle":
        """Retourne une copie avec les champs mis à jour."""
        return replace(self, **changes)

    # ----- Sérialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Sérialisation JSON stable pour les fichiers ``.kut``."""
        return {
            "font_family": self.font_family,
            "font_size": float(self.font_size),
            "color": self.color,
            "opacity": float(self.opacity),
            "outline_color": self.outline_color,
            "outline_width": float(self.outline_width),
            "shadow_color": self.shadow_color,
            "shadow_offset": float(self.shadow_offset),
            "background_color": self.background_color,
            "background_opacity": float(self.background_opacity),
            "padding_x": float(self.padding_x),
            "padding_y": float(self.padding_y),
            "alignment": self.alignment.value,
            "position_x": float(self.position_x),
            "position_y": float(self.position_y),
            "margin_x": float(self.margin_x),
            "margin_y": float(self.margin_y),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TextStyle":
        """Désérialise depuis un dict JSON.

        Un payload ``None``, vide ou invalide renvoie le style standard.
        Un payload partiel est fusionné avec les défauts, ce qui
        protège la compatibilité ascendante (lecture des anciens
        ``.kut`` qui ne stockaient pas encore de style).
        """
        if not isinstance(payload, Mapping):
            return default_text_style()
        try:
            return cls(
                font_family=str(payload.get("font_family", "Arial")),
                font_size=_safe_float(payload.get("font_size"), default=32.0),
                color=_safe_str(payload.get("color"), default="#ffffff"),
                opacity=_safe_float(payload.get("opacity"), default=1.0),
                outline_color=_safe_str(payload.get("outline_color"), default="#000000"),
                outline_width=_safe_float(payload.get("outline_width"), default=2.0),
                shadow_color=_safe_str(payload.get("shadow_color"), default="#000000"),
                shadow_offset=_safe_float(payload.get("shadow_offset"), default=1.5),
                background_color=_safe_optional_color(payload.get("background_color")),
                background_opacity=_safe_float(payload.get("background_opacity"), default=0.0),
                padding_x=_safe_float(payload.get("padding_x"), default=12.0),
                padding_y=_safe_float(payload.get("padding_y"), default=6.0),
                alignment=TextAlignment(_safe_str(payload.get("alignment"), default="bottom_center")),
                position_x=_safe_float(payload.get("position_x"), default=0.0),
                position_y=_safe_float(payload.get("position_y"), default=0.0),
                margin_x=_safe_float(payload.get("margin_x"), default=32.0),
                margin_y=_safe_float(payload.get("margin_y"), default=48.0),
            )
        except ValueError:
            # Une valeur corrompue ne doit pas faire échouer l'ouverture
            # du projet : on retombe sur le style standard.
            return default_text_style()


def _safe_float(value: Any, *, default: float) -> float:
    """Conversion tolérante vers ``float`` avec repli sur ``default``."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, *, default: str) -> str:
    """Conversion tolérante vers ``str`` avec repli sur ``default``."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _safe_optional_color(value: Any) -> str | None:
    """Renvoie la couleur normalisée ou ``None`` si vide."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return _normalize_color(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Style par défaut (sous-titre standard)
# ---------------------------------------------------------------------------


DEFAULT_TEXT_STYLE: TextStyle = TextStyle(
    font_family="Arial",
    font_size=32.0,
    color="#ffffff",
    opacity=1.0,
    outline_color="#000000",
    outline_width=2.0,
    shadow_color="#000000",
    shadow_offset=1.5,
    background_color=None,
    background_opacity=0.0,
    padding_x=12.0,
    padding_y=6.0,
    alignment=TextAlignment.BOTTOM_CENTER,
    position_x=0.0,
    position_y=0.0,
    margin_x=32.0,
    margin_y=48.0,
)


def default_text_style() -> TextStyle:
    """Renvoie le :class:`TextStyle` standard (sous-titre par défaut)."""
    return DEFAULT_TEXT_STYLE


def is_default_style(style: TextStyle | None) -> bool:
    """``True`` si ``style`` est identique au style standard."""
    if style is None:
        return True
    return style == DEFAULT_TEXT_STYLE


# ---------------------------------------------------------------------------
# Helpers pour FFmpeg / ASS
# ---------------------------------------------------------------------------


def ass_color(color: str, alpha: float = 1.0) -> str:
    """Convertit ``#rrggbb`` + alpha en ``&HBBGGRR&`` (format ASS).
    ``alpha`` est dans ``[0.0, 1.0]`` ; ``1.0`` = totalement opaque.
    """
    if not color.startswith("#") or len(color) != 7:
        raise ValueError(f"Couleur ASS invalide : {color!r}")
    rr = color[1:3]
    gg = color[3:5]
    bb = color[5:7]
    # ASS stocke l'alpha comme ``&HAA`` : 00 = opaque, FF = transparent.
    alpha_byte = int(round((1.0 - _normalize_opacity(alpha)) * 255))
    alpha_byte = max(0, min(255, alpha_byte))
    return f"&H{alpha_byte:02X}{bb.upper()}{gg.upper()}{rr.upper()}&"


def alignment_to_ass(alignment: TextAlignment) -> int:
    """Convertit un :class:`TextAlignment` en code d'alignement ASS (1-9).

    L'ordre ASS est inversé verticalement par rapport au nôtre :
    ``1`` = bas-gauche, ``9`` = haut-droite. On retrouve la grille 3×3
    classique attendue par FFmpeg/libass.
    """
    mapping: dict[TextAlignment, int] = {
        TextAlignment.BOTTOM_LEFT: 1,
        TextAlignment.BOTTOM_CENTER: 2,
        TextAlignment.BOTTOM_RIGHT: 3,
        TextAlignment.MIDDLE_LEFT: 4,
        TextAlignment.MIDDLE_CENTER: 5,
        TextAlignment.MIDDLE_RIGHT: 6,
        TextAlignment.TOP_LEFT: 7,
        TextAlignment.TOP_CENTER: 8,
        TextAlignment.TOP_RIGHT: 9,
    }
    return mapping[TextAlignment(alignment)]


def alignment_rows() -> Sequence[tuple[TextAlignment, ...]]:
    """Retourne la grille 3×3 des alignements (haut → bas)."""
    return (
        (TextAlignment.TOP_LEFT, TextAlignment.TOP_CENTER, TextAlignment.TOP_RIGHT),
        (TextAlignment.MIDDLE_LEFT, TextAlignment.MIDDLE_CENTER, TextAlignment.MIDDLE_RIGHT),
        (TextAlignment.BOTTOM_LEFT, TextAlignment.BOTTOM_CENTER, TextAlignment.BOTTOM_RIGHT),
    )


__all__ = [
    "DEFAULT_TEXT_STYLE",
    "TextAlignment",
    "TextStyle",
    "alignment_rows",
    "alignment_to_ass",
    "ass_color",
    "default_text_style",
    "is_default_style",
]


# Imports tolérants : ``Sequence`` est utilisé à l'exécution via
# ``alignment_rows`` ; on garde l'import groupé pour la lisibilité.
_ = field  # pour éviter un lint ``field imported but unused``