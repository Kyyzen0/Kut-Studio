"""Règles du texte 3D extrudé (ADR-0009, lot « texte 3D ») : bornes et validation, sans Qt ni génération de géométrie.

Tout ce qui borne le coût d'un texte se décide ICI, avant que la moindre police soit ouverte :

- ``MAX_TEXT_CHARS`` caractères au plus (espaces et sauts de ligne compris) ;
- seuls les caractères d'écritures simples sont acceptés (latin, grec, cyrillique, ponctuation courante, monnaies) : une écriture
  qui demande une mise en forme (arabe, indien, thaï, hébreu...), un idéogramme, un emoji, un caractère combinant ou de contrôle
  est REFUSÉ avec son code (jamais remplacé par un carré ni ignoré) ;
- ``MAX_GLYPH_POINTS`` points de contour par caractère (vérifié à la génération) : c'est ce qui rend le nombre de triangles
  majorable avant de générer (:func:`triangle_bound`).
"""

from __future__ import annotations

import unicodedata

MAX_TEXT_CHARS = 40
MAX_FONT_CHARS = 64
MAX_GLYPH_POINTS = 160  # points de contour d'un caractère, tous contours confondus
BEVEL_MAX_RATIO = 0.01  # biseau <= 1 % de la taille : au-delà il dépasserait l'épaisseur des traits fins
ALIGNMENTS = ("left", "center", "right")
DEFAULT_FONT = "Anton"  # livrée avec l'application (assets/fonts) : même contour sur toutes les machines

_ALLOWED_RANGES = (
    (0x0020, 0x007E),  # latin de base
    (0x00A0, 0x024F),  # latin-1 et latin étendu A / B
    (0x0370, 0x03FF),  # grec
    (0x0400, 0x052F),  # cyrillique
    (0x1E00, 0x1EFF),  # latin étendu additionnel
    (0x2010, 0x2027),  # tirets, guillemets, puces
    (0x2030, 0x205E),  # pour mille, primes, guillemets simples...
    (0x20A0, 0x20CF),  # symboles monétaires
)
_BLANKS = "  "  # espaces : avance sans contour


def code(char: str) -> str:
    return f"U+{ord(char):04X}"


def check_text(value: object) -> str:
    """Le texte, après vérification ; ``ValueError`` en français sinon. ``\\n`` sépare les lignes."""
    if not isinstance(value, str):
        raise ValueError(f"text : un texte est attendu (reçu {type(value).__name__}).")
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError(
            f"text : {len(value)} caractères, plus que le maximum de {MAX_TEXT_CHARS}."
        )
    if not value.strip("  \n"):
        raise ValueError("text : un texte sans aucun caractère visible est refusé.")
    for char in value:
        if char == "\n":
            continue
        if unicodedata.category(char)[0] in "CM":
            raise ValueError(
                f"text : caractère de contrôle, combinant ou invisible {code(char)} refusé "
                "(écrivez le caractère accentué en un seul caractère)."
            )
        if not any(low <= ord(char) <= high for low, high in _ALLOWED_RANGES):
            raise ValueError(
                f"text : le caractère {code(char)} n'est pas pris en charge "
                "(latin, grec, cyrillique, chiffres, ponctuation et monnaies seulement : ni emoji, ni idéogramme, "
                "ni écriture à mise en forme complexe)."
            )
    return value


def check_font(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"font : un nom de famille est attendu (reçu {type(value).__name__})."
        )
    if not value.strip() or len(value) > MAX_FONT_CHARS:
        raise ValueError(
            f"font : un nom de famille de 1 à {MAX_FONT_CHARS} caractères est attendu."
        )
    if any(unicodedata.category(c)[0] == "C" for c in value) or value != value.strip():
        raise ValueError(
            "font : le nom de la famille contient un caractère de contrôle ou des espaces en bordure."
        )
    return value


def check_align(value: object) -> str:
    if value not in ALIGNMENTS:
        raise ValueError(
            f"align : « left », « center » ou « right » attendu (reçu {str(value)[:20]!r})."
        )
    return str(value)


def visible_chars(text: str) -> int:
    """Nombre de caractères qui produisent un contour (ni espace, ni saut de ligne)."""
    return sum(1 for c in text if c != "\n" and c not in _BLANKS)


def is_blank(char: str) -> bool:
    return char in _BLANKS


def strips(beveled: bool) -> int:
    """Bandes de paroi : 1 (droite) ou 3 (chanfrein avant, paroi, chanfrein arrière)."""
    return 3 if beveled else 1


def glyph_triangle_bound(beveled: bool) -> int:
    """Majorant des triangles d'UN caractère de ``MAX_GLYPH_POINTS`` points : faces avant et arrière (n + 2 trous par
    contour extérieur ; au plus un trou par 3 points) et ``2 n`` triangles par bande de paroi."""
    p = MAX_GLYPH_POINTS
    return 2 * (p + 2 * (p // 3)) + 2 * p * strips(beveled)


def glyph_vertex_bound(beveled: bool) -> int:
    """Majorant des sommets d'un caractère : faces avant et arrière + 4 sommets par arête et par bande."""
    return 2 * MAX_GLYPH_POINTS + 4 * MAX_GLYPH_POINTS * strips(beveled)


def triangle_bound(text: str, beveled: bool) -> int:
    return visible_chars(text) * glyph_triangle_bound(beveled)
