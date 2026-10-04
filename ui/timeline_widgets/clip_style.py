"""Le style d'un clip de la timeline : sa couleur de fond, ses étiquettes, sa bordure.

Séparé de :mod:`ui.timeline_widgets.clip_widget` pour garder ce dernier petit. Fonctions pures, testables sans widget.

**La couleur d'un clip dit sa catégorie, jamais son identité.** Vidéo, audio, titre, calque graphique, séquence imbriquée,
hors ligne : six fonds du thème (``clip_*_fill``), chacun avec un texte blanc lisible (≥ 4,5:1, vérifié par
``tests/test_design_tokens.py``). Autrefois la couleur était un hachage de l'identifiant : deux clips vidéo d'une même piste
pouvaient être bleu et violet, et rien ne distinguait un clip sélectionné d'un clip menthe. La sélection se lit maintenant à la
bordure d'accent *et* à un fond éclairci ; un clip désactivé s'efface dans le fond de la timeline.
"""

from __future__ import annotations

from ui.design_system import Radius, Typography, Weights
from ui.theme import BLACK, WHITE, ThemePalette, mix_colors

_BROKEN = {"missing", "cycle", "angle_missing"}

_SELECTED_LIGHTEN = 0.16
_HOVER_LIGHTEN = 0.07
_DISABLED_FADE = 0.62
_EDGE_DARKEN = 0.32


def clip_fill(view, palette: ThemePalette) -> str:
    """Fond d'un clip (``#rrggbb``) selon sa catégorie ; ``clip_broken_fill`` pour une séquence hors ligne ou circulaire."""
    if getattr(view, "is_nested", False) or getattr(view, "sequence_id", ""):
        status = getattr(view, "nested_status", "") or getattr(view, "status", "")
        return palette.clip_broken_fill if status in _BROKEN else palette.clip_nested_fill
    return {
        "video": palette.clip_video_fill,
        "audio": palette.clip_audio_fill,
        "subtitle": palette.clip_title_fill,
        "graphics": palette.clip_graphic_fill,
    }.get(getattr(view, "track_type", "video") or "video", palette.clip_video_fill)


def clip_surface(view, palette: ThemePalette, *, selected: bool = False, hovered: bool = False) -> str:
    """Le fond tel qu'il se peint : éclairci quand le clip est sélectionné (puis survolé), fondu dans la timeline s'il est désactivé."""
    fill = clip_fill(view, palette)
    if not getattr(view, "enabled", True):
        return mix_colors(fill, palette.timeline_bg, _DISABLED_FADE)
    if selected:
        return mix_colors(fill, WHITE, _SELECTED_LIGHTEN)
    if hovered:
        return mix_colors(fill, WHITE, _HOVER_LIGHTEN)
    return fill


def clip_edge(view, palette: ThemePalette, *, selected: bool) -> str:
    """Bordure : l'accent quand le clip est sélectionné, sinon un ton plus sombre du fond (un trait net, pas un contour)."""
    if selected:
        return palette.clip_border_selected
    return mix_colors(clip_fill(view, palette), BLACK, _EDGE_DARKEN)


def clip_body_style(view, palette: ThemePalette, *, selected: bool, hovered: bool = False) -> str:
    """Feuille de style du corps du clip. Le sélecteur est limité au corps : un ``QWidget`` nu peindrait aussi les libellés."""
    return (
        f"QWidget#clipBody {{ background: {clip_surface(view, palette, selected=selected, hovered=hovered)}; "
        f"border: 2px solid {clip_edge(view, palette, selected=selected)}; border-radius: {Radius.md}px; }}"
        f"QLabel {{ background: transparent; border: none; color: {palette.clip_text}; }}"
    )


def title_style(palette: ThemePalette) -> str:
    """Le nom du clip : le texte le plus lisible du clip, en semi-gras (le gras est réservé aux titres d'application)."""
    return (
        f"color: {palette.clip_text}; font-weight: {Weights.semibold}; font-size: {Typography.small}px; "
        "background: transparent;"
    )


def duration_style(palette: ThemePalette) -> str:
    """La durée : une métadonnée, plus discrète que le nom."""
    return f"color: {palette.clip_text_dim}; font-size: {Typography.micro}px; background: transparent;"


def cache_dot_color(state: str, palette: ThemePalette) -> str:
    """Pastille d'état du cache de rendu : succès (en cache), alerte (en cours), rien sinon."""
    return {"cached": palette.success, "pending": palette.warning}.get(state, "transparent")


__all__ = [
    "cache_dot_color",
    "clip_body_style",
    "clip_edge",
    "clip_fill",
    "clip_surface",
    "duration_style",
    "title_style",
]
