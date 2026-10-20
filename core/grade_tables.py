"""Tables de niveaux de l'exposition et des roues, pour l'étalonnage animé (ADR-0008, étape 6).

Pourquoi des tables. L'export statique applique l'exposition et les roues avec ``lutrgb`` (:mod:`core.color_wheels`) : FFmpeg
évalue l'expression pour chaque niveau d'entrée et en fait une table. Animée par ``sendcmd``, la même commande **recalcule la
table à chaque image** et coûte ×100 à ×200 le statique (mesuré, ADR-0008, « Résultats de l'étape 6 »). ``curves`` reçoit au
contraire sa courbe par commande pour le prix du statique ; pour qu'elle donne **exactement** les niveaux de ``lutrgb`` on lui
envoie un point par niveau d'entrée (256 points), à la hauteur du centre du niveau de sortie.

Ces fonctions refont, dans le même ordre d'opérations et avec les mêmes constantes arrondies à six décimales, l'expression que
:func:`core.color_wheels.exposure_filter` et :func:`core.color_wheels.wheels_filter` écrivent : leur sortie est comparée à
FFmpeg niveau par niveau (``tests/test_grade_render.py``).

Pourquoi ces deux détails de ``curves`` :

- ``curves`` refuse deux abscisses dans la même case de ``1/255`` (``(int)(x·255)`` doit croître strictement) : l'abscisse du
  niveau ``i`` est donc **juste au-dessus** de ``i/255`` (jamais en dessous : ``0,003922 × 255 = 1,0001`` mais ``0,007843 × 255 =
  1,9999``) ; c'est la division entière exacte qui la donne ;
- la table de sortie est tronquée : l'ordonnée est le **centre** de la case (``niveau + 0,5``), à plus d'un demi-niveau de toute
  frontière d'arrondi, ce qui absorbe l'écart de la dernière décimale écrite. Le blanc est ramené à 1.
"""

from __future__ import annotations

from .color_grading import ColorGrade
from .color_wheels import SRGB_TOE_ENCODED, SRGB_TOE_LINEAR, channel_transfer

LEVELS = 256
MAX_LEVEL = LEVELS - 1
POINT_BYTES = 17
"""Octets d'un point ``x/y`` écrit (``0.003922/0.001961 ``) : sert à borner la taille du fichier de commandes avant de l'écrire."""

_ENCODE_EXPONENT = float(f"{1.0 / 2.4:.6f}")
_ABSCISSAE: tuple[str, ...] = tuple(
    "0.000000" if i == 0 else "1.000000" if i == MAX_LEVEL else f"{(i * 1_000_000 // MAX_LEVEL + 1) / 1_000_000:.6f}"
    for i in range(LEVELS)
)


def _six(value: float) -> float:
    """``value`` tel que FFmpeg le relit après ``f"{value:.6f}"``."""
    return float(f"{value:.6f}")


def _level(value: float) -> int:
    """``clip(value*maxval+0.5, 0, maxval)`` puis troncature, comme ``lutrgb``."""
    scaled = value * MAX_LEVEL + 0.5
    return 0 if scaled <= 0.0 else MAX_LEVEL if scaled >= MAX_LEVEL else int(scaled)


def exposure_table(stops: float) -> tuple[int, ...]:
    """Niveau de sortie de chaque niveau d'entrée pour ``stops`` diaphragmes (la table de ``lutrgb`` de l'export statique)."""
    gain = _six(2.0 ** float(stops))
    table = []
    for level in range(LEVELS):
        value = level / MAX_LEVEL
        linear = value / 12.92 if value <= SRGB_TOE_ENCODED else ((value + 0.055) / 1.055) ** 2.4
        scaled = gain * linear
        encoded = 12.92 * scaled if scaled <= SRGB_TOE_LINEAR else 1.055 * scaled ** _ENCODE_EXPONENT - 0.055
        table.append(_level(encoded))
    return tuple(table)


def transfer_table(slope: float, base: float, power: float) -> tuple[int, ...]:
    """Table d'un canal de roue : ``clip(pow(max(0, pente·val/maxval + origine), exposant)·maxval + 0,5, 0, maxval)``."""
    slope, base, power = _six(slope), _six(base), _six(power)
    table = []
    for level in range(LEVELS):
        inner = slope * level / MAX_LEVEL + base
        table.append(_level((inner if inner > 0.0 else 0.0) ** power))
    return tuple(table)


def wheel_tables(grade: ColorGrade) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """Les trois tables (R, V, B) des roues de ``grade`` (identité si les roues sont neutres)."""
    return tuple(transfer_table(*channel_transfer(grade, index)) for index in range(3))     # type: ignore[return-value]


def curves_points(table: tuple[int, ...]) -> str:
    """Les 256 points ``x/y`` de ``table`` pour l'option d'un filtre ``curves`` (séparés par une espace)."""
    return " ".join(
        f"{_ABSCISSAE[level]}/{min(1.0, (output + 0.5) / MAX_LEVEL):.6f}" for level, output in enumerate(table)
    )


__all__ = [
    "LEVELS",
    "POINT_BYTES",
    "curves_points",
    "exposure_table",
    "transfer_table",
    "wheel_tables",
]
