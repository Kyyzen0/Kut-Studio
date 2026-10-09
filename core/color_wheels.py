"""Roues lift / gamma / gain / offset : la formule, son filtre FFmpeg exact, et le palet de l'interface.

Formule « Grade » (celle de Nuke), canal par canal ``c`` sur des valeurs normalisées ``0..1`` ; chaque roue apporte
sa composante de canal (le palet) et son maître ``y`` (la molette) :

- ``L = 0,25·(lift.c + lift.y)`` : le point noir, l'entrée 0 sort à ``L`` ;
- ``G = max(0, 1 + gain.c + gain.y)`` : le point blanc, l'entrée 1 sort à ``G`` ;
- ``Γ = 2^(gamma.c + gamma.y)`` : les tons moyens (``Γ > 1`` les éclaircit, les extrémités ne bougent pas) ;
- ``O = 0,25·(offset.c + offset.y)`` : tout le signal d'autant ;
- ``sortie = clip(max(0, (G − L)·entrée + L + O)^(1/Γ), 0, 1)``.

L'export l'applique avec ``lutrgb`` : FFmpeg évalue l'expression pour **chaque niveau d'entrée** de chaque canal et
en fait une table, donc aucune approximation (``+0.5`` : ``lutrgb`` tronque le résultat, on arrondit au plus proche).
L'étape vient après ``colorbalance`` et avant les courbes, dans la chaîne de
:func:`core.export_engine._build_color_grade_filters` ; le moniteur GPU cuit cette même chaîne en LUT 3D
(:mod:`core.gpu_grade`), les roues y sont donc en temps réel sans autre code.
"""

from __future__ import annotations

import math

from .color_grading import WHEEL_MAX, ColorGrade, Wheel

PUCK_REACH = 0.5
"""Décalage de canal maximal au bord de la roue : à fond vers le rouge, le gain rouge vaut 1,5 et le vert / bleu 0,75
(1 rendait le bord inutilisable, comme une roue qu'on ne pourrait pousser qu'à moitié)."""


def _channel(wheel: Wheel, index: int) -> float:
    return (wheel.r, wheel.g, wheel.b)[index] + wheel.y


def channel_transfer(grade: ColorGrade, index: int) -> tuple[float, float, float]:
    """``(pente, origine, exposant)`` du canal ``index`` (0 R, 1 V, 2 B) : ``sortie = max(0, pente·x + origine)^exposant``."""
    lift = 0.25 * _channel(grade.lift, index)
    gain = max(0.0, 1.0 + _channel(grade.gain, index))
    gamma = 2.0 ** _channel(grade.gamma, index)
    offset = 0.25 * _channel(grade.offset, index)
    return gain - lift, lift + offset, 1.0 / gamma


def wheels_filter(grade: ColorGrade) -> str:
    """Filtre ``lutrgb`` des roues (vide : roues neutres, rien à émettre).

    Expressions entre apostrophes (les virgules de ``clip(…)`` ne coupent pas la chaîne de filtres) et sans ``:``.
    """
    if grade.wheels_are_neutral():
        return ""
    terms = []
    for key, index in (("r", 0), ("g", 1), ("b", 2)):
        slope, base, power = channel_transfer(grade, index)
        terms.append(f"{key}='clip(pow(max(0,{slope:.6f}*val/maxval{base:+.6f}),{power:.6f})*maxval+0.5,0,maxval)'")
    return "lutrgb=" + ":".join(terms)


def wheel_from_puck(hue_degrees: float, radius: float, master: float = 0.0) -> Wheel:
    """Roue pour un palet poussé vers la teinte ``hue_degrees`` (0 rouge, 120 vert, 240 bleu), à ``radius`` (0 centre,
    1 bord) : les trois décalages sont de moyenne nulle, le palet change la couleur sans changer le niveau."""
    reach = PUCK_REACH * max(0.0, min(1.0, float(radius)))
    hue = math.radians(hue_degrees)
    r, g, b = (reach * math.cos(hue - math.radians(120.0 * k)) for k in range(3))
    return Wheel(r, g, b, max(-WHEEL_MAX, min(WHEEL_MAX, float(master))))


def puck_from_wheel(wheel: Wheel) -> tuple[float, float]:
    """``(teinte en degrés, rayon 0..1)`` du palet d'une roue (inverse de :func:`wheel_from_puck` ; une roue dont les
    décalages ne sont pas de moyenne nulle est projetée sur le plan de la chroma)."""
    x = wheel.r - (wheel.g + wheel.b) / 2.0
    y = math.sqrt(3.0) / 2.0 * (wheel.g - wheel.b)
    amplitude = math.hypot(x, y) / 1.5
    if amplitude < 1e-9:
        return 0.0, 0.0
    return math.degrees(math.atan2(y, x)) % 360.0, min(1.0, amplitude / PUCK_REACH)
