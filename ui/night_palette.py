"""Mode nuit : la palette de l'interface sans sa lumière bleue (transformation des couleurs, jamais un voile).

:func:`night_palette` produit une **nouvelle** :class:`~ui.theme.ThemePalette` ; la palette d'origine n'est jamais modifiée (pas
d'état partagé). Seules les couleurs de l'interface sont transformées. Restent telles quelles :

* les couleurs d'identité des canaux de l'éditeur de courbes (``channel_*``) et des angles Multicam (``angle_colors``) ;
* tout ce qui n'est pas un jeton de palette : pixels du moniteur et de l'export, scopes, roue des teintes, miniatures. Les
  peintres qui montrent de l'analyse ou de l'image lisent :data:`ui.theme.PURE_COLORS` (la palette d'origine).

Transformation par couleur (``k`` = intensité / 100) : rouge × (1 − 0,04 k), vert × (1 − 0,18 k), bleu × (1 − 0,55 k), puis un
éclairage global × (1 − 0,06 k). Le réchauffement abîme le contraste des textes (le texte clair perd du vert et du bleu) : une
fois la palette transformée, :data:`CONTRAST_PAIRS` (texte sur fond, texte sur pastille, bordures) est vérifiée et la couleur de
**premier plan** est éclaircie ou assombrie, au plus juste, jusqu'au ratio de la palette d'origine (plafonné au seuil WCAG du
paire). ``tests/test_night_mode_contrast.py`` le prouve à 100 % sur les thèmes existants. Deux jetons distincts ne partagent jamais la même couleur après transformation
(``retheme_application`` s'appuie sur cette unicité pour revenir en arrière).
"""

from __future__ import annotations

from dataclasses import fields, replace

from core.night_mode import INTENSITY_RANGE
from ui.theme import BLACK, WHITE, ThemePalette

UNTOUCHED_TOKENS: frozenset[str] = frozenset({"channel_red", "channel_green", "channel_blue", "angle_colors"})
"""Jetons d'identité (canaux de couleur, angles) : le mode nuit n'y touche pas."""

_ARGB_TOKENS: frozenset[str] = frozenset({"playhead_dim", "snap_line"})
"""Jetons « #aarrggbb » (alpha en premier, pour ``QColor``) ; les autres sont « #rrggbb[aa] »."""

_FACTORS: tuple[float, float, float] = (0.04, 0.18, 0.55)
_DIM: float = 0.06
_MARGIN: float = 0.1
"""Marge de contraste (le nudge d'unicité d'un cran de bleu ne doit jamais repasser sous le seuil)."""

_TEXTS = ("text", "text_strong", "muted", "muted_strong", "disabled_text")
_SURFACES = ("background", "panel", "panel_alt", "panel_elevated", "surface", "input_bg")
_CLIP_FILLS = (
    "clip_video_fill", "clip_audio_fill", "clip_title_fill", "clip_graphic_fill", "clip_nested_fill", "clip_broken_fill",
)
CONTRAST_PAIRS: tuple[tuple[str, str, float], ...] = (
    *((text, surface, 4.5) for text in _TEXTS for surface in _SURFACES),
    ("accent", "panel", 4.5), ("accent", "background", 4.5), ("on_accent", "accent", 4.5),      # l'accent d'abord : il sert de fond ensuite
    *(("clip_text", fill, 4.5) for fill in _CLIP_FILLS),
    ("border_strong", "panel", 3.0), ("playhead", "timeline_bg", 3.0), ("selection_line", "panel", 3.0),
)
"""``(premier plan, fond, ratio minimal)`` : le ratio gardé est ``min(seuil, ratio de la palette d'origine)``."""


def night_color(color: str, intensity: int) -> str:
    """``#rrggbb`` (ou ``#rrggbbaa``) adouci ; l'alpha est conservé tel quel. Intensité 0 : la couleur, inchangée."""
    low, high = INTENSITY_RANGE
    k = max(low, min(high, int(intensity))) / high
    if k == 0:
        return color
    text = color.lstrip("#")
    channels = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    light = 1.0 - _DIM * k
    out = [round(c * (1.0 - f * k) * light) for c, f in zip(channels, _FACTORS)]
    return "#{:02X}{:02X}{:02X}".format(*out) + text[6:].upper()


def _is_color(value: object) -> bool:
    return isinstance(value, str) and value.startswith("#") and len(value) in (7, 9)


def _rgb_part(token: str, value: str) -> str:
    """``#RRGGBB`` du jeton (l'alpha, devant ou derrière, est mis de côté)."""
    return ("#" + value[3:9] if token in _ARGB_TOKENS else value[:7]).upper()


def _unique(night: str, used: set[str]) -> str:
    """``night`` s'il est libre, sinon la couleur voisine libre la plus proche (bleu ± 1, ± 2…)."""
    if night not in used:
        return night
    base = int(night[5:7], 16)
    for step in range(1, 256):
        for blue in (base + step, base - step):
            if 0 <= blue <= 255:
                candidate = night[:5] + f"{blue:02X}"
                if candidate not in used:
                    return candidate
    return night                                                                  # pragma: no cover - 256 voisins pris


def _luminance(rgb: str) -> float:
    def lin(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(rgb[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(first: str, second: str) -> float:
    """Ratio de contraste WCAG entre deux couleurs ``#rrggbb`` (1 à 21)."""
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _mix(color: str, extreme: str, amount: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(extreme[i:i + 2], 16) for i in (1, 3, 5)]
    return "#{:02X}{:02X}{:02X}".format(*(round(x + (y - x) * amount) for x, y in zip(a, b)))


def _repair(foreground: str, background: str, target: float) -> str:
    """``foreground`` éclairci (sur un fond plus sombre) ou assombri, par petits pas, jusqu'à ``target``."""
    extreme = WHITE if _luminance(foreground) >= _luminance(background) else BLACK
    amount = 0.0
    color = foreground
    while contrast_ratio(color, background) < target and amount < 1.0:
        amount = min(1.0, amount + 0.02)
        color = _mix(foreground, extreme, amount)
    return color


def night_palette(base: ThemePalette, intensity: int) -> ThemePalette:
    """Copie de ``base`` sans lumière bleue à ``intensity`` (0 à 100). Intensité 0 : ``base`` lui-même."""
    if max(INTENSITY_RANGE[0], min(INTENSITY_RANGE[1], int(intensity))) == 0:
        return base
    names = [f.name for f in fields(base) if f.name not in UNTOUCHED_TOKENS]
    originals: dict[str, set[str]] = {}
    for name in names:
        value = getattr(base, name)
        for item in value if isinstance(value, tuple) else (value,):
            if _is_color(item):
                originals.setdefault(_rgb_part(name, item), set()).add(name)
    used: set[str] = set()
    for name in UNTOUCHED_TOKENS:
        value = getattr(base, name)
        used.update(_rgb_part(name, item) for item in (value if isinstance(value, tuple) else (value,)) if _is_color(item))
    mapping = {rgb: night_color(rgb, intensity) for rgb in originals}              # ordre d'apparition : déterministe
    for foreground, background, threshold in CONTRAST_PAIRS:
        fg, bg = _rgb_part(foreground, getattr(base, foreground)), _rgb_part(background, getattr(base, background))
        target = min(threshold, contrast_ratio(fg, bg)) + _MARGIN
        if contrast_ratio(mapping[fg], mapping[bg]) < target:
            mapping[fg] = _repair(mapping[fg], mapping[bg], target)
    for rgb in originals:
        mapping[rgb] = _unique(mapping[rgb], used)
        used.add(mapping[rgb])

    def convert(name: str, value: str) -> str:
        night = mapping[_rgb_part(name, value)]
        if name in _ARGB_TOKENS:
            return "#" + value[1:3] + night[1:]
        return night + value[7:]

    changes: dict[str, object] = {}
    for name in names:
        value = getattr(base, name)
        if isinstance(value, tuple):
            changes[name] = tuple(convert(name, item) if _is_color(item) else item for item in value)
        elif _is_color(value):
            changes[name] = convert(name, value)
    return replace(base, **changes)


__all__ = ["CONTRAST_PAIRS", "UNTOUCHED_TOKENS", "contrast_ratio", "night_color", "night_palette"]
